"""枚举候选改为按 pydantic 注解取（一次性补丁）。

背景：这个版本的 OAS 在 merge_value 里把 `allOf → enumEnum` 的逻辑注释掉了，
所以 script_task() 永远不带枚举候选。桌面 GUI 是直接读 Enum 类的，这里照做：
从 task 模型的字段注解里找到子模型，再找字段注解是不是 Enum 子类。
"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

NEW = '''def _enum_values_from_annotation(task_model, group_name: str, arg_name: str) -> list:
    """\u4ece pydantic \u6ce8\u89e3\u91cc\u53d6\u679a\u4e3e\u5019\u9009\uff08OAS \u81ea\u5df1\u4e0d\u586b enumEnum\uff09\u3002"""
    try:
        from enum import Enum

        group_field = getattr(task_model, "model_fields", {}).get(group_name)
        group_model = getattr(group_field, "annotation", None)
        fields = getattr(group_model, "model_fields", None)
        if not fields:
            return []
        field = fields.get(arg_name)
        annotation = getattr(field, "annotation", None)
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return [str(member.value) for member in annotation]
    except Exception:  # noqa: BLE001
        return []
    return []


def _attach_labels(payload: dict, script_name: str = "", task_name: str = "") -> dict:
    """\u7ed9 OAS \u53c2\u6570\u6811\u8865\u4e2d\u6587\u6807\u9898\u3001\u8bf4\u660e\u548c\u679a\u4e3e\u4e2d\u6587\u6807\u7b7e\u3002

    script_task() \u7ed3\u6784\uff1a{"\u5206\u7ec4": [{"name","title","value","type",...}, ...]}\u3002
    \u4e2d\u6587\u6765\u81ea i18n\uff08<name> / <name>_help\uff09\uff0c\u679a\u4e3e\u5019\u9009\u6765\u81ea pydantic \u6ce8\u89e3\u3002\u53ea\u52a0\u5b57\u6bb5\u4e0d\u6539\u539f\u952e\u3002
    """
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
            enum_values = []
            if task_model is not None:
                enum_values = _enum_values_from_annotation(task_model, group_name, name)
            if not enum_values:
                continue
            field["type"] = "enum"
            field["option_values"] = list(enum_values)
            field["options"] = [{"key": v, "label": v} for v in enum_values]
    return payload
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
pattern = re.compile(r"def _attach_labels\(payload: dict\) -> dict:.*?\n    return payload\n", re.S)
if not pattern.search(text):
    raise SystemExit("anchor _attach_labels not found")
text = pattern.sub(NEW, text, count=1)

old_call = "payload = _attach_labels(payload)"
new_call = "payload = _attach_labels(payload, script_name, task_name)"
if old_call not in text:
    raise SystemExit("call site not found")
text = text.replace(old_call, new_call, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log6.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
