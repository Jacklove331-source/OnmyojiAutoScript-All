"""修正设备截图顺序：root 只当兜底，不能每帧都吃 su 的开销。

实测：把 root 放到 `Device.screenshot()` 最前面后，Chess 任务线程两次 12 秒采样都停在
`bridge.capture_via_su()`，等于每帧都在跑一次 `su -c screencap`，任务看起来"卡住"。

新顺序：
1. vdhost（宿主屏）优先 —— 本来就是为连续取帧设计的；
2. 隐藏屏/MediaProjection 分支保持原样；
3. 走到虚拟屏兜底前，如果还没拿到帧且 root 开关打开，才用 root 取一帧；
4. 每次都记一行耗时日志，便于判断到底慢在哪。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\module\device\device.py"

OLD_HEAD = '''        # root 截图开关打开时优先 su screencap：MuMu 上无障碍/vdhost 都是黑帧
        if bridge.root_capture_enabled():
            try:
                data = bridge.capture_via_su()
                if data:
                    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        with self._lock:
                            self._image = frame
                            self._image_frame_id += 1
                        return frame
            except Exception as error:  # noqa: BLE001
                logger.debug(f'[mobile] root capture failed: {error!r}')

        # 首选无头宿主屏：帧由 shell 宿主进程提供，App 在后台也照样有画面'''

NEW_HEAD = '''        # 首选无头宿主屏：帧由 shell 宿主进程提供，App 在后台也照样有画面'''

OLD_TAIL = '''        display = self._ensure_display()
        if display < 0:
            raise RuntimeError("virtual display unavailable")'''

NEW_TAIL = '''        # vdhost / 隐藏屏都没帧时才用 root 兜底：su screencap 一帧要几百毫秒起，
        # 绝不能放在每帧必经路径上（否则任务线程会整体卡在 su 上）
        if bridge.root_capture_enabled():
            try:
                started = time.perf_counter()
                data = bridge.capture_via_su()
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                logger.info(f'[mobile] root capture {elapsed_ms:.0f}ms bytes={len(data or b"")}')
                if data:
                    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        with self._lock:
                            self._image = frame
                            self._image_frame_id += 1
                        return frame
            except Exception as error:  # noqa: BLE001
                logger.debug(f'[mobile] root capture failed: {error!r}')

        display = self._ensure_display()
        if display < 0:
            raise RuntimeError("virtual display unavailable")'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
for old, new, label in ((OLD_HEAD, NEW_HEAD, "head"), (OLD_TAIL, NEW_TAIL, "tail")):
    if old not in text:
        raise SystemExit(f"anchor missing: {label}")
    text = text.replace(old, new, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log32.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
