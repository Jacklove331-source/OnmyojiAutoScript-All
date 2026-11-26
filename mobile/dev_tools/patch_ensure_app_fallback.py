"""没有 Shizuku/虚拟屏时，display_ensure_app 不能卡死：直接用 monkey 拉起游戏。

真机栈证据：login.ensure_logged_in 第 149 行调用 bridge.display_ensure_app(package)，
在 _SHIZUKU 为 None 时会走 Shizuku binder 分支，永远不返回，日志停在
"login: starting via OCR"，看起来像任务死掉。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\bridge.py"

OLD = "def display_ensure_app(package: str, width: int = 1280, height: int = 720) -> int:"

NEW = '''def display_ensure_app(package: str, width: int = 1280, height: int = 720) -> int:
    # 没有 Shizuku 就没有虚拟屏：退化成"直接拉起游戏"，返回 -1 让上层走普通截图/点击
    if _SHIZUKU is None:
        try:
            from oas_mobile.shizuku_exec import exec_shell

            exec_shell("monkey -p %s 1" % package)
        except Exception:  # noqa: BLE001
            pass
        return -1'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if OLD not in text:
    raise SystemExit("anchor missing: display_ensure_app")
if "没有 Shizuku 就没有虚拟屏" in text:
    raise SystemExit("already patched")
text = text.replace(OLD, NEW, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log35.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
