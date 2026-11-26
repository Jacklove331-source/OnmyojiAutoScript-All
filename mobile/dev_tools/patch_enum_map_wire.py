"""把构建期枚举表接进 _attach_labels（一次性补丁）。"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

NEW = '''_FIELD_ENUMS = {}


def _field_enums() -> dict:
    """\u6784\u5efa\u671f\u5bfc\u51fa\u7684\u679a\u4e3e\u8868\uff08\u8fd0\u884c\u65f6\u4e0d\u80fd\u53cd\u5c04 pydantic\uff0c\u53ea\u80fd\u67e5\u8868\uff09\u3002"""
    if _FIELD_ENUMS:
        return _FIELD_ENUMS
    try:
        from .enum_map import FIELD_ENUMS

        _FIELD_ENUMS.update(FIELD_ENUMS)
    except Exception:  # noqa: BLE001
        pass
    return _FIELD_ENUMS


def _attach_labels(payload: dict, script_name: str = "", task_name: str = "") -> dict:
    """\u7ed9\u53c2\u6570\u6811\u8865\u4e2d\u6587\u6807\u9898\u3001\u8bf4\u660e\u548c\u679a\u4e3e\u5019\u9009\uff08\u53ea\u52a0\u5b57\u6bb5\uff0c\u4e0d\u6539\u539f\u952e\uff09\u3002"""
    if not isinstance(payload, dict):
        return payload
    mapping = _i18n_map("zh_CN")
    enums = _field_enums()
    for group_name, fields in payload.items():
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
            if isinstance(field.get("value"), list):
                continue
            candidates = enums.get((group_name, name)) or enums.get(name) or []
            if not candidates:
                continue
            field["type"] = "enum"
            field["option_values"] = list(candidates)
            field["options"] = [{"key": v, "label": v} for v in candidates]
    return payload
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
pattern = re.compile(r"def _attach_labels\(payload: dict, script_name: str = \"\", task_name: str = \"\"\) -> dict:.*?\n    return payload\n", re.S)
if not pattern.search(text):
    raise SystemExit("anchor _attach_labels not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(pattern.sub(NEW, text, count=1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log12.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-300:]))
