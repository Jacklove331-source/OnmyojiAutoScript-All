import io
P = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\kotlin\com\oas\oasx\ScreenStream.kt"
t = io.open(P, "rb").read().decode("utf-8-sig")
OLD = '''            val local = java.io.File(context.filesDir, "scrcpy-server")'''
NEW = '''            // shell uid 读不到 app 私有目录（/data/user/0/... 是 0700），
            // 先落到外部私有目录 /sdcard/Android/data/<pkg>/files，再让 shell 复制到 /data/local/tmp。
            val staging = context.getExternalFilesDir(null) ?: context.filesDir
            val local = java.io.File(staging, "scrcpy-server")'''
if OLD not in t:
    raise SystemExit("anchor missing")
t = t.replace(OLD, NEW, 1)
io.open(P, "w", encoding="utf-8", newline="\n").write(t)
print("patched")