package com.oas.oasx

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Bitmap
import android.graphics.Path
import android.os.Build
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import java.io.ByteArrayOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * OAS Mobile 的 Android 执行层（免 Root）。
 *
 * 设计：
 *  - 截图：Android 11+（API 30+）用 AccessibilityService.takeScreenshot（无需额外弹窗授权）；
 *    低版本走 MediaProjection（见 CaptureService，后续阶段补）。
 *  - 手势：dispatchGesture（API 24+），点击/滑动/长按。
 *  - Python 侧（Chaquopy）通过 java 模块直接调用本类的静态方法，拿 byte[] 自己解 PNG。
 *
 * 线程模型：所有方法都是阻塞式（Python 调用方便），内部用 latch 等回调。
 */
class OasAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "accessibility service connected")
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // 「后台运行」的关键：记录当前前台包名，Python 侧据此决定能否注入手势
        if (event?.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            val pkg = event.packageName?.toString()
            if (!pkg.isNullOrBlank()) {
                foregroundPackage = pkg
            }
        }
    }

    override fun onInterrupt() { /* 不需要 */ }

    companion object {
        private const val TAG = "OasAccessibility"

        @Volatile
        private var instance: OasAccessibilityService? = null

        private val executor = Executors.newSingleThreadExecutor()

        /** 无障碍事件更新的前台包名（快路径；可能为空）。 */
        @Volatile
        @JvmStatic
        var foregroundPackage: String = ""

        /**
         * 当前前台应用包名：优先事件值，空则主动查活动窗口。
         * 事件不可靠（部分机型/模拟器不派发）时，这个回退能保证「后台判定」始终有值。
         */
        @JvmStatic
        fun currentForegroundPackage(): String {
            val cached = foregroundPackage
            if (cached.isNotEmpty()) return cached
            val service = instance ?: return ""
            return try {
                val active = service.rootInActiveWindow?.packageName?.toString()
                if (!active.isNullOrBlank()) {
                    foregroundPackage = active
                    return active
                }
                val focused = service.windows.firstOrNull { it.isFocused }
                val pkg = focused?.root?.packageName?.toString()
                if (!pkg.isNullOrBlank()) foregroundPackage = pkg
                pkg ?: ""
            } catch (error: Throwable) {
                Log.w(TAG, "currentForegroundPackage failed", error)
                ""
            }
        }

        /** 目标游戏包名；可由 Python 侧调用 setTargetPackage 覆盖。 */
        @Volatile
        private var targetPackage: String = "com.netease.onmyoji"

        @JvmStatic
        fun setTargetPackage(pkg: String) {
            if (pkg.isNotBlank()) targetPackage = pkg
        }

        /** 目标游戏是否在前台（后台运行时用来决定「暂停/继续」）。 */
        @JvmStatic
        fun isTargetForeground(): Boolean {
            val current = currentForegroundPackage()
            return current.isNotEmpty() && current == targetPackage
        }

        /** Python 侧检查服务是否已连接。 */
        @JvmStatic
        fun isServiceReady(): Boolean = instance != null

        /**
         * 截取当前屏幕，返回 PNG 字节流。
         * Android 11+ 走 takeScreenshot；失败返回 null（Python 侧必须处理）。
         */
        @JvmStatic
        fun capturePng(): ByteArray? {
            val service = instance ?: run {
                Log.w(TAG, "capture failed: service not connected")
                return null
            }
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
                Log.w(TAG, "takeScreenshot requires API 30+; current=${Build.VERSION.SDK_INT}")
                return null
            }
            val latch = CountDownLatch(1)
            var result: ByteArray? = null
            try {
                service.takeScreenshot(
                    android.view.Display.DEFAULT_DISPLAY,
                    executor,
                    object : TakeScreenshotCallback {
                        override fun onSuccess(screenshot: ScreenshotResult) {
                            var bitmap: Bitmap? = null
                            var soft: Bitmap? = null
                            try {
                                bitmap = Bitmap.wrapHardwareBuffer(
                                    screenshot.hardwareBuffer, screenshot.colorSpace
                                )
                                if (bitmap != null) {
                                    // 转软件位图再压 PNG（硬件位图不能直接 compress 的机型上更稳）
                                    soft = bitmap.copy(Bitmap.Config.ARGB_8888, false)
                                    if (soft != null) {
                                        val out = ByteArrayOutputStream()
                                        soft.compress(Bitmap.CompressFormat.PNG, 100, out)
                                        result = out.toByteArray()
                                    }
                                }
                            } catch (error: Throwable) {
                                Log.e(TAG, "encode screenshot failed", error)
                            } finally {
                                soft?.recycle()
                                bitmap?.recycle()
                                try {
                                    screenshot.hardwareBuffer.close()
                                } catch (_: Throwable) {
                                }
                                latch.countDown()
                            }
                        }

                        override fun onFailure(errorCode: Int) {
                            Log.w(TAG, "takeScreenshot failed: $errorCode")
                            latch.countDown()
                        }
                    })
                latch.await(5, TimeUnit.SECONDS)
            } catch (error: Throwable) {
                Log.e(TAG, "capturePng error", error)
            }
            return result
        }

        /**
         * Root 通道截图：Kotlin 侧直接 exec `su -c screencap -p <file>`。
         * MuMu 等模拟器有 su，这条路不依赖无障碍，也能绕开 takeScreenshot 返回黑图的问题。
         * 真机没有 su 时返回 null，调用方自动回落无障碍。
         */
        @JvmStatic
        fun capturePngRoot(): ByteArray? {
            val ctx = instance?.applicationContext ?: VdHostDisplay.application ?: return null
            val file = java.io.File(ctx.cacheDir, "_screencap.png")
            try {
                file.delete()
            } catch (_: Throwable) {
            }
            val su = listOf("/system/bin/su", "/system/xbin/su", "/sbin/su", "/su/bin/su")
                .firstOrNull { java.io.File(it).exists() } ?: return null
            return try {
                val process = ProcessBuilder(su, "-c", "screencap -p ${file.absolutePath}")
                    .redirectErrorStream(true)
                    .start()
                // su 若在等 stdin（授权提示）会永久挂住，必须立刻关掉 stdin
                try {
                    process.outputStream.close()
                } catch (_: Throwable) {
                }
                if (!process.waitFor(8, TimeUnit.SECONDS)) {
                    process.destroy()
                    Log.w(TAG, "root capture timed out")
                    return null
                }
                if (file.exists() && file.length() > 8L) file.readBytes() else null
            } catch (error: Throwable) {
                Log.w(TAG, "root capture failed", error)
                null
            }
        }

        /** 点击 (x, y)，坐标基于当前屏幕像素。 */
        @JvmStatic
        fun tap(x: Int, y: Int, durationMs: Long = 60L): Boolean {
            val service = instance ?: return false
            val path = Path().apply { moveTo(x.toFloat(), y.toFloat()) }
            return dispatch(service, path, durationMs)
        }

        /** 滑动 (x1,y1) -> (x2,y2)。 */
        @JvmStatic
        fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Long = 300L): Boolean {
            val service = instance ?: return false
            val path = Path().apply {
                moveTo(x1.toFloat(), y1.toFloat())
                lineTo(x2.toFloat(), y2.toFloat())
            }
            return dispatch(service, path, durationMs)
        }

        private fun dispatch(service: AccessibilityService, path: Path, durationMs: Long): Boolean {
            val latch = CountDownLatch(1)
            var ok = false
            try {
                val gesture = GestureDescription.Builder()
                    .addStroke(GestureDescription.StrokeDescription(path, 0L, durationMs))
                    .build()
                ok = service.dispatchGesture(gesture, object : GestureResultCallback() {
                    override fun onCompleted(description: GestureDescription?) {
                        latch.countDown()
                    }

                    override fun onCancelled(description: GestureDescription?) {
                        latch.countDown()
                    }
                }, null)
                latch.await(3, TimeUnit.SECONDS)
            } catch (error: Throwable) {
                Log.e(TAG, "dispatchGesture error", error)
            }
            return ok
        }
    }
}
