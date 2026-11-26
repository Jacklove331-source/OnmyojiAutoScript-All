"""Transactional repair jobs, audit events and post-deployment task feedback."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, account TEXT NOT NULL, task TEXT NOT NULL, fingerprint TEXT NOT NULL,
 revision TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 next_try REAL NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
 payload TEXT NOT NULL, artifact TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
 applied_at REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL, at REAL NOT NULL,
 kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rejected_plans(
 fingerprint TEXT NOT NULL, revision TEXT NOT NULL, signature TEXT NOT NULL,
 reason TEXT NOT NULL, PRIMARY KEY(fingerprint,revision,signature));
'''
TERMINAL = {'succeeded','rolled_back','needs_attention','exhausted','superseded'}


class RepairStore:
    def __init__(self, root: Path, *, clock=None):
        self.root = Path(root)
        self.path = self.root/'code_repair'/'jobs.sqlite3'
        self.clock = clock or time.time

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db = sqlite3.connect(self.path,timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript(SCHEMA)
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:return None
        value = dict(row)
        value['payload'] = json.loads(value['payload'])
        return value

    def enqueue(self, account, payload):
        fingerprint = str(payload['fingerprint'])
        revision = str(payload.get('revision') or '')
        job_id = hashlib.sha256((account+'|'+fingerprint+'|'+revision).encode()).hexdigest()[:24]
        now = self.clock()
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row is None:
                db.execute('INSERT INTO jobs(id,account,task,fingerprint,revision,status,created,updated,payload) '
                           'VALUES(?,?,?,?,?,?,?,?,?)',(job_id,account,payload['task'],fingerprint,revision,
                                                       'queued',now,now,json.dumps(payload,ensure_ascii=False)))
                db.execute('INSERT INTO events(job,at,kind,detail) VALUES(?,?,?,?)',
                           (job_id,now,'queued','故障进入代码维修队列'))
            elif row['status'] not in TERMINAL and row['status'] not in {'probation','rollback_pending'}:
                db.execute('UPDATE jobs SET payload=?,updated=? WHERE id=?',
                           (json.dumps(payload,ensure_ascii=False),now,job_id))
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone())

    def get(self, job_id):
        if not self.path.exists():return None
        with self.db() as db:
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone())

    def list(self, *, account=None, statuses=None):
        if not self.path.exists():return []
        clauses, args = [], []
        if account is not None:clauses.append('account=?');args.append(account)
        if statuses:
            clauses.append('status IN ('+','.join('?' for _ in statuses)+')');args.extend(statuses)
        sql = 'SELECT * FROM jobs'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY created'
        with self.db() as db:return [self.decode(row) for row in db.execute(sql,args)]

    def update(self, job_id, status, message='', **fields):
        allowed = {'next_try','artifact','applied_at','attempts'}
        if not set(fields)<=allowed:raise ValueError('invalid repair field')
        now = self.clock()
        values = {'status':status,'message':str(message)[:500],'updated':now,**fields}
        with self.db() as db:
            db.execute('UPDATE jobs SET '+','.join(key+'=?' for key in values)+' WHERE id=?',
                       [*values.values(),job_id])
            db.execute('INSERT INTO events(job,at,kind,detail) VALUES(?,?,?,?)',
                       (job_id,now,status,str(message)[:1000]))
        self._sync_legacy(job_id,status,message)

    def _sync_legacy(self,job_id,status,message):
        """Expose progress through the existing OASX repair queue immediately."""
        job=self.get(job_id)
        legacy_id=(job or {}).get('payload',{}).get('legacy_id')
        if not legacy_id:return
        digest=hashlib.sha256(job['account'].encode()).hexdigest()[:12]
        path=self.root/('repair_requests_'+digest+'.jsonl')
        try:
            latest={}
            for line in path.read_text(encoding='utf-8').splitlines():
                try:item=json.loads(line)
                except ValueError:continue
                if item.get('id')==legacy_id:latest=item
            if not latest:return
            latest.update({'status':'resolved' if status=='succeeded' else 'pending',
                'updated_at':self.clock(),'message':('[自动代码维修:'+status+'] '+str(message))[:400],
                'code_repair_job':job_id})
            with path.open('a',encoding='utf-8') as handle:handle.write(json.dumps(latest,ensure_ascii=False)+'\n')
        except OSError:
            pass

    def claim_next(self, account, max_attempts):
        now = self.clock()
        with self.db() as db:
            # A crashed planner may retry; applying jobs require journal recovery.
            db.execute("UPDATE jobs SET status='queued' WHERE status IN ('planning','testing') AND updated<?",
                       (now-600,))
            db.execute("UPDATE jobs SET status='exhausted' WHERE status IN ('queued','deferred') AND attempts>=?",
                       (max_attempts,))
            row = db.execute("SELECT * FROM jobs WHERE account=? AND status IN ('queued','deferred') "
                             'AND next_try<=? AND attempts<? ORDER BY created LIMIT 1',
                             (account,now,max_attempts)).fetchone()
            if row is None:return None
            db.execute("UPDATE jobs SET status='planning',attempts=attempts+1,updated=? WHERE id=?",(now,row['id']))
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone())

    def reject(self, job, signature, reason):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO rejected_plans VALUES(?,?,?,?)',
                       (job['fingerprint'],job['revision'],signature,str(reason)[:300]))

    def rejected(self, job):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT signature,reason FROM rejected_plans '
                'WHERE fingerprint=? AND revision=?',(job['fingerprint'],job['revision']))]

    def task_finished(self, account, task, success):
        for job in self.list(account=account,statuses=['probation']):
            if job['task'] == task:
                self.update(job['id'],'succeeded' if success else 'rollback_pending',
                            '原任务已正常完成' if success else '应用补丁后原任务仍失败，等待安全回滚')

    def status(self, account=None):
        jobs = self.list(account=account)
        counts = {}
        for job in jobs:counts[job['status']] = counts.get(job['status'],0)+1
        return {'jobs':len(jobs),'counts':counts,'recent':[
            {key:job[key] for key in ('id','account','task','status','attempts','updated','message')}
            for job in jobs[-15:]]}
