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
from dataclasses import dataclass, field
from pathlib import Path

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
        }


class BudgetGuard:
    def __init__(self, cfg, config_name: str = 'oas', path: Path | None = None, now: float | None = None):
        self.cfg = cfg
        self.config_name = config_name
        root = Path(os.environ.get('OAS_AI_LOG_DIR') or (ROOT / 'log' / 'ai_recovery'))
        self.path = Path(path) if path else root / f'budget_{config_name}.json'
        self._now = now
        self.state = self._load()
        self._incident_calls = 0
        self._roll()

    # ---------- public ----------

    def start_incident(self) -> None:
        self._incident_calls = 0

    def reserve(self, *, model: str = '') -> tuple[bool, str]:
        """调用前预检；不修改计数（调用失败不扣配额）。"""
        self._roll()
        cfg = self.cfg.budget
        if self._incident_calls >= cfg.per_incident_calls:
            return False, f'per_incident_calls={cfg.per_incident_calls}'
        if self.state.calls_hour >= cfg.per_hour_calls:
            return False, f'per_hour_calls={cfg.per_hour_calls}'
        if self.state.calls_today >= cfg.per_day_calls:
            return False, f'per_day_calls={cfg.per_day_calls}'
        if self.state.cost_today >= cfg.per_day_cny:
            return False, f'per_day_cny={cfg.per_day_cny}'
        return True, ''

    def commit(self, usage: Usage) -> None:
        self._roll()
        self._incident_calls += 1
        self.state.calls_today += 1
        self.state.calls_hour += 1
        self.state.cost_today += float(usage.cost_cny)
        self.state.tokens_today += usage.total_tokens
        self.state.last_call = time.time()
        self.state.history.append({
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'model': usage.model or self.cfg.api.model,
            **usage.to_dict(),
        })
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
                'per_day_calls': self.cfg.budget.per_day_calls,
                'per_day_cny': self.cfg.budget.per_day_cny,
            },
            'incident_calls': self._incident_calls,
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

    def _load(self) -> BudgetState:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding='utf-8'))
                return BudgetState(
                    day=data.get('day', ''),
                    hour=int(data.get('hour', 0)),
                    calls_today=int(data.get('calls_today', 0)),
                    calls_hour=int(data.get('calls_hour', 0)),
                    cost_today=float(data.get('cost_today', 0.0)),
                    tokens_today=int(data.get('tokens_today', 0)),
                    last_call=float(data.get('last_call', 0.0)),
                    history=list(data.get('history') or []),
                )
        except (OSError, ValueError, TypeError):
            pass
        return BudgetState()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self.path)
        except OSError:
            pass


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
