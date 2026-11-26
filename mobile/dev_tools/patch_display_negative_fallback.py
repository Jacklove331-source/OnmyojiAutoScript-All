"""虚拟屏不存在（display_id < 0）时，不要走 display 截图，直接回落到普通截图。

真机栈证据：login._frame → bridge.capture_display_png → display_screenshot → _capture_to_file
卡在这里（当时 virtual_display=-1，等于拿 -1 去 screencap -d，永不返回），
表现就是日志停在 "login: starting via OCR" 再也不动。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\bridge.py"

OLD = '''def display_screenshot(display_id: int) -> bytes:
    if _SHIZUKU is None:
        raise RuntimeError("shizuku not available")'''

NEW = '''def display_screenshot(display_id: int) -> bytes:
    # 没有真正的虚拟屏（-1）时，screencap -d -1 会永久阻塞；直接用普通截图通道
    if int(display_id) < 0:
        return capture_png()
    if _SHIZUKU is None:
        # 没有 Shizuku 也要能干活：回落无障碍/root 截图
        return capture_png()'''

OLD2 = '''def capture_display_png(display_id: int) -> bytes:
    display_id = int(display_id)'''

NEW2 = '''def capture_display_png(display_id: int) -> bytes:
    display_id = int(display_id)
    if display_id < 0:
        return capture_png()'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
for old, new, label in ((OLD, NEW, "display_screenshot"), (OLD2, NEW2, "capture_display_png")):
    if old not in text:
        raise SystemExit(f"anchor missing: {label}")
    text = text.replace(old, new, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log33.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
