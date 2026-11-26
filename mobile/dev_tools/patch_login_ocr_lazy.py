"""登录判定改造：模板优先，OCR 降级为低频兜底 + 硬超时。

真机实测：1260×2800 帧首帧 PaddleOCR 80 秒不返回，登录循环整轮被拖死，
日志停在 "login: starting via OCR"。改法：
1. TextSystem 单例懒加载，启动后不重复构造；
2. OCR 只每 3 轮跑一次，且放在独立线程里 join(20s)，超时直接跳过本轮；
3. OCR 结果只用于兜底判定（模板匹配仍然在它后面，保持原逻辑）。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\login.py"

OLD_INIT = '''    from ppocronnx.predict_system import TextSystem

    system = TextSystem()
    deadline = time.time() + timeout'''

NEW_INIT = '''    deadline = time.time() + timeout'''

OLD_OCR = '''        try:
            ocr_frame = _shrink_for_ocr(frame)
            started = time.perf_counter()
            results = system.detect_and_ocr(ocr_frame)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if elapsed_ms > 3000:
                logger.warning(f"[mobile] ocr slow: {elapsed_ms:.0f}ms frame={ocr_frame.shape}")
        except Exception as error:  # noqa: BLE001
            logger.warning(f"[mobile] ocr failed: {error!r}")
            results = []
        texts = [r.ocr_text for r in results]
        logger.info(f"[mobile] screen texts: {texts[:6]}")'''

NEW_OCR = '''        # OCR 降级：每 3 轮才跑一次，且带硬超时；模板匹配仍是主判据
        results = []
        if loop_index % 3 == 0:
            results = _ocr_with_timeout(frame, timeout=20.0)
        loop_index += 1
        texts = [r.ocr_text for r in results]
        if texts:
            logger.info(f"[mobile] screen texts: {texts[:6]}")'''

HELPER_ANCHOR = "def ensure_logged_in(display: int, timeout: float = 240.0) -> bool:"

HELPER = '''_OCR_SYSTEM = None


def _ocr_system():
    """PaddleOCR 单例：构造一次就缓存，避免每轮重复初始化。"""
    global _OCR_SYSTEM
    if _OCR_SYSTEM is None:
        from ppocronnx.predict_system import TextSystem

        _OCR_SYSTEM = TextSystem()
    return _OCR_SYSTEM


def _ocr_with_timeout(frame, timeout: float = 20.0):
    """OCR 放子线程跑，超时就放弃本轮，避免整轮被拖死。"""
    import threading

    box = {}

    def _work():
        try:
            box["results"] = _ocr_system().detect_and_ocr(_shrink_for_ocr(frame))
        except Exception as error:  # noqa: BLE001
            box["error"] = error

    worker = threading.Thread(target=_work, daemon=True)
    started = time.perf_counter()
    worker.start()
    worker.join(timeout)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if worker.is_alive():
        logger.warning(f"[mobile] ocr timeout after {elapsed_ms:.0f}ms, skip this round")
        return []
    if "error" in box:
        logger.warning(f"[mobile] ocr failed: {box['error']!r}")
        return []
    if elapsed_ms > 3000:
        logger.warning(f"[mobile] ocr slow: {elapsed_ms:.0f}ms")
    return box.get("results") or []


def ensure_logged_in(display: int, timeout: float = 240.0) -> bool:'''

OLD_LOOP = '''    taps = 0
    idle = 0
    while time.time() < deadline:'''

NEW_LOOP = '''    taps = 0
    idle = 0
    loop_index = 0
    while time.time() < deadline:'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
pairs = [
    (OLD_INIT, NEW_INIT, "init"),
    (OLD_OCR, NEW_OCR, "ocr block"),
    (HELPER_ANCHOR, HELPER, "helpers"),
    (OLD_LOOP, NEW_LOOP, "loop counters"),
]
for old, new, label in pairs:
    if old not in text:
        raise SystemExit(f"anchor missing: {label}")
    text = text.replace(old, new, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log36.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-500:]))
