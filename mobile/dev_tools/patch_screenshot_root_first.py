"""让 OAS 的截图在 root 开关打开时优先走 su screencap。

背景：MuMu 上无障碍截图是黑图，vdhost/MediaProjection 也拿不到真画面；
只有 `su -c screencap -p` 能拿到真帧（实测 1.1MB 的真实屏幕）。
用户明确"先用 root 做"，所以开 root 时截图就该走 root，而不是被 vdhost 挡住。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\module\device\device.py"

ANCHOR = '''    def screenshot(self):
        """从虚拟屏截一帧，转成 RGB ndarray 并递增帧号。"""
        # 首选无头宿主屏：帧由 shell 宿主进程提供，App 在后台也照样有画面
        try:'''

NEW = '''    def screenshot(self):
        """从虚拟屏截一帧，转成 RGB ndarray 并递增帧号。"""
        # root 截图开关打开时优先 su screencap：MuMu 上无障碍/vdhost 都是黑帧
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

        # 首选无头宿主屏：帧由 shell 宿主进程提供，App 在后台也照样有画面
        try:'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if ANCHOR not in text:
    raise SystemExit("anchor not found in device.py")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(ANCHOR, NEW, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log29.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
