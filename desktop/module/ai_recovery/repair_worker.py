"""Persistent local code-repair service. No scheduler task cards or shell agent."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import time

from module.ai_recovery.config import ROOT,load_config
from module.ai_recovery.evidence import EvidenceStore
from module.ai_recovery.learning import LearningMemory,error_fingerprint
from module.ai_recovery.repair_agent import RepairAgent,RepairDeferredError
from module.ai_recovery.repair_context import source_path,redact,assert_unclaimed
from module.ai_recovery.repair_deploy import RepairDeployer,OASControl,atomic_json
from module.ai_recovery.repair_store import RepairStore,TERMINAL
from module.ai_recovery.runtime import RecoveryLease


def enqueue_fault(cfg,root,anomaly,result):
    if not (cfg.enabled and cfg.auto and cfg.code_repair.enabled):return None
    if result.outcome.value=='recovered':return None
    if anomaly.type not in {'script_error','task_exception'}:return None
    evidence=anomaly.evidence or {}
    payload={'task':anomaly.task,'kind':anomaly.type,'error':str(anomaly.detail)[:1200],
             'fingerprint':error_fingerprint(anomaly),'revision':str(evidence.get('source_revision') or ''),
             'source_path':str(evidence.get('source_path') or ''),'frames':evidence.get('frames') or [],
             'incident':anomaly.incident_id,'evidence_dir':result.evidence_dir,
             'recovery_result':result.message[:500]}
    payload['legacy_id']=hashlib.sha256((cfg.config_name+'|'+error_fingerprint(anomaly)).encode()).hexdigest()[:20]
    key=cfg.api_key or cfg.resolve_api_key()
    payload=json.loads(redact(json.dumps(payload,ensure_ascii=False),[key]))
    return RepairStore(root).enqueue(cfg.config_name,payload)


def _resolve_primary_source(repo, job):
    """把 payload 的 source_path 解析成真实业务源码，工具库/受保护帧自动跳过。

    顺序：payload.source_path → reversed(frames) 中第一个合法业务路径 →
    tasks/<task>/script_task.py。解析结果写回 job['payload']['source_path']。
    """
    payload = job.get('payload') or {}
    candidate = str(payload.get('source_path') or '')
    if candidate:
        try:
            source_path(repo, candidate)
            return candidate
        except ValueError:
            pass
    for frame in reversed(payload.get('frames') or []):
        name = str(frame.get('path') or '')
        if not name:
            continue
        try:
            source_path(repo, name)
            return name
        except ValueError:
            continue
    return f'tasks/{job["task"]}/script_task.py'


def prune_old_workdirs(root, store, keep_days: float = 7.0, now=None) -> int:
    """清理 code_repair/work 下早于 keep_days 的旧沙盒目录。

    任何非终态/观察期任务（deferred/ready/probation/…）正在引用的 artifact
    目录一律跳过。返回清理掉的目录数；异常由调用方决定是否吞掉。
    """
    current = time.time() if now is None else float(now)
    work = Path(root) / 'code_repair' / 'work'
    if not work.is_dir():
        return 0
    cutoff = current - float(keep_days) * 86400.0
    active: set[Path] = set()
    try:
        for job in store.list():
            if job.get('status') not in {'succeeded', 'rolled_back', 'exhausted', 'superseded'}:
                artifact = str(job.get('artifact') or '')
                if artifact:
                    active.add(Path(artifact).resolve())
    except Exception:  # noqa: BLE001 - 门店不可读时保守地不清任何目录
        return 0
    removed = 0
    try:
        for child in work.iterdir():
            try:
                if not child.is_dir() or child.resolve() in active:
                    continue
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        return removed
    return removed


class RepairEngine:
    def __init__(self,repo=ROOT,log_root=None,*,agent_factory=None,deployer=None):
        self.repo=Path(repo)
        self.root=Path(log_root) if log_root else EvidenceStore().root
        self.store=RepairStore(self.root)
        self.agent_factory=agent_factory or (lambda cfg:RepairAgent(cfg,self.store,self.repo))
        self.deployer=deployer or RepairDeployer(self.repo,self.store)
        self._last_prune=0.0

    def import_legacy(self,account):
        from module.ai_recovery.reporting import pending_repairs
        from module.ai_recovery.types import Anomaly,RecoveryResult,Outcome
        cfg=load_config(config_name=account)
        for item in pending_repairs(account):
            if item.get('anomaly') not in {'script_error','task_exception'}:continue
            folder=Path(item.get('evidence_dir') or '').resolve()
            if not folder.is_relative_to((self.root/'incidents').resolve()):continue
            try:
                anomaly=Anomaly.from_dict(json.loads((folder/'anomaly.json').read_text(encoding='utf-8')))
                # Never repair a historical failure against already changed code.
                name=(anomaly.evidence or {}).get('source_path')
                revision=(anomaly.evidence or {}).get('source_revision')
                if name and revision and hashlib.sha256(source_path(self.repo,name).read_bytes()).hexdigest()[:16]!=revision:
                    continue
                enqueue_fault(cfg,self.root,anomaly,RecoveryResult(Outcome.STOPPED,anomaly.incident_id,
                    message=item.get('message',''),evidence_dir=str(folder)))
            except (OSError,ValueError,TypeError):continue

    def tick(self,account):
        cfg=load_config(config_name=account,force=True)
        if not (cfg.enabled and cfg.auto and cfg.code_repair.enabled):return {'disabled':True}
        self.import_legacy(account)
        now=time.time()
        if now-self._last_prune>=3600.0:
            self._last_prune=now
            try:prune_old_workdirs(self.store.root,self.store,
                                  keep_days=cfg.code_repair.work_retention_days)
            except OSError:pass  # 清理失败绝不影响维修主流程
        for job in self.store.list(account=account,statuses=['applying','rollback_pending']):
            try:self.deployer.rollback(job)
            except Exception:
                # A competing maintenance lock/claim must not prevent other
                # accounts or independent repairs from making progress.
                continue
        for job in self.store.list(account=account,statuses=['probation']):
            if time.time()-job['applied_at']>cfg.code_repair.probation_s:
                # Time passing is not proof of successful repair.
                self.store.update(job['id'],'rollback_pending','观察期内原任务未确认完成，保守回滚')
        if cfg.code_repair.auto_apply:
            for job in self.store.list(account=account,statuses=['ready']):
                try:self.deployer.apply(job)
                except Exception as error:
                    current=self.store.get(job['id'])
                    if current['status'] not in TERMINAL|{'rollback_pending'}:
                        self.store.update(job['id'],'deferred',redact(str(error),[cfg.api_key]),
                                          next_try=time.time()+cfg.code_repair.retry_backoff_s)
        memory=LearningMemory(cfg,self.root)
        if not memory.provider_allowed():return {'provider_backoff':True}
        job=self.store.claim_next(account,cfg.code_repair.max_attempts)
        if job is None:return {'idle':True}
        try:
            primary=_resolve_primary_source(self.repo,job)
            job['payload']['source_path']=primary
            target=source_path(self.repo,primary)
            if job['revision'] and digest_revision(target)!=job['revision']:
                self.store.update(job['id'],'superseded','故障后源码已经更新，丢弃旧版本维修请求')
                return {'job':job['id'],'status':'superseded'}
            assert_unclaimed(self.repo,[primary],max_age_hours=cfg.code_repair.claim_max_age_hours)
            log=self.repo/'log'/f'{datetime.now():%Y-%m-%d}_{account}.txt'
            if log.exists():
                with log.open('rb') as stream:
                    stream.seek(max(0,log.stat().st_size-11000))
                    job['payload']['log_tail']=redact(stream.read().decode('utf-8',errors='replace'),
                                                     [cfg.api_key or cfg.resolve_api_key()])[-8000:]
            self.agent_factory(cfg).run(job)
            memory.provider_result(success=True)
            current=self.store.get(job['id'])
            if current['status']=='ready' and cfg.code_repair.auto_apply:self.deployer.apply(current)
            return {'job':job['id'],'status':self.store.get(job['id'])['status']}
        except Exception as error:
            from module.ai_recovery.client import AiError
            if isinstance(error,AiError):memory.provider_result(success=False,reason=type(error).__name__)
            current=self.store.get(job['id'])
            if isinstance(error,RepairDeferredError) or (
                    isinstance(error,ValueError) and '另一个维护任务正在认领' in str(error)):
                # 软延期：认领冲突/维修预算暂缓是可恢复的，绝不因此耗尽 max_attempts
                if current['status'] not in TERMINAL|{'rollback_pending','probation','ready'}:
                    self.store.update(job['id'],'deferred',
                        redact(type(error).__name__+': '+str(error),[cfg.api_key or cfg.resolve_api_key()]),
                        attempts=max(0,int(current['attempts'])-1),
                        next_try=time.time()+cfg.code_repair.retry_backoff_s)
                return {'job':job['id'],'status':self.store.get(job['id'])['status'],'soft_defer':True}
            if current['status'] not in TERMINAL|{'rollback_pending','probation','ready'}:
                self.store.update(job['id'],'exhausted' if current['attempts']>=cfg.code_repair.max_attempts else 'deferred',
                    redact(type(error).__name__+': '+str(error),[cfg.api_key or cfg.resolve_api_key()]),
                    next_try=time.time()+cfg.code_repair.retry_backoff_s)
            return {'job':job['id'],'status':self.store.get(job['id'])['status']}


def digest_revision(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def serve(account):
    root=EvidenceStore(account).root
    with RecoveryLease(root,'global-code-repair-worker') as lease:
        if not lease.acquired:return
        engine=RepairEngine(ROOT,root)
        control=OASControl(root)
        last_online=time.time()
        while True:
            atomic_json(root/'code_repair/runtime/worker.json',{'pid':__import__('os').getpid(),
                'at':time.time(),'state':'running','version':1})
            try:
                states=control.states();last_online=time.time()
                for name in states:
                    result=engine.tick(name)
                    if not result.get('idle') and not result.get('disabled'):
                        print(json.dumps({'account':name,**result},ensure_ascii=False),flush=True)
            except Exception as error:
                print(json.dumps({'worker_error':type(error).__name__},ensure_ascii=False),flush=True)
                if time.time()-last_online>300:return
            poll=load_config(config_name=account,force=True).code_repair.poll_seconds
            time.sleep(poll)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--account',default='oas1')
    serve(parser.parse_args().account)
