"""打通"截一帧→最近帧"闭环。

现状：/vd/screenshot 抓到图只回给前端、不落盘；/frame/latest 又去读一个根本没人写的 frames/ 目录，
所以监控页永远"没有帧"。这里改成：抓到就存到 files/oas/screenshot/，/frame/latest 优先读那里。
"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

SAVE_HELPER = '''def _save_frame(data) -> None:
    """\u628a\u521a\u6293\u5230\u7684 PNG \u5b58\u8fdb screenshot/\uff0c\u6700\u65b0\u7684\u4fdd\u7559 20 \u5f20\u3002"""
    import os
    import time

    try:
        frame_dir = os.path.join(_workspace(), "screenshot")
        os.makedirs(frame_dir, exist_ok=True)
        name = "frame-%d.png" % int(time.time() * 1000)
        with open(os.path.join(frame_dir, name), "wb") as handle:
            handle.write(data)
        files = sorted(
            (os.path.join(frame_dir, item) for item in os.listdir(frame_dir) if item.endswith(".png")),
            key=os.path.getmtime,
            reverse=True,
        )
        for old in files[20:]:
            try:
                os.remove(old)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass


'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")

if "def _save_frame(" not in text:
    anchor = "def _latest_frame() -> dict:"
    if anchor not in text:
        raise SystemExit("_latest_frame anchor missing")
    text = text.replace(anchor, SAVE_HELPER + anchor, 1)

# 1) 保存到 screenshot/ 而不是 frames/
old_dirs = '''        frame_dir = os.path.join(_workspace(), "frames")
        files = sorted(glob.glob(os.path.join(frame_dir, "*.png")),
                       key=os.path.getmtime, reverse=True)'''
new_dirs = '''        frame_dir = os.path.join(_workspace(), "screenshot")
        files = sorted(glob.glob(os.path.join(frame_dir, "*.png")),
                       key=os.path.getmtime, reverse=True)
        if not files:
            frame_dir = os.path.join(_workspace(), "frames")
            files = sorted(glob.glob(os.path.join(frame_dir, "*.png")),
                           key=os.path.getmtime, reverse=True)'''
if old_dirs not in text:
    raise SystemExit("frame dir block missing")
text = text.replace(old_dirs, new_dirs, 1)

# 2) 成功截图时落盘（两处 _send(200, data, "image/png")）
old_send = '''                        data = bridge.hidden_screenshot()
                        if data:
                            self._send(200, data, "image/png")
                            return'''
new_send = '''                        data = bridge.hidden_screenshot()
                        if data:
                            _save_frame(data)
                            self._send(200, data, "image/png")
                            return'''
if old_send in text:
    text = text.replace(old_send, new_send, 1)

old_send2 = '''            if not data:
                self._send_json(503, {"ok": False, "error": f"empty frame (display={display})"})
                return
            self._send(200, data, "image/png")
            return'''
new_send2 = '''            if not data:
                self._send_json(503, {"ok": False, "error": f"empty frame (display={display})"})
                return
            _save_frame(data)
            self._send(200, data, "image/png")
            return'''
if old_send2 in text:
    text = text.replace(old_send2, new_send2, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log17.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
