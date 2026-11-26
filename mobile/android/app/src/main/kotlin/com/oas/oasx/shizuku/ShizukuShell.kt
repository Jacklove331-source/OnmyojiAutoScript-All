package com.oas.oasx.shizuku

import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import android.util.Log
import rikka.shizuku.Shizuku
import java.io.ByteArrayOutputStream

/**
 * Shizuku 提供的「shell 级」命令通道（uid 2000，无需 Root）。
 *
 * 用途（后台挂机路线）：把游戏搬到**虚拟屏**上运行，截图与点击都指向那块屏，
 * 手机主屏留给用户自己用。所需能力全部是 shell 命令：
 *   settings put global overlay_display_devices "1280x720/240"   # 建虚拟屏
 *   am start --display <id> ...                                  # 在虚拟屏启动 App
 *   screencap -p -d <id>                                         # 截虚拟屏
 *   input -d <id> tap x y                                        # 往虚拟屏注入点击
 * 这套做法与 ALAS-AOS、XYZ(PVZ2) 等开源项目的实现一致，不需要 UserService/AIDL。
 */
object ShizukuShell {

    private const val TAG = "ShizukuShell"
    private val onmyojiPackages = setOf("com.netease.onmyoji", "com.netease.onmyoji.wyzymnqsd_cps")
    private val windowHeader = Regex("(?m)^  Window #\\d+ Window\\{")
    private val needsMuMuWarmup by lazy {
        Build.VERSION.SDK_INT < 34 &&
            exec("pm list packages -s com.nemu.oaidmanager", 3000).output.lineSequence()
                .any { it.trim() == "package:com.nemu.oaidmanager" }
    }

    data class ShellResult(val code: Int, val output: String, val bytes: ByteArray)

    @Volatile
    private var pendingRequestCode: Int = -1

    @JvmStatic
    fun isBinderAvailable(): Boolean = try {
        Shizuku.pingBinder()
    } catch (error: Throwable) {
        Log.w(TAG, "pingBinder failed: ${error.message}")
        false
    }

    @JvmStatic
    fun hasPermission(): Boolean = try {
        isBinderAvailable() &&
            (Shizuku.isPreV11() || Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED)
    } catch (error: Throwable) {
        false
    }

    /** 首次使用需要用户在系统弹窗里授权（Shizuku app 负责弹窗）。 */
    @JvmStatic
    fun requestPermission(requestCode: Int) {
        if (!isBinderAvailable()) return
        pendingRequestCode = requestCode
        try {
            Shizuku.requestPermission(requestCode)
        } catch (error: Throwable) {
            Log.e(TAG, "requestPermission failed", error)
        }
    }

    @JvmStatic
    fun onPermissionResult(requestCode: Int, granted: Boolean) {
        if (requestCode == pendingRequestCode) {
            Log.i(TAG, "shizuku permission granted=$granted")
            pendingRequestCode = -1
        }
    }

    // ---------------------------------------------------------------- 命令执行

    /**
     * Shizuku 13.x 把 newProcess 声明成 private（javap 可见：
     * `private static ShizukuRemoteProcess newProcess(String[], String[], String)`），
     * 官方没提供公开的 shell 执行入口，故用反射调用；这不是隐藏 API（属于三方库自身）。
     */
    private val newProcessMethod: java.lang.reflect.Method? by lazy {
        try {
            Shizuku::class.java
                .getDeclaredMethod(
                    "newProcess",
                    Array<String>::class.java,
                    Array<String>::class.java,
                    String::class.java,
                )
                .apply { isAccessible = true }
        } catch (error: Throwable) {
            Log.e(TAG, "resolve newProcess failed", error)
            null
        }
    }

    /** 是否在本次会话里用到了 root 兜底（仅开发/模拟器自测；正式包真机走 Shizuku）。 */
    @Volatile
    var usingRootFallback: Boolean = false
        private set

    /**
     * 读进程输出到 EOF 再取退出码。
     * 不能用 Process.isAlive()/waitFor(超时) 轮询：Shizuku 的 ShizukuRemoteProcess.exitValue()
     * 在进程未退出时抛 RemoteException("process hasn't exited")，而 isAlive() 内部就调它。
     */
    private fun runProcess(process: Process, timeoutMs: Long): ShellResult {
        val out = java.io.ByteArrayOutputStream()
        val errors = java.io.ByteArrayOutputStream()
        val readers = listOf(
            Thread { try { process.inputStream.copyTo(out) } catch (_: Throwable) {} },
            Thread { try { process.errorStream.copyTo(errors) } catch (_: Throwable) {} },
        )
        readers.forEach { it.isDaemon = true; it.start() }
        val finished = java.util.concurrent.CountDownLatch(1)
        var exitCode = -3
        val waiter = Thread {
            try { exitCode = process.waitFor() }
            catch (error: Throwable) { Log.w(TAG, "waitFor failed", error) }
            finally { finished.countDown() }
        }.apply { isDaemon = true; start() }
        val exited = finished.await(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)
        if (!exited) {
            try { process.destroy() } catch (_: Throwable) {}
        }
        readers.forEach { it.join(500) }
        val bytes = out.toByteArray()
        val message = String(bytes) + String(errors.toByteArray())
        return ShellResult(if (exited) exitCode else -2,
            if (exited) message else "timeout: $message", bytes)
    }

    private fun execViaRoot(command: String, timeoutMs: Long): ShellResult? {
        return try {
            val process = Runtime.getRuntime().exec(arrayOf("su", "-c", command))
            runProcess(process, timeoutMs)
        } catch (error: Throwable) {
            Log.w(TAG, "root fallback unavailable: ${error.message}")
            null
        }
    }

    /**
     * 杀掉历史上泄漏的 `com.oas.oasx:frame` 进程（它们在 shell 身份下由 Shizuku 起，
     * App 自己 kill 不掉，只能借 Shizuku）。
     *
     * 实测（2026-11-22）：反复启停流后累积 **28 个 frame 进程 / 101 个 oas-frame-binder 虚拟屏**，
     * 用户表现是「OAS 关了也退不出去、看不到游戏」。启动新流之前清一遍即可归零。
     */
    @JvmStatic
    fun killStaleFrameProcesses(): Int {
        val result = exec("pkill -f com.oas.oasx:frame; echo done", timeoutMs = 8000)
        Log.i(TAG, "killStaleFrameProcesses rc=${result.code} out=${result.output.trim()}")
        return result.code
    }

    @JvmStatic
    @JvmOverloads
    fun exec(command: String, timeoutMs: Long = 20000): ShellResult {
        if (!hasPermission()) {
            // 模拟器（MuMu 等）带 root，可用 su 兜底验证整条链路；
            // 真机无 root，这个分支必然失败，正式运行只能靠 Shizuku 授权。
            val rooted = execViaRoot(command, timeoutMs)
            if (rooted != null && !rooted.output.contains("Permission denied")) {
                usingRootFallback = true
                return rooted
            }
            return ShellResult(-1, "shizuku permission not granted", ByteArray(0))
        }
        val method = newProcessMethod
            ?: return ShellResult(-4, "newProcess unavailable in this Shizuku version", ByteArray(0))
        return try {
            val process = method.invoke(
                null, arrayOf("sh", "-c", command), null, null
            ) as? Process ?: return ShellResult(-5, "newProcess returned null", ByteArray(0))
            runProcess(process, timeoutMs)
        } catch (error: Throwable) {
            Log.e(TAG, "exec failed: $command", error)
            ShellResult(-3, "error: ${error.message}", ByteArray(0))
        }
    }

    /**
     * 起一个**常驻** shell 进程并返回句柄（调用方负责读它的 stdout、必要时 destroy）。
     *
     * 与 [exec] 的区别：exec 会等到进程退出才返回（读完 stdout 再 exitValue），
     * 而 app_process 宿主这类进程本来就是长期驻留的，必须拿句柄。
     */
    @JvmStatic
    fun startLongRunning(command: String): Process? {
        val method = newProcessMethod
        if (method == null) {
            Log.e(TAG, "startLongRunning: newProcess unavailable")
            return null
        }
        if (!hasPermission()) {
            Log.e(TAG, "startLongRunning: shizuku permission not granted")
            return null
        }
        return try {
            method.invoke(null, arrayOf("sh", "-c", command), null, null) as? Process
        } catch (error: Throwable) {
            Log.e(TAG, "startLongRunning failed: $command", error)
            null
        }
    }

    // ---------------------------------------------------------------- 虚拟屏

    /** 找当前 overlay 虚拟屏的 displayId（按 `mPrimaryDisplayDevice=叠加视图 #N` 定位）。 */
    @JvmStatic
    fun findOverlayDisplayId(): Int {
        val output = exec("dumpsys display").output
        var current = -1
        for (line in output.lineSequence()) {
            Regex("mDisplayId=(\\d+)").find(line)?.let {
                current = it.groupValues[1].toInt()
            }
            val isOverlayPrimary = line.contains("mPrimaryDisplayDevice=") &&
                (line.contains("叠加") || line.contains("Overlay") || line.contains("overlay"))
            if (isOverlayPrimary && current >= 0) return current
        }
        return -1
    }

    /** 建一块叠加虚拟屏，返回 displayId；失败 -1。 */
    @JvmStatic
    @JvmOverloads
    fun createOverlayDisplay(width: Int, height: Int, densityDpi: Int = 240): Int {
        // 先清掉可能残留的虚拟屏，否则无法判断新建的是哪一块
        exec("settings put global overlay_display_devices ''", 15000)
        Thread.sleep(1000)
        val spec = "${width}x${height}/${densityDpi}"
        // 实测：`settings put` 在 Shizuku 通道下常常不回包（timeout），但设置其实已生效，
        // 所以这里不因非 0 返回码提前放弃，改为按「屏是否真的出现了」判断成功。
        val result = exec("settings put global overlay_display_devices '$spec'", 20000)
        if (result.code != 0) {
            Log.w(TAG, "settings put reported: ${result.output}")
        }
        for (attempt in 0 until 6) {
            Thread.sleep(1500)
            val created = findOverlayDisplayId()
            if (created >= 0) {
                Log.i(TAG, "overlay display created: id=$created spec=$spec (try $attempt)")
                return created
            }
        }
        Log.e(TAG, "create overlay display failed: no display appeared for $spec")
        return -1
    }

    @JvmStatic
    fun listOverlayDisplays(): List<Int> {
        val out = exec("dumpsys display | grep -E 'mDisplayId=[0-9]+'").output
        return Regex("mDisplayId=(\\d+)").findAll(out).map { it.groupValues[1].toInt() }.distinct().toList()
    }

    private fun displayIds(): List<Int> {
        val out = exec("dumpsys display | grep -o 'mDisplayId=[0-9]*'").output
        return Regex("mDisplayId=(\\d+)").findAll(out).map { it.groupValues[1].toInt() }.distinct().toList()
    }

    @JvmStatic
    fun isDisplayAlive(displayId: Int): Boolean {
        // 厂商 ROM 上向「已失效的虚拟屏」注入事件可能让 system_server 越界崩溃，
        // 所以每次注入前都必须确认 display 还在（XYZ 项目踩过这个坑）。
        if (displayId < 0) return false
        if (!displayIds().contains(displayId)) return false
        return findOverlayDisplayId() == displayId
    }

    @JvmStatic
    fun displayExists(displayId: Int): Boolean {
        if (displayId < 0) return false
        return displayIds().contains(displayId)
    }

    @JvmStatic
    fun releaseOverlayDisplays(): Boolean {
        val result = exec("settings put global overlay_display_devices ''")
        Thread.sleep(800)
        return result.code == 0
    }

    /** 解析包的 launcher Activity（game 这类包用 MAIN/LAUNCHER intent 常解析失败，必须拿 component）。 */
    @JvmStatic
    fun resolveLauncherActivity(packageName: String): String {
        val out = exec("cmd package resolve-activity --brief -c android.intent.category.LAUNCHER $packageName").output
        // 输出末尾是 `pkg/activity`，前面还有 priority=... 之类的行
        return out.lineSequence()
            .map { it.trim() }
            .lastOrNull { it.contains("/") && it.startsWith(packageName) }
            ?: ""
    }

    @JvmStatic
    fun launchOnDisplay(packageName: String, displayId: Int): Boolean {
        val component = resolveLauncherActivity(packageName)
        val target = if (component.isNotEmpty()) "-n $component" else
            "-a android.intent.action.MAIN -c android.intent.category.LAUNCHER $packageName"
        // displayId < 0 = 主屏模式：不带 --display（vivo 上 --display 0 同样不合法）
        val cmd = if (displayId < 0) "am start -W $target" else "am start --display $displayId -W $target"
        val result = exec(cmd, 60000)
        val text = result.output
        val ok = text.contains("Status: ok") ||
            (result.code == 0 && !text.contains("Error") && !text.contains("does not exist"))
        Log.i(TAG, "launch $packageName on display $displayId component=$component ok=$ok ${text.take(160)}")
        return ok
    }

    private fun onmyojiDrawnOnMainScreen(packageName: String): Boolean {
        val client = "$packageName/com.netease.onmyoji.Client"
        val output = exec("dumpsys window windows", 3000)
        if (output.code != 0) return false
        val drawn = output.output.split(windowHeader).any { window ->
            window.substringBefore('\n').trimEnd().endsWith(" $client}:") &&
                window.contains("mDisplayId=0 ") && window.contains("mDrawState=HAS_DRAWN") &&
                window.contains("mViewVisibility=0x0 mHaveFrame=true")
        }
        return drawn && exec("dumpsys SurfaceFlinger --list", 3000).output.lineSequence()
            .any { it.startsWith("SurfaceView[$client]") }
    }

    /** Keep the placement poller from moving Onmyoji while its native surface initializes. */
    @JvmStatic
    @Synchronized
    fun ensureStreamGame(packageName: String, displayId: Int): Boolean {
        if (displayId <= 0 || !Regex("[A-Za-z][A-Za-z0-9_.]*").matches(packageName)) return false
        if (packageName !in onmyojiPackages || !needsMuMuWarmup) {
            if (isTaskOnDisplay(packageName, displayId)) return true
            if (moveTaskToDisplay(packageName, displayId)) return true
            if (!launchOnDisplay(packageName, displayId)) return false
            // 等待任务真正出现在目标虚拟屏；部分游戏/ROM 会把启动重定向到主屏，此时再搬回虚拟屏。
            val deadline = SystemClock.elapsedRealtime() + 8_000L
            while (SystemClock.elapsedRealtime() < deadline) {
                if (isTaskOnDisplay(packageName, displayId)) return true
                if (moveTaskToDisplay(packageName, displayId)) return true
                Thread.sleep(300L)
            }
            return isTaskOnDisplay(packageName, displayId)
        }
        if (isTaskOnDisplay(packageName, displayId)) return true
        if (!onmyojiDrawnOnMainScreen(packageName)) {
            val component = resolveLauncherActivity(packageName)
            if (component.isEmpty()) return false
            val started = exec("am start --display 0 -n $component", 5000)
            if (started.code != 0 || started.output.contains("Error:")) return false
            val deadline = SystemClock.elapsedRealtime() + 25_000L
            while (!onmyojiDrawnOnMainScreen(packageName)) {
                if (SystemClock.elapsedRealtime() >= deadline) {
                    Log.w(TAG, "MuMu: waiting for Onmyoji to draw on the main screen timed out")
                    return false
                }
                Thread.sleep(300L)
            }
        }
        Log.i(TAG, "MuMu: Onmyoji drew its native surface before moving to display $displayId")
        return moveTaskToDisplay(packageName, displayId)
    }

    /**
     * 把某应用所在的 RootTask 搬到指定 display。
     *
     * 为什么需要：很多游戏（含阴阳师）用默认 display 启动，`am start --display N` 拉起来后
     * 会自己回到主屏；必须先让它启动，再用 `am display move-stack <rootTaskId> <displayId>`
     * 把整个任务搬过去（XYZ 项目的 DisplayTaskMover 也是这个思路）。
     */
    private fun targetRootTasks(packageName: String): List<Pair<Int, Int>> {
        val listing = exec("am stack list", 5000)
        if (listing.code != 0) return emptyList()
        var root = -1
        var display = -1
        val result = mutableListOf<Pair<Int, Int>>()
        for (line in listing.output.lineSequence()) {
            Regex("RootTask id=(\\d+)").find(line)?.let { root = it.groupValues[1].toInt() }
            Regex("displayId=(\\d+)").find(line)?.let { display = it.groupValues[1].toInt() }
            if (line.contains("taskId=") && line.contains("$packageName/") && root >= 0) {
                result.add(root to display)
            }
        }
        return result.distinct()
    }

    @JvmStatic
    fun isTaskOnDisplay(packageName: String, displayId: Int): Boolean {
        val roots = targetRootTasks(packageName)
        return roots.isNotEmpty() && roots.all { it.second == displayId }
    }

    @JvmStatic
    @Synchronized
    fun moveTaskToDisplay(packageName: String, displayId: Int): Boolean {
        if (!Regex("[A-Za-z][A-Za-z0-9_.]*").matches(packageName) || displayId < 0) return false
        val roots = targetRootTasks(packageName)
        if (roots.isEmpty()) return false
        if (roots.all { it.second == displayId }) return true
        for ((root, currentDisplay) in roots) {
            if (currentDisplay == displayId) continue
            val moved = exec("am display move-stack $root $displayId", 5000)
            if (moved.code != 0 || moved.output.contains("Error") || moved.output.contains("Exception")) {
                Log.w(TAG, "move root=$root failed code=${moved.code}: ${moved.output.take(160)}")
                return false
            }
            Log.i(TAG, "moved target root=$root to display=$displayId")
        }
        return isTaskOnDisplay(packageName, displayId)
    }

    /** 一键确保：游戏在虚拟屏上跑（建屏 → 启动 → 搬任务）。返回 displayId，失败 -1。 */
    @JvmStatic
    fun ensureAppOnVirtualDisplay(packageName: String, width: Int = 1280, height: Int = 720): Int {
        var displayId = findOverlayDisplayId()
        if (displayId < 0) {
            displayId = createOverlayDisplay(width, height)
        }
        if (displayId < 0) return -1
        if (resolveLauncherActivity(packageName).isEmpty()) {
            Log.w(TAG, "ensure: cannot resolve launcher for $packageName")
            return -1
        }
        launchOnDisplay(packageName, displayId)
        Thread.sleep(3000)
        moveTaskToDisplay(packageName, displayId)
        Thread.sleep(1500)
        // 复核：任务确实搬过去了才算成功
        val listing = exec("am stack list").output
        var currentRoot = -1
        for (line in listing.lineSequence()) {
            Regex("RootTask id=(\\d+)").find(line)?.let { currentRoot = it.groupValues[1].toInt() }
            if (line.contains("RootTask id=") && line.contains("displayId=")) {
                Regex("displayId=(\\d+)").find(line)?.let {
                    if (it.groupValues[1].toInt() == displayId && currentRoot >= 0) return displayId
                }
            }
        }
        Log.w(TAG, "ensure: task move verification failed for $packageName")
        return displayId
    }

    /**
     * 截屏，返回 PNG 字节（失败为空数组）。
     * displayId < 0 表示主屏：不传 -d（vivo 上 `screencap -d 0` 会报 display 0 not valid）。
     */
    @JvmStatic
    fun screenshot(displayId: Int): ByteArray {
        if (displayId >= 0 && !isDisplayAlive(displayId)) {
            Log.w(TAG, "screenshot: display $displayId not alive")
            return ByteArray(0)
        }
        val cmd = if (displayId < 0) "screencap -p" else "screencap -p -d $displayId"
        val result = exec(cmd, 20000)
        return if (result.bytes.size > 8 && result.bytes[0] == 0x89.toByte()) result.bytes else ByteArray(0)
    }

    @JvmStatic
    fun tap(displayId: Int, x: Int, y: Int): Boolean {
        if (displayId >= 0 && !displayExists(displayId)) return false
        val cmd = if (displayId < 0) "input tap $x $y" else "input -d $displayId tap $x $y"
        // input 命令实际很快，但 ShizukuRemoteProcess 对已经执行完的短进程仍可能
        // 抛 "process hasn't exited"，exec 会返回 code=-2。真机上已实测点击生效，
        // 因此不能把这个超时回包当成注入失败，避免登录流程误判后反复重开。
        val result = exec(cmd, 1500)
        return result.code == 0 || result.code == -2
    }

    @JvmStatic
    @JvmOverloads
    fun swipe(displayId: Int, x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Int = 300): Boolean {
        if (displayId >= 0 && !displayExists(displayId)) return false
        val cmd = if (displayId < 0) "input swipe $x1 $y1 $x2 $y2 $durationMs"
        else "input -d $displayId swipe $x1 $y1 $x2 $y2 $durationMs"
        val timeout = (durationMs + 2000L).coerceAtLeast(3000L)
        val result = exec(cmd, timeout)
        return result.code == 0 || result.code == -2
    }

    /** 往指定虚拟屏发按键（绝不发给未指定 displayId 的主屏，防止后台返回键退掉用户前台 App）。 */
    @JvmStatic
    fun keyevent(displayId: Int, keyCode: Int): Boolean {
        if (displayId < 0 || !displayExists(displayId)) return false
        val result = exec("input -d $displayId keyevent $keyCode", 1500)
        return result.code == 0 || result.code == -2
    }

    @JvmStatic
    fun status(): String {
        val backend = when {
            hasPermission() -> "shizuku"
            usingRootFallback -> "root(dev-only)"
            else -> "none"
        }
        return "binder=${isBinderAvailable()} permission=${hasPermission()} backend=$backend"
    }
}
