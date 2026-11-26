import io
P = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\kotlin\com\oas\oasx\ScreenStream.kt"
t = io.open(P, "rb").read().decode("utf-8-sig")
OLD_START = t.index("            val copy = ShizukuShell.exec(")
OLD_END = t.index("        } catch (error: Throwable) {\n            lastError = \"deploy failed")
NEW = '''            // 注意：exec 对带输出的短命令常走超时分支返回 code=-2（进程没及时退出），
            // 所以不能用返回码判成败，直接用 stat 复核文件大小。
            ShizukuShell.exec("cp ${local.absolutePath} $target; chmod 644 $target", 20000)
            val check = ShizukuShell.exec("stat -c %s $target 2>/dev/null || echo 0", 10000)
            val size = check.output.trim().toIntOrNull() ?: 0
            Log.i(TAG, "deploy size=$size out=${check.output.take(80)}")
            if (size <= 0) {
                lastError = "deploy size=$size"
                ""
            } else {
                target
            }
'''
t = t[:OLD_START] + NEW + t[OLD_END:]
io.open(P, "w", encoding="utf-8", newline="\n").write(t)
print("patched")