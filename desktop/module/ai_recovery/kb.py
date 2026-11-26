# This Python file uses the following encoding: utf-8

"""本地异常知识库 + 截图去重。

- 成功恢复的方案按「任务 + 异常类型 + 页面 + 截图感知哈希 + 分辨率 + 游戏版本」归档；
- 再次遇到相同异常时，只有环境匹配、历史成功率高、未过期才复用；
- 复用后仍会截图验证；失败会累计 fail，连续失效自动淘汰（不永久相信历史坐标）；
- 文件为 append-only JSONL，最新一条同 id 记录生效。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from module.ai_recovery.imageutil import hamming
from module.ai_recovery.learning import error_fingerprint, plan_signature
from module.ai_recovery.types import Action, Anomaly, Diagnosis

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class KnowledgeEntry:
    id: str
    task: str
    anomaly: str
    page: str
    phash: str
    resolution: list
    game_version: str
    actions: list = field(default_factory=list)
    expected_change: str = ''
    success: int = 0
    fail: int = 0
    created_at: float = 0.0
    last_used: float = 0.0
    last_success: float = 0.0
    note: str = ''
    config_name: str = ''
    expected_page: str = ''
    verification_version: int = 0
    error_signature: str = ''
    consecutive_failures: int = 0

    @property
    def total(self) -> int:
        return int(self.success) + int(self.fail)

    @property
    def rate(self) -> float:
        total = self.total
        return (float(self.success) / total) if total else 0.0

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'task': self.task,
            'anomaly': self.anomaly,
            'page': self.page,
            'phash': self.phash,
            'resolution': self.resolution,
            'game_version': self.game_version,
            'actions': self.actions,
            'expected_change': self.expected_change,
            'success': self.success,
            'fail': self.fail,
            'created_at': self.created_at,
            'last_used': self.last_used,
            'last_success': self.last_success,
            'note': self.note,
            'config_name': self.config_name,
            'expected_page': self.expected_page,
            'verification_version': self.verification_version,
            'error_signature': self.error_signature,
            'consecutive_failures': self.consecutive_failures,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'KnowledgeEntry':
        return cls(
            id=str(data.get('id') or ''),
            task=str(data.get('task') or ''),
            anomaly=str(data.get('anomaly') or ''),
            page=str(data.get('page') or ''),
            phash=str(data.get('phash') or ''),
            resolution=list(data.get('resolution') or []),
            game_version=str(data.get('game_version') or ''),
            actions=list(data.get('actions') or []),
            expected_change=str(data.get('expected_change') or ''),
            success=int(data.get('success') or 0),
            fail=int(data.get('fail') or 0),
            created_at=float(data.get('created_at') or 0.0),
            last_used=float(data.get('last_used') or 0.0),
            last_success=float(data.get('last_success') or 0.0),
            note=str(data.get('note') or ''),
            config_name=str(data.get('config_name') or ''),
            expected_page=str(data.get('expected_page') or ''),
            verification_version=int(data.get('verification_version') or 0),
            error_signature=str(data.get('error_signature') or ''),
            consecutive_failures=max(0, int(data.get('consecutive_failures') or 0)),
        )


class KnowledgeBase:
    def __init__(self, cfg, config_name: str = 'oas', path: Path | None = None, clock=None):
        self.cfg = cfg.knowledge
        self.failure_limit = cfg.learning.plan_failure_limit
        self.config_name = config_name
        root = Path(os.environ.get('OAS_AI_LOG_DIR') or (ROOT / 'log' / 'ai_recovery'))
        self.path = Path(path) if path else (ROOT / self.cfg.path if not Path(self.cfg.path).is_absolute()
                                             else Path(self.cfg.path))
        self.seen_path = self.path.parent / f'seen_{config_name}.json'
        self.clock = clock or time.time
        self.entries: dict[str, KnowledgeEntry] = {}
        self._seen: dict[str, float] = {}
        self._path_sig: tuple[int, int] | None = None
        self._load()

    # ---------- 查询 ----------

    def lookup(self, anomaly: Anomaly, phash: str, resolution: tuple[int, int],
               game_version: str = '', *, now: float | None = None) -> KnowledgeEntry | None:
        if not self.cfg.enabled:
            return None
        self._reload_if_changed()
        now = self.clock() if now is None else now
        best: KnowledgeEntry | None = None
        best_rank = (999, 1)
        signature = error_fingerprint(anomaly)
        required_page = (anomaly.evidence or {}).get('expected_page', '')
        for entry in self.entries.values():
            if entry.verification_version < 1:
                continue
            if entry.config_name != self.config_name:
                # 跨账号共享：仅限已验证成功的非代码异常；代码错误方案永远账号隔离
                if not (self.cfg.share_across_accounts
                        and anomaly.type not in {'script_error', 'task_exception'}
                        and entry.success >= 1):
                    continue
            if entry.task and entry.task != anomaly.task:
                continue
            if entry.anomaly != anomaly.type:
                continue
            if entry.error_signature and entry.error_signature != signature:
                continue
            if anomaly.type in {'script_error', 'task_exception'} and not entry.error_signature:
                continue
            if required_page and entry.expected_page != required_page:
                continue
            if entry.consecutive_failures >= self.failure_limit:
                continue
            if entry.page and anomaly.page and entry.page != anomaly.page:
                continue
            if entry.resolution and list(entry.resolution) != list(resolution):
                continue
            if entry.game_version and game_version and entry.game_version != game_version:
                continue
            if entry.total < 1 or entry.rate < self.cfg.min_success_rate:
                continue
            age_days = (now - (entry.last_success or entry.created_at)) / 86400.0
            if age_days > self.cfg.max_age_days:
                continue
            distance = hamming(entry.phash, phash)
            if distance > self.cfg.max_ham_distance:
                continue
            # 距离相同优先本账号条目；否则距离近者优先
            rank = (distance, 0 if entry.config_name == self.config_name else 1)
            if rank < best_rank:
                best, best_rank = entry, rank
        if best is not None:
            best.last_used = now
        return best

    def record(self, anomaly: Anomaly, diagnosis: Diagnosis, actions_done: list[dict],
               verified: bool, phash: str, resolution: tuple[int, int], game_version: str = '',
               note: str = '', *, entry_id: str | None = None) -> KnowledgeEntry:
        now = self.clock()
        signature = error_fingerprint(anomaly)
        if not entry_id:
            target_plan = plan_signature(actions_done or diagnosis.actions, diagnosis.expected_page)
            for candidate in self.entries.values():
                if (candidate.config_name == self.config_name and candidate.error_signature == signature
                        and candidate.game_version == game_version
                        and list(candidate.resolution) == list(resolution)
                        and hamming(candidate.phash, phash) <= self.cfg.max_ham_distance
                        and plan_signature(candidate.actions, candidate.expected_page) == target_plan):
                    entry_id = candidate.id
                    break
        if entry_id and entry_id in self.entries:
            entry = self.entries[entry_id]
        else:
            entry = KnowledgeEntry(
                id=entry_id or f'{self.config_name}-{anomaly.task}-{anomaly.type}-{time.time_ns()}',
                task=anomaly.task,
                anomaly=anomaly.type,
                page=anomaly.page,
                phash=phash,
                resolution=list(resolution),
                game_version=game_version,
                actions=[_action_dict(action) for action in diagnosis.actions] if diagnosis else [],
                expected_change=diagnosis.expected_change if diagnosis else '',
                expected_page=diagnosis.expected_page if diagnosis else '',
                config_name=self.config_name,
                verification_version=1,
                created_at=now,
                error_signature=signature,
            )
        entry.last_used = now
        if verified:
            entry.success += 1
            entry.last_success = now
            entry.consecutive_failures = 0
        else:
            entry.fail += 1
            entry.consecutive_failures += 1
        if note:
            entry.note = note[:200]
        entry.actions = actions_done or entry.actions
        self.entries[entry.id] = entry
        if entry.consecutive_failures >= self.failure_limit:
            # 失效方案不删除，但 lookup 的 rate 门槛会自然淘汰
            entry.note = (entry.note + ' | 连续失效，已降级').strip(' |')[:200]
        self._append(entry)
        return entry

    def mark_reuse(self, entry: KnowledgeEntry, success: bool, *, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        entry.last_used = now
        if success:
            entry.success += 1
            entry.last_success = now
            entry.consecutive_failures = 0
        else:
            entry.fail += 1
            entry.consecutive_failures += 1
        self.entries[entry.id] = entry
        self._append(entry)

    # ---------- 截图去重 ----------

    def was_seen(self, content_hash: str, ttl_s: float = 600.0, *, now: float | None = None) -> bool:
        if not content_hash:
            return False
        now = self.clock() if now is None else now
        last = self._seen.get(content_hash)
        return last is not None and (now - last) < ttl_s

    def mark_seen(self, content_hash: str, *, now: float | None = None) -> None:
        if not content_hash:
            return
        self._seen[content_hash] = self.clock() if now is None else now
        # 只保留最近 200 条
        if len(self._seen) > 200:
            for key in sorted(self._seen, key=self._seen.get)[:len(self._seen) - 200]:
                self._seen.pop(key, None)
        try:
            self.seen_path.parent.mkdir(parents=True, exist_ok=True)
            self.seen_path.write_text(json.dumps(self._seen), encoding='utf-8')
        except OSError:
            pass

    def stats(self) -> dict:
        total = len(self.entries)
        verified = sum(1 for entry in self.entries.values()
                       if entry.config_name == self.config_name and entry.verification_version >= 1
                       and entry.rate >= self.cfg.min_success_rate and entry.consecutive_failures < self.failure_limit)
        legacy = sum(1 for entry in self.entries.values() if entry.verification_version < 1)
        return {'entries': total, 'reliable': verified, 'legacy_not_replayed': legacy,
                'quarantined': sum(e.consecutive_failures >= self.failure_limit for e in self.entries.values()
                                   if e.config_name == self.config_name), 'path': str(self.path)}

    def compact(self, *, now: float | None = None) -> int:
        """按内存最新状态原子重写知识库文件，剔除过期废条目，返回清理条数。

        废条目 = 从未成功过（success == 0）且已超过 max_age_days 的方案。
        """
        now = self.clock() if now is None else now
        cutoff = now - float(self.cfg.max_age_days) * 86400.0
        before = len(self.entries)
        kept: dict[str, KnowledgeEntry] = {}
        for entry_id, entry in self.entries.items():
            if entry.success == 0 and (entry.last_used or entry.created_at) < cutoff:
                continue
            kept[entry_id] = entry
        self.entries = kept
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + '.tmp')
            with tmp.open('w', encoding='utf-8') as handle:
                for entry in self.entries.values():
                    handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + '\n')
            tmp.replace(self.path)
            self._path_sig = self._stat_sig()
        except OSError:
            pass
        return before - len(self.entries)

    # ---------- 内部 ----------

    def _stat_sig(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
            return (int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            return None

    def _reload_if_changed(self) -> None:
        sig = self._stat_sig()
        if sig is None or sig == self._path_sig:
            return
        self._load_entries()

    def _load_entries(self) -> None:
        if self.path.exists():
            try:
                loaded: dict[str, KnowledgeEntry] = {}
                for line in self.path.read_text(encoding='utf-8').splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = KnowledgeEntry.from_dict(json.loads(line))
                    except (ValueError, TypeError):
                        continue
                    if entry.id:
                        loaded[entry.id] = entry
                self.entries = loaded
                self._path_sig = self._stat_sig()
            except OSError:
                pass

    def _load(self) -> None:
        self._load_entries()
        if self.seen_path.exists():
            try:
                data = json.loads(self.seen_path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    self._seen = {str(k): float(v) for k, v in data.items()}
            except (OSError, ValueError, TypeError):
                self._seen = {}

    def _append(self, entry: KnowledgeEntry) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + '\n')
            self._path_sig = self._stat_sig()
        except OSError:
            pass


def _action_dict(action: Action) -> dict:
    return {'type': action.type, 'x': action.x, 'y': action.y, 'text': action.text,
            'wait_ms': action.wait_ms, 'page_key': action.page_key}


def replay_plan(entry: KnowledgeEntry) -> list[Action]:
    return [
        Action(type=str(item.get('type') or ''), x=item.get('x'), y=item.get('y'),
               text=str(item.get('text') or ''), wait_ms=int(item.get('wait_ms') or 0),
               page_key=str(item.get('page_key') or ''), reason='知识库复用')
        for item in entry.actions or []
    ]
