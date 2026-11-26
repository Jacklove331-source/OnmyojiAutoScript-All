package com.oas.oasx

import android.content.ComponentName
import android.content.ServiceConnection
import android.os.IBinder
import android.os.Parcel
import android.util.Log
import org.json.JSONObject
import com.oas.oasx.shizuku.ShizukuShell
import rikka.shizuku.Shizuku
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * 通过 Shizuku user-service 的 Binder 通道取监控帧。
 *
 * MAA-Meow 的实测结论：App 与 shell 进程之间不能连 abstract socket，但可以把 Binder
 * 交给 App。因此这里不再启动 scrcpy-server：
 *   App -> Shizuku.bindUserService -> RemoteFrameService(shell uid)
 *   RemoteFrameService 建虚拟屏、读 ImageReader、压 JPEG
 *   Binder reply -> App -> /monitor/frame
 *
 * 虚拟屏和取帧都在 shell 进程，App 切后台不会停止出帧。
 */
object ScreenStream {

    private const val TAG = "ScreenStream"
    private const val MONITOR_FRAME_INTERVAL_MS = 250L
    private const val IDLE_HEARTBEAT_MS = 1000L
    private const val PLACEMENT_OK_INTERVAL_MS = 15_000L
    private const val PLACEMENT_RETRY_INTERVAL_MS = 3_000L
    private const val CONNECT_TIMEOUT_MS = 10_000L
    private const val REFERENCE_WIDTH = 1600
    private const val REFERENCE_HEIGHT = 900
    private const val REFERENCE_DPI = 240

    @Volatile private var running = false
    @Volatile private var lastError: String = ""
    @Volatile private var frameWidth = 0
    @Volatile private var frameHeight = 0
    @Volatile private var remoteDisplayId = -1
    @Volatile private var latestJpeg: ByteArray? = null
    @Volatile private var lastFrameAt = 0L
    @Volatile private var frameSequence = 0L
    @Volatile private var generation = 0L
    @Volatile private var targetPackage = ""
    @Volatile private var targetOnDisplay = false
    @Volatile private var remoteBinder: IBinder? = null
    @Volatile private var monitorActiveUntil = 0L

    private val transactionLock = Any()
    private val inputLock = Any()
    private val lifecycleLock = Any()
    private var serviceArgs: Shizuku.UserServiceArgs? = null
    private var connectedLatch: CountDownLatch? = null
    private var frameThread: Thread? = null

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            remoteBinder = service
            connectedLatch?.countDown()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            remoteBinder = null
            if (running) lastError = "binder disconnected"
            running = false
        }
    }

    @JvmStatic
    fun isRunning(): Boolean = running

    @JvmStatic
    fun displayId(): Int = remoteDisplayId

    @JvmStatic
    fun status(): String =
        "running=$running transport=binder displayId=$remoteDisplayId " +
            "size=${frameWidth}x$frameHeight jpeg=${latestJpeg?.size ?: 0} " +
            "age=${if (lastFrameAt == 0L) -1 else (System.currentTimeMillis() - lastFrameAt)}ms " +
            "error=$lastError"

    @JvmStatic
    fun frameInfo(): String = JSONObject().apply {
        put("running", running)
        put("transport", "binder")
        put("display_id", remoteDisplayId)
        put("width", frameWidth)
        put("height", frameHeight)
        put("frame_id", frameSequence)
        put("captured_at_ms", lastFrameAt)
        put("age_ms", if (lastFrameAt == 0L) -1 else System.currentTimeMillis() - lastFrameAt)
        put("error", lastError)
        put("target_package", targetPackage)
        put("target_on_display", targetOnDisplay)
    }.toString()

    @JvmStatic
    @JvmOverloads
    fun attachTarget(packageName: String, alreadyOnDisplay: Boolean = false) {
        require(Regex("[A-Za-z][A-Za-z0-9_.]*").matches(packageName))
        targetPackage = packageName
        targetOnDisplay = alreadyOnDisplay
    }

    @JvmStatic
    fun latestJpeg(): ByteArray? = latestJpeg

    /** 监控页活跃租约：仅在用户查看监控画面时开启连续低延迟推流，其余时间零空转省电。 */
    @JvmStatic
    @JvmOverloads
    fun touchMonitor(leaseMs: Long = 5000L): ByteArray? {
        monitorActiveUntil = System.currentTimeMillis() + leaseMs.coerceIn(1000L, 15_000L)
        val age = if (lastFrameAt == 0L) Long.MAX_VALUE else System.currentTimeMillis() - lastFrameAt
        if (latestJpeg == null || age > 600L) {
            captureNow(70)
        }
        return latestJpeg
    }

    /** 按需抓一帧（供任务 screenshot() / login 使用），避免空闲时持续 5fps 压 JPEG 耗电。 */
    @JvmStatic
    @JvmOverloads
    fun captureNow(quality: Int = 85): ByteArray? {
        if (!running) return null
        val binder = remoteBinder
        if (binder == null || !binder.isBinderAlive) return null
        val pkg = targetPackage
        if (pkg.isNotEmpty() && !targetOnDisplay && remoteDisplayId >= 0) {
            targetOnDisplay = try {
                ShizukuShell.moveTaskToDisplay(pkg, remoteDisplayId)
            } catch (error: Throwable) {
                lastError = "target placement: ${error.message}"
                false
            }
        }
        val jpeg = captureRemote(binder, quality)
        if (jpeg != null && jpeg.isNotEmpty()) {
            latestJpeg = jpeg
            lastFrameAt = System.currentTimeMillis()
            frameSequence++
            return jpeg
        }
        return null
    }

    /** 通过 Binder 在 shell 进程内直调 InputManager.injectInputEvent，免去每次点击 fork shell。 */
    @JvmStatic
    @JvmOverloads
    fun injectTap(x: Int, y: Int, durationMs: Int = 45): Boolean {
        val binder = remoteBinder
        if (!running || binder == null || !binder.isBinderAlive || remoteDisplayId < 0) return false
        synchronized(inputLock) {
            val data = Parcel.obtain()
            val reply = Parcel.obtain()
            return try {
                data.writeInterfaceToken(RemoteFrameService.DESCRIPTOR)
                data.writeInt(x)
                data.writeInt(y)
                data.writeInt(durationMs)
                if (!binder.transact(RemoteFrameService.TRANSACTION_TAP, data, reply, 0)) return false
                reply.readException()
                val ok = reply.readInt() == 1
                val error = reply.readString().orEmpty()
                if (!ok && error.isNotEmpty()) {
                    lastError = error
                    Log.w(TAG, "injectTap failed: $error")
                }
                ok
            } catch (error: Throwable) {
                Log.w(TAG, "injectTap transact failed: ${error.message}")
                false
            } finally {
                reply.recycle()
                data.recycle()
            }
        }
    }

    @JvmStatic
    @JvmOverloads
    fun injectSwipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Int = 300): Boolean {
        val binder = remoteBinder
        if (!running || binder == null || !binder.isBinderAlive || remoteDisplayId < 0) return false
        synchronized(inputLock) {
            val data = Parcel.obtain()
            val reply = Parcel.obtain()
            return try {
                data.writeInterfaceToken(RemoteFrameService.DESCRIPTOR)
                data.writeInt(x1)
                data.writeInt(y1)
                data.writeInt(x2)
                data.writeInt(y2)
                data.writeInt(durationMs)
                if (!binder.transact(RemoteFrameService.TRANSACTION_SWIPE, data, reply, 0)) return false
                reply.readException()
                val ok = reply.readInt() == 1
                val error = reply.readString().orEmpty()
                if (!ok && error.isNotEmpty()) {
                    lastError = error
                    Log.w(TAG, "injectSwipe failed: $error")
                }
                ok
            } catch (error: Throwable) {
                Log.w(TAG, "injectSwipe transact failed: ${error.message}")
                false
            } finally {
                reply.recycle()
                data.recycle()
            }
        }
    }

    @JvmStatic
    fun injectKeyEvent(keyCode: Int): Boolean {
        val binder = remoteBinder
        if (!running || binder == null || !binder.isBinderAlive || remoteDisplayId < 0) return false
        synchronized(inputLock) {
            val data = Parcel.obtain()
            val reply = Parcel.obtain()
            return try {
                data.writeInterfaceToken(RemoteFrameService.DESCRIPTOR)
                data.writeInt(keyCode)
                if (!binder.transact(RemoteFrameService.TRANSACTION_KEYEVENT, data, reply, 0)) return false
                reply.readException()
                val ok = reply.readInt() == 1
                val error = reply.readString().orEmpty()
                if (!ok && error.isNotEmpty()) {
                    lastError = error
                    Log.w(TAG, "injectKeyEvent failed: $error")
                }
                ok
            } catch (error: Throwable) {
                Log.w(TAG, "injectKeyEvent transact failed: ${error.message}")
                false
            } finally {
                reply.recycle()
                data.recycle()
            }
        }
    }

    /**
     * 启动 Binder 帧服务。displayId 参数保留给 Python 接口兼容；新服务始终创建由 shell
     * 持有的虚拟屏，并通过 [displayId] 暴露实际 id，供上层把游戏搬到这块屏。
     */
    @JvmStatic
    fun start(width: Int = 1280, height: Int = 720, displayId: Int = -1): Boolean {
        synchronized(lifecycleLock) {
            if (running) return true
            lastError = ""
            latestJpeg = null
            lastFrameAt = 0L

            val context = VdHostDisplay.appContext()
            if (context == null) {
                lastError = "no application context"
                return false
            }
            if (!ShizukuShell.isBinderAvailable() || !ShizukuShell.hasPermission()) {
                lastError = "shizuku unavailable"
                return false
            }

            stopInternal(setError = false)
            // 起新流之前先清掉历史遗留的 frame 进程/虚拟屏（否则会一路累积到上百个，
            // 表现为「关掉 OAS 也退不出去」）。上一次的进程已经 stop，这里杀的都是僵尸。
            try {
                ShizukuShell.killStaleFrameProcesses()
            } catch (_: Throwable) {
            }
            return try {
                val args = Shizuku.UserServiceArgs(
                    ComponentName(
                        context.packageName,
                        RemoteFrameService::class.java.name,
                    )
                )
                    .daemon(false)
                    .processNameSuffix("frame")
                    .version(8)
                    .debuggable(false)
                serviceArgs = args

                val latch = CountDownLatch(1)
                connectedLatch = latch
                Shizuku.bindUserService(args, connection)
                if (!latch.await(CONNECT_TIMEOUT_MS, TimeUnit.MILLISECONDS)) {
                    lastError = "bind user service timeout"
                    stopInternal(setError = false)
                    return false
                }
                val binder = remoteBinder
                if (binder == null || !binder.isBinderAlive) {
                    lastError = "frame service binder unavailable"
                    stopInternal(setError = false)
                    return false
                }

                var captureWidth = width
                var captureHeight = height
                var captureDpi = 0
                if (android.os.Build.VERSION.SDK_INT >= 34 && width == 1280 && height == 720) {
                    // Android 14+（含 OriginOS 6 / Android 16 等真机）走 VDM 独立虚拟屏：
                    // 直接按 OAS 模板参考视口 1600x900@240dpi（666x375dp）建屏，
                    // 彻底摆脱改主屏 wm size/density，也不继承真机 480~560dpi 把 UI 撑爆。
                    captureWidth = REFERENCE_WIDTH
                    captureHeight = REFERENCE_HEIGHT
                    captureDpi = REFERENCE_DPI
                } else if (android.os.Build.VERSION.SDK_INT < 34 && width == 1280 && height == 720) {
                    val manager = context.getSystemService(android.content.Context.DISPLAY_SERVICE)
                        as android.hardware.display.DisplayManager
                    val metrics = android.util.DisplayMetrics()
                    manager.getDisplay(android.view.Display.DEFAULT_DISPLAY)?.getRealMetrics(metrics)
                    val longEdge = maxOf(metrics.widthPixels, metrics.heightPixels)
                    val shortEdge = minOf(metrics.widthPixels, metrics.heightPixels)
                    if (longEdge in 320..2560 && shortEdge in 240..2560) {
                        // Android <14（如 MuMu Android 12）保持与主屏几何一致，避免搬屏重建 Activity。
                        captureWidth = longEdge
                        captureHeight = shortEdge
                    }
                }
                // Some native games retain main-display metrics for input. Keep their
                // real geometry; the Python device normalizes frames and coordinates.
                val started = startRemote(binder, captureWidth, captureHeight, captureDpi)
                if (!started) {
                    stopInternal(setError = false)
                    return false
                }

                running = true
                val token = ++generation
                frameThread = Thread({ pollFrames(token) }, "oas-frame-binder").also {
                    it.isDaemon = true
                    it.start()
                }
                Log.i(
                    TAG,
                    "binder frame service started display=$remoteDisplayId ${frameWidth}x$frameHeight",
                )
                true
            } catch (error: Throwable) {
                lastError = "start: ${error.javaClass.simpleName}: ${error.message}"
                Log.e(TAG, lastError, error)
                stopInternal(setError = false)
                false
            }
        }
    }

    @JvmStatic
    fun stop(): Boolean {
        synchronized(lifecycleLock) {
            stopInternal(setError = true)
            return true
        }
    }

    private fun stopInternal(setError: Boolean) {
        generation++
        running = false
        targetPackage = ""
        targetOnDisplay = false
        try {
            frameThread?.interrupt()
            frameThread?.join(500)
        } catch (_: Throwable) {
        }
        frameThread = null

        val binder = remoteBinder
        if (binder != null) {
            try {
                val data = Parcel.obtain()
                val reply = Parcel.obtain()
                try {
                    data.writeInterfaceToken(RemoteFrameService.DESCRIPTOR)
                    binder.transact(RemoteFrameService.TRANSACTION_STOP, data, reply, 0)
                    reply.readException()
                    reply.readInt()
                } finally {
                    reply.recycle()
                    data.recycle()
                }
            } catch (_: Throwable) {
            }
        }

        val args = serviceArgs
        if (args != null) {
            try {
                Shizuku.unbindUserService(args, connection, true)
            } catch (_: Throwable) {
            }
        }
        serviceArgs = null
        connectedLatch = null
        remoteBinder = null
        remoteDisplayId = -1
        frameWidth = 0
        frameHeight = 0
        latestJpeg = null
        lastFrameAt = 0L
        if (setError) lastError = ""
    }

    private fun startRemote(binder: IBinder, width: Int, height: Int, dpi: Int = 0): Boolean {
        val data = Parcel.obtain()
        val reply = Parcel.obtain()
        return try {
            data.writeInterfaceToken(RemoteFrameService.DESCRIPTOR)
            data.writeInt(width)
            data.writeInt(height)
            // 第三个参数是 densityDpi：0 = 跟随物理屏（会随手机 dpi 变化毁掉模板匹配），
            // 非 0 = 用参考视口的固定密度（1600x900 @240）。
            data.writeInt(dpi)
            if (!binder.transact(RemoteFrameService.TRANSACTION_START, data, reply, 0)) {
                lastError = "start transact returned false"
                return false
            }
            reply.readException()
            val ok = reply.readInt() == 1
            remoteDisplayId = reply.readInt()
            frameWidth = reply.readInt()
            frameHeight = reply.readInt()
            lastError = reply.readString().orEmpty()
            ok && remoteDisplayId >= 0
        } catch (error: Throwable) {
            lastError = "start transact: ${error.javaClass.simpleName}: ${error.message}"
            false
        } finally {
            reply.recycle()
            data.recycle()
        }
    }

    private fun captureRemote(binder: IBinder, quality: Int = 70): ByteArray? {
        synchronized(transactionLock) {
            val data = Parcel.obtain()
            val reply = Parcel.obtain()
            return try {
                data.writeInterfaceToken(RemoteFrameService.DESCRIPTOR)
                data.writeInt(quality)
                if (!binder.transact(RemoteFrameService.TRANSACTION_CAPTURE, data, reply, 0)) {
                    lastError = "capture transact returned false"
                    return null
                }
                reply.readException()
                val failed = reply.readInt() != 0
                val error = reply.readString().orEmpty()
                val jpeg = reply.createByteArray()
                if (failed) {
                    lastError = error
                    null
                } else {
                    if (error.isEmpty() && jpeg != null && jpeg.isNotEmpty()) lastError = ""
                    jpeg
                }
            } catch (error: Throwable) {
                lastError = "capture transact: ${error.javaClass.simpleName}: ${error.message}"
                null
            } finally {
                reply.recycle()
                data.recycle()
            }
        }
    }

    private fun pollFrames(token: Long) {
        var lastPlacementCheck = 0L
        while (token == generation && running && !Thread.currentThread().isInterrupted) {
            val startedAt = System.currentTimeMillis()
            val binder = remoteBinder
            if (binder == null || !binder.isBinderAlive) {
                if (running) {
                    lastError = "frame service binder died"
                    running = false
                }
                return
            }
            val packageName = targetPackage
            val placementInterval = if (targetOnDisplay) PLACEMENT_OK_INTERVAL_MS else PLACEMENT_RETRY_INTERVAL_MS
            if (packageName.isNotEmpty() && startedAt - lastPlacementCheck >= placementInterval) {
                lastPlacementCheck = startedAt
                targetOnDisplay = try {
                    ShizukuShell.moveTaskToDisplay(packageName, remoteDisplayId)
                } catch (error: Throwable) {
                    lastError = "target placement: ${error.message}"
                    false
                }
            }
            val monitoring = startedAt < monitorActiveUntil
            if (monitoring) {
                val jpeg = captureRemote(binder, quality = 70)
                if (token != generation || !running) return
                if (jpeg != null && jpeg.isNotEmpty()) {
                    latestJpeg = jpeg
                    lastFrameAt = System.currentTimeMillis()
                    frameSequence++
                }
            }
            val elapsed = System.currentTimeMillis() - startedAt
            val targetInterval = if (monitoring) MONITOR_FRAME_INTERVAL_MS else IDLE_HEARTBEAT_MS
            val sleepMs = (targetInterval - elapsed).coerceAtLeast(50L)
            try {
                Thread.sleep(sleepMs)
            } catch (_: InterruptedException) {
                return
            }
        }
    }
}
