package com.oas.oasx

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log

/**
 * 隐藏虚拟屏用的前台服务（type=mediaProjection）。
 *
 * Android 14+ 硬性要求：**先**起一个 foregroundServiceType=mediaProjection 的前台服务，
 * 再调 `MediaProjectionManager.getMediaProjection()`，否则 `MediaProjection.start()` 会抛
 * `SecurityException: Media projections require a foreground service of type ...`。
 * 实测报错：`MediaProjectionManagerService$MediaProjection.start` RemoteException。
 */
class HiddenCaptureService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        startAsForeground()
        Log.i(TAG, "capture service created")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startAsForeground()
        return START_STICKY
    }

    private fun startAsForeground() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "OASX 虚拟屏", NotificationManager.IMPORTANCE_LOW)
            )
        }
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        val notification = builder
            .setContentTitle("OASX Mobile")
            .setContentText("虚拟屏后台运行中")
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    companion object {
        private const val TAG = "HiddenCaptureService"
        private const val CHANNEL_ID = "oasx_hidden_capture"
        private const val NOTIFICATION_ID = 1002

        @JvmStatic
        fun start(context: Context) {
            val intent = Intent(context, HiddenCaptureService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
