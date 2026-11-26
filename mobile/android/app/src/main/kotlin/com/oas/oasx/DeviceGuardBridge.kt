package com.oas.oasx

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.util.Log
import org.json.JSONObject

/**
 * 手机端护航桥接（供 Chaquopy Python 读取电池温度/电量/网络状态、控制媒体静音、刷新实时通知和发送异常强提醒）。
 */
object DeviceGuardBridge {
    private const val TAG = "OasDeviceGuard"

    @Volatile
    private var savedMediaVolume: Int = -1

    private fun appContext(): Context? {
        return OasForegroundService.instance?.applicationContext
            ?: VdHostDisplay.application
            ?: HiddenDisplay.currentActivity?.applicationContext
    }

    @JvmStatic
    fun getTelemetry(): String {
        val out = JSONObject()
        out.put("battery_pct", -1)
        out.put("battery_temp_c", -1.0)
        out.put("charging", false)
        out.put("network_online", true)
        out.put("network_type", "unknown")

        val ctx = appContext() ?: return out.toString()
        try {
            val intent = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            if (intent != null) {
                val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
                val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
                if (level >= 0 && scale > 0) {
                    out.put("battery_pct", (level * 100) / scale)
                }
                val tenths = intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1)
                if (tenths > 0) {
                    out.put("battery_temp_c", tenths / 10.0)
                }
                val status = intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
                val plugged = intent.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0)
                val charging = plugged != 0 ||
                    status == BatteryManager.BATTERY_STATUS_CHARGING ||
                    status == BatteryManager.BATTERY_STATUS_FULL
                out.put("charging", charging)
            }
        } catch (t: Throwable) {
            Log.w(TAG, "battery telemetry failed", t)
        }

        try {
            val cm = ctx.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            val network = cm?.activeNetwork
            val caps = if (network != null) cm.getNetworkCapabilities(network) else null
            val online = caps != null &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            val netType = when {
                caps == null -> "none"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
                else -> "other"
            }
            out.put("network_online", online)
            out.put("network_type", netType)
        } catch (t: Throwable) {
            Log.w(TAG, "network telemetry failed", t)
        }
        return out.toString()
    }

    @JvmStatic
    fun setMediaMuted(muted: Boolean): Boolean {
        val ctx = appContext() ?: return false
        return try {
            val am = ctx.getSystemService(Context.AUDIO_SERVICE) as? AudioManager ?: return false
            if (muted) {
                val cur = am.getStreamVolume(AudioManager.STREAM_MUSIC)
                if (savedMediaVolume < 0 && cur > 0) {
                    savedMediaVolume = cur
                }
                am.setStreamVolume(AudioManager.STREAM_MUSIC, 0, 0)
            } else if (savedMediaVolume >= 0) {
                val maxVol = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
                am.setStreamVolume(
                    AudioManager.STREAM_MUSIC,
                    savedMediaVolume.coerceIn(0, maxVol),
                    0
                )
                savedMediaVolume = -1
            }
            true
        } catch (t: Throwable) {
            Log.w(TAG, "setMediaMuted($muted) failed", t)
            false
        }
    }

    @JvmStatic
    fun updateProgressNotification(title: String, text: String, isPaused: Boolean): Boolean {
        return OasForegroundService.updateLiveStatus(title, text, isPaused)
    }

    @JvmStatic
    fun sendAlertNotification(title: String, body: String, alertId: Int): Boolean {
        return OasForegroundService.postAlert(title, body, alertId)
    }

    @JvmStatic
    fun apkCacheDir(): String {
        val ctx = appContext() ?: return ""
        val dir = java.io.File(ctx.cacheDir, "apk_updates")
        dir.mkdirs()
        return dir.absolutePath
    }
}
