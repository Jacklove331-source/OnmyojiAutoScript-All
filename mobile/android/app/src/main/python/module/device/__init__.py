"""手机端 `module.device` 包的入口。

OAS 桌面版这里是 adb/nemu/uiautomator2 的合集；手机端只导出 `Device`，
外加几个任务代码会 import 的兼容符号。
"""

from .device import (  # noqa: F401
    ORIENTATION_LANDSCAPE,
    ORIENTATION_PORTRAIT,
    Device,
)

__all__ = ["Device", "ORIENTATION_LANDSCAPE", "ORIENTATION_PORTRAIT"]
