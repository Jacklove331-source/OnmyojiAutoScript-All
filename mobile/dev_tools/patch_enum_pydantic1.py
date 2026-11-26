import io, re, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"
NEW = '''def _model_fields(obj) -> dict:
    """\u517c\u5bb9 pydantic v1/v2\uff1av2 \u53eb model_fields\uff0cv1 \u53eb __fields__\u3002"""
    if obj is None:
        return {}
    fields = getattr(obj, "model_fields", None)
    if fields:
        return fields
    return getattr(obj, "__fields__", None) or {}


def _field_annotation(field):
    for attr in ("annotation", "outer_type_", "type_"):
        value = getattr(field, attr, None)
        if value is not None:
            return value
    return None


def _enum_index(task_model) -> dict:
    """\u628a\u4efb\u52a1\u6a21\u578b\u91cc\u6240\u6709\u679a\u4e3e\u5b57\u6bb5\u62b9\u5e73\u6210 {(\u5206\u7ec4, \u5b57\u6bb5): \u5019\u9009}\uff0c\u5e76\u6309\u5b57\u6bb5\u540d\u518d\u7d22\u5f15\u4e00\u4efd\u3002"""
    result = {}
    try:
        from enum import Enum

        for group_name, group_field in _model_fields(task_model).items():
            group_model = _field_annotation(group_field)
            fields = _model_fields(group_model)
            if not fields:
                continue
            for field_name, field in fields.items():
                annotation = _field_annotation(field)
                if not (isinstance(annotation, type) and issubclass(annotation, Enum)):
                    continue
                values = [str(member.value) for member in annotation]
                result[(group_name, field_name)] = values
                result.setdefault(field_name, values)
    except Exception:  # noqa: BLE001
        return {}
    return result
'''
text = io.open(PATH, "rb").read().decode("utf-8-sig")
pattern = re.compile(r"def _enum_index\(task_model\) -> dict:.*?\n    return result\n", re.S)
if not pattern.search(text):
    raise SystemExit("anchor _enum_index not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(pattern.sub(NEW, text, count=1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log9.txt", "w", encoding="utf-8").write("rc=%d\n%s" % (p.returncode, p.stderr[-300:]))