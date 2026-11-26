"""让 /vd/screenshot 不再把连接打死：自动确保虚拟屏 + 全链路兜底 + 落盘。"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

NEW = '''        if route.path == "/vd/screenshot":
            # \u4efb\u4f55\u5f02\u5e38\u90fd\u8981\u56de JSON\uff1b\u4e0d\u80fd\u8ba9\u8bf7\u6c42\u7ebf\u7a0b\u6b7b\u5728\u8fd9\u91cc\uff08\u4e4b\u524d\u76f4\u63a5\u65ad\u8fde\u3001\u524d\u7aef\u53ea\u80fd\u770b\u5230\u7a7a\uff09\u3002
            try:
                hidden_id = -1
                try:
                    if bridge.hidden_ready():
                        hidden_id = bridge.hidden_display()
                        requested = int(query.get("display", -1))
                        if requested in (-1, hidden_id):
                            data = bridge.hidden_screenshot()
                            if data:
                                _save_frame(data)
                                self._send(200, data, "image/png")
                                return
                except Exception as error:  # noqa: BLE001
                    logger.warning(f'[mobile] hidden screenshot failed: {error!r}')

                global VIRTUAL_DISPLAY_ID
                if VIRTUAL_DISPLAY_ID < 0:
                    created = bridge.display_create(1280, 720)
                    if created >= 0:
                        VIRTUAL_DISPLAY_ID = created
                display = int(query.get("display", VIRTUAL_DISPLAY_ID))
                if display < 0:
                    self._send_json(503, {"ok": False, "error": "no display available"})
                    return
                data = bridge.display_screenshot(display)
                if not data:
                    self._send_json(503, {"ok": False, "error": f"empty frame (display={display})"})
                    return
                _save_frame(data)
                self._send(200, data, "image/png")
            except Exception as error:  # noqa: BLE001
                logger.warning(f'[mobile] vd screenshot failed: {error!r}')
                self._send_json(503, {"ok": False, "error": repr(error)})
            return
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
pattern = re.compile(r'        if route\.path == "/vd/screenshot":.*?\n            return\n', re.S)
if not pattern.search(text):
    raise SystemExit("vd/screenshot block not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(pattern.sub(NEW, text, count=1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log18.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
