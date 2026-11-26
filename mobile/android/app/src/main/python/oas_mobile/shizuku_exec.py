"""Shizuku shell 命令执行（Python 侧封装）。

`module.device.adb_shell` / `app_is_running` / `app_stop` 需要执行 shell 命令；
手机端统一走 `com.oas.oasx.shizuku.ShizukuShell`（无授权时退回 root 兜底，仅开发机可用）。
"""

from __future__ import annotations

from module.logger import logger

try:
    from java import jclass  # type: ignore
    _SHELL = jclass("com.oas.oasx.shizuku.ShizukuShell")
except Exception:  # noqa: BLE001 - 桌面环境
    _SHELL = None


def _join(command) -> str:
    """把 list/tuple 形式的命令拼成字符串（OAS 两种都会传）。"""
    if isinstance(command, (list, tuple)):
        import shlex
        return " ".join(shlex.quote(str(part)) for part in command)
    return str(command)


def exec_shell(command, timeout_ms: int = 20000) -> str:
    """执行 shell 命令（str 或 list 均可），返回 stdout 文本。"""
    command = _join(command)
    if _SHELL is None:
        logger.warning("[mobile] shizuku shell unavailable (not on Android)")
        return ""
    try:
        result = _SHELL.exec(command, int(timeout_ms))
        # Kotlin: data class ShellResult(val code: Int, val output: String, val bytes: ByteArray)
        output = str(result.getOutput()) if hasattr(result, "getOutput") else str(result.output)
        if getattr(result, "code", 0) != 0:
            logger.debug(f"[mobile] shell rc={result.code} cmd={command} out={output[:120]}")
        return output
    except Exception as error:  # noqa: BLE001
        logger.error(f"[mobile] shell exec failed: {error!r}")
        return ""


def available() -> bool:
    if _SHELL is None:
        return False
    try:
        return bool(_SHELL.hasPermission())
    except Exception:  # noqa: BLE001
        return False
