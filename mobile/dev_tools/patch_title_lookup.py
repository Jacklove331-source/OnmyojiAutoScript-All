"""给 _attach_labels 的标题查找加"标题式"兜底（一次性补丁）。

i18n 里没有 green_mark 这种裸名键，只有 "Green Mark Type" 这类标题式键；
所以按 name → Title Case → "Title Case Type" 依次查。
"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

HELPER = '''def _title_of(mapping: dict, name: str) -> str:
    """\u4ece i18n \u91cc\u627e\u5b57\u6bb5\u4e2d\u6587\u6807\u9898\uff1a\u88f8\u540d -> Title Case -> "Title Case Type"\u3002"""
    if not name:
        return ""
    title_case = " ".join(word.capitalize() for word in name.split("_"))
    for key in (name, title_case, title_case + " Type"):
        found = mapping.get(key)
        if found:
            return found
    return ""


'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if "def _title_of(" not in text:
    anchor = "def _attach_labels(payload: dict) -> dict:"
    if anchor not in text:
        raise SystemExit("anchor _attach_labels not found")
    text = text.replace(anchor, HELPER + anchor, 1)

old = '            title = mapping.get(name) or ""\n'
new = '            title = _title_of(mapping, name)\n'
if old not in text:
    raise SystemExit("title line not found")
text = text.replace(old, new, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log3.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
