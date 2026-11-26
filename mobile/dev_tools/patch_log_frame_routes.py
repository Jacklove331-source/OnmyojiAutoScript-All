"""补上缺的两个只读接口：/log/tail 与 /frame/latest。

11-06 快照里这两个路由不存在（返回 404），所以运行页永远"暂无日志"、监控页永远"没有帧"。
这里按最小实现补齐：只读文件，不产生副作用。
"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

NEW = '''        if route.path == "/log/tail":
            payload = _log_tail(int(query.get("lines") or 60))
            self._send_json(200, payload)
            return
        if route.path == "/frame/latest":
            payload = _latest_frame()
            self._send_json(200, payload)
            return
'''

HELPERS = '''def _log_tail(lines: int = 60) -> dict:
    """\u8bfb\u6700\u8fd1\u4e00\u4e2a\u65e5\u5fd7\u6587\u4ef6\u7684\u672b\u5c3e\u82e5\u5e72\u884c\uff1b\u6ca1\u6709\u65e5\u5fd7\u5c31\u5982\u5b9e\u56de\u7a7a\u3002"""
    import glob
    import os

    try:
        log_dir = os.path.join(_workspace(), "log")
        files = sorted(glob.glob(os.path.join(log_dir, "**", "*.txt"), recursive=True),
                       key=os.path.getmtime, reverse=True)
        if not files:
            files = sorted(glob.glob(os.path.join(log_dir, "**", "*.log"), recursive=True),
                           key=os.path.getmtime, reverse=True)
        if not files:
            return {"ok": True, "file": None, "lines": []}
        newest = files[0]
        with open(newest, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.readlines()
        return {
            "ok": True,
            "file": os.path.basename(newest),
            "mtime": int(os.path.getmtime(newest)),
            "lines": [line.rstrip("\\n") for line in content[-max(1, lines):]],
        }
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": repr(error), "lines": []}


def _latest_frame() -> dict:
    """\u8fd4\u56de\u6700\u8fd1\u4e00\u5e27 PNG\uff08base64\uff09\u53ca\u5176\u65f6\u95f4\uff1b\u6ca1\u6709\u5c31\u5982\u5b9e\u544a\u8bc9\u524d\u7aef\u3002"""
    import base64
    import glob
    import os

    try:
        frame_dir = os.path.join(_workspace(), "frames")
        files = sorted(glob.glob(os.path.join(frame_dir, "*.png")),
                       key=os.path.getmtime, reverse=True)
        if not files:
            return {"ok": True, "has_frame": False, "image": None}
        newest = files[0]
        with open(newest, "rb") as handle:
            data = base64.b64encode(handle.read()).decode("ascii")
        return {
            "ok": True,
            "has_frame": True,
            "mtime": int(os.path.getmtime(newest)),
            "image": data,
        }
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": repr(error), "has_frame": False}


'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if 'route.path == "/log/tail"' in text:
    raise SystemExit("already patched")
anchor = '        if route.path == "/health":\n'
if anchor not in text:
    raise SystemExit("health route anchor not found")
text = text.replace(anchor, NEW + anchor, 1)

helper_anchor = "def _health_payload() -> dict:\n"
if helper_anchor not in text:
    raise SystemExit("health payload anchor not found")
text = text.replace(helper_anchor, HELPERS + helper_anchor, 1)

if "\nimport io\n" not in text:
    text = text.replace("\nimport base64\n", "\nimport base64\n", 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log16.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
