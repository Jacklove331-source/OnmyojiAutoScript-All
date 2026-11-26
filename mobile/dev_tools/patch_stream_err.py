import io
P = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\kotlin\com\oas\oasx\ScreenStream.kt"
t = io.open(P, "rb").read().decode("utf-8-sig")
OLD = '''        if (serverPath.isEmpty()) {
            lastError = "deploy scrcpy-server failed"
            return false
        }'''
NEW = '''        if (serverPath.isEmpty()) {
            lastError = lastError.ifEmpty { "deploy scrcpy-server failed" }
            return false
        }'''
if OLD not in t: raise SystemExit("anchor missing")
io.open(P, "w", encoding="utf-8", newline="\n").write(t.replace(OLD, NEW, 1))
print("patched")