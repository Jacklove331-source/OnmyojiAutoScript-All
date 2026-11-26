package com.oas.oasx.ocr

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * OCR 桥：给嵌入的 CPython 用（Chaquopy 里没有 onnxruntime，PP-OCR 跑不了）。
 *
 * 实现用 ML Kit 的中文识别（**bundled** 版，模型随 APK 打包，不依赖 Google Play 服务），
 * 输出与 OAS 期望的结构对齐：
 *     [{"text": "...", "score": 0.0~1.0, "box": [x1, y1, x2, y2]}, ...]
 *
 * 为什么不用 PP-OCR：它的 det/rec 都是 onnx 模型，需要 onnxruntime（无 Android wheel，
 * 而且 det 后处理还要 pyclipper）；ML Kit 精度对游戏 UI 文本够用，且零额外运行库。
 */
object OcrBridge {

    private const val TAG = "OcrBridge"

    private val recognizer by lazy {
        TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())
    }

    /** 输入 PNG 字节，返�� JSON 字符串（识别结果数组）。 */
    @JvmStatic
    fun recognize(png: ByteArray): String {
        val bitmap: Bitmap = try {
            BitmapFactory.decodeByteArray(png, 0, png.size)
        } catch (error: Throwable) {
            Log.e(TAG, "decode bitmap failed", error)
            return "[]"
        } ?: return "[]"
        return recognizeBitmap(bitmap)
    }

    @JvmStatic
    fun recognizeBitmap(bitmap: Bitmap): String {
        val results = JSONArray()
        try {
            val image = InputImage.fromBitmap(bitmap, 0)
            val text = Tasks.await(recognizer.process(image), 20, TimeUnit.SECONDS)
            for (block in text.textBlocks) {
                for (line in block.lines) {
                    val box = line.boundingBox ?: continue
                    val item = JSONObject()
                    item.put("text", line.text)
                    // ML Kit 不返回置信度；给一个稳定值，OAS 侧阈值通常 0.3~0.6
                    item.put("score", 0.95)
                    item.put("box", JSONArray(listOf(box.left, box.top, box.right, box.bottom)))
                    results.put(item)
                }
            }
        } catch (error: Throwable) {
            Log.e(TAG, "recognize failed", error)
        } finally {
            bitmap.recycle()
        }
        return results.toString()
    }

    /** 单行识别（OAS 的 `ocr_single_line` 只要文本 + 分数）。 */
    @JvmStatic
    fun recognizeSingleLine(png: ByteArray): String {
        val json = recognize(png)
        return try {
            val array = JSONArray(json)
            if (array.length() == 0) {
                return JSONObject().put("text", "").put("score", 0.0).toString()
            }
            // 取纵向最靠上的那一行
            var best = array.getJSONObject(0)
            for (index in 1 until array.length()) {
                val candidate = array.getJSONObject(index)
                val boxA = best.getJSONArray("box")
                val boxB = candidate.getJSONArray("box")
                if (boxB.getInt(1) < boxA.getInt(1)) best = candidate
            }
            JSONObject().put("text", best.getString("text"))
                .put("score", best.optDouble("score", 0.95)).toString()
        } catch (error: Throwable) {
            JSONObject().put("text", "").put("score", 0.0).toString()
        }
    }

    /** 自检：返回识别器是否可用。 */
    @JvmStatic
    fun available(): Boolean = try {
        recognizer
        true
    } catch (error: Throwable) {
        Log.e(TAG, "recognizer unavailable", error)
        false
    }
}
