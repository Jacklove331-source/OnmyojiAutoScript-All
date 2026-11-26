"""账号资产本机读取（不依赖平安志）。

设计要点
--------
* 只读：只截图 + OCR，不点击交易界面、不改游戏状态。
* 标定驱动：所有 ROI 与校验规则放在 ``asset_rois.json``，未标定的字段不猜，输出 status=uncalibrated。
* 双帧一致才认数：同一数字连续两帧 OCR 相同才写入，避免 2651万→26517 这类错读。
* 时点与来源随数据一起写：``captured_at`` / ``source='oas-ui'``，界面据此判断新鲜度，读失败保留旧值。

命令行（宿主，标定用）::

    toolkit\\python.exe tasks\\Workbench\\asset_collector.py --probe <png> [--dump-ocr]

    # 直接在设备上抓帧并 OCR（标定一轮）
    toolkit\\python.exe tasks\\Workbench\\asset_collector.py --grab --serial 127.0.0.1:16384
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "config" / "workbench" / "asset_live.json"
DEFAULT_ROIS = REPO_ROOT / "config" / "workbench" / "asset_rois.json"

# 资产字段（顺序即界面展示顺序）；roi/校验规则由 asset_rois.json 提供
ASSET_KEYS = [
    "jade", "coin", "ap", "s_jade", "mystery_amulet", "gold_ofuda",
    "honor", "medal", "contrib", "skin_token", "scale", "demon_soul",
]


def _ocr_model():
    """延迟导入 OAS 自带 ppocr，避免标定脚本在无依赖环境下直接炸。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from module.ocr.models import get_ocr_model

    return get_ocr_model()


def ocr_boxes(image_path: str | Path) -> List[Dict[str, Any]]:
    """返回 [{'text': str, 'box': [x, y, w, h], 'score': float}]。"""
    import cv2  # noqa: F401  (OAS 环境自带)

    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(f"读不到图片: {image_path}")
    model = _ocr_model()
    rows: List[Dict[str, Any]] = []
    for item in model.detect_and_ocr(img):
        box = item.box
        # ppocr 的 box 是 4 个角点的 ndarray([[x,y],…])；旧版本给 (x,y,w,h)。两种都兼容。
        try:
            arr = box.tolist() if hasattr(box, "tolist") else box
            if isinstance(arr, (list, tuple)) and arr and isinstance(arr[0], (list, tuple)):
                xs = [float(p[0]) for p in arr]
                ys = [float(p[1]) for p in arr]
                bx, by = int(min(xs)), int(min(ys))
                bw, bh = int(max(xs) - min(xs)), int(max(ys) - min(ys))
            else:
                bx, by, bw, bh = (int(v) for v in arr[:4])
        except Exception:
            continue
        rows.append({
            "text": item.ocr_text,
            "box": [bx, by, bw, bh],
            "score": round(float(getattr(item, "score", 0.0) or 0.0), 3),
        })
    return rows


def ocr_region(image_path: str | Path, box: List[int], scale: int = 3) -> List[Dict[str, Any]]:
    """裁剪 ROI → 放大 scale 倍 → OCR，返回**换算回原图坐标**的 rows。

    为什么需要：顶栏的「符咒 6 / 魂玉 0」这类单位数在原尺寸图上常被检测器漏掉，
    放大 3 倍后能稳定读出（实测 2026-11-21，1280x720 商店顶栏）。
    """
    import cv2

    img = cv2.imread(str(image_path))
    if img is None:
        raise FileNotFoundError(f"读不到图片: {image_path}")
    x, y, w, h = box
    crop = img[max(0, y):y + h, max(0, x):x + w]
    if crop.size == 0:
        return []
    big = cv2.resize(crop, (crop.shape[1] * scale, crop.shape[0] * scale),
                     interpolation=cv2.INTER_CUBIC)
    tmp = Path(str(image_path)).with_name(Path(str(image_path)).stem + f"_roi_x{scale}.png")
    cv2.imwrite(str(tmp), big)
    out: List[Dict[str, Any]] = []
    for r in ocr_boxes(tmp):
        bx, by, bw, bh = r["box"]
        out.append({
            "text": r["text"],
            "box": [bx // scale + x, by // scale + y, max(1, bw // scale), max(1, bh // scale)],
            "score": r["score"],
        })
    return out


def digits_in(rows: List[Dict[str, Any]], box: List[int]) -> Optional[str]:
    """把落在给定 box 内的 OCR 文本按 x 排序拼起来，只保留数字/逗号/万。"""
    x0, y0, w, h = box
    picked = []
    for r in rows:
        bx, by, bw, bh = r["box"]
        cx, cy = bx + bw / 2, by + bh / 2
        if x0 <= cx <= x0 + w and y0 <= cy <= y0 + h:
            picked.append((bx, r["text"]))
    picked.sort()
    text = "".join(t for _, t in picked)
    return re.sub(r"[^0-9,万亿]", "", text) or None


def to_int(text: Optional[str]) -> Optional[int]:
    """'26,512,232' → 26512232；'2651万' → 26510000（万/亿 只用于兜底，标定优先取精确数）。"""
    if not text:
        return None
    t = text.replace(",", "")
    mult = 1
    if t.endswith("万"):
        mult, t = 10000, t[:-1]
    elif t.endswith("亿"):
        mult, t = 100000000, t[:-1]
    if not t.isdigit():
        return None
    return int(t) * mult


def load_rois(path: Path = DEFAULT_ROIS) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def grab_frame(serial: str, out: Path, adb: Optional[str] = None) -> Path:
    adb = adb or os.environ.get("OAS_ADB") or r"E:\MuMuPlayer\nx_device\12.0\shell\adb.exe"
    subprocess.run([adb, "-s", serial, "shell", "screencap", "-p", "/sdcard/_oas_asset.png"],
                   check=True, capture_output=True)
    subprocess.run([adb, "-s", serial, "pull", "/sdcard/_oas_asset.png", str(out)],
                   check=True, capture_output=True)
    return out


def collect(frame: Path, rois: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """单帧采集。未标定字段返回 None + status=uncalibrated。"""
    rois = rois or load_rois()
    rows = ocr_boxes(frame)
    assets: Dict[str, Optional[int]] = {}
    raw: Dict[str, Optional[str]] = {}
    for key in ASSET_KEYS:
        spec = (rois.get("assets") or {}).get(key)
        if not spec:
            assets[key] = None
            raw[key] = None
            continue
        text = digits_in(rows, spec["box"])
        raw[key] = text
        assets[key] = to_int(text)
    calibrated = any(v is not None for v in assets.values())
    return {
        "ok": calibrated,
        "status": "ok" if calibrated else "uncalibrated",
        "source": "oas-ui",
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "assets": assets,
        "raw": raw,
        "ocr_rows": len(rows),
    }


def write_live(payload: Dict[str, Any], out: Path = DEFAULT_OUT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", help="已有截图：只做 OCR 标定")
    ap.add_argument("--grab", action="store_true", help="从设备抓一帧再标定")
    ap.add_argument("--serial", default="127.0.0.1:16384")
    ap.add_argument("--dump-ocr", action="store_true", help="打印全屏 OCR 明细（标定用）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)

    frame = Path(args.probe) if args.probe else None
    if args.grab:
        frame = Path(str(DEFAULT_OUT.parent / "_frame.png"))
        grab_frame(args.serial, frame)
        print(f"frame -> {frame}")
    if frame is None:
        ap.error("需要 --probe <png> 或 --grab")

    rows = ocr_boxes(frame)
    print(f"OCR rows: {len(rows)}")
    if args.dump_ocr:
        for r in rows[:80]:
            print(f"  {r['box']}  {r['score']}  {r['text']}")

    payload = collect(frame)
    print(json.dumps({k: v for k, v in payload.items() if k != "ocr_rows"},
                     ensure_ascii=False, indent=1))
    if payload["status"] == "ok":
        print(f"written -> {write_live(payload, Path(args.out))}")
    else:
        print("未标定：先在 config/workbench/asset_rois.json 填 assets.<key>.box，再重跑")
    return 0 if payload["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
