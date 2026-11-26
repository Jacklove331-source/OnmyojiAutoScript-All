"""OCR 前把帧缩小：vivo 上 1260x2800 直接喂 PaddleOCR 会卡到分钟级。

真机栈证据：`login.ensure_logged_in` 卡在 `system.detect_and_ocr(frame)` 不返回，
日志停在 "login: starting via OCR"。这里把帧按宽度缩到 720 再 OCR，
并记录 OCR 耗时，供以后调阈值。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\login.py"

OLD = '''        try:
            results = system.detect_and_ocr(frame)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"[mobile] ocr failed: {error!r}")
            results = []'''

NEW = '''        try:
            ocr_frame = _shrink_for_ocr(frame)
            started = time.perf_counter()
            results = system.detect_and_ocr(ocr_frame)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if elapsed_ms > 3000:
                logger.warning(f"[mobile] ocr slow: {elapsed_ms:.0f}ms frame={ocr_frame.shape}")
        except Exception as error:  # noqa: BLE001
            logger.warning(f"[mobile] ocr failed: {error!r}")
            results = []'''

HELPER_ANCHOR = '''def ensure_logged_in(display: int, timeout: float = 240.0) -> bool:'''

HELPER = '''def _shrink_for_ocr(frame, max_width: int = 720):
    """按宽度等比缩小后再 OCR：大图在手机上会把 PaddleOCR 拖到分钟级。"""
    try:
        import cv2

        height, width = int(frame.shape[0]), int(frame.shape[1])
        if width <= max_width:
            return frame
        scale = max_width / float(width)
        return cv2.resize(frame, (max_width, max(1, int(round(height * scale)))),
                          interpolation=cv2.INTER_AREA)
    except Exception:  # noqa: BLE001
        return frame


def ensure_logged_in(display: int, timeout: float = 240.0) -> bool:'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
for old, new, label in ((OLD, NEW, "ocr call"), (HELPER_ANCHOR, HELPER, "helper")):
    if old not in text:
        raise SystemExit(f"anchor missing: {label}")
    text = text.replace(old, new, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log34.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
