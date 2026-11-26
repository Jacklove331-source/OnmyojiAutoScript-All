"""实时资产监控（按配置）：读 OAS 实例自己产出的实时帧 log/live/<config>.jpg，
OCR 顶部栏拿勾玉 / 金币 / 体力，**不点设备、不抢托管**。

门槛（实测 2026-11-21，1280x720 横屏）：
  * 只认庭院帧：命中「探索/町中」，或顶栏呈 金币(万)+勾玉+体力(万) 三段固定排布；
  * 数值只取 y<60 且 x>=450 的行（跳过电量/时钟/昵称），按 x 升序 = 金币→勾玉→体力；
  * 勾玉 <1万 才是精确整数；显示成「11万」这类简写只能记 approx；
  * 某账号已有精确值时，实时帧的简写**不覆盖**它（只更新"尝试时间"，不降级）。

启动：
  toolkit\\python.exe dev_tools\\asset_live_watch.py --tick 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tasks" / "Workbench"))

import asset_store as S  # noqa: E402

WB = REPO / "config" / "workbench"
LIVE_DIR = REPO / "log" / "live"
LOG = REPO / "log" / "asset_live_watch.log"
FRAME_MAX_AGE = 90
TOP_Y = 60
LEFT_X = 450          # 顶栏资源区左界：跳过电量(10)/时钟(136)/昵称
COURT_MARKERS = ("探索", "町中")


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def configs() -> list[str]:
    return sorted(p.stem for p in (REPO / "config").glob("oas*.json"))


def serial_of(config: str) -> str:
    try:
        cfg = json.loads((REPO / "config" / f"{config}.json").read_text(encoding="utf-8"))
        ser = cfg["script"]["device"]["serial"]
        return ser if isinstance(ser, str) else (ser[0] if ser else "")
    except Exception:
        return ""


def top_values(rows) -> list[dict]:
    """顶栏资源数值：y<60 且 x>=450，按 x 升序（金币 → 勾玉 → 体力）。"""
    vals: list[dict] = []
    for r in rows:
        x, y, w, h = r["box"]
        if y >= TOP_Y or x < LEFT_X:
            continue
        t = r["text"].replace(",", "").strip()
        m = re.fullmatch(r"(\d+(?:\.\d+)?)([万亿])?\s*[+＋]?", t)
        if not m:
            continue
        unit = m.group(2) or ""
        mult = {"万": 10000, "亿": 100000000}.get(unit, 1)
        vals.append({"x": x, "text": r["text"], "unit": unit,
                     "num": int(round(float(m.group(1)) * mult))})
    vals.sort(key=lambda v: v["x"])
    return vals


def parse_top(rows) -> dict:
    """只认庭院顶栏。不是庭院结构就返回 in_court=False（调用方不写值）。"""
    texts = "|".join(r["text"] for r in rows)
    has_marker = any(m in texts for m in COURT_MARKERS)
    vals = top_values(rows)
    shaped = bool(vals) and len(vals) >= 3 and vals[0]["unit"] == "万" and vals[2]["unit"] == "万"
    if not (has_marker or shaped) or len(vals) < 3:
        return {"in_court": False, "marker": has_marker, "vals": vals,
                "jade": None, "jade_exact": False, "coin": None, "ap": None}
    coin, jade, ap = vals[0], vals[1], vals[2]
    return {
        "in_court": True, "marker": has_marker, "vals": vals,
        "coin": coin["num"], "coin_text": coin["text"],
        "jade": jade["num"], "jade_exact": jade["unit"] == "", "jade_text": jade["text"],
        "ap": ap["num"], "ap_text": ap["text"],
    }


def _cur(items: dict, key: str) -> dict:
    return dict(items.get(key) or {})


def tick_once() -> None:
    from asset_collector import ocr_boxes

    for cfg in configs():
        frame = LIVE_DIR / f"{cfg}.jpg"
        if not frame.exists():
            continue
        age = time.time() - frame.stat().st_mtime
        if age > FRAME_MAX_AGE:
            continue
        rows = ocr_boxes(frame)
        info = parse_top(rows)
        before = S.load(WB, cfg)
        items = before.get("items") or {}
        readings: dict = {}

        if not info["in_court"]:
            # 只写失败原因 + 尝试时间：不带 precision，避免把已有 approx 值升格成 exact
            readings["jade"] = {"value": None, "source": "live-frame",
                                "error": "实时帧非庭院（无可信顶栏）"}
        else:
            jade_cur = _cur(items, "jade")
            if info["jade_exact"]:
                readings["jade"] = {"value": info["jade"], "precision": "exact",
                                    "source": "live-frame", "error": None}
            elif jade_cur.get("precision") == "exact" and jade_cur.get("value") is not None:
                readings["jade"] = {"value": None, "touch": True}      # 简写不覆盖精确值
            else:
                readings["jade"] = {"value": info["jade"], "precision": "approx",
                                    "source": "live-frame", "error": None,
                                    "text": info["jade_text"]}
            readings["coin"] = {"value": info["coin"], "precision": "approx",
                                "source": "live-frame", "error": None, "text": info["coin_text"]}
            ap_cur = _cur(items, "ap")
            if ap_cur.get("precision") == "exact" and ap_cur.get("value") is not None:
                readings["ap"] = {"value": None, "touch": True}
            else:
                readings["ap"] = {"value": info["ap"], "precision": "approx",
                                  "source": "live-frame", "error": None, "text": info["ap_text"]}

        state = S.merge(WB, cfg, serial=serial_of(cfg), readings=readings,
                        raw={"live_frame": str(frame.name), "top": info},
                        source="live-frame")
        after = state.get("items") or {}
        for key in ("jade", "coin", "ap"):
            old = _cur(items, key).get("value")
            new = _cur(after, key).get("value")
            if new is not None and new != old:
                log(f"[{cfg}] {key} {old} -> {new} "
                    f"(precision={(after.get(key) or {}).get('precision')} frame age {int(age)}s)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tick", type=int, default=10)
    args = ap.parse_args()
    log(f"live watch start tick={args.tick}s configs={configs()}")
    while True:
        try:
            tick_once()
        except KeyboardInterrupt:
            log("stop")
            return 0
        except Exception:
            log("error:\n" + traceback.format_exc())
        time.sleep(args.tick)


if __name__ == "__main__":
    raise SystemExit(main())
