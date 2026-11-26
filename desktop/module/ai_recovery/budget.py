# This Python file uses the following encoding: utf-8

"""Token / 费用预算的硬上限。

每次调用前 ``reserve()``，调用后 ``commit()``。状态落盘到
``log/ai_recovery/budget_<config>.json``，进程重启后仍然有效；
达到任一上限时直接拒绝调用（返回 False + 原因），绝不先花后算。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from module.ai_recovery.runtime import RecoveryLease

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Usage:
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    model: str = ''

    @property
    def total_tokens(self) -> int:
        return int(self.prompt_tokens) + int(self.completion_tokens)

    def to_dict(self) -> dict:
        return {
            'prompt_tokens': self.prompt_tokens,
            'cached_tokens': self.cached_tokens,
            'completion_tokens': self.completion_tokens,
            'total_tokens': self.total_tokens,
            'cost_cny': round(self.cost_cny, 6),
            'model': self.model,
        }


@dataclass
class BudgetState:
    day: str = ''
    hour: int = 0
    calls_today: int = 0
    calls_hour: int = 0
    cost_today: float = 0.0
    tokens_today: int = 0
    last_call: float = 0.0
    history: list = field(default_factory=list)
    # 单故障任务小时计数：task -> 本小时已调用次数
    task_calls_hour: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'day': self.day,
            'hour': self.hour,
            'calls_today': self.calls_today,
            'calls_hour': self.calls_hour,
            'cost_today': round(self.cost_today, 6),
            'tokens_today': self.tokens_today,
            'last_call': self.last_call,
            'history': self.history[-120:],
            'task_calls_hour': {str(key): int(value) for key, value in (self.task_calls_hour or {}).items()},
        }


class BudgetGuard:
    def __init__(self, cfg, config_name: str = 'oas', path: Path | None = None, now: float | None = None):
        self.cfg = cfg
        self.config_name = config_name
        root = Path(os.environ.get('OAS_AI_LOG_DIR') or (ROOT / 'log' / 'ai_recovery'))
        self.path = Path(path) if path else root / f'budget_{config_name}.json'
        self._now = now
        self._load_failed = False
        self.state = self._load()
        self._incident_calls = 0
        self._incident_task = ''
        self._pending = []
        self._roll()

    # ---------- public ----------

    def start_incident(self, task: str = '') -> None:
        self._incident_calls = 0
        self._incident_task = str(task or '')

    def check(self, *, model: str = '', task: str = '') -> tuple[bool, str]:
        self._roll()
        cfg = self.cfg.budget
        if self._load_failed:
            return False, 'budget_state_unreadable'
        if self._incident_calls >= cfg.per_incident_calls:
            return False, f'per_incident_calls={cfg.per_incident_calls}'
        active_task = str(task or self._incident_task or '')
        if (active_task and cfg.per_task_hour_calls > 0
                and int(self.state.task_calls_hour.get(active_task, 0)) >= cfg.per_task_hour_calls):
            return False, f'per_task_hour_calls={cfg.per_task_hour_calls}'
        if self.state.calls_hour >= cfg.per_hour_calls:
            return False, f'per_hour_calls={cfg.per_hour_calls}'
        if self.state.calls_today >= cfg.per_day_calls:
            return False, f'per_day_calls={cfg.per_day_calls}'
        if self.state.cost_today + self._reservation_cost() > cfg.per_day_cny:
            return False, f'per_day_cny={cfg.per_day_cny}'
        return True, ''

    def _reservation_cost(self) -> float:
        # Conservative input allowance plus the configured output ceiling.
        worst_case = (6000 * self.cfg.budget.price_input_cny_per_mtok
                      + self.cfg.api.max_tokens * self.cfg.budget.price_output_cny_per_mtok) / 1_000_000
        return max(float(self.cfg.budget.reservation_cny), worst_case)

    def reserve(self, *, model: str = '', task: str = '') -> tuple[bool, str]:
        """Count and persist each request before sending, including failed attempts."""
        with RecoveryLease(self.path.parent, 'budget:' + str(self.path.resolve())) as lease:
            if not lease.acquired:
                return False, 'budget_state_busy'
            self.state = self._load()
            allowed, reason = self.check(model=model, task=task)
            if not allowed:
                return False, reason
            active_task = str(task or self._incident_task or '')
            reservation = {'id': uuid.uuid4().hex, 'day': self.state.day,
                           'cost': self._reservation_cost()}
            self._incident_calls += 1
            self.state.calls_today += 1
            self.state.calls_hour += 1
            if active_task:
                self.state.task_calls_hour[active_task] = int(self.state.task_calls_hour.get(active_task, 0)) + 1
            self.state.cost_today += reservation['cost']
            self.state.last_call = self._now_ts()
            self.state.history.append({'reservation_id': reservation['id'], 'status': 'reserved',
                                       'model': model or self.cfg.api.model,
                                       'cost_cny': reservation['cost'], 'time': self._now_ts()})
            if not self._save():
                self.state = self._load()
                self._incident_calls -= 1
                return False, 'budget_state_write_failed'
            self._pending.append(reservation)
            return True, ''

    def commit(self, usage: Usage) -> None:
        with RecoveryLease(self.path.parent, 'budget:' + str(self.path.resolve())) as lease:
            if not lease.acquired:
                return  # Retain conservative reservation when accounting cannot be updated.
            self.state = self._load()
            if self._load_failed:
                return
            self._roll()
            reserved = self._pending.pop() if self._pending else None
            if reserved and reserved['day'] == self.state.day:
                self.state.cost_today = max(0.0, self.state.cost_today - reserved['cost'])
            if reserved is None:
                self._incident_calls += 1
                self.state.calls_today += 1
                self.state.calls_hour += 1
                if self._incident_task:
                    self.state.task_calls_hour[self._incident_task] = \
                        int(self.state.task_calls_hour.get(self._incident_task, 0)) + 1
            self.state.cost_today += max(0.0, float(usage.cost_cny))
            self.state.tokens_today += usage.total_tokens
            self.state.last_call = self._now_ts()
            self.state.history.append({'time': self._now_ts(), 'status': 'completed',
                                       'reservation_id': reserved['id'] if reserved else '',
                                       **usage.to_dict()})
            self.state.history = self.state.history[-120:]
            self._save()

    def snapshot(self) -> dict:
        self._roll()
        return {
            'config': self.config_name,
            'state': self.state.to_dict(),
            'limits': {
                'per_incident_calls': self.cfg.budget.per_incident_calls,
                'per_hour_calls': self.cfg.budget.per_hour_calls,
                'per_task_hour_calls': self.cfg.budget.per_task_hour_calls,
                'per_day_calls': self.cfg.budget.per_day_calls,
                'per_day_cny': self.cfg.budget.per_day_cny,
            },
            'incident_calls': self._incident_calls,
            'incident_task': self._incident_task,
            'task_calls_hour': dict(self.state.task_calls_hour),
            'reservation_cny': self._reservation_cost(),
            'state_readable': not self._load_failed,
        }

    # ---------- internal ----------

    def _now_ts(self) -> float:
        return self._now if self._now is not None else time.time()

    def _roll(self) -> None:
        now = self._now_ts()
        day = time.strftime('%Y-%m-%d', time.localtime(now))
        hour = int(now // 3600)
        if self.state.day != day:
            self.state.day = day
            self.state.calls_today = 0
            self.state.cost_today = 0.0
            self.state.tokens_today = 0
        if self.state.hour != hour:
            self.state.hour = hour
            self.state.calls_hour = 0
            self.state.task_calls_hour = {}

    def _load(self) -> BudgetState:
        self._load_failed = False
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding='utf-8'))
                task_calls = {
                    str(key): int(value) for key, value in (data.get('task_calls_hour') or {}).items() if str(key)
                }
                return BudgetState(
                    day=data.get('day', ''),
                    hour=int(data.get('hour', 0)),
                    calls_today=int(data.get('calls_today', 0)),
                    calls_hour=int(data.get('calls_hour', 0)),
                    cost_today=float(data.get('cost_today', 0.0)),
                    tokens_today=int(data.get('tokens_today', 0)),
                    last_call=float(data.get('last_call', 0.0)),
                    history=list(data.get('history') or []),
                    task_calls_hour=task_calls,
                )
        except (OSError, ValueError, TypeError, AttributeError):
            self._load_failed = True
        return BudgetState()

    def _save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self.path)
            return True
        except OSError:
            return False


def estimate_cost(usage: dict, cfg) -> Usage:
    """按 DeepSeek usage 字段估算费用（缓存命中单独计价）。"""
    prompt_details = usage.get('prompt_tokens_details') or {}
    cached = int(usage.get('prompt_cache_hit_tokens') or prompt_details.get('cached_tokens') or 0)
    prompt = int(usage.get('prompt_tokens') or 0)
    completion = int(usage.get('completion_tokens') or 0)
    normal_prompt = max(0, prompt - cached)
    budget = cfg.budget
    cost = (
        normal_prompt / 1_000_000.0 * budget.price_input_cny_per_mtok
        + cached / 1_000_000.0 * budget.price_cached_input_cny_per_mtok
        + completion / 1_000_000.0 * budget.price_output_cny_per_mtok
    )
    return Usage(prompt_tokens=prompt, cached_tokens=cached, completion_tokens=completion,
                 cost_cny=cost, model=str(usage.get('model') or ''))
