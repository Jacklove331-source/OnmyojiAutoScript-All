package com.oas.oasx

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.util.Log

/**
 * 「隐藏虚拟屏」：用户完全看不到，App/游戏投在上面被自动化。
 *
 * 与 `overlay_display_devices` 的区别（实测很重要）：
 * - overlay 屏 owner 是 system，而且会在主屏上渲染成小窗（用户能看见）；
 * - 这里用 **MediaProjection** 建屏：owner 是本应用、渲染到我们自己造的 Surface 上，
 *   主屏完全不受影响；带 `VIRTUAL_DISPLAY_FLAG_PUBLIC` 后其它任务也能通过
 *   `am start --display <id>`（Shizuku/shell）投到这块屏。
 *
 * 参考实现思路：HakaI963/headless-android、MerlinShieh/vdisplay-android（后者明确写了
 * overlay 屏 `am start --display` 会被 `SafeActivityOptions.checkPermissions` 拒绝）。
 */
object HiddenDisplay {

    private const val TAG = "HiddenDisplay"
    const val REQUEST_CODE = 33001

    private const val defaultWidth = 1280
    private const val defaultHeight = 720
    private const val defaultDpi = 240

    @Volatile
    private var projection: MediaProjection? = null

    @Volatile
    private var virtualDisplay: VirtualDisplay? = null

    @Volatile
    private var imageReader: ImageReader? = null

    @Volatile
    private var displayId: Int = -1

    @Volatile
    private var pendingActivity: Activity? = null

    /** 由 MainActivity 在 onCreate 里赋值，供 Python 侧发起授权。 */
    @Volatile
    var currentActivity: Activity? = null

    /** Python 侧先调它：没有授权就拉起系统录屏授权弹窗（只需一次）。 */
    @JvmStatic
    fun ensure(activity: Activity): Boolean {
        if (displayId >= 0) return true
        pendingActivity = activity
        val manager = activity.getSystemService(Context.MEDIA_PROJECTION_SERVICE) as? MediaProjectionManager
        if (manager == null) {
            Log.e(TAG, "MediaProjectionManager unavailable")
            return false
        }
        return try {
            activity.startActivityForResult(manager.createScreenCaptureIntent(), REQUEST_CODE)
            Log.i(TAG, "screen capture consent requested")
            true
        } catch (error: Throwable) {
            Log.e(TAG, "request consent failed", error)
            false
        }
    }

    /**
     * 从我们自己的 ImageReader 抓一帧并编码成 PNG。
     *
     * 为什么要自己抓：MediaProjection 建出来的屏用 `screencap -d <id>` 抓会得到空帧，
     * 而这块屏的内容本来就渲染在我们给的 Surface 上，直接读 ImageReader 最可靠也最快。
     */
    @JvmStatic
    fun capturePng(timeoutMs: Long = 3000): ByteArray? {
        val reader = imageReader ?: return null
        val deadline = System.currentTimeMillis() + timeoutMs
        var image: android.media.Image? = null
        while (System.currentTimeMillis() < deadline) {
            image = try {
                reader.acquireLatestImage()
            } catch (error: Throwable) {
                Log.w(TAG, "acquireLatestImage failed", error)
                null
            }
            if (image != null) break
            try {
                Thread.sleep(40)
            } catch (_: InterruptedException) {
            }
        }
        if (image == null) return null
        var bitmap: android.graphics.Bitmap? = null
        var cropped: android.graphics.Bitmap? = null
        return try {
            val plane = image.planes[0]
            val buffer = plane.buffer
            val pixelStride = plane.pixelStride
            val rowStride = plane.rowStride
            val width = image.width
            val height = image.height
            val rowPadding = rowStride - pixelStride * width
            val bitmapWidth = width + rowPadding / pixelStride
            bitmap = android.graphics.Bitmap.createBitmap(
                bitmapWidth, height, android.graphics.Bitmap.Config.ARGB_8888
            )
            bitmap.copyPixelsFromBuffer(buffer)
            cropped = if (bitmapWidth != width) {
                android.graphics.Bitmap.createBitmap(bitmap, 0, 0, width, height)
            } else {
                bitmap
            }
            val stream = java.io.ByteArrayOutputStream()
            cropped.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, stream)
            stream.toByteArray()
        } catch (error: Throwable) {
            Log.e(TAG, "capturePng failed", error)
            null
        } finally {
            if (cropped != null && cropped !== bitmap) {
                cropped.recycle()
            }
            bitmap?.recycle()
            try {
                image.close()
            } catch (_: Throwable) {
            }
        }
    }

    @JvmStatic
    fun requestCode(): Int = REQUEST_CODE

    /** MainActivity 的 onActivityResult 转发到这里。 */
    @JvmStatic
    fun onActivityResult(resultCode: Int, data: Intent?): Boolean {
        val activity = pendingActivity
        if (activity == null || resultCode != Activity.RESULT_OK || data == null) {
            Log.w(TAG, "consent denied or activity missing")
            return false
        }
        val manager = activity.getSystemService(Context.MEDIA_PROJECTION_SERVICE) as? MediaProjectionManager
            ?: return false
        return try {
            // Android 14+：必须先有 mediaProjection 类型的前台服务，否则 start() 抛 SecurityException
            HiddenCaptureService.start(activity)
            // 服务在 App 启动时已拉起，这里再等一小会儿确保前台状态已生效
            Thread.sleep(600)
            val created = manager.getMediaProjection(resultCode, data) ?: return false
            created.registerCallback(object : MediaProjection.Callback() {
                override fun onStop() {
                    Log.w(TAG, "projection stopped")
                    release()
                }
            }, null)
            projection = created
            Log.i(TAG, "projection acquired")
            // 立刻建屏：createVirtualDisplay 内部会校验调用方是否在前台，
            // 从后台线程稍后再建会抛 SecurityException。
            val display = create(defaultWidth, defaultHeight, defaultDpi)
            Log.i(TAG, "hidden display after consent: $display")
            display >= 0
        } catch (error: Throwable) {
            Log.e(TAG, "acquire projection failed", error)
            false
        }
    }

    /** 建隐藏屏，返回 displayId；失败 -1。 */
    @JvmStatic
    fun create(width: Int, height: Int, dpi: Int = 240): Int {
        if (displayId >= 0) return displayId
        val mediaProjection = projection ?: run {
            Log.w(TAG, "create: projection not ready")
            return -1
        }
        return try {
            val reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
            imageReader = reader
            var flags = DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                flags = flags or DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY
            }
            val display = mediaProjection.createVirtualDisplay(
                "oas-hidden-display", width, height, dpi, flags, reader.surface, null, null
            ) ?: return -1
            virtualDisplay = display
            displayId = display.display.displayId
            Log.i(TAG, "hidden display created id=$displayId ${width}x$height@$dpi")
            displayId
        } catch (error: Throwable) {
            Log.e(TAG, "create hidden display failed", error)
            -1
        }
    }

    @JvmStatic
    fun currentDisplayId(): Int = displayId

    @JvmStatic
    fun release(): Boolean {
        try {
            virtualDisplay?.release()
        } catch (_: Throwable) {
        }
        try {
            imageReader?.close()
        } catch (_: Throwable) {
        }
        try {
            projection?.stop()
        } catch (_: Throwable) {
        }
        virtualDisplay = null
        imageReader = null
        projection = null
        displayId = -1
        Log.i(TAG, "hidden display released")
        return true
    }

    /** 自检：投影是否已授权、屏是否已创建。 */
    @JvmStatic
    fun status(): String {
        return "projection=${projection != null} displayId=$displayId"
    }
}
