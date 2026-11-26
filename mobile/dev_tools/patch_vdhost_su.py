import io, subprocess, sys
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\kotlin\com\oas\oasx\VdHostDisplay.kt"
text = io.open(PATH, "rb").read().decode("utf-8-sig")

OLD_APK = '''        val apk = apkPath(context)
        if (apk.isEmpty()) return -1'''
NEW_APK = '''        val apk = apkPath(context)
        if (apk.isEmpty()) {
            lastError = "apkPath empty"
            Log.e(TAG, "apkPath empty, cannot start host")
            return -1
        }'''

OLD_CMD = '''            val inner = "CLASSPATH=$apk exec app_process /system/bin $HOST_CLASS $width $height $dpi"
            val command = "su 2000 sh -c '$inner'"'''
NEW_CMD = '''            val inner = "CLASSPATH=$apk exec app_process /system/bin $HOST_CLASS $width $height $dpi"
            // 真机没 root：Shizuku 若是 ADB 起的，newProcess 本来就是 uid 2000(shell)，不能再套 su；
            // 只有 root 起的 Shizuku（uid 0）才需要 su 2000 降权到 shell。
            val uid = ShizukuShell.exec("id -u", 5000).output.trim()
            val command = if (uid == "0") "su 2000 sh -c '$inner'" else "sh -c '$inner'"
            Log.i(TAG, "start host uid=$uid cmd=$command")'''

for old, new, label in ((OLD_APK, NEW_APK, "apk"), (OLD_CMD, NEW_CMD, "cmd")):
    if old not in text:
        raise SystemExit(f"anchor missing: {label}")
    text = text.replace(old, new, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
print("patched")