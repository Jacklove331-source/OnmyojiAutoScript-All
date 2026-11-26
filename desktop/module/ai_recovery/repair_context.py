"""Bounded source access, sanitization and reviewable exact-match patches."""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import time

PROTECTED = ('module/ai_recovery/','module/server/','dev_tools/','config/','.git/',
             'toolkit/','mobile/','deploy/')


def digest(data):return hashlib.sha256(data).hexdigest()


def redact(text, secrets=()):
    text = str(text)
    for secret in secrets:
        if secret:text = text.replace(secret,'<redacted>')
    text = re.sub(r'(?i)(Bearer\s+)[^\s"\']+',r'\1<redacted>',text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{10,}\b','<redacted>',text)
    return text


def source_path(root, name):
    name = str(name).replace('\\','/')
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or ':' in name or p.suffix != '.py':
        raise ValueError('只允许仓库内相对 Python 源码路径')
    if not name.startswith(('tasks/','module/')) or name.startswith(PROTECTED):
        raise ValueError('受保护路径不可由自动维修修改/读取: '+name)
    root = Path(root).resolve()
    target = root.joinpath(*p.parts).resolve()
    if not target.is_relative_to(root) or target.is_symlink():raise ValueError('path escape')
    # Reject symlink/junction traversal even when the endpoint remains inside.
    current = root
    for part in p.parts:
        current = current/part
        if current.is_symlink() or (current.exists() and current.resolve()!=current.absolute()):
            raise ValueError('indirect source path')
    return target


_CLAIM_TS = re.compile(r'(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?')


def _claim_timestamp(line: str) -> float | None:
    """行内可解析的认领时间（本地时区）→ 时间戳；解析不到返回 None。"""
    match = _CLAIM_TS.search(line)
    if match is None:
        return None
    text = match.group(1) + (' ' + match.group(2) if match.group(2) else '')
    try:
        return time.mktime(time.strptime(text, '%Y-%m-%d' if match.group(2) is None else '%Y-%m-%d %H:%M'))
    except (ValueError, OverflowError, OSError):
        return None


def active_claims(root, ignore_owner='', *, max_age_hours: float = 24.0, now: float | None = None):
    board = Path(root)/'AI_COORD.md'
    if not board.exists():return []
    current = time.time() if now is None else float(now)
    max_age_s = float(max_age_hours) * 3600.0
    result = []
    for line in board.read_text(encoding='utf-8-sig').splitlines():
        if ignore_owner and ignore_owner in line:continue
        if line.startswith('|') and '进行中' in line:
            ts = _claim_timestamp(line)
            if ts is not None and current - ts > max_age_s:
                # 认领时间早于 max_age_hours：视为陈旧过期认领，直接忽略
                continue
            for name in re.findall(r'`([^`]+)`',line):
                value = name.replace('\\','/').split('（',1)[0].strip().rstrip('/')
                if value.startswith(('tasks/','module/')):result.append(value)
    return result


def assert_unclaimed(root, names, ignore_owner='', *, max_age_hours: float = 24.0, now: float | None = None):
    for claim in active_claims(root, ignore_owner, max_age_hours=max_age_hours, now=now):
        for name in names:
            if name == claim or name.startswith(claim+'/') or (claim.endswith('*.py') and name.startswith(claim[:-4])):
                raise ValueError('另一个维护任务正在认领: '+claim)


class SourceWorkspace:
    def __init__(self, repo, folder, cfg, payload, *, secrets=()):
        self.repo, self.folder = Path(repo).resolve(),Path(folder).resolve()
        self.cfg, self.payload, self.secrets = cfg,payload,secrets
        self.base, self.candidate = self.folder/'baseline',self.folder/'candidate'
        self.files = {}
        self.patches = {}
        self.test_code = ''
        self.report = None
        self.allowed = set()

    def create(self):
        self.folder.mkdir(parents=True,exist_ok=False)
        primary = str(self.payload.get('source_path') or '')
        if not primary:
            task = str(self.payload.get('task') or '')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,60}',task):raise ValueError('invalid task')
            primary = f'tasks/{task}/script_task.py'
        source_path(self.repo,primary)
        self.allowed.add(primary)
        # Source-only snapshot: no account config, credentials, logs, emulator
        # handles or live process objects are copied into the candidate.
        for top in ('tasks','module'):
            for path in (self.repo/top).rglob('*.py'):
                name = path.relative_to(self.repo).as_posix()
                try:source_path(self.repo,name)
                except ValueError:continue
                data = path.read_bytes()
                if len(data)>300000:continue
                self.files[name] = digest(data)
                for tree in (self.base,self.candidate):
                    dest=tree/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        if primary not in self.files:raise ValueError('fault source not available')
        # Only source locations named by the real traceback and the task's own
        # directory may be edited. Discovery elsewhere remains read-only.
        for frame in self.payload.get('frames') or []:
            name = frame.get('path','')
            if name in self.files:self.allowed.add(name)
        for name in self.files:
            if primary.startswith('tasks/') and name.startswith('/'.join(primary.split('/')[:2])+'/'):
                self.allowed.add(name)
        return self

    def read(self, name, start=1, lines=120):
        source_path(self.candidate,name)
        if name not in self.files:raise ValueError('source unavailable')
        text = (self.candidate/name).read_text(encoding='utf-8-sig').splitlines()
        start=max(1,int(start));lines=max(1,min(160,int(lines)))
        return {'path':name,'sha256':self.files[name],'editable':name in self.allowed,
                'content':self.clean('\n'.join(f'{i+1}: {text[i]}' for i in range(start-1,min(len(text),start-1+lines))))}

    def search(self, query):
        query=str(query)
        if not query or len(query)>120:raise ValueError('invalid search')
        hits=[]
        for name in self.files:
            for i,line in enumerate((self.candidate/name).read_text(encoding='utf-8-sig').splitlines()):
                if query.lower() in line.lower():
                    hits.append({'path':name,'line':i+1,'text':self.clean(line[:220])})
                    if len(hits)>=20:return hits
        return hits

    def clean(self, text):return redact(text,self.secrets)

    def patch(self, name, old, new):
        source_path(self.candidate,name)
        if name not in self.allowed:raise ValueError('修改范围超出真实故障源码')
        if name not in self.patches and len(self.patches)>=self.cfg.max_files:raise ValueError('too many files')
        if not isinstance(old,str) or not old or not isinstance(new,str) or len(new)>14000:raise ValueError('invalid patch')
        data=(self.candidate/name).read_text(encoding='utf-8-sig')
        if data.count(old)!=1:raise ValueError('old text must match exactly once')
        changed=data.replace(old,new,1)
        tree=ast.parse(changed,filename=name)
        # A repair may adjust existing behavior, but cannot introduce process,
        # network, dynamic-code or credential access as a new capability.
        previous=ast.parse(data,filename=name)
        def risky(t):
            found=set()
            for node in ast.walk(t):
                if isinstance(node,(ast.Import,ast.ImportFrom)):
                    names=[alias.name for alias in node.names] if isinstance(node,ast.Import) else [node.module or '']
                    for value in names:
                        if value.split('.')[0] in {'os','sys','subprocess','socket','ctypes','requests','shutil','importlib','builtins'}:
                            found.add(ast.dump(node,include_attributes=False))
                if isinstance(node,ast.Call):
                    value=ast.unparse(node.func)
                    if value in {'exec','eval','compile','open','__import__'} or any(part in value for part in
                        ('.system','.Popen','.unlink','.rmtree','.write','.connect','.request','.environ',
                         '.adb_shell','.emulator_start','.app_start','.click','.swipe','.long_click')):
                        found.add(ast.dump(node,include_attributes=False))
                    elif any(part in value for part in ('.run','.remove','.replace')):
                        # 普通 Python 的 str.replace / list.remove / timer.run 不是危险能力；
                        # 只有 os/subprocess/shutil/path/file 这类接收者上的同名调用才拦截。
                        lowered=value.lower()
                        if any(keyword in lowered for keyword in ('os','subprocess','shutil','path','file')):
                            found.add(ast.dump(node,include_attributes=False))
            return found
        if risky(tree)-risky(previous):raise ValueError('补丁引入了新的外部执行/文件/网络能力')
        base=(self.base/name).read_text(encoding='utf-8-sig')
        diff=list(difflib.unified_diff(base.splitlines(True),changed.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
        all_patches=dict(self.patches);all_patches[name]=''.join(diff)
        count=sum(sum(line.startswith(('+','-')) and not line.startswith(('+++','---')) for line in p.splitlines())
                  for p in all_patches.values())
        if count>self.cfg.max_changed_lines:raise ValueError('patch too large')
        (self.candidate/name).write_text(changed,encoding='utf-8')
        self.patches=all_patches;self.report=None
        return {'changed':name,'changed_lines':count}

    def set_test(self, code):
        if not isinstance(code,str) or len(code)>9000:raise ValueError('invalid regression test')
        ast.parse(code)
        self.test_code=code;self.report=None
        return {'regression_saved':True}

    def manifest(self):
        if not self.report or not self.report.get('passed'):raise ValueError('尚未通过原错复现和修复验证')
        for name,expected in self.report.get('source_hashes',{}).items():
            if digest((self.candidate/name).read_bytes())!=expected:raise ValueError('测试后源码发生变化')
        data={'files':{name:{'before':self.files[name],'after':digest((self.candidate/name).read_bytes())}
                       for name in self.patches},'test':self.report,'task':self.payload['task']}
        data['signature']=digest(json.dumps(data['files'],sort_keys=True).encode())
        (self.folder/'manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        (self.folder/'change.patch').write_text(''.join(self.patches.values()),encoding='utf-8')
        (self.folder/'regression.py').write_text(self.test_code,encoding='utf-8')
        return data
