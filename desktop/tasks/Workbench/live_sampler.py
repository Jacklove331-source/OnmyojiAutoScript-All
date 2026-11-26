# -*- coding: utf-8 -*-
"""实时货币采样器：adb 截图 → OCR 顶栏 → 写 log/workbench/live_currency.json

用途：OASX 资产页轮询这个 JSON，做到「像后台监控一样自动更新」。
精度说明：游戏顶栏本身是简写（2651万 / 2.0万），所以这里拿到的是「万级近似值」，
不是精确到个位的真实余额；要精确值必须走游戏内存读取，见 README。

用法：
    toolkit\\python.exe tasks\\Workbench\\live_sampler.py            # 循环跑
    toolkit\\python.exe tasks\\Workbench\\live_sampler.py --once     # 采一次就退出（调试用）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ADB = r"E:\MuMuPlayer\nx_device\12.0\shell\adb.exe"
DEVICE = "127.0.0.1:7555"
PACKAGE = "com.netease.onmyoji.wyzymnqsd_cps"

OUT_DIR = os.path.join(ROOT, "log", "workbench")
OUT_FILE = os.path.join(OUT_DIR, "live_currency.json")

# 顶栏三个货币的裁剪框（1280x720 坐标系，x1,y1,x2,y2）
ROIS = {
    "coin": (470, 8, 600, 50),      # 金币（左侧金色）
    "action_point": (725, 8, 830, 50),  # 体力（中间红色）
    "jade": (895, 8, 1010, 50),     # 勾玉（右侧橙色）
}

CN_NUM = {"万": 10000, "亿": 100000000, "千": 1000}


def parse_cn_number(text: str):
    """把 '2651万' / '2.0万' / '1105' 解析成整数；解析不了返回 None。"""
    if not text:
        return None
    t = text.strip().replace(" ", "").replace(",", "")
    mult = 1
    for k, v in CN_NUM.items():
        if t.endswith(k):
            mult = v
            t = t[:-1]
            break
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        return int(float(m.group(1)) * mult)
    except ValueError:
        return None


def adb(args, timeout=25):
    return subprocess.run(
        [ADB, "-s", DEVICE] + args,
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


def ensure_device() -> bool:
    r = adb(["shell", "echo", "ok"])
    if "ok" in (r.stdout or ""):
        return True
    adb(["connect", DEVICE])
    r = adb(["shell", "echo", "ok"])
    return "ok" in (r.stdout or "")


def game_running() -> bool:
    r = adb(["shell", "pidof", PACKAGE])
    return bool((r.stdout or "").strip())


def sample_once(cv2, ocr_model) -> dict:
    tmp_local = os.path.join(tempfile.gettempdir(), "oasx_live_shot.png")
    r = adb(["shell", "screencap", "-p", "/sdcard/_oasx_live.png"])
    if r.returncode != 0:
        raise RuntimeError(f"screencap failed: {r.stderr[:120]}")
    r = adb(["pull", "/sdcard/_oasx_live.png", tmp_local])
    if not os.path.exists(tmp_local):
        raise RuntimeError(f"pull failed: {r.stderr[:120]}")

    img = cv2.imread(tmp_local)
    if img is None:
        raise RuntimeError("cv2.imread returned None")

    values, raw = {}, {}
    for key, (x1, y1, x2, y2) in ROIS.items():
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        try:
            text, score = ocr_model.ocr_single_line(crop)
        except Exception as e:  # OCR 单点失败不影响其它货币
            raw[key] = f"ocr-error:{e}"
            continue
        raw[key] = f"{text}@{score:.2f}"
        val = parse_cn_number(text)
        if val is not None and score >= 0.5:
            values[key] = val
    return {"values": values, "raw": raw}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只采一次")
    ap.add_argument("--interval", type=int, default=30, help="采样间隔秒，默认 30")
    args = ap.parse_args()

    import cv2  # noqa: F401
    from module.ocr.models import get_ocr_model

    os.makedirs(OUT_DIR, exist_ok=True)
    ocr_model = get_ocr_model()

    while True:
        started = time.time()
        payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": False}
        try:
            if not ensure_device():
                payload["reason"] = "adb 设备未连接"
            elif not game_running():
                payload["reason"] = "游戏未运行"
            else:
                got = sample_once(cv2, ocr_model)
                if got["values"]:
                    payload.update(ok=True, source="顶栏 OCR（万级近似）", **got)
                else:
                    payload.update(reason="顶栏没识别出数字", **got)
        except Exception as e:
            payload["reason"] = f"{type(e).__name__}: {e}"

        payload["elapsed_ms"] = int((time.time() - started) * 1000)
        try:
            tmp = OUT_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(tmp, OUT_FILE)
        except Exception as e:
            print(f"[live] 写文件失败: {e}")

        print(f"[live] ok={payload['ok']} values={payload.get('values')} "
              f"raw={payload.get('raw')} reason={payload.get('reason', '')}")

        if args.once:
            return 0
        time.sleep(max(5, args.interval - int(time.time() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
