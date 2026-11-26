"""构建期导出枚举取值表。

为什么要有这个脚本：Chaquopy 打包后只剩 `*.imy`（编译产物），运行时的 App 里
读不到 `tasks/**/config_*.py` 源码，所以「按当前值反查枚举类」在手机上永远为空。
枚举取值必须在**构建期**从源码抽出来，生成一个可被 import 的 Python 模块随包发布。

用法（在 oasx_mobile 根目录）：
    <oas 的 python> dev_tools/gen_enum_values.py

产物：android/app/src/main/python/oas_mobile/enum_values.py
      内容形如 ENUM_VALUES = {"green_left1": ["green_left1", …], …}
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_ROOT = os.path.join(ROOT, "android", "app", "src", "main", "python")
TARGET = os.path.join(PY_ROOT, "oas_mobile", "enum_values.py")
JSON_TARGET = os.path.join(ROOT, "assets", "enum_values.json")

CLASS_PATTERN = re.compile(
    r"class\s+(\w+)\(([^)]*Enum[^)]*)\):(.*?)(?=\nclass |\Z)", re.S)
VALUE_PATTERN = re.compile(r"=\s*'([^']+)'")


def scan() -> dict:
    table: dict = {}
    for base in ("tasks", "module"):
        base_dir = os.path.join(PY_ROOT, base)
        if not os.path.isdir(base_dir):
            continue
        for dirpath, _dirnames, filenames in os.walk(base_dir):
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as handle:
                        text = handle.read()
                except OSError:
                    continue
                if "Enum" not in text:
                    continue
                for match in CLASS_PATTERN.finditer(text):
                    values = VALUE_PATTERN.findall(match.group(3))
                    if len(values) < 2:
                        continue
                    for value in values:
                        table.setdefault(value, values)
    return table


def main() -> int:
    table = scan()
    if not table:
        print("没扫到任何枚举类，检查 PY_ROOT 是否正确：", PY_ROOT)
        return 1
    header = (
        '"""自动生成，别手改。来源见 dev_tools/gen_enum_values.py。"""\n\n'
        "# value -> 该枚举的全部取值（顺序与 OAS 源码里的声明一致）\n"
        "ENUM_VALUES = ")
    with open(TARGET, "w", encoding="utf-8") as handle:
        handle.write(header)
        handle.write(json.dumps(table, ensure_ascii=False, indent=0, sort_keys=True))
        handle.write("\n")
    # 同一份表再导出一份 Dart 资源：Chaquopy 的打包行为不保证 .py 以外的文件，Dart asset 一定在 APK 里
    os.makedirs(os.path.dirname(JSON_TARGET), exist_ok=True)
    with open(JSON_TARGET, "w", encoding="utf-8") as handle:
        json.dump(table, handle, ensure_ascii=False, sort_keys=True)
    print(f"写出 {JSON_TARGET}")
    print(f"写出 {TARGET}：{len(table)} 个取值，覆盖 "
          f"{len({tuple(v) for v in table.values()})} 个枚举类")
    return 0


if __name__ == "__main__":
    sys.exit(main())
