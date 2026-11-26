import io
P = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\kotlin\com\oas\oasx\ScreenStream.kt"
t = io.open(P, "rb").read().decode("utf-8-sig")
OLD = '''            val size = check.output.trim().toIntOrNull() ?: 0'''
NEW = '''            // exec 对短命令常返回 -2（"timeout: <输出>"），输出完整但拿不到干净整数，
            // 所以从文本里抠出最大的数字当文件大小。
            val size = Regex("\\\\d+").findAll(check.output)
                .mapNotNull { it.value.toIntOrNull() }
                .maxOrNull() ?: 0'''
if OLD not in t: raise SystemExit("anchor missing")
io.open(P, "w", encoding="utf-8", newline="\n").write(t.replace(OLD, NEW, 1))
print("patched")