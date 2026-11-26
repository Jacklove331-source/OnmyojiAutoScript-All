"""把 _attach_labels 换成适配扁平结构的实现（一次性补丁，跑完即可弃）。"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

NEW = '''def _attach_labels(payload: dict) -> dict:
    """\u7ed9\u53c2\u6570\u6811\u8865\u4e2d\u6587\u6807\u9898/\u8bf4\u660e/\u679a\u4e3e\u63aa\u3002

    OAS \u7684 script_task() \u8fd4\u56de\u6241\u5e73\u7ed3\u6784 `{\u53c2\u6570\u540d: \u503c}`\uff08\u591a\u9009\u662f list\uff09\uff0c
    \u6240\u4ee5\u989d\u5916\u6302\u4e00\u4e2a `_meta` \u5e73\u884c\u5b57\u5178\uff1b\u524d\u7aef\u6709\u5c31\u7528\uff0c\u6ca1\u6709\u5c31\u9000\u56de\u539f\u6837\u3002
    """
    if not isinstance(payload, dict):
        return payload
    mapping = _i18n_map("zh_CN")
    table = _enum_table()
    meta = {}
    for name, value in payload.items():
        if name.startswith("_"):
            continue
        item = {}
        title = mapping.get(name) or ""
        help_text = mapping.get(name + "_help") or ""
        if title:
            item["title"] = title
        if help_text:
            item["help_text"] = help_text
        if isinstance(value, bool):
            item["type"] = "boolean"
        elif isinstance(value, (int, float)):
            item["type"] = "number"
        elif isinstance(value, list):
            item["type"] = "list"
        else:
            item["type"] = "string"
        labels = []
        if "[" in help_text and "]" in help_text:
            inner = help_text[help_text.find("[") + 1: help_text.rfind("]")]
            labels = [part.strip() for part in inner.split(",") if part.strip()]
        values = [] if isinstance(value, list) else (table.get(str(value)) or [])
        if labels:
            item["type"] = "enum"
            item["option_labels"] = labels
            if values and len(labels) == len(values):
                item["option_values"] = list(values)
                item["options"] = [
                    {"key": v, "label": l} for v, l in zip(values, labels)
                ]
        if item:
            meta[name] = item
    if meta:
        payload["_meta"] = meta
    return payload
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
pattern = re.compile(r"def _attach_labels\(payload: dict\) -> dict:.*?\n    return payload\n", re.S)
if not pattern.search(text):
    raise SystemExit("anchor _attach_labels not found")
text = pattern.sub(NEW, text, count=1)
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
