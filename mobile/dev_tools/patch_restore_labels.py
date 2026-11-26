"""恢复成已知可用的 _attach_labels（只补中文标题/说明），并去掉诊断日志。"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

SAFE = '''def _attach_labels(payload: dict, script_name: str = "", task_name: str = "") -> dict:
    """\u7ed9\u53c2\u6570\u6811\u8865\u4e2d\u6587\u6807\u9898\u4e0e\u8bf4\u660e\uff08\u53ea\u52a0\u5b57\u6bb5\uff0c\u4e0d\u6539\u539f\u952e\uff09\u3002\u679a\u4e3e\u5019\u9009\u5f85\u5b9a\u3002"""
    if not isinstance(payload, dict):
        return payload
    mapping = _i18n_map("zh_CN")
    for fields in payload.values():
        if not isinstance(fields, list):
            continue
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = field.get("name") or ""
            if not name:
                continue
            title = _title_of(mapping, name)
            if title:
                field["title_zh"] = title
            help_text = mapping.get(name + "_help") or ""
            if help_text:
                field["help_text"] = help_text
    return payload
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
pattern = re.compile(r"def _model_fields\(obj\).*?\n    return payload\n", re.S)
if not pattern.search(text):
    raise SystemExit("broad block not found")
text = pattern.sub(SAFE, text, count=1)
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log11.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-300:]))
