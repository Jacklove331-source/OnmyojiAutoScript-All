"""不要把黑帧当成"有画面"。

实测：MuMu 上 capture_png() 能返回 720x1280 的 PNG，但整张全黑（6.3KB）。
黑帧冒充实时画面就是虚报，所以这里按体积做个廉价判据：太小的 PNG 当空帧处理并带原因。
"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

OLD = '''            if not data:
                return {"ok": True, "has_frame": False, "image": None}
            _save_frame(data)
            return {
                "ok": True,
                "has_frame": True,
                "mtime": 0,
                "image": base64.b64encode(data).decode("ascii"),
            }'''

NEW = '''            if not data or len(data) < 12000:
                # 720x1280 \u7684\u771f\u5b9e\u753b\u9762\u538b\u7f29\u540e\u8fdc\u8d85 12KB\uff1b\u5c0f\u4e8e\u8be5\u503c\u57fa\u672c\u662f\u5168\u9ed1\u5e27
                return {"ok": True, "has_frame": False,
                        "reason": "\u6293\u5230\u7684\u5e27\u662f\u5168\u9ed1\uff08\u6a21\u62df\u5668\u65e0\u969c\u788d\u622a\u5c4f\u9650\u5236\uff09",
                        "image": None}
            _save_frame(data)
            return {
                "ok": True,
                "has_frame": True,
                "mtime": 0,
                "image": base64.b64encode(data).decode("ascii"),
            }'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if OLD not in text:
    raise SystemExit("latest-frame capture branch not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(OLD, NEW, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log20.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-300:]))
