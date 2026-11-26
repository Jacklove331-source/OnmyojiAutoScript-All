import io
P = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\kotlin\com\oas\oasx\ScreenStream.kt"
t = io.open(P, "rb").read().decode("utf-8-sig")

# 1) tunnel_forward 改为 false：让 server 主动连我们的 localabstract:scrcpy
t = t.replace('"tunnel_forward=true log_level=info', '"tunnel_forward=false log_level=info', 1)

# 2) 连接逻辑改成「App 先建 LocalServerSocket 再 accept」
start = t.index("            // server 起来后会监听 localabstract:scrcpy，我们直接连")
end = t.index("            if (sock == null) {")
NEW = '''            // SELinux：app 进程连不上 shell 进程创建的 abstract socket（Permission denied），
            // 所以反过来——App 自己建 localabstract:scrcpy，让 server 用 tunnel_forward=false 连进来。
            val listener = try {
                LocalServerSocket(SOCKET_NAME)
            } catch (error: Throwable) {
                lastError = "listen failed: ${error.message}"
                stop()
                return false
            }
            serverSocket = listener
            val accepted = try {
                listener.accept()
            } catch (error: Throwable) {
                lastError = "accept failed: ${error.message}"
                stop()
                return false
            }
            val sock: LocalSocket? = accepted
'''
t = t[:start] + NEW + t[end:]

# 3) stop() 里关闭 listener；字段声明
t = t.replace("    private var socket: LocalSocket? = null",
              "    private var socket: LocalSocket? = null\n    private var serverSocket: LocalServerSocket? = null", 1)
t = t.replace("        try { socket?.close() } catch (_: Throwable) {}\n        socket = null",
              "        try { socket?.close() } catch (_: Throwable) {}\n        socket = null\n        try { serverSocket?.close() } catch (_: Throwable) {}\n        serverSocket = null", 1)
t = t.replace("import android.net.LocalSocketAddress", "import android.net.LocalServerSocket\nimport android.net.LocalSocketAddress", 1)

io.open(P, "w", encoding="utf-8", newline="\n").write(t)
print("patched")