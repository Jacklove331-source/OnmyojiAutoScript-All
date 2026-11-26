# This file uses the following encoding: utf-8
"""数据读取 —— 实机通道：adb 抓帧 → OAS ppocr → 文本行 → 御魂对象。

依赖 OAS 自带 OCR（`module.ocr.models.OCR_MODEL`），必须在仓库根目录、用 toolkit python 跑。
没有装 ppocronnx 时 `available()` 返回 False，调用方回退到 JSON 导入。

与 `ingest/__init__.py` 的分工：
    ingest/__init__.py  —— JSON 档案 + 纯文本行解析（无依赖、可在任何环境跑）
    ingest/screen.py    —— 抓帧 + OCR（依赖 OAS 运行时）
"""
from __future__ import annotations

import io
import json
import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from tasks.Workbench.ingest import parse_ocr_lines

ADB = r"E:\MuMuPlayer\nx_main\adb.exe"


# ---------------------------------------------------------------- 抓帧
def capture(serial: str, out_path: str) -> Dict[str, Any]:
    """adb exec-out 抓帧落盘（不要走 PowerShell 文本重定向，会破坏二进制）。"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    try:
        with open(out_path, "wb") as fh:
            p = subprocess.run([ADB, "-s", serial, "exec-out", "screencap", "-p"],
                               stdout=fh, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, timeout=60)
        size = os.path.getsize(out_path)
        # PNG 体积随画面内容变化（大面积纯色可能只有几十 KB），只要求是有效图片
        ok = p.returncode == 0 and size > 8192 and open(out_path, "rb").read(8) == b"\x89PNG\r\n\x1a\n"
        return {"ok": ok, "path": out_path, "bytes": size}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "path": out_path, "reason": str(exc)}


# ---------------------------------------------------------------- OCR
def available() -> bool:
    try:
        import cv2  # noqa: F401
        import ppocronnx.predict_system  # noqa: F401
        return True
    except Exception:
        return False


_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        from module.ocr.models import OCR_MODEL
        _MODEL = OCR_MODEL.ch
    return _MODEL


def ocr_rows(image_path: str) -> List[Dict[str, Any]]:
    """返回 [{text,x,y,x2,y2,cx,cy}]，按 y 再按 x 排序（= 阅读顺序）。"""
    import cv2
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(image_path)
    rows = []
    for r in _model().detect_and_ocr(img):
        text = getattr(r, "ocr_text", "") or getattr(r, "text", "")
        box = getattr(r, "box", None)
        xs = [p[0] for p in box] if box is not None else []
        ys = [p[1] for p in box] if box is not None else []
        rows.append({
            "text": str(text),
            "x": int(min(xs)) if xs else 0, "y": int(min(ys)) if ys else 0,
            "x2": int(max(xs)) if xs else 0, "y2": int(max(ys)) if ys else 0,
            "cx": int((min(xs) + max(xs)) / 2) if xs else 0,
            "cy": int((min(ys) + max(ys)) / 2) if ys else 0,
        })
    rows.sort(key=lambda r: (r["y"], r["x"]))
    return rows


def find_text(rows: List[Dict[str, Any]], keyword: str) -> List[Dict[str, Any]]:
    return [r for r in rows if keyword in r["text"]]


# ---------------------------------------------------------------- 组合
def souls_from_rows(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """把 OCR 行喂给 ingest.parse_ocr_lines（保持 y,x 顺序）。"""
    return parse_ocr_lines([r["text"] for r in rows])


def snapshot_from_screen(serial: str, out_png: str,
                         dump_json: Optional[str] = None) -> Dict[str, Any]:
    """抓一帧 → OCR → 尝试还原御魂；返回 snapshot 结构。"""
    cap = capture(serial, out_png)
    if not cap.get("ok"):
        return {"ok": False, "stage": "capture", "detail": cap}
    if not available():
        return {"ok": False, "stage": "ocr", "reason": "ppocronnx 未安装（用 toolkit python 跑）",
                "frame": out_png}
    rows = ocr_rows(out_png)
    souls, unparsed = souls_from_rows(rows)
    payload = {"source": "screen", "serial": serial, "frame": out_png,
               "rows": len(rows), "souls": souls, "unparsed": unparsed}
    if dump_json:
        io.open(dump_json, "w", encoding="utf-8").write(
            json.dumps(payload, ensure_ascii=False, indent=1))
    return {"ok": True, "frame": out_png, "rows": len(rows),
            "souls": souls, "unparsed": unparsed, "dump": dump_json}
