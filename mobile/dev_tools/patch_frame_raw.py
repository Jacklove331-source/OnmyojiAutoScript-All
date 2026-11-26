import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"
text = io.open(PATH, "rb").read().decode("utf-8-sig")
old = """        if route.path == "/frame/latest":
            payload = _latest_frame()
            self._send_json(200, payload)
            return
"""
new = """        if route.path == "/frame/latest":
            payload = _latest_frame()
            encoded = payload.get("image")
            if encoded:
                import base64 as _b64

                self._send(200, _b64.b64decode(encoded), "image/png")
                return
            self._send_json(200, payload)
            return
"""
if old not in text:
    raise SystemExit("frame route not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(old, new, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log26.txt", "w", encoding="utf-8").write("rc=%d\n%s" % (p.returncode, p.stderr[-300:]))