"""构建期把 OAS 的中文翻译表导成 Python 模块。

为什么不是运行时读 XML：Chaquopy 只保证 `.py` 被打进 APK，`module/config/i18n/*.xml`
在设备上读不到（实测解析路径不存在 → 标题全变英文键名）。所以这里在构建前把
`zh_CN.xml` 转成 `oas_mobile/i18n_zh.py` 的 `MESSAGES` 字典。

用法：
    python dev_tools/gen_i18n.py
"""

import io
import os
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XML_PATH = os.path.join(ROOT, "android", "app", "src", "main", "python",
                        "module", "config", "i18n", "zh_CN.xml")
TARGET = os.path.join(ROOT, "android", "app", "src", "main", "python",
                      "oas_mobile", "i18n_zh.py")


def build_table() -> dict:
    table = {}
    # Qt .ts 里 message 嵌在 <context> 下，必须用 iter 递归找
    for message in ET.parse(XML_PATH).getroot().iter("message"):
        source = (message.findtext("source") or "").strip()
        translation = (message.findtext("translation") or "").strip()
        if source and translation and source != translation:
            table[source] = translation
    return table


def main() -> int:
    if not os.path.exists(XML_PATH):
        print("找不到 i18n XML：%s" % XML_PATH)
        return 1
    table = build_table()
    header = (
        "# 由 dev_tools/gen_i18n.py 生成，不要手改。\n"
        "# 键是 OAS 的 source（参数名），值是中文翻译。\n\n"
        "MESSAGES = "
    )
    with io.open(TARGET, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(header)
        handle.write(io.StringIO(str({})).read() if False else "{\n")
        for key in sorted(table):
            handle.write("    %r: %r,\n" % (key, table[key]))
        handle.write("}\n")
    print("写出 %s：%d 条" % (TARGET, len(table)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
