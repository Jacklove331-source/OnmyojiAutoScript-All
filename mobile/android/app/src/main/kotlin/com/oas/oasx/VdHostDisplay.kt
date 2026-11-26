package com.oas.oasx

import android.content.Context
import android.util.Log
import com.oas.oasx.shizuku.ShizukuShell
import java.io.BufferedReader
import java.io.InputStreamReader

/**
 * 无头虚拟屏宿主（shell uid，app_process 跑 [com.oas.oasx.host.VdHost]）。
 *
 * 优势：屏的 owner 是 shell 进程，**应用切后台也不会停帧**，且主屏完全看不到。
 * overlay 屏会在主屏渲染小窗，MediaProjection 屏后台会停帧，所以这条路才是「真·后台」。
 */
object VdHostDisplay {

    private const val TAG = "VdHostDisplay"
    private const val HOST_CLASS = "com.oas.oasx.host.VdHost"

    /** MainActivity 启动时写入，供 Python 侧调用 start() 时使用。 */
    @Volatile
    var application: Context? = null

    @Volatile
    private var process: Process? = null

    @Volatile
    private var displayId: Int = -1

    @Volatile
    private var lastError: String = ""

    /** 宿主 stdout 的最近若干行（PROBE=/HOST_ERROR= 等），排障时直接看，不用连 logcat。 */
    private val probeLog = java.util.ArrayDeque<String>()

    /** 当前 APK 路径（app_process 的 CLASSPATH 指向它即可加载我们的 classes.dex）。 */
    private fun apkPath(context: Context): String {
        return try {
            context.applicationInfo.publicSourceDir ?: context.applicationInfo.sourceDir
        } catch (error: Throwable) {
            lastError = "apkPath failed: ${error.message}"
            ""
        }
    }


    /** 给 ScreenStream 用：拿本 APK 路径（app_process 的 CLASSPATH）。 */
    @JvmStatic
    fun apkPathForStream(): String {
        val context = application ?: return ""
        return apkPath(context)
    }


    /** 给其它组件取 Application Context（Chaquopy 侧不方便直连）。 */
    @JvmStatic
    fun appContext(): android.content.Context? = application

    @JvmStatic
    fun isRunning(): Boolean {
        val proc = process ?: return false
        return try {
            // Java 8 的 Process 没有 isAlive，用 exitValue 检测。
            // 注意：Shizuku 的 ShizukuRemoteProcess 在进程未退出时抛的是
            // IllegalArgumentException("process hasn't exited")（不是 IllegalThreadStateException），
            // 只捕获后者会让异常一路冒到 Python，表现为 status() 直接抛错。
            proc.exitValue()
            false
        } catch (_: IllegalThreadStateException) {
            true
        } catch (_: IllegalArgumentException) {
            true
        } catch (_: Throwable) {
            false
        }
    }

    @JvmStatic
    fun displayId(): Int = displayId

    @JvmStatic
    fun status(): String {
        val tail = synchronized(probeLog) { probeLog.joinToString(" | ") }
        return "running=${isRunning()} displayId=$displayId error=$lastError probe=$tail"
    }

    /** 启动宿主并返回 displayId；失败 -1。 */
    @JvmStatic
    fun start(width: Int = 1280, height: Int = 720, dpi: Int = 240): Int {
        val context = application
        if (context == null) {
            lastError = "no application context"
            return -1
        }
        if (isRunning() && displayId >= 0) return displayId
        stop()

        val apk = apkPath(context)
        if (apk.isEmpty()) {
            lastError = "apkPath empty"
            Log.e(TAG, "apkPath empty, cannot start host")
            return -1
        }

        if (!ShizukuShell.isBinderAvailable() || !ShizukuShell.hasPermission()) {
            lastError = "shizuku unavailable"
            return -1
        }

        return try {
            // 关键：MuMu 的 Shizuku 是 root 启动的，newProcess 出来是 uid=0，
            // 而 DisplayManagerService 校验「packageName 必须匹配调用 uid」(root 没有包名) →
            // 用 `su 2000` 把宿主降到 shell uid 再跑。
            val inner = "CLASSPATH=$apk exec app_process /system/bin $HOST_CLASS $width $height $dpi"
            // 真机没 root：Shizuku 若是 ADB 起的，newProcess 本来就是 uid 2000(shell)，不能再套 su；
            // 只有 root 起的 Shizuku（uid 0）才需要 su 2000 降权到 shell。
            // 不在这里跑 exec 探测（exec 对短命令会走到超时分支抛 "process hasn't exited"）：
            // 只看 su 是否存在。真机没 su 就是 ADB 起的 Shizuku（本来就是 uid 2000 shell），直接跑；
            // 模拟器是 root 起的 Shizuku（uid 0），需要 su 2000 降权。
            val hasSu = java.io.File("/system/xbin/su").exists() ||
                java.io.File("/system/bin/su").exists() ||
                java.io.File("/su/bin/su").exists()
            val command = if (hasSu) "su 2000 sh -c '$inner'" else "sh -c '$inner'"
            Log.i(TAG, "start host hasSu=$hasSu cmd=$command")
            val created = ShizukuShell.startLongRunning(command)
            if (created == null) {
                lastError = "startLongRunning returned null"
                return -1
            }
            process = created

            // 等宿主打印 DISPLAY_ID=<n>
            val reader = BufferedReader(InputStreamReader(created.inputStream))
            val deadline = System.currentTimeMillis() + 15_000
            var parsed = -1
            while (System.currentTimeMillis() < deadline) {
                val line = reader.readLine() ?: break
                Log.i(TAG, "host: $line")
                synchronized(probeLog) {
                    probeLog.addLast(line)
                    while (probeLog.size > 12) probeLog.removeFirst()
                }
                if (line.startsWith("DISPLAY_ID=")) {
                    parsed = line.removePrefix("DISPLAY_ID=").trim().toIntOrNull() ?: -1
                    break
                }
                if (line.startsWith("HOST_ERROR=")) {
                    lastError = line
                    break
                }
            }
            displayId = parsed
            if (parsed < 0) lastError = lastError.ifEmpty { "no DISPLAY_ID from host" }
            Log.i(TAG, "host display=$parsed err=$lastError")
            parsed
        } catch (error: Throwable) {
            lastError = "start failed: ${error.javaClass.simpleName}: ${error.message}"
            Log.e(TAG, "start host failed", error)
            -1
        }
    }

    /** 从宿主进程的帧服务取一帧 PNG（本地 socket，后台也能取）。 */
    @JvmStatic
    fun capturePng(timeoutMs: Int = 3000): ByteArray? {
        var socket: android.net.LocalSocket? = null
        return try {
            val client = android.net.LocalSocket()
            socket = client
            client.connect(android.net.LocalSocketAddress("oas.vdhost.frame"))
            client.soTimeout = timeoutMs
            val output = client.outputStream
            output.write(byteArrayOf('F'.code.toByte(), 'R'.code.toByte(), 'A'.code.toByte(),
                'M'.code.toByte(), 'E'.code.toByte(), '\n'.code.toByte()))
            output.flush()
            val input = client.inputStream
            val header = ByteArray(4)
            var stage = 0
            while (stage < 4) {
                val count = input.read(header, stage, 4 - stage)
                if (count <= 0) return null
                stage += count
            }
            val length = ((header[0].toInt() and 0xFF) shl 24) or
                ((header[1].toInt() and 0xFF) shl 16) or
                ((header[2].toInt() and 0xFF) shl 8) or
                (header[3].toInt() and 0xFF)
            if (length <= 0 || length > 12 * 1024 * 1024) return null
            val payload = ByteArray(length)
            var filled = 0
            while (filled < length) {
                val count = input.read(payload, filled, length - filled)
                if (count <= 0) return null
                filled += count
            }
            payload
        } catch (error: Throwable) {
            Log.w(TAG, "capturePng failed: " + error.message)
            null
        } finally {
            try {
                socket?.close()
            } catch (_: Throwable) {
            }
        }
    }

    @JvmStatic
    fun stop(): Boolean {
        val proc = process
        process = null
        displayId = -1
        if (proc == null) return true
        return try {
            proc.destroy()
            true
        } catch (error: Throwable) {
            Log.w(TAG, "destroy host failed", error)
            false
        }
    }
}
