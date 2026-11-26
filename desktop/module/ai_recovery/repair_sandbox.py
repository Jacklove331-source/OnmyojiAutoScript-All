"""Offline behavioral replay. Never imports an OAS task or a real device.

The generated regression sees selected real AST functions with mock dependencies.
Its child process has no credentials, network/process operations or write access.
This is a bounded Python replay harness, not a general-purpose OS sandbox.
"""
from __future__ import annotations

import ast
import builtins
import collections
import datetime
import functools
import itertools
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import types
import unittest
import unittest.mock


def run_child(tree, test_file, changed):
    tree=Path(tree).resolve();test_file=Path(test_file).resolve()
    sys.dont_write_bytecode=True
    allowed_modules={'unittest':unittest,'unittest.mock':unittest.mock,'types':types,
        'datetime':datetime,'re':re,'math':math,'collections':collections,
        'functools':functools,'itertools':itertools,'json':json}
    allowed_builtins=dict(vars(builtins))
    for key in ('open','exec','eval','compile','input','breakpoint','exit','quit'):
        allowed_builtins.pop(key,None)
    original_import=builtins.__import__
    def limited_import(name,globals=None,locals=None,fromlist=(),level=0):
        if level or name not in allowed_modules:raise ImportError('offline regression import blocked: '+name)
        return original_import(name,globals,locals,fromlist,level)
    allowed_builtins['__import__']=limited_import
    loaded=[];executed={}
    def load_symbol(path,symbol,namespace=None):
        target=(tree/str(path)).resolve()
        if not target.is_relative_to(tree) or target.suffix!='.py':raise ValueError('source escape')
        parsed=ast.parse(target.read_text(encoding='utf-8-sig'),filename=str(target))
        node=parsed
        for part in str(symbol).split('.'):
            node=next(n for n in node.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.name==part)
        if isinstance(node,ast.AsyncFunctionDef):raise ValueError('async replay unsupported')
        node.decorator_list=[]
        env={'__builtins__':allowed_builtins,'__name__':'offline_replay',
             'datetime':datetime.datetime,'timedelta':datetime.timedelta,'re':re,'math':math}
        if namespace:
            if any(str(key).startswith('__') for key in namespace):raise ValueError('reserved namespace')
            env.update(namespace)
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(target),'exec'),env)
        loaded.append({'path':str(path),'symbol':symbol})
        return env[node.name]
    # Keep the source/test bytes available before restricting file access.
    test_code=test_file.read_text(encoding='utf-8')
    stdlib=Path(sys.base_prefix).resolve()
    def audit(event,args):
        if event.startswith(('socket.','subprocess.','ctypes.')) or event in {
            'os.system','os.spawn','os.posix_spawn','os.fork','os.exec','os.startfile',
            'os.remove','os.rename','os.rmdir','os.mkdir','os.symlink','os.link','os.chdir',
            'shutil.copyfile','shutil.rmtree','winreg.OpenKey','winreg.SetValue'}:
            raise PermissionError('offline replay blocked: '+event)
        if event=='open':
            value,mode,flags=args
            if isinstance(value,int):return
            p=Path(value).resolve()
            writable=(isinstance(mode,str) and any(c in mode for c in 'wax+')) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
            if writable:raise PermissionError('offline replay is read only')
            if p.is_relative_to(tree) or p==test_file:return
            if p.is_relative_to(stdlib) and p.suffix.lower() in {'.py','.pyc','.pyd','.dll'}:return
            raise PermissionError('offline replay read blocked')
    sys.addaudithook(audit)
    def trace(frame,event,arg):
        if event=='line' and frame.f_code.co_name!='<module>':
            try:name=Path(frame.f_code.co_filename).resolve().relative_to(tree).as_posix()
            except (ValueError,OSError):return trace
            if name in changed:executed.setdefault(name,set()).add(frame.f_lineno)
        return trace
    module=types.ModuleType('offline_regression')
    module.__dict__.update({'__builtins__':allowed_builtins,'load_symbol':load_symbol})
    sys.settrace(trace)
    try:
        exec(compile(test_code,str(test_file),'exec'),module.__dict__)
        suite=unittest.defaultTestLoader.loadTestsFromModule(module)
        import io
        output=io.StringIO()
        result=unittest.TextTestRunner(stream=output,verbosity=2).run(suite)
        report={'ok':result.wasSuccessful() and result.testsRun>0,'tests':result.testsRun,
                'failures':len(result.failures),'errors':len(result.errors),
                'output':output.getvalue()[-7000:],'loaded':loaded,
                'executed':{name:sorted(lines) for name,lines in executed.items()}}
    except Exception as error:
        report={'ok':False,'tests':0,'failures':0,'errors':1,'error':type(error).__name__+': '+str(error)[:500],
                'loaded':loaded,'executed':{name:sorted(lines) for name,lines in executed.items()}}
    finally:sys.settrace(None)
    print(json.dumps(report,ensure_ascii=False))


def replay(workspace,timeout=30):
    if not workspace.patches or not workspace.test_code:raise ValueError('需要补丁和行为回归测试')
    test=workspace.folder/'regression.py'
    test.write_text(workspace.test_code,encoding='utf-8')
    changed={}
    import difflib
    for name in workspace.patches:
        ast.parse((workspace.candidate/name).read_text(encoding='utf-8-sig'))
        before=(workspace.base/name).read_text(encoding='utf-8-sig').splitlines()
        after=(workspace.candidate/name).read_text(encoding='utf-8-sig').splitlines()
        changed[name]=sorted({i+1 for tag,a,b,c,d in difflib.SequenceMatcher(a=before,b=after).get_opcodes()
                              if tag!='equal' for i in range(c,max(d,min(len(after),c+1)))})
    reports={}
    # Inherit only runtime necessities; no API credentials or user's home path.
    env={key:value for key,value in os.environ.items() if key.upper() in
         {'SYSTEMROOT','WINDIR','PATH','PATHEXT','COMSPEC','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE'}}
    env.update({'TEMP':str(workspace.folder),'TMP':str(workspace.folder),'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'})
    for label,tree in (('before',workspace.base),('after',workspace.candidate)):
        try:
            proc=subprocess.run([sys.executable,'-I','-X','utf8','-B',str(Path(__file__).resolve()),
                str(tree),str(test),json.dumps(changed)],cwd=workspace.folder,env=env,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,encoding='utf-8',errors='replace')
            reports[label]=json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode==0 else {
                'ok':False,'tests':0,'errors':1,'error':proc.stderr[-1000:]}
        except (subprocess.TimeoutExpired,ValueError,IndexError) as error:
            reports[label]={'ok':False,'tests':0,'errors':1,'error':type(error).__name__}
    before,after=reports['before'],reports['after']
    exercised=all(set(lines)&set(after.get('executed',{}).get(name,[])) for name,lines in changed.items() if lines)
    # A syntax error or broken test setup does not prove reproduction. Require
    # an actual assertion failure on the old behavior, then clean new behavior.
    passed=(before.get('tests',0)>0 and before.get('failures',0)>0 and not before.get('ok')
            and after.get('ok') and after.get('tests',0)>=2 and exercised)
    from module.ai_recovery.repair_context import digest
    report={'passed':bool(passed),'baseline':before,'candidate':after,'changed_lines_exercised':exercised,
            'source_hashes':{name:digest((workspace.candidate/name).read_bytes()) for name in workspace.patches}}
    (workspace.folder/'test-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    workspace.report=report
    return report


if __name__=='__main__':run_child(sys.argv[1],sys.argv[2],json.loads(sys.argv[3]))
