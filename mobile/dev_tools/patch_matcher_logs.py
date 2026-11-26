"""给模板匹配加日志 + 默认降采样，解决"看起来卡住"。

症状：Chess 任务 instantiated 后 log_tail 不再增长，线程栈显示卡在
matcher.match → cv2.matchTemplate（720x1280 全分辨率 × 5 个尺度，无日志、无耗时记录）。

做法：
1. 调用方不传 max_frame_width 时默认降到 540 宽再匹配（面积约少一半，速度明显快）；
2. 每次匹配打一行 info：素材名、耗时 ms、最佳分数、命中尺度 —— 之后"卡住"能一眼看出卡在哪个素材；
3. 单个尺度超过 8 秒打 warning，便于以后继续调。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\matcher.py"

OLD_SIG = '''def match(asset_name: str, frame_png: bytes, scales=(0.8, 0.9, 1.0, 1.1, 1.25),
          max_frame_width: int = 0) -> dict:'''

NEW_SIG = '''def match(asset_name: str, frame_png: bytes, scales=(0.8, 0.9, 1.0, 1.1, 1.25),
          max_frame_width: int = 540) -> dict:'''

OLD_LOOP = '''    best = {"score": -1.0, "loc": None, "scale": 1.0}
    for scale in scales:'''

NEW_LOOP = '''    import time

    from module.logger import logger

    started = time.perf_counter()
    best = {"score": -1.0, "loc": None, "scale": 1.0}
    for scale in scales:'''

OLD_TAIL = '''    loc = best["loc"]
    if loc is not None and coord_scale != 1.0:'''

NEW_TAIL = '''    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if elapsed_ms > 8000:
        logger.warning(f'[match] {asset_name} slow: {elapsed_ms:.0f}ms frame={orig_w}x{orig_h}')
    else:
        logger.info(f'[match] {asset_name} {elapsed_ms:.0f}ms score={best["score"]:.3f} scale={best["scale"]}')

    loc = best["loc"]
    if loc is not None and coord_scale != 1.0:'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
for old, new, label in (
    (OLD_SIG, NEW_SIG, "sig"),
    (OLD_LOOP, NEW_LOOP, "loop"),
    (OLD_TAIL, NEW_TAIL, "tail"),
):
    if old not in text:
        raise SystemExit(f"anchor missing: {label}")
    text = text.replace(old, new, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log31.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
