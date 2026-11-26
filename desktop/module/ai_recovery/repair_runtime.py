"""Cooperative idle handshake and fresh worker heartbeats for safe code reloads."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid


def runtime_root():
    from module.ai_recovery.reporting import log_root
    return log_root()/'code_repair'/'runtime'


class RuntimeReporter:
    def __init__(self,account,root=None):
        self.root=Path(root) if root else runtime_root()
        self.account=account
        self.token=uuid.uuid4().hex
        self.phase='starting';self.wake=0.0
        self.path=self.root/(hashlib.sha256(account.encode()).hexdigest()[:16]+'.json')

    def write(self,phase=None,*,wake=None,maintenance=''):
        if phase is not None:self.phase=phase
        if wake is not None:self.wake=float(wake)
        data={'account':self.account,'pid':os.getpid(),'instance':self.token,'at':time.time(),
              'phase':self.phase,'wake':self.wake,'maintenance':maintenance}
        self.root.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_name(self.path.name+'.'+self.token+'.tmp')
        temp.write_text(json.dumps(data),encoding='utf-8');temp.replace(self.path)

    def intent(self):
        try:
            value=json.loads((self.root/'maintenance.json').read_text(encoding='utf-8'))
            return value if float(value.get('expires',0))>time.time() else None
        except (OSError,ValueError,TypeError):return None

    def gate(self,phase='running',*,wake=None,sleep=time.sleep):
        intent=self.intent()
        while intent:
            self.write('maintenance',wake=wake,maintenance=str(intent.get('token','')))
            sleep(0.5)
            intent=self.intent()
        self.write(phase,wake=wake)


def task_feedback(account,task,success):
    from module.ai_recovery.config import load_config
    cfg=load_config(config_name=account)
    if not (cfg.enabled and cfg.code_repair.enabled):return
    from module.ai_recovery.repair_store import RepairStore
    RepairStore(runtime_root().parent.parent).task_finished(account,task,bool(success))


def ensure_worker(account):
    """The worker survives account reloads, while a file lease prevents duplicates."""
    from module.ai_recovery.config import load_config,ROOT
    cfg=load_config(config_name=account)
    if not (cfg.enabled and cfg.auto and cfg.code_repair.enabled):return False
    root=runtime_root();root.mkdir(parents=True,exist_ok=True)
    status=root/'worker.json'
    try:
        item=json.loads(status.read_text(encoding='utf-8'))
        import psutil
        if time.time()-float(item['at'])<120 and psutil.pid_exists(int(item['pid'])):return True
    except (OSError,ValueError,KeyError,TypeError):pass
    import subprocess,sys
    env=dict(os.environ);env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONUTF8']='1'
    with (root.parent/'worker.log').open('ab') as log:
        subprocess.Popen([sys.executable,'-X','utf8','-B','-m','module.ai_recovery.repair_worker','--account',account],
            cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return True
