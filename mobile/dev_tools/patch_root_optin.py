"""root 截图改为显式开启：默认只用无障碍，调用 su 会弹系统授权框（用户明令零弹窗）。"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\bridge.py"

OLD = '''def capture_png() -> bytes:
    """\u622a\u53d6\u5f53\u524d\u5c4f\u5e55\uff0c\u8fd4\u56de PNG \u5b57\u8282\u3002\u5931\u8d25\u629b RuntimeError \u5e76\u8bf4\u660e\u539f\u56e0\u3002"""
    rooted = capture_via_su()
    if rooted:
        return rooted
    if _SERVICE is None:
        raise RuntimeError(f"screenshot unavailable: {unavailable_reason()}")
    data = _SERVICE.capturePng()
    if data is None:
        raise RuntimeError(f"screenshot failed: {unavailable_reason()}")
    return bytes(data)
'''

NEW = '''_ROOT_CAPTURE_FLAG = None


def root_capture_enabled() -> bool:
    """root \u622a\u56fe\u9ed8\u8ba4\u5173\u95ed\u3002

    \u8c03\u7528 su \u4f1a\u5728\u8bbe\u5907\u4e0a\u5f39\u300c\u8bf7\u6c42\u8d85\u7ea7\u7528\u6237\u6743\u9650\u300d\u5bf9\u8bdd\u6846\uff0c\u7528\u6237\u660e\u4ee4\u96f6\u5f39\u7a97\uff0c
    \u6240\u4ee5\u53ea\u6709\u5b58\u5728 files/su_capture.on \u65f6\u624d\u8d70 root \u901a\u9053\uff08\u7528\u6237\u81ea\u5df1\u653e\u7684\u5f00\u5173\u6587\u4ef6\uff09\u3002
    """
    global _ROOT_CAPTURE_FLAG
    if _ROOT_CAPTURE_FLAG is None:
        try:
            import os

            _ROOT_CAPTURE_FLAG = os.path.exists(os.path.join(_files_dir(), "su_capture.on"))
        except Exception:  # noqa: BLE001
            _ROOT_CAPTURE_FLAG = False
    return bool(_ROOT_CAPTURE_FLAG)


def _files_dir() -> str:
    try:
        from java import jclass

        app = jclass("com.chaquo.python.Python").getPlatform().getApplication()
        return str(app.getFilesDir().getAbsolutePath())
    except Exception:  # noqa: BLE001
        return ""


def capture_png() -> bytes:
    """\u622a\u53d6\u5f53\u524d\u5c4f\u5e55\uff0c\u8fd4\u56de PNG \u5b57\u8282\u3002\u5931\u8d25\u629b RuntimeError \u5e76\u8bf4\u660e\u539f\u56e0\u3002

    \u987a\u5e8f\uff1a\u65e0\u969c\u788d\uff08\u4e0d\u5f39\u7a97\uff09\u2192 \u53ea\u6709\u5728\u7528\u6237\u663e\u5f0f\u5f00\u542f root \u622a\u56fe\u65f6\u624d\u8bd5 su\u3002
    """
    if _SERVICE is not None:
        try:
            data = _SERVICE.capturePng()
        except Exception:  # noqa: BLE001
            data = None
        if data is not None:
            return bytes(data)
    if root_capture_enabled():
        rooted = capture_via_su()
        if rooted:
            return rooted
    raise RuntimeError(f"screenshot unavailable: {unavailable_reason()}")
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if "_ROOT_CAPTURE_FLAG" in text:
    raise SystemExit("already patched")
if OLD not in text:
    raise SystemExit("capture_png block not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(OLD, NEW, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log22.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
