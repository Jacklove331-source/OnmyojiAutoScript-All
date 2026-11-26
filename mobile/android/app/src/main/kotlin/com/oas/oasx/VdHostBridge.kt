package com.oas.oasx

/** Python(Chaquopy) 调无头虚拟屏宿主的静态入口。 */
object VdHostBridge {

    @JvmStatic
    fun start(width: Int, height: Int, dpi: Int): Int =
        VdHostDisplay.start(width, height, dpi)

    @JvmStatic
    fun displayId(): Int = VdHostDisplay.displayId()

    @JvmStatic
    fun ready(): Boolean = VdHostDisplay.isRunning() && VdHostDisplay.displayId() >= 0

    @JvmStatic
    fun status(): String = VdHostDisplay.status()

    @JvmStatic
    fun capturePng(): ByteArray? = VdHostDisplay.capturePng()

    @JvmStatic
    fun stop(): Boolean = VdHostDisplay.stop()
}
