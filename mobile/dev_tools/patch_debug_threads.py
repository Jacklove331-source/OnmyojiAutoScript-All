"""加 /debug/threads：dump 各 Python 线程栈（排查"跑着但没进展"）。"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

ANCHOR = '''        if route.path == "/health":'''

NEW = '''        if route.path == "/debug/threads":
            import sys as _sys
            import threading as _threading
            import traceback as _tb

            names = {t.ident: t.name for t in _threading.enumerate()}
            parts = []
            for tid, frame in _sys._current_frames().items():  # noqa: SLF001
                parts.append("=== %s (%s) ===" % (tid, names.get(tid, "?")))
                parts.extend(_tb.format_stack(frame))
            self._send_json(200, {"ok": True, "threads": "".join(parts)})
            return
        if route.path == "/health":'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if ANCHOR not in text:
    raise SystemExit("anchor not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(ANCHOR, NEW, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log30.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
