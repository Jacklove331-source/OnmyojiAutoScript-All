"""Kotlin 执行层桥接：截图 / 点击 / 滑动。

在真机上由 Chaquopy 的 `java` 模块直接调用 `com.oas.oasx.OasAccessibilityService`
的静态方法；在桌面端（开发机无 Android）导入本模块会走 fallback，返回明确的不可用错误，
避免把桌面环境的结果当成真机结果。
"""

from __future__ import annotations

import os
import time

from module.logger import logger

_JAVA_IMPORT_ERROR = None
_SERVICE = None
DEFAULT_TARGET_PACKAGE = os.environ.get("OAS_TARGET_PACKAGE", "com.netease.onmyoji")
_TARGET_PACKAGE = DEFAULT_TARGET_PACKAGE

try:  # 只有在 Android(Chaquopy) 里才存在 java 模块
    from java import jclass  # type: ignore

    _SERVICE = jclass("com.oas.oasx.OasAccessibilityService")
except Exception as error:  # noqa: BLE001 - 桌面环境预期会失败
    _JAVA_IMPORT_ERROR = error
    _SERVICE = None


def available() -> bool:
    """执行层是否可用（Android + 无障碍服务已连接）。"""
    if _SERVICE is None:
        return False
    try:
        return bool(_SERVICE.isServiceReady())
    except Exception:  # noqa: BLE001
        return False


def unavailable_reason() -> str:
    if _SERVICE is None:
        return f"not on Android/Chaquopy: {_JAVA_IMPORT_ERROR!r}"
    return "accessibility service not connected (user must enable it in system settings)"


def _su_binary() -> str:
    """设备是否有 su（模拟器一般有，真机没有）。"""
    for candidate in ("/system/bin/su", "/system/xbin/su", "/sbin/su", "/su/bin/su"):
        if os.path.exists(candidate):
            return candidate
    return ""


def capture_via_su() -> bytes:
    """有 root 时走 Kotlin 侧的 `su -c screencap -p`（capturePngRoot）。

    为什么不用 Python subprocess：Chaquopy 里 exec su 会卡到超时且拿不到文件；
    Kotlin ProcessBuilder 同样有 SELinux 限制，所以这条失败就安静回落到无障碍。
    """
    if _SERVICE is None:
        return b""
    try:
        data = _SERVICE.capturePngRoot()
    except Exception:  # noqa: BLE001
        return b""
    if data is None:
        return b""
    try:
        raw = bytes(data)
    except Exception:  # noqa: BLE001
        return b""
    return raw if raw[:1] == b"\x89" else b""


_ROOT_CAPTURE_FLAG = None


def root_capture_enabled() -> bool:
    """root 截图默认关闭。

    调用 su 会在设备上弹「请求超级用户权限」对话框，用户明令零弹窗，
    所以只有存在 files/su_capture.on 时才走 root 通道（用户自己放的开关文件）。
    """
    global _ROOT_CAPTURE_FLAG
    if _ROOT_CAPTURE_FLAG is None:
        try:
            import os

            _ROOT_CAPTURE_FLAG = os.path.exists(os.path.join(_files_dir(), "su_capture.on"))
        except Exception:  # noqa: BLE001
            _ROOT_CAPTURE_FLAG = False
    return bool(_ROOT_CAPTURE_FLAG)


def _files_dir() -> str:
    try:
        from java import jclass

        app = jclass("com.chaquo.python.Python").getPlatform().getApplication()
        return str(app.getFilesDir().getAbsolutePath())
    except Exception:  # noqa: BLE001
        return ""


def capture_png() -> bytes:
    """截取当前屏幕，返回 PNG 字节。失败抛 RuntimeError 并说明原因。

    顺序：无障碍（不弹窗）→ 只有在用户显式开启 root 截图时才试 su。
    """
    if _SERVICE is not None:
        try:
            data = _SERVICE.capturePng()
        except Exception:  # noqa: BLE001
            data = None
        if data is not None:
            return bytes(data)
    if root_capture_enabled():
        rooted = capture_via_su()
        if rooted:
            return rooted
    raise RuntimeError(f"screenshot unavailable: {unavailable_reason()}")


def tap(x: int, y: int, duration_ms: int = 60) -> bool:
    if _SERVICE is None:
        raise RuntimeError(f"tap unavailable: {unavailable_reason()}")
    return bool(_SERVICE.tap(int(x), int(y), int(duration_ms)))


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> bool:
    if _SERVICE is None:
        raise RuntimeError(f"swipe unavailable: {unavailable_reason()}")
    return bool(_SERVICE.swipe(int(x1), int(y1), int(x2), int(y2), int(duration_ms)))


# ---------------------------------------------------------------- 后台运行相关

try:  # 前台服务类（Chaquopy 里可用；桌面环境为 None）
    _FOREGROUND_SERVICE = jclass("com.oas.oasx.OasForegroundService")
except Exception:  # noqa: BLE001
    _FOREGROUND_SERVICE = None


def foreground_via_shell() -> str:
    """用 Shizuku 跑 dumpsys 取前台包名（无障碍没绑定时用，不弹任何窗）。"""
    if _SHIZUKU is None:
        return ""
    try:
        if not bool(_SHIZUKU.hasPermission()):
            return ""
    except Exception:  # noqa: BLE001
        return ""
    import re
    texts = []
    for cmd in ("dumpsys window | grep mCurrentFocus",
                "dumpsys activity activities | grep mResumedActivity"):
        try:
            res = _SHIZUKU.exec(cmd, 8000)
            texts.append(str(getattr(res, "output", "") or ""))
        except Exception:  # noqa: BLE001
            continue
    for text in texts:
        hits = re.findall(r"(?:u0|u10) ([A-Za-z0-9_.]+)/", text)
        others = [pkg for pkg in hits if pkg != "com.oas.oasx"]
        if others:
            return others[-1]
    # 实在判不出就返回空串，让上层不要在"自己在前台"时乱抓
    return ""


def foreground_package() -> str:
    """当前前台应用包名（无障碍事件更新）。"""
    if _SERVICE is None:
        return ""
    try:
        return str(_SERVICE.currentForegroundPackage())
    except Exception:  # noqa: BLE001
        try:
            return str(_SERVICE.foregroundPackage)
        except Exception:  # noqa: BLE001
            return ""


def set_target_package(package: str) -> None:
    """设置目标游戏包名（默认阴阳师），用于判断「游戏是否在前台」。"""
    global _TARGET_PACKAGE
    _TARGET_PACKAGE = str(package or DEFAULT_TARGET_PACKAGE)
    if _SERVICE is None:
        return
    try:
        _SERVICE.setTargetPackage(package)
    except Exception:  # noqa: BLE001
        pass


def target_package() -> str:
    return _TARGET_PACKAGE


def is_target_foreground() -> bool:
    """目标游戏是否在前台——后台运行时用它决定能不能注入手势。"""
    if _SERVICE is None:
        return False
    try:
        return bool(_SERVICE.isTargetForeground())
    except Exception:  # noqa: BLE001
        return False


def service_running() -> bool:
    """前台服务（承载 Python 与任务循环）是否在跑。

    优先用注入的实例句柄；拿不到时问 Kotlin 的静态方法（companion isRunning），
    否则 release 包里会误报 false（服务其实在跑）。
    """
    if _FOREGROUND_SERVICE is not None:
        try:
            return bool(_FOREGROUND_SERVICE.isRunning())
        except Exception:  # noqa: BLE001
            pass
    try:
        from java import jclass

        return bool(jclass("com.oas.oasx.OasForegroundService").isRunning())
    except Exception:  # noqa: BLE001
        return False


def paused() -> bool:
    if _FOREGROUND_SERVICE is None:
        return False
    try:
        return bool(_FOREGROUND_SERVICE.getPaused())
    except Exception:  # noqa: BLE001
        try:
            return bool(_FOREGROUND_SERVICE.paused)
        except Exception:  # noqa: BLE001
            return False


def set_paused(value: bool) -> bool:
    if _FOREGROUND_SERVICE is None:
        return False
    try:
        result = _FOREGROUND_SERVICE.updatePaused(bool(value))
        return True if result is None else bool(result)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- Shizuku 虚拟屏（后台挂机）

try:  # Shizuku shell 通道（需要用户在 App 里授权）
    _SHIZUKU = jclass("com.oas.oasx.shizuku.ShizukuShell")
except Exception:  # noqa: BLE001
    _SHIZUKU = None


def shizuku_available() -> bool:
    if _SHIZUKU is None:
        return False
    try:
        return bool(_SHIZUKU.hasPermission())
    except Exception:  # noqa: BLE001
        return False


def shizuku_status() -> str:
    if _SHIZUKU is None:
        return "not on Android"
    try:
        return str(_SHIZUKU.status())
    except Exception as error:  # noqa: BLE001
        return f"error: {error!r}"


def shizuku_request_permission() -> bool:
    if _SHIZUKU is None:
        return False
    try:
        _SHIZUKU.requestPermission(10086)
        return True
    except Exception:  # noqa: BLE001
        return False


def display_create(width: int = 1280, height: int = 720, dpi: int = 240) -> int:
    """建虚拟屏，返回 displayId；失败 -1。"""
    if _SHIZUKU is None:
        return -1
    try:
        return int(_SHIZUKU.createOverlayDisplay(int(width), int(height), int(dpi)))
    except Exception:  # noqa: BLE001
        return -1


def display_launch(package: str, display_id: int) -> bool:
    if _SHIZUKU is None:
        return False
    try:
        return bool(_SHIZUKU.launchOnDisplay(package, int(display_id)))
    except Exception:  # noqa: BLE001
        return False


def _shot_file_path() -> str:
    """截图落盘路径：优先 App 的 external files dir（shell 可写、App 可读）。"""
    base = ""
    try:
        from com.chaquo.python import Python  # type: ignore

        folder = Python.getPlatform().getApplication().getExternalFilesDir(None)
        if folder is not None:
            base = str(folder)
    except Exception:  # noqa: BLE001
        base = ""
    if not base:
        base = "/sdcard/Android/data/com.oas.oasx/files"
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return os.path.join(base, "_oasx_shot.png")


def _capture_to_file(display_id: int) -> bytes:
    """主屏/虚拟屏截图：设备上写成 PNG 文件再读回来。

    为什么不直接读 `screencap -p` 的 stdout：游戏画面单帧 4MB+，
    二进制走 Shizuku 的 stdout 管道时 App 侧会卡死（整条 HTTP 桥一起假死）。
    落盘后只有几行文本输出，App 再按本地文件读，稳定得多。
    """
    if _SHIZUKU is None:
        return b""
    path = _shot_file_path()
    try:
        os.remove(path)
    except OSError:
        pass
    flag = "" if int(display_id) < 0 else f" -d {int(display_id)}"
    try:
        _SHIZUKU.exec(f"screencap -p{flag} {path}", 20000)
    except Exception:  # noqa: BLE001
        return b""
    for _ in range(40):
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            data = b""
        if len(data) > 8 and data[0] == 0x89:
            return data
        time.sleep(0.05)
    return b""


def display_screenshot(display_id: int) -> bytes:
    display_id = int(display_id)
    # Android 14+ VDM 虚拟屏带 FLAG_PRIVATE，screencap -d 会返回空字节；
    # 只要流服务正持着这块屏，直接走 Binder 按需抓帧最快也最稳。
    if vdstream_running() and (display_id < 0 or display_id == vdstream_display()):
        stream_frame = vdstream_capture(85) or vdstream_jpeg()
        if stream_frame:
            return stream_frame
    # 没有真正的虚拟屏（-1）时，screencap -d -1 会永久阻塞；直接用普通截图通道
    if display_id < 0:
        return capture_png()
    if _SHIZUKU is None:
        # 没有 Shizuku 也要能干活：回落无障碍/root 截图
        return capture_png()
    data = _capture_to_file(display_id)
    if data:
        return data
    fallback = _SHIZUKU.screenshot(display_id)
    return bytes(fallback) if fallback is not None else b""


def capture_display_png(display_id: int) -> bytes:
    display_id = int(display_id)
    if vdstream_running():
        stream_display = vdstream_display()
        if display_id < 0 or display_id == stream_display:
            stream_frame = vdstream_capture(85) or vdstream_jpeg()
            if stream_frame:
                return stream_frame
    if display_id < 0:
        return capture_png()
    try:
        if hidden_ready() and hidden_display() == display_id:
            data = hidden_screenshot()
            if data:
                return bytes(data)
    except Exception:
        pass
    try:
        return display_screenshot(display_id)
    except Exception:
        return b""


def display_tap(display_id: int, x: int, y: int) -> bool:
    display_id = int(display_id)
    if _STREAM is not None and display_id >= 0 and vdstream_running() and display_id == vdstream_display():
        try:
            if bool(_STREAM.injectTap(int(x), int(y), 45)):
                return True
        except Exception:  # noqa: BLE001
            pass
    if _SHIZUKU is None:
        return False
    try:
        return bool(_SHIZUKU.tap(display_id, int(x), int(y)))
    except Exception:  # noqa: BLE001
        return False


def display_swipe(display_id: int, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> bool:
    display_id = int(display_id)
    if _STREAM is not None and display_id >= 0 and vdstream_running() and display_id == vdstream_display():
        try:
            if bool(_STREAM.injectSwipe(int(x1), int(y1), int(x2), int(y2), int(duration_ms))):
                return True
        except Exception:  # noqa: BLE001
            pass
    if _SHIZUKU is None:
        return False
    try:
        return bool(_SHIZUKU.swipe(display_id, int(x1), int(y1), int(x2), int(y2), int(duration_ms)))
    except Exception:  # noqa: BLE001
        return False


def display_keyevent(display_id: int, keycode: int) -> bool:
    """只往指定虚拟屏发按键，绝不发给主屏。"""
    display_id = int(display_id)
    if display_id < 0:
        return False
    if _STREAM is not None and vdstream_running() and display_id == vdstream_display():
        try:
            if bool(_STREAM.injectKeyEvent(int(keycode))):
                return True
        except Exception:  # noqa: BLE001
            pass
    if _SHIZUKU is None:
        return False
    try:
        return bool(_SHIZUKU.keyevent(display_id, int(keycode)))
    except Exception:  # noqa: BLE001
        return False


def display_ensure_app(package: str, width: int = 1280, height: int = 720) -> int:
    """确保目标 App 跑在虚拟屏上（建屏→启动→搬到虚拟屏），返回 displayId；失败 -1。"""
    # 没有 Shizuku 就没有虚拟屏：退化成"直接拉起游戏"，返回 -1 让上层走普通截图/点击
    if _SHIZUKU is None:
        try:
            from oas_mobile.shizuku_exec import exec_shell

            exec_shell("monkey -p %s 1" % package)
        except Exception:  # noqa: BLE001
            pass
        return -1
    try:
        # Binder 帧服务持有的虚拟屏优先。它由 shell 进程创建，不写任何系统设置，
        # 且任务截图/监控/注入都指向同一块 display。
        stream_display = vdstream_display() if vdstream_running() else -1
        if stream_display >= 0:
            if not _SHIZUKU.ensureStreamGame(package, stream_display):
                return -1
            if _STREAM is not None:
                _STREAM.attachTarget(package, True)
            return stream_display

        # 优先隐藏屏（用户完全看不见）：已建好就把游戏搬过去；被禁用则跳过
        hidden = hidden_display() if hidden_preferred() else -1
        if hidden >= 0:
            display_launch(package, hidden)
            display_move_task(package, hidden)
            return hidden
        # 禁止回落到 settings put global overlay_display_devices：用户明确不允许
        # 改系统设置。没有 Binder/MediaProjection 屏时由上层走无障碍截图。
        return -1
    except Exception:  # noqa: BLE001
        return -1


def game_running(package: str | None = None) -> bool:
    """目标游戏进程是否在跑（`pidof`）。"""
    if _SHIZUKU is None:
        return False
    package = package or target_package()
    try:
        result = _SHIZUKU.exec(f"pidof {package}", 5000)
        return bool((result.output or "").strip())
    except Exception:  # noqa: BLE001
        return False


def ensure_game_started(package: str | None = None, timeout: float = 40.0) -> bool:
    """保证游戏已经在跑（在主屏），再去建虚拟屏搬它。

    为什么需要：`app_start()` 直接去 `ensureStreamGame` 时，如果游戏根本没启动，
    要么命中「直接往虚拟屏上 am start」那条分支（阴阳师会拒绝在副屏渲染），
    要么等 25 s 超时 → 用户看到的现象就是「点了开始托管，游戏不自己起来，然后登录干等到超时」
    （实测 2026-07-xx 与 2026-11-22 都出现过）。
    这里先用 monkey 拉起默认入口，再等 `pidof` 出现；已经在跑就直接返回。
    """
    if _SHIZUKU is None:
        return False
    package = package or target_package()
    if game_running(package):
        return True
    logger.info(f"[mobile] {package} not running, launching it on the main display")
    try:
        _SHIZUKU.exec(
            f"monkey -p {package} -c android.intent.category.LAUNCHER 1", 15000)
    except Exception as error:  # noqa: BLE001
        logger.warning(f'[mobile] launch {package} failed: {error!r}')
    deadline = time.time() + max(5.0, float(timeout))
    while time.time() < deadline:
        if game_running(package):
            logger.info(f'[mobile] {package} launched')
            # 给它一点时间画出首帧，再搬屏
            time.sleep(3.0)
            return True
        time.sleep(1.0)
    logger.warning(f'[mobile] {package} did not start within {timeout:.0f}s')
    return False


def send_home(times: int = 2) -> bool:
    """回桌面（input keyevent 3，连按两次并留间隔）。

    为什么要按两次：停流瞬间刚被搬回主屏的游戏窗口会抢一次焦点，
    只按一次 Home 之后前台又变回游戏（实测 2026-11-22：一次 → top=onmyoji/.Client，
    两次 → top=lawnchair）。用户诉求是「停掉 OAS 后能回到桌面」。
    """
    if _SHIZUKU is None:
        return False
    ok = True
    for index in range(max(1, int(times))):
        try:
            ok = bool(_SHIZUKU.exec("input keyevent 3", 5000).code == 0) and ok
        except Exception:  # noqa: BLE001
            return False
        if index + 1 < max(1, int(times)):
            time.sleep(0.8)
    return ok


def display_move_task(package: str, display_id: int) -> bool:
    if _SHIZUKU is None:
        return False
    try:
        return bool(_SHIZUKU.moveTaskToDisplay(package, int(display_id)))
    except Exception:  # noqa: BLE001
        return False


def display_release() -> bool:
    """停流 + **把游戏搬回主屏** + 释放虚拟屏。

    「搬回主屏」这步不能省：模拟器窗口只渲染 display 0，游戏一旦留在虚拟屏上，
    用户侧表现就是「点阴阳师标签回不去、想关也关不掉」（实测 2026-11-22 用户反馈）。
    """
    if _SHIZUKU is None:
        return False
    package = target_package()
    # 先搬回主屏，再停流（停流会销毁虚拟屏，反序会把游戏 Activity 一起带走）
    try:
        moved = bool(_SHIZUKU.moveTaskToDisplay(package, 0))
    except Exception:  # noqa: BLE001
        moved = False
    try:
        vdstream_stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        released = bool(_SHIZUKU.releaseOverlayDisplays())
    except Exception:  # noqa: BLE001
        released = False
    logger.info(f'[mobile] display released (moved_back={moved}, released={released}, package={package})')
    return released or moved


def display_alive(display_id: int) -> bool:
    # 隐藏屏（MediaProjection）由 App 自己持有，用 hidden_ready 判断
    try:
        if hidden_display() == int(display_id) and hidden_ready():
            return True
    except Exception:  # noqa: BLE001
        pass
    if _SHIZUKU is None:
        return False
    try:
        return bool(_SHIZUKU.isDisplayAlive(int(display_id)))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 隐藏虚拟屏（MediaProjection）

try:  # 只有 Android(Chaquopy) 里存在
    _HIDDEN = jclass("com.oas.oasx.HiddenDisplayBridge")
except Exception:  # noqa: BLE001
    _HIDDEN = None


# 是否优先使用隐藏屏（默认关闭：overlay 路径已验证可用）
_HIDDEN_PREFERRED = False


def hidden_set_preferred(value: bool) -> None:
    global _HIDDEN_PREFERRED
    _HIDDEN_PREFERRED = bool(value)


def hidden_preferred() -> bool:
    return bool(_HIDDEN_PREFERRED and not _HIDDEN_DISABLED)


_HIDDEN_DISABLED = False


def hidden_disable() -> None:
    """隐藏屏抓不到帧时由设备层调用：之后不再优先使用它。"""
    global _HIDDEN_DISABLED
    _HIDDEN_DISABLED = True


def hidden_disabled() -> bool:
    return bool(_HIDDEN_DISABLED)


# ---------------------------------------------------------------- 无头虚拟屏宿主（shell app_process）
try:
    _VDHOST = jclass("com.oas.oasx.VdHostBridge")
except Exception:  # noqa: BLE001
    _VDHOST = None


def vdhost_start(width: int = 1280, height: int = 720, dpi: int = 240) -> int:
    """用 Shizuku 起 shell 侧 app_process 宿主，返回 displayId（-1 失败）。

    这块屏的 owner 是 shell 进程，应用切后台不影响它，主屏也看不到。
    """
    if _VDHOST is None:
        return -1
    try:
        return int(_VDHOST.start(int(width), int(height), int(dpi)))
    except Exception:  # noqa: BLE001
        return -1


def vdhost_display() -> int:
    if _VDHOST is None:
        return -1
    try:
        return int(_VDHOST.displayId())
    except Exception:  # noqa: BLE001
        return -1


def vdhost_ready() -> bool:
    if _VDHOST is None:
        return False
    try:
        return bool(_VDHOST.ready())
    except Exception:  # noqa: BLE001
        return False


def vdhost_status() -> str:
    if _VDHOST is None:
        return "not on Android"
    try:
        return str(_VDHOST.status())
    except Exception as error:  # noqa: BLE001
        return f"error: {error!r}"


def vdhost_screenshot() -> bytes:
    """从宿主帧服务取一帧 PNG（真后台 + 主屏不可见）。"""
    if _VDHOST is None:
        return b""
    try:
        data = _VDHOST.capturePng()
        if data is None:
            return b""
        return bytes(data)
    except Exception:  # noqa: BLE001
        return b""


def vdhost_stop() -> bool:
    if _VDHOST is None:
        return False
    try:
        return bool(_VDHOST.stop())
    except Exception:  # noqa: BLE001
        return False


def hidden_status() -> str:
    if _HIDDEN is None:
        return "not on Android"
    try:
        return str(_HIDDEN.status())
    except Exception as error:  # noqa: BLE001
        return f"error: {error!r}"


def hidden_ensure() -> bool:
    """请求录屏授权（首次会弹系统窗，用户点一次即可）。"""
    if _HIDDEN is None:
        return False
    try:
        return bool(_HIDDEN.ensure())
    except Exception:  # noqa: BLE001
        return False


def hidden_create(width: int = 1280, height: int = 720, dpi: int = 240) -> int:
    """建隐藏屏并返回 displayId（-1 表示还没授权/创建失败）。"""
    if _HIDDEN is None:
        return -1
    try:
        return int(_HIDDEN.create(int(width), int(height), int(dpi)))
    except Exception:  # noqa: BLE001
        return -1


def hidden_display() -> int:
    if _HIDDEN is None:
        return -1
    try:
        return int(_HIDDEN.displayId())
    except Exception:  # noqa: BLE001
        return -1


def hidden_ready() -> bool:
    if _HIDDEN is None:
        return False
    try:
        return bool(_HIDDEN.ready())
    except Exception:  # noqa: BLE001
        return False


def hidden_screenshot() -> bytes:
    """从隐藏屏的 ImageReader 抓一帧 PNG（MediaProjection 屏不能用 screencap -d）。"""
    if _HIDDEN is None:
        return b""
    try:
        data = _HIDDEN.capturePng()
        if data is None:
            return b""
        return bytes(data)
    except Exception:  # noqa: BLE001
        return b""


def hidden_release() -> bool:
    if _HIDDEN is None:
        return False
    try:
        return bool(_HIDDEN.release())
    except Exception:  # noqa: BLE001
        return False


def wait_ready(timeout_s: float = 30.0) -> bool:
    """等服务连接（用户开无障碍后通常几秒内就绪）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if available():
            return True
        time.sleep(0.5)
    return available()


_STREAM = None
try:
    _STREAM = jclass("com.oas.oasx.ScreenStream")
except Exception:  # noqa: BLE001
    _STREAM = None


def vdstream_running() -> bool:
    if _STREAM is None:
        return False
    try:
        return bool(_STREAM.isRunning())
    except Exception:  # noqa: BLE001
        return False


def vdstream_display() -> int:
    if _STREAM is None:
        return -1
    try:
        return int(_STREAM.displayId())
    except Exception:  # noqa: BLE001
        return -1


def vdstream_start(width: int = 1280, height: int = 720, display_id: int = -1) -> bool:
    """按 MAA-Meow 的 Binder 方案启动 shell 侧虚拟屏并持续取 JPEG 帧。"""
    if _STREAM is None:
        return False
    try:
        return bool(_STREAM.start(int(width), int(height), int(display_id)))
    except Exception:  # noqa: BLE001
        return False


def vdstream_stop() -> bool:
    if _STREAM is None:
        return False
    try:
        return bool(_STREAM.stop())
    except Exception:  # noqa: BLE001
        return False


def vdstream_status() -> str:
    if _STREAM is None:
        return "no ScreenStream"
    try:
        return str(_STREAM.status())
    except Exception as error:  # noqa: BLE001
        return f"error: {error}"


def vdstream_jpeg() -> bytes:
    if _STREAM is None:
        return b""
    try:
        data = _STREAM.latestJpeg()
        return bytes(data) if data else b""
    except Exception:  # noqa: BLE001
        return b""


def vdstream_capture(quality: int = 85) -> bytes:
    """按需经 Binder 抓一帧新鲜画面（任务用，不依赖后台连续轮询）。"""
    if _STREAM is None:
        return b""
    try:
        data = _STREAM.captureNow(int(quality))
        return bytes(data) if data else b""
    except Exception:  # noqa: BLE001
        return b""


def vdstream_monitor_jpeg() -> bytes:
    """监控页取帧并续期 5 秒直播租约（关掉监控页自动停止后台连拍省电）。"""
    if _STREAM is None:
        return b""
    try:
        data = _STREAM.touchMonitor(5000)
        return bytes(data) if data else b""
    except Exception:  # noqa: BLE001
        return vdstream_jpeg()


def vdstream_info() -> dict:
    if _STREAM is None:
        return {"running": False, "display_id": -1, "error": "ScreenStream unavailable"}
    try:
        import json
        return json.loads(str(_STREAM.frameInfo()))
    except Exception as error:
        return {"running": vdstream_running(), "display_id": vdstream_display(), "error": str(error)}
