# This Python file uses the following encoding: utf-8

"""构造异常事件的小工具（统一字段，避免各处手写）。"""

from __future__ import annotations

from module.ai_recovery.types import Anomaly


def build_anomaly(kind: str, task: str, page: str = '', *, detail: str = '',
                  severity: str = 'warning', evidence: dict | None = None) -> Anomaly:
    return Anomaly(type=str(kind), task=str(task or ''), page=str(page or ''),
                   severity=severity, detail=str(detail or ''),
                   evidence=dict(evidence or {}))
