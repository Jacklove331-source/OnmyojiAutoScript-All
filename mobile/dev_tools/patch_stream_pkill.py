import io
P = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\kotlin\com\oas\oasx\ScreenStream.kt"
t = io.open(P, "rb").read().decode("utf-8-sig")
ANCHOR = '''            val listener = try {
                LocalServerSocket(SOCKET_NAME)'''
NEW = '''            // 先清掉可能残留的旧 server（上次没退干净的会占着 localabstract:scrcpy，
            // 表现就是这里 Address already in use）。
            ShizukuShell.exec("pkill -f scrcpy.Server", 8000)
            Thread.sleep(400)

            val listener = try {
                LocalServerSocket(SOCKET_NAME)'''
if ANCHOR not in t: raise SystemExit("anchor missing")
io.open(P, "w", encoding="utf-8", newline="\n").write(t.replace(ANCHOR, NEW, 1))
print("patched")