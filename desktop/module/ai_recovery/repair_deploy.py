"""Journaled deployment, cooperative worker reload and conflict-aware rollback."""
from __future__ import annotations

import asyncio
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

from module.ai_recovery.repair_context import source_path,digest,assert_unclaimed


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_bytes(json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8'));temp.replace(path)


class OASControl:
    def __init__(self,log_root,port=22267):
        self.root=Path(log_root)/'code_repair'/'runtime'
        self.base='http://127.0.0.1:'+str(int(port))
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.token=uuid.uuid4().hex

    def http(self,path):
        with self.opener.open(self.base+path,timeout=10) as response:
            return json.load(response)

    def states(self):
        names=self.http('/config_list')
        import websockets
        async def read():
            result={}
            for name in names:
                if name in ('Home','template'):continue
                url='ws://127.0.0.1:'+self.base.rsplit(':',1)[1]+'/ws/'+urllib.parse.quote(name,safe='')
                async with websockets.connect(url,open_timeout=5) as ws:
                    for _ in range(4):
                        value=json.loads(await asyncio.wait_for(ws.recv(),5))
                        if 'state' in value:result[name]=int(value['state']);break
                    else:raise RuntimeError('OAS worker state missing')
            return result
        return asyncio.run(read())

    def heartbeat(self,name):
        path=self.root/(hashlib.sha256(name.encode()).hexdigest()[:16]+'.json')
        try:return json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}

    def safe(self,states):
        now=time.time()
        for name,state in states.items():
            if state==0:continue
            heartbeat=self.heartbeat(name)
            if state==2:
                import psutil
                if not heartbeat.get('pid') or not psutil.pid_exists(int(heartbeat['pid'])):continue
            if state!=1 or heartbeat.get('phase')!='idle' or now-float(heartbeat.get('at',0))>20:
                return False
            if float(heartbeat.get('wake',0))<now+90:return False
        return True

    def freeze(self,states):
        if not self.safe(states):raise RuntimeError('账号正在执行任务，等待空闲窗口')
        atomic_json(self.root/'maintenance.json',{'token':self.token,'expires':time.time()+180})
        active=[name for name,state in states.items() if state==1]
        deadline=time.time()+15
        while time.time()<deadline:
            if all(self.heartbeat(name).get('phase')=='maintenance' and
                   self.heartbeat(name).get('maintenance')==self.token and
                   time.time()-float(self.heartbeat(name).get('at',0))<5 for name in active):
                break
            time.sleep(.5)
        else:
            self.clear();raise RuntimeError('账号未确认空闲维护，未停止任何任务')
        # Recheck the application state after acknowledgement before stopping.
        if self.states()!=states:self.clear();raise RuntimeError('账号状态已变化')
        stopped=[]
        try:
            for name in active:
                self.http('/'+urllib.parse.quote(name,safe='')+'/stop');stopped.append(name)
        except Exception:
            self.clear();self.resume(stopped);raise
        return stopped

    def clear(self):
        path=self.root/'maintenance.json'
        try:
            if json.loads(path.read_text(encoding='utf-8')).get('token')==self.token:path.unlink()
        except (OSError,ValueError):pass

    def resume(self,names):
        self.clear()
        states=self.states()
        for name in names:
            if states.get(name)==2:
                self.http('/'+urllib.parse.quote(name,safe='')+'/stop');states[name]=0
            if states.get(name)==0:self.http('/'+urllib.parse.quote(name,safe='')+'/start')

    def healthy(self,names,old_instances):
        deadline=time.time()+25
        while time.time()<deadline:
            states=self.states()
            if all(states.get(name)==1 and self.heartbeat(name).get('instance')!=old_instances.get(name)
                   and self.heartbeat(name).get('phase') in ('idle','running','scheduling')
                   and time.time()-float(self.heartbeat(name).get('at',0))<15 for name in names):return True
            time.sleep(.5)
        return not names


class ExecutorLock:
    def __init__(self,repo,owner):self.repo,self.owner=Path(repo),owner;self.store=None
    def __enter__(self):
        path=self.repo/'dev_tools/dual_agent/state.py'
        if not path.exists():raise RuntimeError('缺少正式执行锁，不应用代码')
        spec=importlib.util.spec_from_file_location('oas_code_repair_executor_state',path)
        mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
        self.store=mod.State(self.repo/'dev_tools/dual_agent/state',self.repo)
        acquired,_=self.store.acquire_lock('executor',self.owner,300)
        if not acquired:self.store.close();self.store=None;raise RuntimeError('另一个维护任务持有执行锁')
        return self
    def __exit__(self,*args):
        if self.store:
            self.store.release_lock('executor',self.owner);self.store.close()


class RepairDeployer:
    def __init__(self,repo,store,*,control=None,lock_factory=None,checkpoint=True):
        self.repo,self.store=Path(repo).resolve(),store
        self.control=control or OASControl(store.root)
        self.lock_factory=lock_factory or (lambda owner:ExecutorLock(self.repo,owner))
        self.checkpoint=checkpoint

    def artifact(self,job,validate_candidate=True):
        folder=Path(job['artifact']).resolve()
        if not folder.is_relative_to((self.store.root/'code_repair'/'work').resolve()):raise ValueError('invalid repair artifact')
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        if not manifest.get('files') or not manifest.get('test',{}).get('passed'):raise ValueError('untested repair')
        for name,hashes in manifest['files'].items():
            source_path(self.repo,name)
            if validate_candidate and digest((folder/'candidate'/name).read_bytes())!=hashes['after']:raise ValueError('candidate changed after test')
        return folder,manifest

    def board(self,job,names,status,message):
        path=self.repo/'AI_COORD.md'
        if not path.exists():return
        text=path.read_text(encoding='utf-8-sig')
        label='OAS 自动维修 '+job['id'][:8]
        lines=[line for line in text.splitlines() if not (line.startswith('|') and label in line)]
        text='\n'.join(lines).rstrip()+'\n'
        row='| '+'、'.join('`'+name+'`' for name in names)+f' | {label} | '+datetime.now().strftime('%Y-%m-%d %H:%M')+f' | {status} |'
        text=text.replace('|---|---|---|---|','|---|---|---|---|\n'+row,1)
        header='## 交接记录（最新在上）'
        note=f'\n- {datetime.now():%Y-%m-%d %H:%M} | {label} | {message}\n'
        text=text.replace(header,header+'\n'+note,1)
        path.write_bytes(text.encode('utf-8'))

    def commit(self,names,message):
        if not self.checkpoint:return
        paths=list(names)
        if (self.repo/'AI_COORD.md').exists():paths.append('AI_COORD.md')
        command=['git','-c','safe.directory='+self.repo.as_posix(),'-C',str(self.repo)]
        subprocess.run(command+['add','--']+paths,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
        pending=subprocess.run(command+['diff','--cached','--quiet','--']+paths,timeout=15)
        if pending.returncode==1:
            subprocess.run(command+['commit','--only','-m',message,'--']+paths,check=True,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        elif pending.returncode:raise RuntimeError('无法检查源码检查点')

    def apply(self,job):
        folder,manifest=self.artifact(job);names=list(manifest['files'])
        with self.lock_factory('oas-code-repair:'+job['id']):
            assert_unclaimed(self.repo,names)
            for name,hashes in manifest['files'].items():
                if digest(source_path(self.repo,name).read_bytes())!=hashes['before']:
                    raise RuntimeError('源码已被其他修改更新，原补丁不再适用')
            states=self.control.states()
            if not self.control.safe(states):return False
            journal={'phase':'preparing','files':manifest['files'],'resume':[n for n,s in states.items() if s==1],
                     'old_instances':{n:self.control.heartbeat(n).get('instance') for n,s in states.items() if s==1}}
            for name in names:
                backup=folder/'backup'/name;backup.parent.mkdir(parents=True,exist_ok=True)
                backup.write_bytes((self.repo/name).read_bytes())
            atomic_json(folder/'journal.json',journal)
            self.store.update(job['id'],'applying','已备份源码，等待账号维护确认')
            stopped=[]
            try:
                stopped=self.control.freeze(states)
            except Exception:
                self.store.update(job['id'],'ready','维护未获得空闲确认，保留已测试补丁')
                self.control.clear()
                return False
            try:
                journal['phase']='writing';atomic_json(folder/'journal.json',journal)
                self.board(job,names,'进行中','离线原错复现/修复回归通过，账号已确认空闲维护；应用限定源码补丁。')
                for name,hashes in manifest['files'].items():
                    target=source_path(self.repo,name)
                    if digest(target.read_bytes())!=hashes['before']:raise RuntimeError('Concurrent source edit')
                    temp=target.with_name(target.name+'.'+job['id']+'.tmp')
                    temp.write_bytes((folder/'candidate'/name).read_bytes());temp.replace(target)
                journal['phase']='reloading';atomic_json(folder/'journal.json',journal)
                self.control.resume(stopped)
                if not self.control.healthy(stopped,journal['old_instances']):raise RuntimeError('新工作进程未通过启动检查')
                journal['phase']='probation';atomic_json(folder/'journal.json',journal)
                self.board(job,names,'完成','补丁已加载，等待原任务正常结束；若仍失败则自动回滚。')
                self.commit(names,'fix: autonomous OAS repair '+job['id'][:8])
                self.store.update(job['id'],'probation','测试和重载通过，等待原任务完成反馈',applied_at=time.time())
                return True
            except Exception as error:
                self.control.clear()
                self.store.reject(job,manifest['signature'],type(error).__name__)
                current_states=self.control.states()
                if not self.control.safe(current_states):
                    self.store.update(job['id'],'rollback_pending','重载未通过，等待安全边界回滚')
                    return False
                more_stopped=self.control.freeze(current_states)
                try:self._restore(job,folder,manifest)
                finally:self.control.resume(list(dict.fromkeys(stopped+more_stopped)))
                raise
            finally:self.control.clear()

    def _restore(self,job,folder,manifest):
        conflicts=[]
        # Validate all backups before changing the first live file.
        backups={}
        for name,hashes in manifest['files'].items():
            backups[name]=(folder/'backup'/name).read_bytes()
            if digest(backups[name])!=hashes['before']:raise RuntimeError('rollback backup corrupted')
        for name,hashes in manifest['files'].items():
            target=source_path(self.repo,name);current=digest(target.read_bytes())
            if current==hashes['before']:continue
            if current!=hashes['after']:
                conflicts.append(name);continue
            backup=backups[name]
            temp=target.with_name(target.name+'.rollback.tmp');temp.write_bytes(backup);temp.replace(target)
        status='needs_attention' if conflicts else 'rolled_back'
        self.board(job,manifest['files'],'闲置' if conflicts else '完成',
                   '回滚遇到其他人的后续修改，保留现场：'+','.join(conflicts) if conflicts else '补丁未通过后续验证，已恢复原源码。')
        self.store.update(job['id'],status,'回滚冲突: '+','.join(conflicts) if conflicts else '已回滚原源码')
        if not conflicts:self.commit(manifest['files'],'revert: autonomous OAS repair '+job['id'][:8])
        atomic_json(folder/'journal.json',{'phase':status,'files':manifest['files']})
        return not conflicts

    def rollback(self,job):
        folder,manifest=self.artifact(job,validate_candidate=False)
        with self.lock_factory('oas-code-repair:'+job['id']):
            assert_unclaimed(self.repo,manifest['files'],ignore_owner='OAS 自动维修 '+job['id'][:8])
            states=self.control.states()
            if not self.control.safe(states):return False
            try:journal=json.loads((folder/'journal.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):journal={}
            resume=list(journal.get('resume') or []) if journal.get('phase') in ('preparing','writing','reloading') else []
            atomic_json(folder/'journal-before-rollback.json',{'resume':resume})
            stopped=self.control.freeze(states)
            try:
                ok=self._restore(job,folder,manifest)
                self.store.reject(job,manifest['signature'],'task_failed_after_deployment')
            finally:
                # Recover account processes stopped by an interrupted rollout.
                try:journal=json.loads((folder/'journal-before-rollback.json').read_text(encoding='utf-8'))
                except (OSError,ValueError):journal={}
                self.control.resume(list(dict.fromkeys(stopped+list(journal.get('resume') or []))))
                self.control.clear()
            return ok
