"""构建期导出「字段 → 枚举候选」表。

为什么不在运行时反射：Chaquopy 把源码编成 .imy 后，pydantic 模型的注解信息在设备上取不到，
运行时 `model_fields` 恒为空（实测 index=0）。所以改成构建前在桌面端把枚举摊平，
写成 `oas_mobile/enum_map.py`（和 i18n_zh.py 同一套路），运行时只查字典。

用法：
    python dev_tools/gen_enum_map.py
"""

import io
import os
import sys

OAS_ROOT = r"D:\3D\YYS2\OnmyojiAutoScript-easy-install-1"
TARGET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "android", "app", "src", "main", "python", "oas_mobile", "enum_map.py",
)


def main() -> int:
    sys.path.insert(0, OAS_ROOT)
    os.chdir(OAS_ROOT)
    from enum import Enum

    from pydantic import BaseModel

    table = {}
    tasks_dir = os.path.join(OAS_ROOT, "tasks")
    names = sorted(
        name for name in os.listdir(tasks_dir)
        if os.path.isdir(os.path.join(tasks_dir, name))
        and os.path.exists(os.path.join(tasks_dir, name, "config.py"))
    )
    for name in names:
        try:
            module = __import__("tasks.%s.config" % name, fromlist=["config"])
        except Exception:  # noqa: BLE001
            continue
        for attr_name in dir(module):
            obj = getattr(module, attr_name, None)
            if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
                continue
            group = attr_name
            for field_name, field in getattr(obj, "model_fields", {}).items():
                annotation = getattr(field, "annotation", None)
                if isinstance(annotation, type) and issubclass(annotation, Enum):
                    values = [str(member.value) for member in annotation]
                    table[(group, field_name)] = values
                    table.setdefault(field_name, values)

    lines = [
        "# 由 dev_tools/gen_enum_map.py 生成，不要手改。",
        "# 键是 (分组名, 字段名) 或字段名；值是 enum 的候选值。",
        "FIELD_ENUMS = {",
    ]
    for key in sorted(table, key=repr):
        lines.append("    %r: %r," % (key, table[key]))
    lines.append("}")
    io.open(TARGET, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print("写出 %s：%d 条" % (TARGET, len(table)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
