"""枚举候选项兜底：OAS 的 schema 没填 enumEnum，改用构建期导出的 enum_values 表（一次性补丁）。

表结构：{当前值: [允许值, ...]}；允许值本身是中文（肆层/悲鸣…）时直接当标签用。
"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

OLD = '''            field["option_labels"] = labels
            enum_values = field.get("enumEnum") or []
            if enum_values and len(enum_values) == len(labels):
                field["options"] = [
                    {"key": key, "label": label}
                    for key, label in zip(enum_values, labels)
                ]
'''

NEW = '''            enum_values = field.get("enumEnum") or []
            if not enum_values and not isinstance(field.get("value"), list):
                # schema 里没写 $ref，退回构建期导出的枚举表（键是当前值，值是候选值）
                enum_values = table.get(str(field.get("value"))) or []
                if enum_values:
                    field["option_labels"] = list(enum_values)
                    field["options"] = [{"key": v, "label": v} for v in enum_values]
                    continue
            field["option_labels"] = labels
            if enum_values and len(enum_values) == len(labels):
                field["options"] = [
                    {"key": key, "label": label}
                    for key, label in zip(enum_values, labels)
                ]
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if OLD not in text:
    raise SystemExit("enum block not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(OLD, NEW, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log4.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
