import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"
text = io.open(PATH, "rb").read().decode("utf-8-sig")
old = """            if route.path == "/ai/config":
                self._send_json(200, _ai_config_apply(body))
                return
"""
new = """            if route.path == "/ai/config":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                except OSError:
                    raw = b"{}"
                self._send_json(200, _ai_config_apply(raw))
                return
"""
if old not in text:
    raise SystemExit("post route not found")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(old, new, 1))
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log24.txt", "w", encoding="utf-8").write("rc=%d\n%s" % (p.returncode, p.stderr[-300:]))