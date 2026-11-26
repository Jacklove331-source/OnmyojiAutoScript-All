import io
PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\kotlin\com\oas\oasx\VdHostDisplay.kt"
text = io.open(PATH, "rb").read().decode("utf-8-sig")

# 1) 增加诊断缓冲字段
OLD_FIELD = '''    @Volatile
    private var lastError: String = ""'''
NEW_FIELD = '''    @Volatile
    private var lastError: String = ""

    /** 宿主 stdout 的最近若干行（PROBE=/HOST_ERROR= 等），排障时直接看，不用连 logcat。 */
    private val probeLog = java.util.ArrayDeque<String>()'''
if OLD_FIELD not in text:
    raise SystemExit("anchor missing: field")
text = text.replace(OLD_FIELD, NEW_FIELD, 1)

# 2) status() 带上最近行
OLD_STATUS = '''    fun status(): String = "running=${isRunning()} displayId=$displayId error=$lastError"'''
NEW_STATUS = '''    fun status(): String {
        val tail = synchronized(probeLog) { probeLog.joinToString(" | ") }
        return "running=${isRunning()} displayId=$displayId error=$lastError probe=$tail"
    }'''
if OLD_STATUS not in text:
    raise SystemExit("anchor missing: status")
text = text.replace(OLD_STATUS, NEW_STATUS, 1)

# 3) 读宿主输出时把每行记进缓冲
OLD_READ = '''                Log.i(TAG, "host: $line")'''
NEW_READ = '''                Log.i(TAG, "host: $line")
                synchronized(probeLog) {
                    probeLog.addLast(line)
                    while (probeLog.size > 12) probeLog.removeFirst()
                }'''
if OLD_READ not in text:
    raise SystemExit("anchor missing: read")
text = text.replace(OLD_READ, NEW_READ, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
print("patched")