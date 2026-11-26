"""修 service_running()：release 包里 Python 侧拿不到 service 实例时，改问 Kotlin 静态方法。

现象：dumpsys 显示 OasForegroundService 已 isForeground=true，但 /health 里 background_service=false，
因为 `_FOREGROUND_SERVICE` 这个静态句柄只在 Java 主动注入时才非空。Companion 里有 `isRunning()` 静态方法，
可以从 Python 直接调用，作为兜底。
"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\bridge.py"

NEW = '''def service_running() -> bool:
    """\u524d\u53f0\u670d\u52a1\uff08\u627f\u8f7d Python \u4e0e\u4efb\u52a1\u5faa\u73af\uff09\u662f\u5426\u5728\u8dd1\u3002

    \u4f18\u5148\u7528\u6ce8\u5165\u7684\u5b9e\u4f8b\u53e5\u67c4\uff1b\u62ff\u4e0d\u5230\u65f6\u95ee Kotlin \u7684\u9759\u6001\u65b9\u6cd5\uff08companion isRunning\uff09\uff0c
    \u5426\u5219 release \u5305\u91cc\u4f1a\u8bef\u62a5 false\uff08\u670d\u52a1\u5176\u5b9e\u5728\u8dd1\uff09\u3002
    """
    if _FOREGROUND_SERVICE is not None:
        try:
            return bool(_FOREGROUND_SERVICE.isRunning())
        except Exception:  # noqa: BLE001
            pass
    try:
        from java import jclass

        return bool(jclass("com.oas.oasx.OasForegroundService").isRunning())
    except Exception:  # noqa: BLE001
        return False
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
pattern = re.compile(r'def service_running\(\) -> bool:.*?\n        return False\n\n\n', re.S)
if not pattern.search(text):
    raise SystemExit("anchor service_running not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(pattern.sub(NEW + "\n\n", text, count=1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log15.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-300:]))
