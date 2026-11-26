"""用走通的 bridge.capture_png() 兜住监控取帧。

实测：/vd/screenshot 的虚拟屏路径在 MuMu 上会把请求线程打死（HTTP 000）；
但 /screenshot.png（bridge.capture_png）能稳定返回 200 + PNG。
所以：/vd/screenshot 末尾兜底用 capture_png；/frame/latest 没有文件时也现抓一张存下来。
"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

# 1) _latest_frame：无文件时现抓
old = '''        if not files:
            return {"ok": True, "has_frame": False, "image": None}'''
new = '''        if not files:
            # \u6ca1\u5386\u53f2\u5e27\u5c31\u73b0\u6293\u4e00\u5f20\uff08\u865a\u62df\u5c4f\u8def\u5f84\u5728\u6a21\u62df\u5668\u4e0a\u4e0d\u53ef\u7528\uff0c\u4f46 capture_png \u53ef\u7528\uff09
            try:
                data = bridge.capture_png()
            except Exception:  # noqa: BLE001
                data = None
            if not data:
                return {"ok": True, "has_frame": False, "image": None}
            _save_frame(data)
            return {
                "ok": True,
                "has_frame": True,
                "mtime": 0,
                "image": base64.b64encode(data).decode("ascii"),
            }'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if old not in text:
    raise SystemExit("latest-frame empty branch not found")
text = text.replace(old, new, 1)

# 2) /vd/screenshot：末尾兜底 capture_png
old2 = '''            except Exception as error:  # noqa: BLE001
                logger.warning(f'[mobile] vd screenshot failed: {error!r}')
                self._send_json(503, {"ok": False, "error": repr(error)})
            return
'''
new2 = '''            except Exception as error:  # noqa: BLE001
                logger.warning(f'[mobile] vd screenshot failed: {error!r}')
                try:
                    data = bridge.capture_png()
                except Exception:  # noqa: BLE001
                    data = None
                if data:
                    _save_frame(data)
                    self._send(200, data, "image/png")
                else:
                    self._send_json(503, {"ok": False, "error": repr(error)})
            return
'''
if old2 not in text:
    raise SystemExit("vd screenshot tail not found")
text = text.replace(old2, new2, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log19.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
