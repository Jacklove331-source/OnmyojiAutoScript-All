package com.oas.oasx

import android.util.Log

/** Python(Chaquopy) 调用隐藏虚拟屏的静态入口。 */
object HiddenDisplayBridge {

    private const val TAG = "HiddenDisplayBridge"

    @JvmStatic
    fun status(): String = HiddenDisplay.status()

    @JvmStatic
    fun ensure(): Boolean {
        val activity = HiddenDisplay.currentActivity
        if (activity == null) {
            Log.w(TAG, "ensure: no activity")
            return false
        }
        return HiddenDisplay.ensure(activity)
    }

    @JvmStatic
    fun create(width: Int, height: Int, dpi: Int): Int =
        HiddenDisplay.create(width, height, dpi)

    @JvmStatic
    fun displayId(): Int = HiddenDisplay.currentDisplayId()

    @JvmStatic
    fun capturePng(): ByteArray? = HiddenDisplay.capturePng()

    @JvmStatic
    fun release(): Boolean = HiddenDisplay.release()

    @JvmStatic
    fun ready(): Boolean = HiddenDisplay.currentDisplayId() >= 0
}
