package com.oas.oasx

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Run the bundled detection model locally; Python receives only 1,800 output floats. */
object OnnxBridge {
    private val environment by lazy { OrtEnvironment.getEnvironment() }
    private var modelPath = ""
    private var session: OrtSession? = null

    @JvmStatic
    @Synchronized
    fun infer(path: String, input: ByteArray): FloatArray {
        require(input.size == 1 * 3 * 640 * 640 * 4) { "expected float32 NCHW 1x3x640x640" }
        if (session == null || modelPath != path) {
            session?.close()
            val options = OrtSession.SessionOptions().apply {
                setIntraOpNumThreads(2)
                setInterOpNumThreads(1)
            }
            session = options.use { environment.createSession(path, it) }
            modelPath = path
        }
        val active = checkNotNull(session)
        val data = ByteBuffer.allocateDirect(input.size).order(ByteOrder.LITTLE_ENDIAN)
        data.put(input)
        data.rewind()
        OnnxTensor.createTensor(environment, data.asFloatBuffer(), longArrayOf(1, 3, 640, 640)).use { tensor ->
            active.run(mapOf(active.inputNames.first() to tensor)).use { result ->
                val batches = result[0].value as Array<*>
                val rows = batches[0] as Array<*>
                val flattened = FloatArray(rows.sumOf { (it as FloatArray).size })
                var position = 0
                for (row in rows) {
                    val values = row as FloatArray
                    values.copyInto(flattened, position)
                    position += values.size
                }
                return flattened
            }
        }
    }

    @JvmStatic
    @Synchronized
    fun close() {
        session?.close()
        session = null
        modelPath = ""
    }
}
