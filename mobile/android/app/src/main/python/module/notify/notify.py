"""最小 `module.notify`：OAS 桌面版用它推送（ServerChan/Telegram/等）。

手机端不需要推送（或后续接 Android 通知），这里给一个**不报错、不联网**的 Notifier，
保证 `module.config.config` 能正常导入；真要用通知时再替换实现。
"""

from __future__ import annotations


class Notifier:
    """与桌面版同名的最小实现：接口保持一致，行为是 no-op。"""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002 - 兼容桌面签名
        self.enabled = False
        self.title = "OAS Mobile"

    def load_config(self, *args, **kwargs) -> None:  # noqa: ARG002
        """桌面版从配置读推送参数；手机端不推送。"""

    def notify(self, title: str = "", content: str = "") -> bool:
        """no-op：返回 False 表示「没有真的推送」。"""
        return False

    def push(self, title: str = "", content: str = "", *, message: str = "") -> bool:
        """Keep the task notification interface available on Android."""
        return self.notify(title=title, content=content or message)

    def send(self, *args, **kwargs) -> bool:  # noqa: ARG002
        return False
