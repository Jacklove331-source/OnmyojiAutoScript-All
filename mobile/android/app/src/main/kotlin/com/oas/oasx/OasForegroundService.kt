package com.oas.oasx

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

/**
 * 让自动化在「后台」常驻的前提下运行，用户前台可以正常用别的 App。
 *
 * 要点（Android 12~15 的约束）：
 *  - 前台服务（foregroundServiceType=dataSync）+ 常驻通知，进程才不会被系统随手杀；
 *  - 唤醒锁只在任务运行期间持有，避免长期耗电；
 *  - 游戏不在前台时**不注入手势**（无障碍手势只作用于当前焦点窗口），由 Python 侧看
 *    `/state` 的 foreground 决定暂停；用户切回游戏即自动继续；
 *  - 独立于 Flutter Activity：关掉界面，Python 服务与任务调度仍在跑。
 */
class OasForegroundService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private val powerHandler = Handler(Looper.getMainLooper())
    private val renewPower = object : Runnable {
        override fun run() {
            if (instance !== this@OasForegroundService) return
            acquireWakeLock()
            powerHandler.postDelayed(this, 15 * 60 * 1000L)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        val notification = buildNotification("OASX Mobile 运行中（后台任务）")
        // API 34+ 必须显式带类型启动；specialUse 才适合长驻自动化（dataSync 有 6 小时配额，且会被按后台处理）
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        powerHandler.post(renewPower)
        startPython()
        Log.i(TAG, "foreground service created")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PAUSE -> {
                paused = true
                updateNotification("OASX Mobile 已暂停（点按继续）")
                Log.i(TAG, "paused by user")
            }
            ACTION_RESUME -> {
                paused = false
                updateNotification("OASX Mobile 运行中（后台任务）")
                Log.i(TAG, "resumed by user")
            }
            ACTION_STOP -> {
                pausePython()
                stopSelf()
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        powerHandler.removeCallbacks(renewPower)
        releaseWakeLock()
        running = false
        instance = null
        super.onDestroy()
        Log.i(TAG, "foreground service destroyed")
    }

    // ---------------------------------------------------------------- python

    private fun startPython() {
        Thread {
            try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(applicationContext))
                }
                val result = Python.getInstance()
                    .getModule("oas_mobile.server")
                    .callAttr("start", PORT)
                running = true
                pythonState = result.toString()
                Log.i(TAG, "python backend: $result")
            } catch (error: Throwable) {
                pythonState = "error: ${error.message}"
                Log.e(TAG, "python backend start failed", error)
            }
        }.start()
    }

    private fun pausePython() {
        try {
            if (Python.isStarted()) {
                Python.getInstance().getModule("oas_mobile.server").callAttr("stop")
            }
        } catch (error: Throwable) {
            Log.w(TAG, "python stop failed", error)
        }
        running = false
    }

    // ---------------------------------------------------------------- notification

    private fun buildNotification(
        text: String,
        title: String = "OASX Mobile",
        isPaused: Boolean = paused
    ): Notification {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID, "OASX 后台运行", NotificationManager.IMPORTANCE_LOW
            ).apply { description = "自动化任务在后台运行时的实时进度提示（低打扰）" }
            manager.createNotificationChannel(channel)
        }
        val openApp = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val toggleAction = if (isPaused) ACTION_RESUME else ACTION_PAUSE
        val toggleLabel = if (isPaused) "继续" else "暂停"
        val toggleIntent = PendingIntent.getService(
            this, 2, Intent(this, OasForegroundService::class.java).setAction(toggleAction),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val stopIntent = PendingIntent.getService(
            this, 1, Intent(this, OasForegroundService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setContentTitle(title)
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setOngoing(true)
            .setContentIntent(openApp)
            .addAction(Notification.Action.Builder(null, toggleLabel, toggleIntent).build())
            .addAction(Notification.Action.Builder(null, "停止", stopIntent).build())
            .build()
    }

    private fun updateNotification(
        text: String,
        title: String = "OASX Mobile",
        isPaused: Boolean = paused
    ) {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(NOTIFICATION_ID, buildNotification(text, title, isPaused))
    }

    private fun sendAlert(title: String, body: String, alertId: Int) {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val alertChannel = NotificationChannel(
                ALERT_CHANNEL_ID, "OASX 异常与完成提醒", NotificationManager.IMPORTANCE_HIGH
            ).apply { description = "挂机异常、过温/低电保护与每日完成强提醒" }
            manager.createNotificationChannel(alertChannel)
        }
        val openApp = PendingIntent.getActivity(
            this, 10 + (alertId and 0xff), Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, ALERT_CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        val n = builder
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setAutoCancel(true)
            .setContentIntent(openApp)
            .build()
        manager.notify(alertId, n)
    }

    // ---------------------------------------------------------------- power

    private fun acquireWakeLock() {
        if (wakeLock?.isHeld == true) {
            wakeLock?.acquire(30 * 60 * 1000L)
            return
        }
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "OASX:automation").apply {
            setReferenceCounted(false)
            acquire(30 * 60 * 1000L)  // 最长 1 小时，避免忘记释放把电耗光
        }
    }

    private fun releaseWakeLock() {
        wakeLock?.let {
            if (it.isHeld) it.release()
        }
        wakeLock = null
    }

    companion object {
        private const val TAG = "OasForeground"
        private const val CHANNEL_ID = "oasx_background"
        private const val ALERT_CHANNEL_ID = "oasx_alerts"
        private const val NOTIFICATION_ID = 1001
        const val PORT = 22267
        const val ACTION_PAUSE = "com.oas.oasx.PAUSE"
        const val ACTION_RESUME = "com.oas.oasx.RESUME"
        const val ACTION_STOP = "com.oas.oasx.STOP"

        @Volatile
        var instance: OasForegroundService? = null
            private set

        // 注意：companion 的 var 不加 @JvmStatic 时只有 Companion.getXxx()，
        // Python(Chaquopy) 调 OasForegroundService.getPaused() 会失败 → 必须加。
        @JvmStatic
        @Volatile
        var paused: Boolean = false

        @JvmStatic
        @Volatile
        var running: Boolean = false
            private set

        @JvmStatic
        @Volatile
        var pythonState: String = "not started"

        @JvmStatic
        fun isRunning(): Boolean = instance != null

        /** Flutter/通知栏按钮用：暂停或恢复后台任务。 */
        @JvmStatic
        fun updatePaused(value: Boolean): Boolean {
            val service = instance ?: return false
            service.onStartCommand(
                Intent(service, OasForegroundService::class.java).apply {
                    action = if (value) ACTION_PAUSE else ACTION_RESUME
                }, 0, 0
            )
            return true
        }

        /** Python 调度器/护航调用：实时更新常驻通知进度。 */
        @JvmStatic
        fun updateLiveStatus(title: String, text: String, isPaused: Boolean): Boolean {
            val service = instance ?: return false
            return try {
                service.updateNotification(text, title, isPaused)
                true
            } catch (t: Throwable) {
                Log.w(TAG, "updateLiveStatus failed", t)
                false
            }
        }

        /** Python 护航/任务调用：推送高优先级异常或完成提醒。 */
        @JvmStatic
        fun postAlert(title: String, body: String, alertId: Int): Boolean {
            val service = instance ?: return false
            return try {
                service.sendAlert(title, body, alertId)
                true
            } catch (t: Throwable) {
                Log.w(TAG, "postAlert failed", t)
                false
            }
        }

        @JvmStatic
        fun start(context: Context) {
            val intent = Intent(context, OasForegroundService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
