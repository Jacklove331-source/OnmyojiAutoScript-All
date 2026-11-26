import io
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\kotlin\com\oas\oasx\VdHostDisplay.kt"
text = io.open(PATH, "rb").read().decode("utf-8-sig")
OLD = '''            val uid = ShizukuShell.exec("id -u", 5000).output.trim()
            val command = if (uid == "0") "su 2000 sh -c '$inner'" else "sh -c '$inner'"
            Log.i(TAG, "start host uid=$uid cmd=$command")'''
NEW = '''            // 不在这里跑 exec 探测（exec 对短命令会走到超时分支抛 "process hasn't exited"）：
            // 只看 su 是否存在。真机没 su 就是 ADB 起的 Shizuku（本来就是 uid 2000 shell），直接跑；
            // 模拟器是 root 起的 Shizuku（uid 0），需要 su 2000 降权。
            val hasSu = java.io.File("/system/xbin/su").exists() ||
                java.io.File("/system/bin/su").exists() ||
                java.io.File("/su/bin/su").exists()
            val command = if (hasSu) "su 2000 sh -c '$inner'" else "sh -c '$inner'"
            Log.i(TAG, "start host hasSu=$hasSu cmd=$command")'''
if OLD not in text:
    raise SystemExit("anchor missing")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(text.replace(OLD, NEW, 1))
print("patched")