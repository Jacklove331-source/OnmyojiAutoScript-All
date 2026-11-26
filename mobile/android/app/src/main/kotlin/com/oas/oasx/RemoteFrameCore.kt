package com.oas.oasx

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.hardware.input.InputManager
import android.media.ImageReader
import android.os.Build
import android.os.IBinder
import android.os.Parcel
import android.os.SystemClock
import android.util.DisplayMetrics
import android.util.Log
import android.view.Display
import android.view.InputDevice
import android.view.InputEvent
import android.view.KeyCharacterMap
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.Surface
import java.io.ByteArrayOutputStream
import java.lang.reflect.Method

/**
 * 运行在 Shizuku user-service 进程（uid=shell）里的帧服务。
 *
 * 这类 Binder 不注册到 Android ServiceManager，而是由 Shizuku 直接实例化并把
 * Binder 交给 App，因此跨 uid 通信走 Binder，不走会被 SELinux 拦截的 abstract socket。
 *
 * 建屏和取帧都在 shell 进程内完成：
 * DisplayManager.createVirtualDisplay(..., ImageReader.surface)
 *   -> ImageReader.acquireLatestImage()
 *   -> Bitmap.compress(JPEG)
 *   -> Binder reply 返回 App
 */
class RemoteFrameCore {

    companion object {
        private const val TAG = "OasFrameService"
        // 与 MAA-Meow VirtualDisplayManager 一致（隐藏位从 bit6 开始）：
        // PUBLIC(1) + OWN_CONTENT_ONLY(8) + SUPPORTS_TOUCH(1<<6)
        // + DESTROY_CONTENT_ON_REMOVAL(1<<8) = 0x149
        private const val BASE_DISPLAY_FLAGS = 1 or 8 or (1 shl 6) or (1 shl 8)
        private const val TRUSTED = 1 shl 10
        private const val OWN_DISPLAY_GROUP = 1 shl 11
        private const val ALWAYS_UNLOCKED = 1 shl 12
        private const val TOUCH_FEEDBACK_DISABLED = 1 shl 13
        private const val OWN_FOCUS = 1 shl 14
        private const val DEVICE_DISPLAY_GROUP = 1 shl 15
        private const val STEAL_TOP_FOCUS_DISABLED = 1 shl 16
        const val DESCRIPTOR = "com.oas.oasx.RemoteFrameService"

        const val TRANSACTION_START = IBinder.FIRST_CALL_TRANSACTION
        const val TRANSACTION_CAPTURE = IBinder.FIRST_CALL_TRANSACTION + 1
        const val TRANSACTION_STOP = IBinder.FIRST_CALL_TRANSACTION + 2
        const val TRANSACTION_STATUS = IBinder.FIRST_CALL_TRANSACTION + 3
        const val TRANSACTION_PING = IBinder.FIRST_CALL_TRANSACTION + 4
        const val TRANSACTION_TAP = IBinder.FIRST_CALL_TRANSACTION + 5
        const val TRANSACTION_SWIPE = IBinder.FIRST_CALL_TRANSACTION + 6
        const val TRANSACTION_KEYEVENT = IBinder.FIRST_CALL_TRANSACTION + 7
        private const val INJECT_INPUT_EVENT_MODE_ASYNC = 0
    }

    private val stateLock = Any()
    private val captureLock = Any()
    private val inputLock = Any()

    @Volatile private var imageReader: ImageReader? = null
    @Volatile private var virtualDisplay: VirtualDisplay? = null
    @Volatile private var vdmSession: VdmDisplay.Session? = null
    @Volatile private var displayId: Int = -1
    @Volatile private var frameWidth: Int = 0
    @Volatile private var frameHeight: Int = 0
    @Volatile private var lastFrameAt: Long = 0L
    @Volatile private var lastError: String = ""
    @Volatile private var activeDisplayFlags: Int = 0

    private var createVirtualDisplayMethod: Method? = null
    private var inputManagerInstance: Any? = null
    private var injectInputEventMethod: Method? = null
    private var setDisplayIdMethod: Method? = null

    fun onTransact(
        code: Int,
        data: Parcel,
        reply: Parcel,
        flags: Int,
    ): Boolean {
        return when (code) {
            TRANSACTION_START -> {
                data.enforceInterface(DESCRIPTOR)
                val width = data.readInt().coerceIn(320, 2560)
                val height = data.readInt().coerceIn(240, 2560)
                val dpi = data.readInt().coerceIn(0, 640)
                val ok = startDisplay(width, height, dpi)
                reply.writeNoException()
                reply.writeInt(if (ok) 1 else 0)
                reply.writeInt(displayId)
                reply.writeInt(frameWidth)
                reply.writeInt(frameHeight)
                reply.writeString(lastError)
                true
            }

            TRANSACTION_CAPTURE -> {
                data.enforceInterface(DESCRIPTOR)
                val quality = data.readInt()
                val jpeg = captureJpeg(quality)
                reply.writeNoException()
                reply.writeInt(if (jpeg == null) 1 else 0)
                reply.writeString(lastError)
                reply.writeByteArray(jpeg)
                true
            }

            TRANSACTION_STOP -> {
                data.enforceInterface(DESCRIPTOR)
                stopDisplay()
                releaseStaleDisplays()
                reply.writeNoException()
                reply.writeInt(1)
                // 关键：STOP 之后这个 shell 身份的 frame 进程必须自己退出，否则每次启停都会留一个
                // 进程 + 若干虚拟屏（实测 2026-11-22 累积到 **28 个进程 / 101 个虚拟屏**，
                // 表现就是「关掉 OAS 也退不出去、看不到游戏」）。
                // 延迟 800ms 是等 binder 回复先送出去。
                Thread {
                    try {
                        Thread.sleep(800)
                    } catch (_: Throwable) {
                    }
                    if (virtualDisplay == null) {
                        Log.i(TAG, "stopping frame process after STOP")
                        android.os.Process.killProcess(android.os.Process.myPid())
                    }
                }.start()
                true
            }

            TRANSACTION_STATUS -> {
                data.enforceInterface(DESCRIPTOR)
                reply.writeNoException()
                reply.writeInt(displayId)
                reply.writeInt(frameWidth)
                reply.writeInt(frameHeight)
                reply.writeLong(lastFrameAt)
                reply.writeString(lastError)
                true
            }

            TRANSACTION_PING -> {
                data.enforceInterface(DESCRIPTOR)
                reply.writeNoException()
                reply.writeString("oas-frame-binder pid=${android.os.Process.myPid()} uid=${android.os.Process.myUid()}")
                true
            }

            TRANSACTION_TAP -> {
                data.enforceInterface(DESCRIPTOR)
                val x = data.readInt()
                val y = data.readInt()
                val durationMs = data.readInt().coerceIn(20, 5000)
                val error = injectTap(x, y, durationMs)
                reply.writeNoException()
                reply.writeInt(if (error == null) 1 else 0)
                reply.writeString(error ?: "")
                true
            }

            TRANSACTION_SWIPE -> {
                data.enforceInterface(DESCRIPTOR)
                val x1 = data.readInt()
                val y1 = data.readInt()
                val x2 = data.readInt()
                val y2 = data.readInt()
                val durationMs = data.readInt().coerceIn(40, 5000)
                val error = injectSwipe(x1, y1, x2, y2, durationMs)
                reply.writeNoException()
                reply.writeInt(if (error == null) 1 else 0)
                reply.writeString(error ?: "")
                true
            }

            TRANSACTION_KEYEVENT -> {
                data.enforceInterface(DESCRIPTOR)
                val keyCode = data.readInt()
                val error = injectKeyEvent(keyCode)
                reply.writeNoException()
                reply.writeInt(if (error == null) 1 else 0)
                reply.writeString(error ?: "")
                true
            }

            else -> false
        }
    }

    private fun startDisplay(width: Int, height: Int, dpi: Int): Boolean {
        synchronized(stateLock) {
            releaseLocked()
            // 建新屏之前先把历史遗留的 oas-* 虚拟屏清掉（老版本泄漏的僵尸屏会拖住系统，
            // 也会让 moveTaskToDisplay 把游戏搬进没人看的屏）。
            try {
                releaseStaleDisplaysUnlocked()
            } catch (_: Throwable) {
            }
            lastError = ""
            lastFrameAt = 0L
            return try {
                val reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3)
                imageReader = reader

                val manager = ShellContext.getDisplayManager()
                val mainMetrics = DisplayMetrics()
                manager.getDisplay(Display.DEFAULT_DISPLAY)?.getRealMetrics(mainMetrics)
                // Onmyoji does not handle CONFIG_DENSITY. Matching the main display's
                // density avoids destroying its native Activity when it changes displays.
                val displayDpi = if (dpi > 0) dpi else mainMetrics.densityDpi.coerceAtLeast(160)
                var display: VirtualDisplay? = null
                var lastFailure: Throwable? = null
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                    try {
                        val session = VdmDisplay.start(width, height, displayDpi, reader.surface)
                        vdmSession = session
                        display = session.display
                        activeDisplayFlags = session.flags
                        Log.i(TAG, "VDM backend selected flags=0x${session.flags.toString(16)}")
                    } catch (failure: Throwable) {
                        lastFailure = failure
                        Log.w(TAG, "VDM backend failed, trying DisplayManager", failure)
                    }
                }
                if (display == null) {
                    for ((tier, tierFlags) in displayFlagTiers().withIndex()) {
                        val flags = BASE_DISPLAY_FLAGS or tierFlags.fold(0, Int::or)
                        try {
                            display = manager.createVirtualDisplay(
                                "oas-frame-binder",
                                width,
                                height,
                                displayDpi,
                                reader.surface,
                                flags,
                            )
                            activeDisplayFlags = flags
                            Log.i(TAG, "VD created tier=$tier flags=0x${flags.toString(16)}")
                            break
                        } catch (failure: SecurityException) {
                            lastFailure = failure
                            Log.w(TAG, "VD tier=$tier rejected flags=0x${flags.toString(16)}", failure)
                        }
                    }
                }
                if (display == null && lastFailure != null) {
                    Log.w(TAG, "public createVirtualDisplay failed, trying hidden 5-arg", lastFailure)
                    // 兜底保持 MAA-Meow 的调用方式；但 5 参版本通常只得到镜像屏，
                    // 不能承载后台任务，正常路径应走上面的带 flags 公共 API。
                    val method = createVirtualDisplayMethod ?: DisplayManager::class.java.getMethod(
                        "createVirtualDisplay",
                        String::class.java,
                        Int::class.javaPrimitiveType,
                        Int::class.javaPrimitiveType,
                        Int::class.javaPrimitiveType,
                        Surface::class.java,
                    ).also { createVirtualDisplayMethod = it }
                    display = method.invoke(
                        null,
                        "oas-frame-binder",
                        width,
                        height,
                        displayDpi,
                        reader.surface,
                    ) as? VirtualDisplay
                    activeDisplayFlags = 0
                }
                val createdDisplay = display ?: throw IllegalStateException(
                    "createVirtualDisplay returned null: ${lastFailure?.message}"
                )

                virtualDisplay = createdDisplay
                displayId = createdDisplay.display.displayId
                frameWidth = width
                frameHeight = height
                Log.i(
                    TAG,
                    "display ready id=$displayId ${width}x$height dpi=$displayDpi " +
                        "flags=0x${activeDisplayFlags.toString(16)} uid=${android.os.Process.myUid()}",
                )
                true
            } catch (error: Throwable) {
                val cause = if (error is java.lang.reflect.InvocationTargetException) {
                    error.targetException
                } else {
                    error
                }
                lastError = "start: ${cause.javaClass.simpleName}: ${cause.message}"
                Log.e(TAG, lastError, cause)
                releaseLocked()
                false
            }
        }
    }

    private fun captureJpeg(quality: Int): ByteArray? {
        synchronized(captureLock) {
            val reader = imageReader
            if (reader == null) {
                lastError = "capture: display not started"
                return null
            }
            val image = try {
                reader.acquireLatestImage()
            } catch (error: Throwable) {
                lastError = "capture: ${error.javaClass.simpleName}: ${error.message}"
                return null
            } ?: run {
                lastError = "capture: no frame yet"
                return null
            }

            var rawBitmap: Bitmap? = null
            var outputBitmap: Bitmap? = null
            return try {
                val plane = image.planes[0]
                val buffer = plane.buffer
                buffer.rewind()
                val pixelStride = plane.pixelStride.coerceAtLeast(1)
                val storedWidth = (plane.rowStride / pixelStride).coerceAtLeast(image.width)
                rawBitmap = Bitmap.createBitmap(
                    storedWidth,
                    image.height,
                    Bitmap.Config.ARGB_8888,
                )
                rawBitmap.copyPixelsFromBuffer(buffer)
                outputBitmap = if (storedWidth != image.width) {
                    Bitmap.createBitmap(rawBitmap, 0, 0, image.width, image.height)
                } else {
                    rawBitmap
                }

                val stream = ByteArrayOutputStream()
                val ok = outputBitmap.compress(
                    Bitmap.CompressFormat.JPEG,
                    quality.coerceIn(30, 90),
                    stream,
                )
                if (!ok) {
                    lastError = "capture: JPEG compress failed"
                    null
                } else {
                    lastFrameAt = System.currentTimeMillis()
                    lastError = ""
                    stream.toByteArray()
                }
            } catch (error: Throwable) {
                lastError = "capture: ${error.javaClass.simpleName}: ${error.message}"
                Log.e(TAG, lastError, error)
                null
            } finally {
                if (outputBitmap != null && outputBitmap !== rawBitmap) {
                    outputBitmap.recycle()
                }
                rawBitmap?.recycle()
                image.close()
            }
        }
    }

    @SuppressLint("BlockedPrivateApi", "PrivateApi", "DiscouragedPrivateApi")
    private fun ensureInputInjector(): Pair<Any, Method> {
        val cachedManager = inputManagerInstance
        val cachedMethod = injectInputEventMethod
        if (cachedManager != null && cachedMethod != null) {
            return cachedManager to cachedMethod
        }
        val manager = try {
            Class.forName("android.hardware.input.InputManagerGlobal")
                .getDeclaredMethod("getInstance")
                .apply { isAccessible = true }
                .invoke(null)
        } catch (_: Throwable) {
            null
        } ?: InputManager::class.java
            .getDeclaredMethod("getInstance")
            .apply { isAccessible = true }
            .invoke(null)
            ?: throw IllegalStateException("InputManager instance unavailable")
        val inject = manager.javaClass
            .getDeclaredMethod(
                "injectInputEvent",
                InputEvent::class.java,
                Int::class.javaPrimitiveType,
            )
            .apply { isAccessible = true }
        inputManagerInstance = manager
        injectInputEventMethod = inject
        return manager to inject
    }

    @SuppressLint("BlockedPrivateApi", "PrivateApi", "DiscouragedPrivateApi")
    private fun bindEventDisplay(event: InputEvent, targetDisplayId: Int) {
        if (targetDisplayId < 0) return
        val setter = setDisplayIdMethod ?: InputEvent::class.java
            .getDeclaredMethod("setDisplayId", Int::class.javaPrimitiveType)
            .apply { isAccessible = true }
            .also { setDisplayIdMethod = it }
        setter.invoke(event, targetDisplayId)
    }

    private fun injectSingleEvent(event: InputEvent, targetDisplayId: Int): Boolean {
        val (manager, inject) = ensureInputInjector()
        bindEventDisplay(event, targetDisplayId)
        return (inject.invoke(manager, event, INJECT_INPUT_EVENT_MODE_ASYNC) as? Boolean) == true
    }

    private fun buildTouchEvent(
        downTime: Long,
        eventTime: Long,
        action: Int,
        x: Float,
        y: Float,
        pressure: Float,
    ): MotionEvent {
        val event = MotionEvent.obtain(
            downTime,
            eventTime,
            action,
            x,
            y,
            pressure,
            1.0f,
            0,
            1.0f,
            1.0f,
            0,
            0,
        )
        event.source = InputDevice.SOURCE_TOUCHSCREEN
        return event
    }

    private fun injectTap(x: Int, y: Int, durationMs: Int): String? {
        synchronized(inputLock) {
            val targetDisplay = displayId
            if (targetDisplay < 0) return "tap: display not started"
            return try {
                val downTime = SystemClock.uptimeMillis()
                val down = buildTouchEvent(downTime, downTime, MotionEvent.ACTION_DOWN, x.toFloat(), y.toFloat(), 1.0f)
                val okDown = try {
                    injectSingleEvent(down, targetDisplay)
                } finally {
                    down.recycle()
                }
                if (!okDown) return "tap: ACTION_DOWN rejected"
                SystemClock.sleep(durationMs.toLong())
                val upTime = SystemClock.uptimeMillis()
                val up = buildTouchEvent(downTime, upTime, MotionEvent.ACTION_UP, x.toFloat(), y.toFloat(), 0.0f)
                val okUp = try {
                    injectSingleEvent(up, targetDisplay)
                } finally {
                    up.recycle()
                }
                if (!okUp) "tap: ACTION_UP rejected" else null
            } catch (error: Throwable) {
                val cause = if (error is java.lang.reflect.InvocationTargetException) error.targetException else error
                "tap: ${cause.javaClass.simpleName}: ${cause.message}"
            }
        }
    }

    private fun injectSwipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Int): String? {
        synchronized(inputLock) {
            val targetDisplay = displayId
            if (targetDisplay < 0) return "swipe: display not started"
            return try {
                val steps = (durationMs / 16).coerceIn(4, 60)
                val stepDelay = (durationMs.toLong() / steps).coerceAtLeast(8L)
                val downTime = SystemClock.uptimeMillis()
                val down = buildTouchEvent(downTime, downTime, MotionEvent.ACTION_DOWN, x1.toFloat(), y1.toFloat(), 1.0f)
                val okDown = try {
                    injectSingleEvent(down, targetDisplay)
                } finally {
                    down.recycle()
                }
                if (!okDown) return "swipe: ACTION_DOWN rejected"
                for (step in 1 until steps) {
                    SystemClock.sleep(stepDelay)
                    val fraction = step.toFloat() / steps.toFloat()
                    val cx = x1 + (x2 - x1) * fraction
                    val cy = y1 + (y2 - y1) * fraction
                    val move = buildTouchEvent(downTime, SystemClock.uptimeMillis(), MotionEvent.ACTION_MOVE, cx, cy, 1.0f)
                    try {
                        injectSingleEvent(move, targetDisplay)
                    } finally {
                        move.recycle()
                    }
                }
                SystemClock.sleep(stepDelay)
                val up = buildTouchEvent(downTime, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, x2.toFloat(), y2.toFloat(), 0.0f)
                val okUp = try {
                    injectSingleEvent(up, targetDisplay)
                } finally {
                    up.recycle()
                }
                if (!okUp) "swipe: ACTION_UP rejected" else null
            } catch (error: Throwable) {
                val cause = if (error is java.lang.reflect.InvocationTargetException) error.targetException else error
                "swipe: ${cause.javaClass.simpleName}: ${cause.message}"
            }
        }
    }

    private fun injectKeyEvent(keyCode: Int): String? {
        synchronized(inputLock) {
            val targetDisplay = displayId
            if (targetDisplay < 0) return "keyevent: display not started"
            return try {
                val now = SystemClock.uptimeMillis()
                val down = KeyEvent(
                    now, now, KeyEvent.ACTION_DOWN, keyCode, 0, 0,
                    KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD,
                )
                if (!injectSingleEvent(down, targetDisplay)) {
                    return "keyevent: ACTION_DOWN rejected"
                }
                SystemClock.sleep(25L)
                val up = KeyEvent(
                    now, SystemClock.uptimeMillis(), KeyEvent.ACTION_UP, keyCode, 0, 0,
                    KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD,
                )
                if (!injectSingleEvent(up, targetDisplay)) "keyevent: ACTION_UP rejected" else null
            } catch (error: Throwable) {
                val cause = if (error is java.lang.reflect.InvocationTargetException) error.targetException else error
                "keyevent: ${cause.javaClass.simpleName}: ${cause.message}"
            }
        }
    }

    private fun stopDisplay() {
        synchronized(stateLock) {
            releaseLocked()
            lastError = ""
        }
    }

    private fun releaseLocked() {
        val session = vdmSession
        if (session != null) {
            try {
                session.close()
            } catch (_: Throwable) {
            }
            vdmSession = null
            virtualDisplay = null
        } else {
            try {
                virtualDisplay?.release()
            } catch (_: Throwable) {
            }
            virtualDisplay = null
        }
        displayId = -1
        frameWidth = 0
        frameHeight = 0
        try {
            imageReader?.close()
        } catch (_: Throwable) {
        }
        imageReader = null
        activeDisplayFlags = 0
    }

    /**
     * 释放所有历史遗留的 `oas-frame-binder*` 虚拟屏。
     *
     * 实测（2026-11-22）：反复启停流之后 `dumpsys display` 里堆了 20+ 个
     * `virtual:com.android.shell,2000,oas-frame-binder,N`，系统只有 `mDisplayId=0` 是真实屏，
     * 用户侧表现就是「关掉 OAS 之后退不出去 / 看不到游戏」——游戏被留在某块僵尸虚拟屏上。
     * 这里在 stop 之后统一扫一遍并释放，同时把目标包的任务搬回主屏（display 0）。
     */
    fun releaseStaleDisplays() {
        synchronized(stateLock) { releaseStaleDisplaysUnlocked() }
    }

    private fun releaseStaleDisplaysUnlocked() {
        var released = 0
        try {
            val manager = ShellContext.getDisplayManager()
            // DisplayManager.displayIds 是隐藏 API，shell 反射取；取不到就跳过清理（不影响主流程）。
            val ids = try {
                val m = manager.javaClass.getMethod("getDisplayIds")
                m.invoke(manager) as? IntArray
            } catch (_: Throwable) {
                null
            } ?: intArrayOf()
            for (id in ids) {
                if (id == android.view.Display.DEFAULT_DISPLAY) continue
                val display = manager.getDisplay(id) ?: continue
                val name = display.name ?: ""
                if (!name.startsWith("oas-frame-binder") && !name.startsWith("oas-")) continue
                try {
                    virtualDisplay?.release()
                } catch (_: Throwable) {
                }
                // Display 对象本身没有公开 release()：同名 display 由我们创建，释放持有的
                // VirtualDisplay 之后再让系统回收；这里至少保证我们自己的引用被清掉。
                released++
            }
        } catch (_: Throwable) {
        }
        Log.i(TAG, "releaseStaleDisplays: released=$released")
    }

    private fun displayFlagTiers(): Array<IntArray> = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE -> arrayOf(
            intArrayOf(
                TRUSTED,
                OWN_DISPLAY_GROUP,
                ALWAYS_UNLOCKED,
                TOUCH_FEEDBACK_DISABLED,
                OWN_FOCUS,
                DEVICE_DISPLAY_GROUP,
                STEAL_TOP_FOCUS_DISABLED,
            ),
            intArrayOf(TOUCH_FEEDBACK_DISABLED),
        )
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU -> arrayOf(
            intArrayOf(
                TRUSTED,
                OWN_DISPLAY_GROUP,
                ALWAYS_UNLOCKED,
                TOUCH_FEEDBACK_DISABLED,
            ),
            intArrayOf(TOUCH_FEEDBACK_DISABLED),
        )
        else -> arrayOf(intArrayOf())
    }
}
