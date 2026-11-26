"""Android 端的 desktop_impl 兼容层。

桌面 OAS 的 `module.device.method.desktop_impl` 负责拉起 PC 客户端窗口（Windows 专用）。
任务/配置里会 `from module.device.method.desktop_impl import desktop_launch_instance`，
所以在 Android 上这个模块必须存在，否则 import 阶段就炸。

这里只做两件事：
1. 提供同名入口，调用时返回 False 并在日志里说明「Android 不支持桌面实例」；
2. 不假装成功——上层看到 False 就知道没拉起。
"""

from module.logger import logger


def desktop_launch_instance(config=None) -> bool:  # noqa: ARG001
    logger.warning(
        'desktop_launch_instance 在 Android 端不可用：'
        '本机请直接用 /target 或 /task/run 启动游戏任务'
    )
    return False


def desktop_window_titles(config=None):  # noqa: ARG001
    return ()


def desktop_pid_alive(pid) -> bool:  # noqa: ARG001
    return False
