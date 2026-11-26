"""补上 _attach_labels 里缺失的 table 变量（一次性补丁）。"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

text = io.open(PATH, "rb").read().decode("utf-8-sig")
start = text.find("def _attach_labels(")
if start < 0:
    raise SystemExit("anchor not found")
end = text.find("\ndef ", start + 1)
body = text[start:end if end > 0 else len(text)]
if "table = _enum_table()" in body:
    print("already patched")
else:
    body_new = body.replace(
        '    mapping = _i18n_map("zh_CN")\n',
        '    mapping = _i18n_map("zh_CN")\n    table = _enum_table()\n',
        1,
    )
    if body_new == body:
        raise SystemExit("mapping line not found")
    text = text[:start] + body_new + text[end if end > 0 else len(text):]
    io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)

p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log5.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-300:]))
