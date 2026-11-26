"""枚举查找改成"整个任务模型里按字段名建索引"（一次性补丁）。

之前按 (分组名, 字段名) 精确匹配，设备上分组名和模型字段名不一定一致 → 命中 0。
改成先把任务模型所有子模型的 Enum 字段摊平成 {字段名: 候选值}，再按字段名兜底匹配。
"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

NEW = '''def _enum_index(task_model) -> dict:
    """\u628a\u4efb\u52a1\u6a21\u578b\u91cc\u6240\u6709\u679a\u4e3e\u5b57\u6bb5\u62b9\u5e73\u6210 {(\u5206\u7ec4, \u5b57\u6bb5): \u5019\u9009}\uff0c\u5e76\u989d\u5916\u6309\u5b57\u6bb5\u540d\u7d22\u5f15\u3002"""
    result = {}
    try:
        from enum import Enum

        for group_name, group_field in getattr(task_model, "model_fields", {}).items():
            group_model = getattr(group_field, "annotation", None)
            fields = getattr(group_model, "model_fields", None)
            if not fields:
                continue
            for field_name, field in fields.items():
                annotation = getattr(field, "annotation", None)
                if not (isinstance(annotation, type) and issubclass(annotation, Enum)):
                    continue
                values = [str(member.value) for member in annotation]
                result[(group_name, field_name)] = values
                result.setdefault(field_name, values)
    except Exception:  # noqa: BLE001
        return {}
    return result


def _attach_labels(payload: dict, script_name: str = "", task_name: str = "") -> dict:
    """\u7ed9 OAS \u53c2\u6570\u6811\u8865\u4e2d\u6587\u6807\u9898\u3001\u8bf4\u660e\u548c\u679a\u4e3e\u4e2d\u6587\u6807\u7b7e\uff08\u53ea\u52a0\u5b57\u6bb5\u4e0d\u6539\u539f\u952e\uff09\u3002"""
    if not isinstance(payload, dict):
        return payload
    mapping = _i18n_map("zh_CN")
    task_model = None
    if script_name and task_name:
        try:
            from module.config.config import Config
            from module.config.utils import convert_to_underscore

            task_model = getattr(
                Config(script_name).model, convert_to_underscore(task_name), None
            )
        except Exception:  # noqa: BLE001
            task_model = None
    enum_index = _enum_index(task_model) if task_model is not None else {}
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
            enum_values = enum_index.get((group_name, name)) or enum_index.get(name) or []
            if not enum_values:
                continue
            field["type"] = "enum"
            field["option_values"] = list(enum_values)
            field["options"] = [{"key": v, "label": v} for v in enum_values]
    return payload
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
pattern = re.compile(r"def _enum_values_from_annotation\(.*?\n    return payload\n", re.S)
if not pattern.search(text):
    raise SystemExit("anchor block not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(pattern.sub(NEW, text, count=1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log7.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
