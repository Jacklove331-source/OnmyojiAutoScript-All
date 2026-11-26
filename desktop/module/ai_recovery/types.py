# This Python file uses the following encoding: utf-8

"""AI 自愈层的数据类型。

所有结构都保持可 JSON 序列化，便于落盘证据、知识库与离线回归。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    """AI 允许建议的动作类型。执行器只认这里列出的类型。"""

    CLICK = 'click'
    WAIT = 'wait'
    BACK = 'back'
    CLOSE_POPUP = 'close_popup'
    GOTO_PAGE = 'goto_page'

    @classmethod
    def values(cls) -> set[str]:
        return {item.value for item in cls}


class Risk(str, Enum):
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'


class Outcome(str, Enum):
    RECOVERED = 'recovered'          # AI 动作生效，页面回到已知状态，交还 OAS
    OBSERVED = 'observed'            # 观察模式：只分析不点击
    STOPPED = 'stopped'              # 安全停止本次接管，原任务按既有逻辑处理
    SKIPPED = 'skipped'              # 未启用/冷却/去重/预算，未调用 AI
    UNAVAILABLE = 'unavailable'      # API 不可用或返回非法，已退回本地逻辑
    NEEDS_HUMAN = 'needs_human'      # 低置信/高风险，保存证据等待人工


@dataclass
class Anomaly:
    """一次异常事件。由检测器或任务代码产生。"""

    type: str
    task: str = ''
    page: str = ''
    severity: str = 'warning'
    detail: str = ''
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    incident_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def key(self) -> str:
        return f'{self.task}|{self.type}|{self.page}'

    def to_dict(self) -> dict:
        data = asdict(self)
        data['created_at_iso'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.created_at))
        return data

    @classmethod
    def from_dict(cls, data: dict) -> 'Anomaly':
        return cls(
            type=data.get('type', 'unknown'),
            task=data.get('task', ''),
            page=data.get('page', ''),
            severity=data.get('severity', 'warning'),
            detail=data.get('detail', ''),
            evidence=data.get('evidence') or {},
            created_at=float(data.get('created_at', time.time())),
            incident_id=data.get('incident_id') or uuid.uuid4().hex[:12],
        )


@dataclass
class Action:
    """AI 返回的单个动作（坐标一律归一化到 0~1，避免分辨率耦合）。"""

    type: str
    x: float | None = None
    y: float | None = None
    text: str = ''
    wait_ms: int = 0
    page_key: str = ''
    reason: str = ''

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Action':
        return cls(
            type=str(data.get('type', '')),
            x=_opt_float(data.get('x')),
            y=_opt_float(data.get('y')),
            text=str(data.get('text', '') or ''),
            wait_ms=int(data.get('wait_ms') or 0),
            page_key=str(data.get('page_key', '') or ''),
            reason=str(data.get('reason', '') or ''),
        )


@dataclass
class Diagnosis:
    """DeepSeek 的结构化分析结果。"""

    page: str
    page_known: bool
    confidence: float
    cause: str
    actions: list[Action] = field(default_factory=list)
    expected_change: str = ''
    risk: str = Risk.MEDIUM.value
    template_update: dict = field(default_factory=dict)
    needs_human: bool = False
    page_category: str = 'unknown'
    raw: dict = field(default_factory=dict)
    expected_page: str = ''
    # 逐个动作校验时被丢弃的非法动作（保留其余动作继续执行）
    dropped_actions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'page': self.page,
            'page_known': self.page_known,
            'page_category': self.page_category,
            'confidence': self.confidence,
            'cause': self.cause,
            'expected_change': self.expected_change,
            'expected_page': self.expected_page,
            'risk': self.risk,
            'needs_human': self.needs_human,
            'actions': [item.to_dict() for item in self.actions],
            'template_update': self.template_update,
            'dropped_actions': self.dropped_actions,
        }


@dataclass
class RecoveryResult:
    outcome: Outcome
    incident_id: str
    message: str = ''
    diagnosis: Diagnosis | None = None
    verified: bool = False
    ai_called: bool = False
    actions_done: list[dict] = field(default_factory=list)
    tokens: int = 0
    cost_cny: float = 0.0
    evidence_dir: str = ''
    verification: dict = field(default_factory=dict)
    # soft-fail：没恢复、但失败是可恢复的（例如预算耗尽/冷却），并给出建议的等待秒数
    soft_fail: bool = False
    retry_after_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            'outcome': self.outcome.value,
            'incident_id': self.incident_id,
            'message': self.message,
            'verified': self.verified,
            'ai_called': self.ai_called,
            'actions_done': self.actions_done,
            'tokens': self.tokens,
            'cost_cny': round(self.cost_cny, 6),
            'evidence_dir': self.evidence_dir,
            'verification': self.verification,
            'soft_fail': self.soft_fail,
            'retry_after_s': round(float(self.retry_after_s), 1),
            'diagnosis': self.diagnosis.to_dict() if self.diagnosis else None,
        }

    @property
    def should_continue(self) -> bool:
        """是否允许原任务继续（恢复成功或观察模式）。"""
        return self.outcome in (Outcome.RECOVERED, Outcome.OBSERVED)


def _opt_float(value) -> float | None:
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
