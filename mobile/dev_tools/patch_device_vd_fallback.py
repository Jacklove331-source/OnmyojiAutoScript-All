"""没有虚拟屏时，Device.screenshot 必须回落无障碍截图，而不是抛异常。

真机证据：
    RuntimeError: virtual display unavailable
    device.py:188 in screenshot  ← chess 任务第一帧就死在这里
手机上 Shizuku 未启用、虚拟屏 -1，但无障碍截图（/screenshot.png 1.18MB）是好的。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\module\device\device.py"

OLD = '''        display = self._ensure_display()
        if display < 0:
            raise RuntimeError("virtual display unavailable")'''

NEW = '''        display = self._ensure_display()
        if display < 0:
            # 没有虚拟屏（手机上未启用 Shizuku）→ 用无障碍直接截一帧，别抛异常把任务打死
            png = bridge.capture_png()
            if not png:
                raise RuntimeError("no frame: virtual display unavailable and capture failed")
            frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise RuntimeError("decode fallback frame failed")
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            with self._lock:
                self._image = frame
                self._image_frame_id += 1
            return frame'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if OLD not in text:
    raise SystemExit("anchor missing: display fallback")
text = text.replace(OLD, NEW, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log38.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-500:]))
