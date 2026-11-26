import io, re, subprocess, sys
ROOT = r"D:\3D\YYS2\oasx_mobile"
KT = ROOT + r"\android\app\src\main\kotlin\com\oas\oasx\ScreenStream.kt"
PYF = ROOT + r"\android\app\src\main\python\oas_mobile\server.py"

# 1) Kotlin: 让 status/latestJpeg/isRunning 变成真静态，Chaquopy 才能从 jclass 调用
kt = io.open(KT, "rb").read().decode("utf-8-sig")
for sig in ("    fun isRunning(): Boolean = running",
            "    fun status(): String =",
            "    fun latestJpeg(): ByteArray? = latestJpeg"):
    if sig in kt and ("@JvmStatic\n" + sig) not in kt:
        kt = kt.replace(sig, "    @JvmStatic\n" + sig, 1)
io.open(KT, "w", encoding="utf-8", newline="\n").write(kt)

# 2) Python: monitor 的 start/stop 属于 POST，补到 do_POST 里
src = io.open(PYF, "rb").read().decode("utf-8-sig")
if "def do_POST" not in src:
    raise SystemExit("no do_POST")
i = src.index("def do_POST")
# 找 do_POST 里第一个 route.path 判断
m = re.search(r"\n(\s+)if route\.path == ", src[i:])
if not m:
    raise SystemExit("no route check in do_POST")
indent = m.group(1)
pos = i + m.start() + 1
BLOCK = (f'{indent}if route.path == "/monitor/start":\n'
         f'{indent}    ok = bridge.vdstream_start(int(query.get("width", 1280)),\n'
         f'{indent}                               int(query.get("height", 720)),\n'
         f'{indent}                               int(query.get("display", -1)))\n'
         f'{indent}    self._send_json(200, {{"ok": ok, "status": bridge.vdstream_status()}})\n'
         f'{indent}    return\n'
         f'{indent}if route.path == "/monitor/stop":\n'
         f'{indent}    self._send_json(200, {{"ok": bridge.vdstream_stop()}})\n'
         f'{indent}    return\n')
if '/monitor/start' in src[i:]:
    print("POST monitor already present")
else:
    src = src[:pos] + BLOCK + src[pos:]
    io.open(PYF, "w", encoding="utf-8", newline="\n").write(src)
    print("POST monitor routes added")

p = subprocess.run([sys.executable, "-m", "py_compile", PYF], capture_output=True, text=True, errors="replace")
print("py_compile", p.returncode, p.stderr[-200:])