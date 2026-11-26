"""修：历史黑帧会让 /frame/latest 永远说"没帧"，连新抓的机会都不给。

把"文件太小=黑帧"从"直接返回空"改成"跳过这些文件"，全被跳过时走现抓分支。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

OLD = '''        newest = files[0]
        raw = open(newest, "rb").read()
        if len(raw) < 12000:  # 720x1280 \u5168\u9ed1\u5e27\u538b\u7f29\u540e\u4e5f\u5c31\u8fd9\u4e48\u5c0f
            return {"ok": True, "has_frame": False,
                    "reason": "\u5386\u53f2\u5e27\u90fd\u662f\u5168\u9ed1\uff08\u6a21\u62df\u5668\u65e0\u969c\u788d\u622a\u5c4f\u9650\u5236\uff09",
                    "image": None}'''

NEW = '''        newest = ""
        raw = b""
        for candidate in files:
            data = open(candidate, "rb").read()
            if len(data) >= 12000:  # \u592a\u5c0f\u7684\u57fa\u672c\u662f\u5168\u9ed1\u5e27\uff0c\u8df3\u8fc7
                newest, raw = candidate, data
                break
        if not newest:
            return {"ok": True, "has_frame": False,
                    "reason": "\u5386\u53f2\u5e27\u90fd\u662f\u5168\u9ed1\uff08\u6a21\u62df\u5668\u65e0\u969c\u788d\u622a\u5c4f\u9650\u5236\uff09",
                    "image": None}'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if OLD not in text:
    raise SystemExit("black-file guard block not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(OLD, NEW, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log27.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-300:]))
