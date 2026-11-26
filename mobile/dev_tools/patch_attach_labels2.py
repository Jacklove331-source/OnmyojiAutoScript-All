"""把 _attach_labels 换成适配 `{group: [field...]}` 结构的实现（一次性补丁）。

OAS 的 script_task() 返回：
    {"分组名": [{"name","title","description","default","value","type","enumEnum"}, ...]}
所以中文/枚举措要挂到每个 field dict 上，而不是挂到分组上。
"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

NEW = '''def _attach_labels(payload: dict) -> dict:
    """\u7ed9 OAS \u53c2\u6570\u6811\u8865\u4e2d\u6587\u6807\u9898\u3001\u8bf4\u660e\u548c\u679a\u4e3e\u4e2d\u6587\u6807\u7b7e\u3002

    script_task() \u7684\u7ed3\u6784\u662f {"\u5206\u7ec4": [{"name","title","enumEnum",...}, ...]}\uff0c
    \u4e2d\u6587\u5168\u90e8\u6765\u81ea i18n\uff08<name> \u548c <name>_help\uff09\uff1b\u53ea\u52a0\u5b57\u6bb5\u3001\u4e0d\u6539\u539f\u6709\u952e\u3002
    """
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
            title = mapping.get(name) or ""
            help_text = mapping.get(name + "_help") or ""
            if title:
                field["title_zh"] = title
            if not help_text:
                continue
            field["help_text"] = help_text
            if "[" not in help_text or "]" not in help_text:
                continue
            inner = help_text[help_text.find("[") + 1: help_text.rfind("]")]
            labels = [part.strip() for part in inner.split(",") if part.strip()]
            if not labels:
                continue
            field["option_labels"] = labels
            enum_values = field.get("enumEnum") or []
            if enum_values and len(enum_values) == len(labels):
                field["options"] = [
                    {"key": key, "label": label}
                    for key, label in zip(enum_values, labels)
                ]
    return payload
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
pattern = re.compile(r"def _attach_labels\(payload: dict\) -> dict:.*?\n    return payload\n", re.S)
if not pattern.search(text):
    raise SystemExit("anchor _attach_labels not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(pattern.sub(NEW, text, count=1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log2.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
