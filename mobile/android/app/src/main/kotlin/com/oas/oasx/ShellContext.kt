package com.oas.oasx

import android.app.Application
import android.app.Instrumentation
import android.content.AttributionSource
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import android.hardware.display.DisplayManager
import android.os.Build
import android.os.Looper
import android.os.Process
import android.util.Log

/**
 * Shizuku user-service 进程里的 shell Context / DisplayManager。
 *
 * 实现移植自 MAA-Meow 的 Workarounds + FakeContext：
 * - 初始化一个 system ActivityThread；
 * - 填充 ConfigurationController / AppBindData / InitialApplication；
 * - 用包名 com.android.shell 的 ContextWrapper；
 * - 用隐藏构造器 new DisplayManager(FakeContext) 建屏，确保 flags 和调用方身份生效。
 */
object ShellContext {
    private const val TAG = "OasShellContext"
    private const val SHELL_PACKAGE = "com.android.shell"

    @Volatile private var initialized = false
    @Volatile private var cached: Context? = null
    @Volatile private var displayManager: DisplayManager? = null

    private var activityThreadClass: Class<*>? = null
    private var activityThread: Any? = null

    @JvmStatic
    fun get(): Context {
        initialize()
        return cached ?: throw IllegalStateException("shell context unavailable")
    }

    @JvmStatic
    fun getDisplayManager(): DisplayManager {
        initialize()
        displayManager?.let { return it }
        synchronized(this) {
            displayManager?.let { return it }
            val constructor = DisplayManager::class.java
                .getDeclaredConstructor(Context::class.java)
                .apply { isAccessible = true }
            val manager = constructor.newInstance(get())
            displayManager = manager
            return manager
        }
    }

    private fun initialize() {
        if (initialized) return
        synchronized(this) {
            if (initialized) return
            ensureMainLooper()

            val threadClass = Class.forName("android.app.ActivityThread")
            val thread = threadClass.getDeclaredConstructor()
                .apply { isAccessible = true }
                .newInstance()
            threadClass.getDeclaredField("sCurrentActivityThread")
                .apply { isAccessible = true }
                .set(null, thread)
            threadClass.getDeclaredField("mSystemThread")
                .apply { isAccessible = true }
                .setBoolean(thread, true)

            activityThreadClass = threadClass
            activityThread = thread

            fillConfigurationController()
            val base = getSystemContext()
                ?: throw IllegalStateException("ActivityThread system context unavailable")
            val fake = object : ContextWrapper(base) {
                override fun getApplicationContext(): Context = this
                override fun getPackageName(): String = SHELL_PACKAGE
                override fun getOpPackageName(): String = SHELL_PACKAGE

                override fun checkCallingPermission(permission: String): Int =
                    PackageManager.PERMISSION_GRANTED

                override fun getAttributionSource(): AttributionSource =
                    AttributionSource.Builder(Process.myUid())
                        .setPackageName(SHELL_PACKAGE)
                        .build()
            }
            cached = fake
            fillAppInfo()
            fillAppContext(fake)
            initialized = true
            Log.i(TAG, "shell context ready uid=${Process.myUid()} package=$SHELL_PACKAGE")
        }
    }

    private fun ensureMainLooper() {
        if (Looper.myLooper() == null) Looper.prepare()
        try {
            Looper::class.java.getDeclaredField("sMainLooper")
                .apply { isAccessible = true }
                .set(null, Looper.myLooper())
        } catch (_: Throwable) {
        }
    }

    private fun fillConfigurationController() {
        if (Build.VERSION.SDK_INT < 31) return
        try {
            val controllerClass = Class.forName("android.app.ConfigurationController")
            val threadInterface = Class.forName("android.app.ActivityThreadInternal")
            val controller = controllerClass.getDeclaredConstructor(threadInterface)
                .apply { isAccessible = true }
                .newInstance(activityThread)
            activityThreadClass!!.getDeclaredField("mConfigurationController")
                .apply { isAccessible = true }
                .set(activityThread, controller)
        } catch (error: Throwable) {
            Log.w(TAG, "fill ConfigurationController failed: ${error.message}")
        }
    }

    private fun fillAppInfo() {
        try {
            val bindDataClass = Class.forName("android.app.ActivityThread\$AppBindData")
            val bindData = bindDataClass.getDeclaredConstructor()
                .apply { isAccessible = true }
                .newInstance()
            val appInfo = ApplicationInfo().apply { packageName = SHELL_PACKAGE }
            bindDataClass.getDeclaredField("appInfo")
                .apply { isAccessible = true }
                .set(bindData, appInfo)
            activityThreadClass!!.getDeclaredField("mBoundApplication")
                .apply { isAccessible = true }
                .set(activityThread, bindData)
        } catch (error: Throwable) {
            Log.w(TAG, "fill AppBindData failed: ${error.message}")
        }
    }

    private fun fillAppContext(context: Context) {
        try {
            val app = Instrumentation.newApplication(Application::class.java, context)
            activityThreadClass!!.getDeclaredField("mInitialApplication")
                .apply { isAccessible = true }
                .set(activityThread, app)
        } catch (error: Throwable) {
            Log.w(TAG, "fill InitialApplication failed: ${error.message}")
        }
    }

    private fun getSystemContext(): Context? =
        activityThreadClass?.getDeclaredMethod("getSystemContext")
            ?.apply { isAccessible = true }
            ?.invoke(activityThread) as? Context
}
