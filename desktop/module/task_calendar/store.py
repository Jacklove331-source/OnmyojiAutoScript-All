from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json,re,sqlite3,uuid

CST=timezone(timedelta(hours=8))
STATES={'running','completed','failed','deferred','unconfirmed','cancelled'}

def moment(value=None):
    if value is None:return datetime.now(CST)
    result=datetime.fromisoformat(str(value)) if not isinstance(value,datetime) else value
    return result.replace(tzinfo=CST) if result.tzinfo is None else result.astimezone(CST)

def now():return moment().isoformat(timespec='milliseconds')

def task_key(value):
    value=re.sub(r'([A-Z]+)([A-Z][a-z])',r'\1_\2',str(value))
    value=re.sub(r'([a-z0-9])([A-Z])',r'\1_\2',value).lower()
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,80}',value):raise ValueError('任务名称无效')
    return value

def account_key(value):
    value=str(value)
    if not re.fullmatch(r'[\w -]{1,80}',value) or value in ('Home','template'):raise ValueError('账号名称无效')
    return value

class CalendarStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS runs(
              id TEXT PRIMARY KEY,account TEXT NOT NULL,task TEXT NOT NULL,
              started_at TEXT NOT NULL,finished_at TEXT,outcome TEXT NOT NULL,
              source TEXT NOT NULL,evidence TEXT NOT NULL DEFAULT '{}');
              CREATE INDEX IF NOT EXISTS account_period ON runs(account,started_at,finished_at);
              CREATE INDEX IF NOT EXISTS account_task ON runs(account,task);''')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL');yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()

    def begin(self,account,task,*,at=None,run_id=None,source='live'):
        run_id=run_id or uuid.uuid4().hex
        with self.db() as db:db.execute('INSERT OR IGNORE INTO runs(id,account,task,started_at,outcome,source) VALUES(?,?,?,?,?,?)',
            (run_id,account_key(account),task_key(task),moment(at).isoformat(timespec='milliseconds'),'running',source))
        return run_id

    def finish(self,run_id,outcome,*,at=None,evidence=None):
        if outcome not in STATES-{'running'}:raise ValueError('完成状态无效')
        with self.db() as db:db.execute('UPDATE runs SET finished_at=?,outcome=?,evidence=? WHERE id=? AND outcome=?',
            (moment(at).isoformat(timespec='milliseconds'),outcome,json.dumps(evidence or {},ensure_ascii=False),run_id,'running'))

    def import_completed(self,account,task,started,finished,run_id,evidence):
        account=account_key(account);task=task_key(task);start=moment(started);end=moment(finished)
        if end<start:return False
        # A legacy log replay must not duplicate a live execution, or overwrite
        # a live failure with an inference from old logging conventions.
        with self.db() as db:
            existing=db.execute('SELECT started_at,finished_at FROM runs WHERE account=? AND task=? AND source=?',
                                (account,task,'live')).fetchall()
            if any(r['finished_at'] and abs((moment(r['finished_at'])-end).total_seconds())<10 and
                   abs((moment(r['started_at'])-start).total_seconds())<180 for r in existing):return False
            cursor=db.execute('INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?,?)',
              (run_id,account,task,start.isoformat(timespec='milliseconds'),end.isoformat(timespec='milliseconds'),
               'completed','log',json.dumps(evidence,ensure_ascii=False)))
            return cursor.rowcount==1

    def records(self,account,start,end):
        account=account_key(account);start=moment(start).isoformat(timespec='milliseconds');end=moment(end).isoformat(timespec='milliseconds')
        with self.db() as db:
            rows=db.execute('SELECT * FROM runs WHERE account=? AND ((finished_at>=? AND finished_at<?) OR '
                '(finished_at IS NULL AND started_at>=? AND started_at<?)) ORDER BY COALESCE(finished_at,started_at),id',
                (account,start,end,start,end)).fetchall()
        return [{**dict(r),'evidence':json.loads(r['evidence'])} for r in rows]
