"""`ppocronnx` 的 Android 实现：接口与 OAS 期望一致，内部走 ML Kit（Kotlin 桥）。

OAS 的 OCR 调用面（实测统计）只有两个：
    model.ocr_single_line(image) -> (text, score)
    model.detect_and_ocr(image)  -> [BoxedResult(box, ocr_text, score), ...]

桌面版这两者由 PP-OCR(onnxruntime) 提供；手机上没有 onnxruntime，
改用 ML Kit 中文识别（bundled 模型，无需 Play 服务），结构保持完全一致。
"""

from __future__ import annotations

import json

import numpy as np

try:  # 只有 Android(Chaquopy) 里存在
    from java import jclass  # type: ignore

    _OCR = jclass("com.oas.oasx.ocr.OcrBridge")
except Exception:  # noqa: BLE001
    _OCR = None


class BoxedResult:
    """与桌面版同名字段：box / ocr_text / score。"""

    def __init__(self, box=None, text="", score=0.0):
        self.box = box
        self.ocr_text = text
        self.score = score

    @property
    def text(self):
        return self.ocr_text

    def __repr__(self):
        return f"<BoxedResult text={self.ocr_text!r} score={self.score}>"


def _encode_png(image: np.ndarray) -> bytes:
    import cv2

    array = np.asarray(image)
    if array.ndim == 2:
        array = cv2.cvtColor(array, cv2.COLOR_GRAY2RGB)
    if array.shape[2] == 3:
        # OAS 内部是 RGB；cv2.imencode 需要 BGR
        array = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
    elif array.shape[2] == 4:
        array = cv2.cvtColor(array, cv2.COLOR_RGBA2BGR)
    ok, buffer = cv2.imencode(".png", array)
    if not ok:
        raise RuntimeError("encode png failed")
    return buffer.tobytes()


def _recognize(image: np.ndarray) -> list:
    if _OCR is None:
        raise RuntimeError("OcrBridge unavailable (not on Android)")
    payload = str(_OCR.recognize(_encode_png(image)))
    items = json.loads(payload)
    results = []
    for item in items:
        box = item.get("box") or [0, 0, 0, 0]
        results.append(BoxedResult(
            box=np.array([[box[0], box[1]], [box[2], box[1]],
                          [box[2], box[3]], [box[0], box[3]]], dtype=np.float32),
            text=str(item.get("text", "")),
            score=float(item.get("score", 0.0)),
        ))
    return results


class TextSystem:
    """与桌面版同名同签名；忽略模型参数（ML Kit 自带模型）。"""

    def __init__(self, use_angle_cls=False, box_thresh=0.6, unclip_ratio=1.6,  # noqa: ARG002
                 rec_model_path=None, det_model_path=None, ort_providers=None):  # noqa: ARG002
        if _OCR is None:
            raise RuntimeError("OCR bridge unavailable (not on Android)")

    @staticmethod
    def _preprocess_variants(array):
        """按「原图 → 灰度 → 高对比 → 放大 + 高对比」给出一串候选。

        游戏 UI 常见「半透明白字叠在插画上」：直接 OCR 会漏，先提亮部/放大再识别命中率明显更高。
        """
        import cv2

        def pad(image, border=24, value=0):
            return cv2.copyMakeBorder(image, border, border, border, border,
                                      cv2.BORDER_CONSTANT, value=value)

        yield array
        gray = array if array.ndim == 2 else cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        yield gray
        _, bright = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
        yield bright
        yield cv2.bitwise_not(gray)
        # 贴边截断的字：加边（黑底/白底）后再识别
        yield pad(array)
        yield pad(bright)
        for base in (array, bright):
            for scale in (2.0, 3.0):
                yield cv2.resize(base, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        for base in (array, bright):
            yield cv2.resize(pad(base), None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)

    # OAS: module/ocr/base_ocr.py 里 `result, score = self.model.ocr_single_line(image)`
    def ocr_single_line(self, image):
        array = np.asarray(image)
        results = _recognize(array)
        if not results:
            for variant in self._preprocess_variants(array):
                results = _recognize(np.ascontiguousarray(variant))
                if results:
                    break
        if not results:
            return "", 0.0
        best = max(results, key=lambda r: r.score)
        text = best.ocr_text.strip()
        # ML Kit 会把「进入游戏」拆成多行/多段时，取拼接后的最长结果更稳
        if len(results) > 1:
            joined = "".join(r.ocr_text.strip() for r in sorted(
                results, key=lambda r: (float(np.asarray(r.box)[0][1]), float(np.asarray(r.box)[0][0]))))
            if len(joined) > len(text):
                return joined, max(r.score for r in results)
        return text, best.score

    # OAS: RuleOcr.detect_and_ocr / module.atom.list
    def detect_and_ocr(self, image, **kwargs):  # noqa: ARG002
        return _recognize(np.asarray(image))

    def detect(self, image):  # 兼容调用
        return [r.box for r in _recognize(np.asarray(image))]


def sorted_boxes(boxes):
    """按「从上到下、从左到右」排序（OAS 会把本函数替换成它自己的实现）。"""
    return sorted(list(boxes), key=lambda b: (float(b[0][1]), float(b[0][0])))
