# This Python file uses the following encoding: utf-8

"""OAS AI 智能自愈层（DeepSeek）。

设计原则（对既有 OAS 的承诺）：
- 默认关闭；关闭时零开销、零调用、不改变任何原有行为。
- 只在原有图色/OCR/本地恢复全部失败后介入；AI 是辅助恢复层，不是主控制器。
- 所有动作都过白名单、坐标边界、次数上限与执行后验证，绝不无限连点。
- 模板变更只进候选/版本库，验证通过才允许替换正式资源，且可一键回滚。
- API Key 只从环境变量或本地密钥文件读取，不写日志、不进 Git。

对外主要入口：
    from module.ai_recovery import get_config, TaskAdapter, AiRecovery
"""

from module.ai_recovery.config import AiRecoveryConfig, get_config
from module.ai_recovery.types import (
    Action,
    ActionType,
    Anomaly,
    Diagnosis,
    Outcome,
    RecoveryResult,
)

__all__ = [
    'AiRecoveryConfig',
    'get_config',
    'Action',
    'ActionType',
    'Anomaly',
    'Diagnosis',
    'Outcome',
    'RecoveryResult',
]
