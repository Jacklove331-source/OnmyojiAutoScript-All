package com.oas.oasx

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

/**
 * OASX Mobile 入口。
 *
 * 启动顺序：
 * 1. 初始化 Chaquopy（嵌入 CPython）；
 * 2. 在本进程内起 Python 的本地 HTTP 服务（默认 127.0.0.1:22267），供 Flutter 界面连接；
 * 3. Flutter 端照旧通过 `ApiClient.address` 指向 127.0.0.1 即可复用 OASX 全部界面。
 */
class MainActivity : FlutterActivity() {

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // 权限引导与标准 APK 安装器调用
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "oasx/system")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "open" -> {
                        val page = call.argument<String>("page") ?: ""
                        result.success(openSystemPage(page))
                    }
                    "installApk" -> {
                        val path = call.argument<String>("path") ?: ""
                        result.success(installApk(path))
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun installApk(path: String): Boolean {
        return try {
            val file = File(path)
            if (!file.isFile || file.length() <= 0L) {
                Log.w(TAG, "installApk: missing file $path")
                return false
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
                !packageManager.canRequestPackageInstalls()
            ) {
                val permIntent = Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:$packageName")
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(permIntent)
                return false
            }
            val uri = FileProvider.getUriForFile(this, "$packageName.fileprovider", file)
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            startActivity(intent)
            true
        } catch (error: Throwable) {
            Log.w(TAG, "installApk failed: $path", error)
            false
        }
    }

    private fun openSystemPage(page: String): Boolean {
        return try {
            val intent = when (page) {
                "accessibility" -> Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                "notification" -> Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                    .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                "app_detail" -> Intent(
                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:$packageName"),
                )
                "shizuku" -> packageManager.getLaunchIntentForPackage("moe.shizuku.privileged.api")
                    ?: Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName"))
                else -> return false
            }
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            startActivity(intent)
            true
        } catch (error: Throwable) {
            Log.w(TAG, "open system page failed: $page", error)
            false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 隐藏虚拟屏授权结果需要回到这个 Activity
        HiddenDisplay.currentActivity = this
        VdHostDisplay.application = applicationContext
        // 后台运行：Python 与任务循环跑在 ForegroundService 里，关掉界面也不停
        requestNotificationPermissionIfNeeded()
        OasForegroundService.start(this)
        // Android 16 targetSdk 36：mediaProjection 前台服务必须等录屏授权通过后再起，
        // 否则 startForeground 会抛 SecurityException（android:project_media 权限尚未授予）。
        autoRequestHiddenDisplay()
    }


    /**
     * 启动后是否自动申请录屏授权：默认**不申请**（避免每次启动弹系统授权框）。
     * 需要隐藏屏的用户在设置里打开 `hidden_auto` 后才会申请一次。
     */
    private fun autoRequestHiddenDisplay() {
        val enabled = getSharedPreferences("oasx", MODE_PRIVATE).getBoolean("hidden_auto", false)
        if (!enabled) {
            Log.i(TAG, "auto hidden display request disabled (popup-free mode)")
            return
        }
        window.decorView.postDelayed({
            try {
                if (HiddenDisplay.currentDisplayId() < 0) {
                    HiddenDisplay.ensure(this)
                }
            } catch (error: Throwable) {
                Log.w(TAG, "auto request hidden display failed", error)
            }
        }, 1500)
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) !=
            android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1001)
        }
    }

    /** 兼容旧调用：Python 现在由 OasForegroundService 负责启动。 */
    @Suppress("unused")
    private fun startPythonBackend() {
        Thread {
            try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(applicationContext))
                }
                val py = Python.getInstance()
                val result = py.getModule("oas_mobile.server").callAttr("start", PYTHON_SERVER_PORT)
                Log.i(TAG, "python backend: $result")
            } catch (error: Throwable) {
                Log.e(TAG, "python backend start failed", error)
            }
        }.start()
    }

    /** 供后续 UI 调用：跳系统设置打开无障碍服务。 */
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == HiddenDisplay.REQUEST_CODE) {
            val ok = HiddenDisplay.onActivityResult(resultCode, data)
            Log.i(TAG, "hidden display consent ok=$ok")
        }
    }

    fun openAccessibilitySettings() {
        startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
    }

    companion object {
        private const val TAG = "OasPython"
        const val PYTHON_SERVER_PORT = 22267
    }
}
