"""Durable, account-scoped recovery feedback and repeated-failure control.

Page recovery is provisional until the original task completes. A recurrence or
an immediate task failure penalizes that plan, including across worker restarts.
This stores outcomes and bounded action plans; it never modifies source code.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time


def error_fingerprint(anomaly) -> str:
    detail = str(anomaly.detail or '').strip()
    detail = re.sub(r'\d{4}-\d\d-\d\d[ T]\d\d:\d\d(?::\d\d(?:\.\d+)?)?', '<time>', detail)
    detail = re.sub(r'\b0x[0-9a-fA-F]+\b', '<address>', detail)
    detail = re.sub(r'\b[0-9a-fA-F]{12,}\b', '<id>', detail)
    detail = re.sub(r'(?i)\b(pid|incident|request[_ -]?id)\s*[=:]\s*[\w-]+', r'\1=<id>', detail)
    detail = re.sub(r'\s+', ' ', detail)[:512]
    evidence = anomaly.evidence or {}
    fields = [anomaly.task, anomaly.type, anomaly.page, detail,
              str(evidence.get('exception_type', '')), str(evidence.get('source', '')),
              str(evidence.get('expected_page', '')), str(evidence.get('source_revision', ''))]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode('utf-8')).hexdigest()[:24]


def plan_signature(actions, expected_page: str = '') -> str:
    normalized = []
    for item in actions or []:
        item = item.to_dict() if hasattr(item, 'to_dict') else item
        normalized.append({
            'type': str(item.get('type') or ''),
            'x': round(float(item['x']), 2) if item.get('x') is not None else None,
            'y': round(float(item['y']), 2) if item.get('y') is not None else None,
            'text': str(item.get('text') or '').strip(),
            'page_key': str(item.get('page_key') or ''),
            'wait_seconds': int(item.get('wait_ms') or 0) // 1000,
        })
    payload = {'actions': normalized, 'expected_page': str(expected_page or '')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


SCHEMA = '''
CREATE TABLE IF NOT EXISTS cases (
 id TEXT PRIMARY KEY, task TEXT NOT NULL, kind TEXT NOT NULL, page TEXT NOT NULL,
 occurrences INTEGER NOT NULL DEFAULT 0, recoveries INTEGER NOT NULL DEFAULT 0,
 confirmed INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
 consecutive_failures INTEGER NOT NULL DEFAULT 0, blocked_until REAL NOT NULL DEFAULT 0,
 last_seen REAL NOT NULL DEFAULT 0, last_message TEXT NOT NULL DEFAULT '',
 pending_plan TEXT NOT NULL DEFAULT '', pending_at REAL NOT NULL DEFAULT 0,
 pending_incident TEXT NOT NULL DEFAULT '', last_finished TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS plans (
 id TEXT NOT NULL, case_id TEXT NOT NULL, actions TEXT NOT NULL, expected_page TEXT NOT NULL,
 successes INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
 consecutive_failures INTEGER NOT NULL DEFAULT 0, last_reason TEXT NOT NULL DEFAULT '',
 updated_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (case_id, id)
);
CREATE TABLE IF NOT EXISTS providers (
 id TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0,
 blocked_until REAL NOT NULL DEFAULT 0, last_reason TEXT NOT NULL DEFAULT ''
);
'''


class LearningMemory:
    def __init__(self, cfg, root: Path, *, clock=None):
        self.cfg = cfg.learning
        self.enabled = bool(self.cfg.enabled)
        self.clock = clock or time.time
        digest = hashlib.sha256(cfg.config_name.encode()).hexdigest()[:16]
        self.path = Path(root) / 'learning' / (digest + '.sqlite3')
        self.provider_id = hashlib.sha256(
            (cfg.api.base_url + '|' + cfg.api.model).encode()).hexdigest()[:24]

    @contextmanager
    def _db(self, *, write=True):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.executescript(SCHEMA)
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _fail_case(self, db, case_id, reason, now):
        row = db.execute('SELECT consecutive_failures FROM cases WHERE id=?', (case_id,)).fetchone()
        if row is None:
            return
        streak = int(row[0]) + 1
        until = 0.0
        if streak >= self.cfg.repeated_error_limit:
            delay = min(self.cfg.max_backoff_s,
                        self.cfg.base_backoff_s * 2 ** min(12, streak - self.cfg.repeated_error_limit))
            until = now + delay
        db.execute('UPDATE cases SET failures=failures+1, consecutive_failures=?, '
                   'blocked_until=?, last_message=? WHERE id=?',
                   (streak, until, str(reason)[:400], case_id))

    def _strike_plan(self, db, case_id, plan_id, reason, now):
        db.execute('UPDATE plans SET failures=failures+1, consecutive_failures=consecutive_failures+1, '
                   'last_reason=?, updated_at=? WHERE case_id=? AND id=?',
                   (str(reason)[:200], now, case_id, plan_id))

    def begin(self, anomaly) -> dict:
        case_id = error_fingerprint(anomaly)
        if not self.enabled:
            return {'case_id':case_id, 'allowed':True, 'retry_after_s':0}
        now = self.clock()
        with self._db() as db:
            db.execute('INSERT OR IGNORE INTO cases(id,task,kind,page) VALUES(?,?,?,?)',
                       (case_id, anomaly.task, anomaly.type, anomaly.page))
            row = dict(db.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone())
            if row['pending_plan'] and row['pending_incident'] != anomaly.incident_id:
                self._strike_plan(db, case_id, row['pending_plan'], 'same_error_recurred', now)
                self._fail_case(db, case_id, '恢复后同一错误再次出现', now)
                db.execute("UPDATE cases SET pending_plan='', pending_incident='' WHERE id=?", (case_id,))
            db.execute('UPDATE cases SET occurrences=occurrences+1,last_seen=? WHERE id=?', (now,case_id))
            row = dict(db.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone())
        remaining = max(0, row['blocked_until'] - now)
        return {'case_id':case_id, 'allowed':remaining <= 0, 'retry_after_s':remaining,
                'occurrences':row['occurrences'], 'consecutive_failures':row['consecutive_failures']}

    def _ensure_plan(self, db, case_id, diagnosis, now):
        signature = plan_signature(diagnosis.actions, diagnosis.expected_page)
        actions = [action.to_dict() for action in diagnosis.actions]
        db.execute('INSERT OR IGNORE INTO plans(id,case_id,actions,expected_page,updated_at) VALUES(?,?,?,?,?)',
                   (signature, case_id, json.dumps(actions,ensure_ascii=False), diagnosis.expected_page, now))
        return signature

    def plan_allowed(self, case_id, diagnosis) -> bool:
        if not self.enabled or not self.path.exists():
            return True
        signature = plan_signature(diagnosis.actions, diagnosis.expected_page)
        with self._db(write=False) as db:
            row = db.execute('SELECT consecutive_failures FROM plans WHERE case_id=? AND id=?',
                             (case_id, signature)).fetchone()
        return row is None or int(row[0]) < self.cfg.plan_failure_limit

    def plan_failed(self, case_id, diagnosis, reason):
        if not self.enabled:
            return
        now = self.clock()
        with self._db() as db:
            signature = self._ensure_plan(db,case_id,diagnosis,now)
            self._strike_plan(db,case_id,signature,reason,now)

    def finish(self, anomaly, result):
        if not self.enabled or not self.path.exists():
            return
        outcome = result.outcome.value
        if outcome not in {'recovered','stopped','needs_human','unavailable'}:
            return
        now, case_id = self.clock(), error_fingerprint(anomaly)
        with self._db() as db:
            row = db.execute('SELECT last_finished FROM cases WHERE id=?', (case_id,)).fetchone()
            if row is None or row[0] == anomaly.incident_id:
                return
            db.execute('UPDATE cases SET last_finished=?,last_message=? WHERE id=?',
                       (anomaly.incident_id, result.message[:400], case_id))
            if outcome == 'recovered' and result.verified:
                signature = self._ensure_plan(db,case_id,result.diagnosis,now) if result.diagnosis else ''
                db.execute('UPDATE cases SET recoveries=recoveries+1, pending_plan=?,pending_at=?, '
                           'pending_incident=? WHERE id=?', (signature,now,anomaly.incident_id,case_id))
                if not signature:
                    db.execute('UPDATE cases SET consecutive_failures=0,blocked_until=0 WHERE id=?', (case_id,))
            else:
                self._fail_case(db,case_id,result.message,now)

    def task_finished(self, task: str, *, success: bool):
        if not self.enabled or not self.path.exists():
            return
        now = self.clock()
        with self._db() as db:
            rows = db.execute("SELECT * FROM cases WHERE task=? AND pending_plan!=''", (task,)).fetchall()
            for row in rows:
                if success:
                    db.execute('UPDATE plans SET successes=successes+1,consecutive_failures=0,updated_at=? '
                               'WHERE case_id=? AND id=?', (now,row['id'],row['pending_plan']))
                    db.execute('UPDATE cases SET confirmed=confirmed+1,consecutive_failures=0,blocked_until=0 '
                               'WHERE id=?', (row['id'],))
                else:
                    self._strike_plan(db,row['id'],row['pending_plan'],'original_task_failed',now)
                    self._fail_case(db,row['id'],'恢复后原任务仍然失败',now)
                db.execute("UPDATE cases SET pending_plan='',pending_incident='' WHERE id=?", (row['id'],))

    def feedback(self, case_id: str) -> list[dict]:
        if not self.enabled or not self.path.exists():
            return []
        with self._db(write=False) as db:
            rows = db.execute('SELECT actions,expected_page,consecutive_failures,last_reason FROM plans '
                              'WHERE case_id=? AND failures>0 ORDER BY updated_at DESC LIMIT 5', (case_id,)).fetchall()
        return [{'actions':json.loads(row['actions']), 'expected_page':row['expected_page'],
                 'consecutive_failures':row['consecutive_failures'], 'failure':row['last_reason']} for row in rows]

    def provider_allowed(self) -> bool:
        if not self.enabled or not self.path.exists():
            return True
        with self._db(write=False) as db:
            row = db.execute('SELECT blocked_until FROM providers WHERE id=?', (self.provider_id,)).fetchone()
        return row is None or float(row[0]) <= self.clock()

    def provider_result(self, *, success: bool, reason=''):
        if not self.enabled:
            return
        with self._db() as db:
            db.execute('INSERT OR IGNORE INTO providers(id) VALUES(?)', (self.provider_id,))
            row = db.execute('SELECT failures FROM providers WHERE id=?', (self.provider_id,)).fetchone()
            failures = 0 if success else int(row[0])+1
            until = self.clock()+self.cfg.provider_backoff_s if failures >= self.cfg.provider_failure_limit else 0
            db.execute('UPDATE providers SET failures=?,blocked_until=?,last_reason=? WHERE id=?',
                       (failures,until,str(reason)[:100],self.provider_id))

    def retry_after(self, anomaly) -> float:
        seconds = float(self.cfg.task_defer_s)
        if self.enabled and self.path.exists():
            with self._db(write=False) as db:
                row = db.execute('SELECT blocked_until FROM cases WHERE id=?', (error_fingerprint(anomaly),)).fetchone()
            if row:
                seconds = max(seconds, float(row[0])-self.clock())
        return seconds

    def stats(self) -> dict:
        empty = {'enabled':self.enabled,'cases':0,'repeated_cases':0,'confirmed_solutions':0,
                 'blocked_cases':0,'blocked_plans':0,'provider_blocked':False,'recent_errors':[]}
        if not self.path.exists():
            return empty
        with self._db(write=False) as db:
            counts = db.execute('SELECT COUNT(*),COALESCE(SUM(occurrences>1),0),COALESCE(SUM(confirmed),0),'
                                'COALESCE(SUM(blocked_until>?),0) FROM cases', (self.clock(),)).fetchone()
            plans = db.execute('SELECT COUNT(*) FROM plans WHERE consecutive_failures>=?',
                               (self.cfg.plan_failure_limit,)).fetchone()[0]
            recent = db.execute('SELECT task,kind,occurrences,consecutive_failures,blocked_until,last_message '
                                'FROM cases ORDER BY last_seen DESC LIMIT 10').fetchall()
        return dict(empty, cases=counts[0], repeated_cases=counts[1], confirmed_solutions=counts[2],
                    blocked_cases=counts[3], blocked_plans=plans, provider_blocked=not self.provider_allowed(),
                    recent_errors=[dict(row) for row in recent])
