# This Python file uses the following encoding: utf-8

"""调度器持久化状态：SQLite + JSONL 事件流 + 心跳。

设计目标（对应「必须支持持久化恢复」）：
- 任务/审核/预算/验收全部落盘，进程随时被杀都能从上次状态继续；
- 每个任务一行，带 phase（pending/executing/verifying/review/rework/accepted/blocked）；
- 危险操作（git 检查点、实机对局）都写事件，恢复时可判断「是否已经做过」；
- 心跳文件用于外部判断调度器是否还活着（不假装在跑）。
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT UNIQUE,
    title TEXT NOT NULL,
    goal TEXT,
    card TEXT,
    phase TEXT NOT NULL DEFAULT 'pending',
    rework_left INTEGER NOT NULL DEFAULT 2,
    created_at TEXT,
    updated_at TEXT,
    verdict TEXT,
    review_note TEXT,
    evidence TEXT,
    agent_b_summary TEXT,
    cost_cny REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT,
    role TEXT,
    started_at TEXT,
    finished_at TEXT,
    ok INTEGER,
    tokens_input INTEGER DEFAULT 0,
    tokens_cached INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    cost_cny REAL DEFAULT 0,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT,
    ac_id TEXT,
    ok INTEGER,
    detail TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS acceptance (
    ac_id TEXT PRIMARY KEY,
    title TEXT,
    ok INTEGER DEFAULT 0,
    detail TEXT,
    checked_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT,
    verdict TEXT,
    provider TEXT,
    model TEXT,
    notes TEXT,
    created_at TEXT
);
"""

# v2 新增列（老库用 ALTER TABLE 补，旧行默认值 = v1 语义：DeepSeek）
RUN_COLUMNS_V2 = (
    ('provider', "TEXT DEFAULT 'deepseek'"),
    ('model', "TEXT DEFAULT ''"),
    ('purpose', "TEXT DEFAULT ''"),
    ('cost_usd', 'REAL DEFAULT 0'),
    ('tokens_cache_write', 'INTEGER DEFAULT 0'),
    ('duration_s', 'REAL DEFAULT 0'),
)


def now_text() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


@dataclass
class Task:
    task_key: str
    title: str
    goal: str
    card: dict
    phase: str = 'pending'
    rework_left: int = 2
    cost_cny: float = 0.0
    attempts: int = 0
    evidence: dict = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.phase not in ('accepted', 'blocked')


class State:
    def __init__(self, state_dir: Path, project_root: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.dir = state_dir
        self.project_root = project_root
        self.db_path = state_dir / 'state.db'
        self.events_path = state_dir / 'events.jsonl'
        self.heartbeat_path = state_dir / 'heartbeat.json'
        self.knowledge_path = state_dir / 'knowledge.jsonl'
        # 调度器与 DSH 会话（deepseek_b.py）可能同时开库：给足等待时间
        self.conn = sqlite3.connect(self.db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        existing = {row[1] for row in self.conn.execute('PRAGMA table_info(runs)')}
        for name, ddl in RUN_COLUMNS_V2:
            if name not in existing:
                self.conn.execute(f'ALTER TABLE runs ADD COLUMN {name} {ddl}')

    # ---------------------------------------------------------------- meta
    def get_meta(self, key: str, default=None):
        row = self.conn.execute(
            'SELECT value FROM meta WHERE key = ?', (key,)
        ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row['value'])
        except (TypeError, ValueError):
            return row['value']

    def set_meta(self, key: str, value) -> None:
        self.conn.execute(
            'INSERT INTO meta(key, value) VALUES(?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

    def bump_meta(self, key: str, delta: float = 1.0) -> float:
        current = float(self.get_meta(key, 0) or 0)
        current += float(delta)
        self.set_meta(key, current)
        return current

    # --------------------------------------------------------------- tasks
    def add_task(self, card: dict) -> Task:
        task_key = str(card.get('id') or f'task-{int(time.time())}')
        self.conn.execute(
            'INSERT OR IGNORE INTO tasks'
            '(task_key, title, goal, card, phase, rework_left, created_at, updated_at)'
            ' VALUES(?,?,?,?,?,?,?,?)',
            (
                task_key,
                str(card.get('title') or task_key),
                str(card.get('goal') or ''),
                json.dumps(card, ensure_ascii=False),
                'pending',
                int(card.get('max_rework', 2)),
                now_text(),
                now_text(),
            ),
        )
        self.conn.commit()
        return self.get_task(task_key)

    def get_task(self, task_key: str) -> Task | None:
        row = self.conn.execute(
            'SELECT * FROM tasks WHERE task_key = ?', (task_key,)
        ).fetchone()
        return self._row_to_task(row) if row else None

    def _row_to_task(self, row) -> Task:
        try:
            card = json.loads(row['card'] or '{}')
        except (TypeError, ValueError):
            card = {}
        try:
            evidence = json.loads(row['evidence'] or '{}')
        except (TypeError, ValueError):
            evidence = {}
        if not isinstance(evidence, dict):
            # v1 在 PASS 时把 verify_rows（list）直接写进 evidence，
            # 之后 `.get()` 会炸（deepseek_b.py next 实测 AttributeError）
            evidence = {'verify_rows': evidence}
        return Task(
            task_key=row['task_key'],
            title=row['title'],
            goal=row['goal'] or '',
            card=card,
            phase=row['phase'],
            rework_left=row['rework_left'],
            cost_cny=float(row['cost_cny'] or 0),
            attempts=int(row['attempts'] or 0),
            evidence=evidence,
        )

    def open_tasks(self) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE phase NOT IN ('accepted','blocked') "
            'ORDER BY id'
        ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def next_task(self) -> Task | None:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE phase NOT IN ('accepted','blocked','review') "
            'ORDER BY id LIMIT 1'
        ).fetchall()
        if rows:
            return self._row_to_task(rows[0])
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE phase = 'review' ORDER BY id LIMIT 1"
        ).fetchall()
        return self._row_to_task(rows[0]) if rows else None

    def update_task(self, task_key: str, **fields) -> None:
        if not fields:
            return
        assignments = ', '.join(f'{key} = ?' for key in fields)
        values = list(fields.values()) + [now_text(), task_key]
        self.conn.execute(
            f'UPDATE tasks SET {assignments}, updated_at = ? WHERE task_key = ?',
            values,
        )
        self.conn.commit()

    def task_counts(self) -> dict:
        rows = self.conn.execute(
            'SELECT phase, COUNT(*) AS count FROM tasks GROUP BY phase'
        ).fetchall()
        return {row['phase']: row['count'] for row in rows}

    def accepted_tasks(self) -> list[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE phase = 'accepted' ORDER BY id"
        ).fetchall()
        return [self._row_to_task(row) for row in rows]

    # ---------------------------------------------------------------- runs
    def record_run(self, task_key: str, role: str, started_at: str,
                   result) -> float:
        cost = float(result.cost_cny or 0.0)
        usage = getattr(result, 'usage', {}) or {}
        provider = str(getattr(result, 'provider', '') or 'deepseek')
        self.conn.execute(
            'INSERT INTO runs(task_key, role, started_at, finished_at, ok, '
            'tokens_input, tokens_cached, tokens_output, cost_cny, notes, '
            'provider, model, purpose, cost_usd, tokens_cache_write, duration_s) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (
                task_key, role, started_at, now_text(),
                1 if result.ok else 0,
                int(usage.get('input_tokens', 0) or 0),
                int(usage.get('cache_read_input_tokens', 0) or 0),
                int(usage.get('output_tokens', 0) or 0),
                cost,
                (result.error or '')[:500],
                provider,
                str(getattr(result, 'model', '') or ''),
                str(getattr(result, 'purpose', '') or ''),
                float(getattr(result, 'cost_usd', 0.0) or 0.0),
                int(usage.get('cache_creation_input_tokens', 0) or 0),
                float(getattr(result, 'duration_s', 0.0) or 0.0),
            ),
        )
        self.conn.commit()
        self.bump_meta('calls_total', 1)
        self.bump_meta('cost_cny_total', cost)
        if task_key:
            task = self.get_task(task_key)
            if task is not None:
                self.update_task(task_key, cost_cny=round(task.cost_cny + cost, 4))
        return cost

    def run_totals(self, since: str = '', provider: str = '') -> dict:
        """调用流水汇总；since=开始时间（含），provider=只算某个供应商。"""
        where = []
        params: list = []
        if since:
            where.append('started_at >= ?')
            params.append(since)
        if provider:
            where.append("COALESCE(provider,'deepseek') = ?")
            params.append(provider)
        clause = (' WHERE ' + ' AND '.join(where)) if where else ''
        row = self.conn.execute(
            'SELECT COUNT(*) AS calls, '
            'COALESCE(SUM(tokens_input),0) AS tokens_input, '
            'COALESCE(SUM(tokens_cached),0) AS tokens_cached, '
            'COALESCE(SUM(tokens_output),0) AS tokens_output, '
            'COALESCE(SUM(tokens_cache_write),0) AS tokens_cache_write, '
            'COALESCE(SUM(cost_cny),0) AS cost, '
            'COALESCE(SUM(cost_usd),0) AS cost_usd '
            'FROM runs' + clause, params,
        ).fetchone()
        return {
            'calls': int(row['calls']),
            'tokens_input': int(row['tokens_input']),
            'tokens_cached': int(row['tokens_cached']),
            'tokens_output': int(row['tokens_output']),
            'tokens_cache_write': int(row['tokens_cache_write']),
            'cost_cny': round(float(row['cost']), 4),
            'cost_usd': round(float(row['cost_usd']), 4),
        }

    def task_cost_table(self, limit: int = 20) -> list[dict]:
        """每个任务的 token/费用/耗时/返工次数（第四条第 10 点）。"""
        rows = self.conn.execute(
            'SELECT t.task_key, t.title, t.phase, t.verdict, t.attempts, '
            "COALESCE(SUM(CASE WHEN r.role='agent_a' THEN r.cost_cny END),0) AS a_cny, "
            "COALESCE(SUM(CASE WHEN r.role='agent_b' THEN r.cost_cny END),0) AS b_cny, "
            'COALESCE(SUM(r.tokens_input + r.tokens_cached + '
            'COALESCE(r.tokens_cache_write,0)),0) AS tin, '
            'COALESCE(SUM(r.tokens_output),0) AS tout, '
            'COALESCE(SUM(r.duration_s),0) AS secs, COUNT(r.id) AS calls '
            'FROM tasks t LEFT JOIN runs r ON r.task_key = t.task_key '
            'GROUP BY t.task_key ORDER BY t.id DESC LIMIT ?', (limit,),
        ).fetchall()
        reworks = {
            row['task_key']: row['n'] for row in self.conn.execute(
                "SELECT task_key, COUNT(*) AS n FROM reviews WHERE verdict='REWORK' "
                'GROUP BY task_key')
        }
        out = []
        for row in rows:
            item = dict(row)
            item['reworks'] = int(reworks.get(row['task_key'], 0))
            out.append(item)
        return out

    # ------------------------------------------------------------- reviews
    def record_review(self, task_key: str, verdict: str, provider: str,
                      model: str, notes: str = '') -> None:
        self.conn.execute(
            'INSERT INTO reviews(task_key, verdict, provider, model, notes, created_at)'
            ' VALUES(?,?,?,?,?,?)',
            (task_key, verdict, provider, model, (notes or '')[:1500], now_text()),
        )
        self.conn.commit()

    def reviews_for(self, task_key: str) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            'SELECT * FROM reviews WHERE task_key = ? ORDER BY id', (task_key,))]

    # ---------------------------------------------------------------- locks
    def acquire_lock(self, name: str, owner: str, ttl_sec: float,
                     task_key: str = '') -> tuple[bool, dict]:
        """原子抢锁（BEGIN IMMEDIATE）。同 owner 重入会续期；过期锁可被抢。

        用途：`executor` 锁保证「同一时间只有一个执行者改主工作区」
        （调度器自己的 Agent B 或 DSH 里的 DeepSeek 会话）。
        """
        key = f'lock:{name}'
        now = time.time()
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            row = self.conn.execute('SELECT value FROM meta WHERE key = ?',
                                    (key,)).fetchone()
            current = {}
            if row is not None:
                try:
                    current = json.loads(row['value']) or {}
                except (TypeError, ValueError):
                    current = {}
            held = bool(current) and float(current.get('expires', 0)) > now
            if held and current.get('owner') != owner:
                self.conn.execute('COMMIT')
                return False, current
            info = {'owner': owner, 'task_key': task_key, 'since': now_text(),
                    'expires': now + float(ttl_sec),
                    'pid': __import__('os').getpid()}
            self.conn.execute(
                'INSERT INTO meta(key, value) VALUES(?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                (key, json.dumps(info, ensure_ascii=False)),
            )
            self.conn.execute('COMMIT')
            return True, info
        except Exception:
            self.conn.execute('ROLLBACK')
            raise

    def release_lock(self, name: str, owner: str = '') -> bool:
        info = self.lock_info(name)
        if not info:
            return False
        if owner and info.get('owner') != owner:
            return False
        self.conn.execute('DELETE FROM meta WHERE key = ?', (f'lock:{name}',))
        self.conn.commit()
        return True

    def lock_info(self, name: str) -> dict:
        """有效锁的信息；过期或不存在返回 {}。"""
        value = self.get_meta(f'lock:{name}')
        if not isinstance(value, dict):
            return {}
        if float(value.get('expires', 0)) <= time.time():
            return {}
        return value

    # ------------------------------------------------------------ knowledge
    def add_knowledge(self, task_key: str, title: str, verdict: str,
                      note: str) -> None:
        """已验证结论缓存：PASS/推翻假设写一行，后续提示词复用，避免重复分析。"""
        record = {'ts': now_text(), 'task_key': task_key, 'title': title[:80],
                  'verdict': verdict, 'note': (note or '')[:300]}
        with open(self.knowledge_path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')

    def knowledge(self, limit: int = 12) -> list[dict]:
        if not self.knowledge_path.exists():
            return []
        items = []
        for line in self.knowledge_path.read_text(
                encoding='utf-8', errors='replace').splitlines()[-limit:]:
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
        return items

    # -------------------------------------------------------- verification
    def record_verification(self, task_key: str, ac_id: str, ok: bool,
                            detail: str = '') -> None:
        self.conn.execute(
            'INSERT INTO verifications(task_key, ac_id, ok, detail, created_at)'
            ' VALUES(?,?,?,?,?)',
            (task_key, ac_id, 1 if ok else 0, detail[:2000], now_text()),
        )
        self.conn.commit()

    def record_acceptance(self, ac_id: str, title: str, ok: bool,
                          detail: str = '') -> None:
        self.conn.execute(
            'INSERT INTO acceptance(ac_id, title, ok, detail, checked_at)'
            ' VALUES(?,?,?,?,?) ON CONFLICT(ac_id) DO UPDATE SET '
            'title = excluded.title, ok = excluded.ok, '
            'detail = excluded.detail, checked_at = excluded.checked_at',
            (ac_id, title, 1 if ok else 0, detail[:2000], now_text()),
        )
        self.conn.commit()

    def acceptance_rows(self) -> list[dict]:
        rows = self.conn.execute(
            'SELECT * FROM acceptance ORDER BY ac_id'
        ).fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------- events
    def event(self, kind: str, **payload) -> None:
        record = {'ts': now_text(), 'kind': kind}
        record.update(payload)
        with open(self.events_path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')

    def heartbeat(self, status: str, detail: dict | None = None) -> None:
        payload = {
            'ts': now_text(),
            'epoch': time.time(),
            'pid': __import__('os').getpid(),
            'status': status,
            'state': self.task_counts(),
            'budget': self.run_totals(),
        }
        if detail:
            payload.update(detail)
        self.heartbeat_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8'
        )

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass
