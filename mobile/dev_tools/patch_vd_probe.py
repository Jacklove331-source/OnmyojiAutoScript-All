"""加 /vd/probe：直接跑一条 Shizuku shell 命令并把 code/output 带回来。

用途：区分「Shizuku 授权了但 exec 本身不可用」和「exec 可用、只是宿主进程起不来」。
/vd/host 只返回 running=false displayId=-1 error=（异常被吞），没法判断。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

ANCHOR = '        if route.path == "/vd/screenshot":'

NEW = '''        if route.path == "/vd/probe":
            payload = {"ok": False, "code": None, "output": "", "error": ""}
            shizuku = getattr(bridge, "_SHIZUKU", None)
            if shizuku is None:
                payload["error"] = "shizuku object missing"
            else:
                try:
                    result = shizuku.exec("id", 5000)
                    payload["ok"] = True
                    payload["code"] = int(getattr(result, "code", -999))
                    payload["output"] = str(getattr(result, "output", ""))[:400]
                except Exception as error:  # noqa: BLE001
                    payload["error"] = f"{type(error).__name__}: {error}"
            self._send_json(200, payload)
            return
        if route.path == "/vd/screenshot":'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if ANCHOR not in text:
    raise SystemExit("anchor missing: /vd/screenshot")
if '"/vd/probe"' in text:
    raise SystemExit("already patched")
text = text.replace(ANCHOR, NEW, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log40.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
