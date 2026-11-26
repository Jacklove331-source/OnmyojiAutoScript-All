"""登录门降级：限时 25 秒、每轮量化耗时、模板分数一律打日志。

真机实测：登录循环单轮就要几十秒（截帧+匹配/OCR），240 秒预算导致任务"停在门口"
看起来像死掉。改成：
1. 无论上层传多少，内部最多用 25 秒；
2. 每轮打一行 "round=N cost=...ms"；
3. 模板分数无论命中与否都记日志（原来只在命中时打，失败时完全静默，排查很难）。
"""

import io
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\login.py"

A_OLD = "    deadline = time.time() + timeout"
A_NEW = """    # 真机上单轮就要几十秒：把登录门压到 25 秒，超时就交给上层继续（不让任务卡在门口）
    timeout = min(float(timeout), 25.0)
    deadline = time.time() + timeout"""

B_OLD = """            home = matcher.match("home_explore.png", png)
            if home.get("ok") and home.get("score", 0) >= 0.80:"""
B_NEW = """            home = matcher.match("home_explore.png", png)
            logger.info(f"[mobile] match home_explore score={home.get('score')} ok={home.get('ok')}")
            if home.get("ok") and home.get("score", 0) >= 0.80:"""

C_OLD = """            enter = matcher.match("login_enter_game.png", png)
            if enter.get("ok") and enter.get("score", 0) >= 0.85:"""
C_NEW = """            enter = matcher.match("login_enter_game.png", png)
            logger.info(f"[mobile] match login_enter_game score={enter.get('score')} ok={enter.get('ok')}")
            if enter.get("ok") and enter.get("score", 0) >= 0.85:"""

D_OLD = "    loop_index = 0\n    while time.time() < deadline:"
D_NEW = """    loop_index = 0
    while time.time() < deadline:
        round_started = time.perf_counter()"""

E_OLD = "        loop_index += 1"
E_NEW = """        loop_index += 1
        logger.info(f"[mobile] login round={loop_index} cost={(time.perf_counter() - round_started) * 1000:.0f}ms")"""

text = io.open(PATH, "rb").read().decode("utf-8-sig")
for old, new, label in ((A_OLD, A_NEW, "clamp"), (B_OLD, B_NEW, "home log"),
                        (C_OLD, C_NEW, "enter log"), (D_OLD, D_NEW, "round start"),
                        (E_OLD, E_NEW, "round cost")):
    if old not in text:
        raise SystemExit(f"anchor missing: {label}")
    text = text.replace(old, new, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH], capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log37.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-500:]))
