"""OAS Mobile asset monitor.

Read-only asset capture and storage for Onmyoji Assistant Mobile.

The parsing and storage functions are pure Python and can be tested on a host
without Android, OpenCV, NumPy or ppocronnx.  Live capture/OCR dependencies are
imported lazily inside the capture functions only.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Iterable, Optional

ASSET_KEYS = ["jade", "coin", "ap", "mystery_amulet", "skin_token", "s_jade"]
ASSET_LABELS = {
    "jade": "勾玉",
    "coin": "金币",
    "ap": "体力",
    "mystery_amulet": "神秘符咒",
    "skin_token": "皮肤券",
    "s_jade": "魂玉",
}

_LOCK = threading.Lock()
_STATUS: dict[str, Any] = {"running": False, "config": "", "last_error": "", "phase": ""}
_FRAME_SIZE: tuple[int, int] = (1280, 720)

_OCR_ENGINE: Any = None
_OCR_INIT_LOCK = threading.Lock()

_NUM_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(亿|万)?")
_STAMINA_RE = re.compile(r"[已己巳]?拥有[:：\s]*(\d[\d,]*)")


# ---------------------------------------------------------------------------
# Time / storage helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _assets_dir(workspace: str) -> str:
    return os.path.join(str(workspace), "assets")


def _safe_config_name(config: str) -> str:
    name = str(config or "oas1").strip() or "oas1"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def _snapshot_path(workspace: str, config: str) -> str:
    return os.path.join(_assets_dir(workspace), _safe_config_name(config) + ".json")


def _history_path(workspace: str, config: str) -> str:
    return os.path.join(
        _assets_dir(workspace), _safe_config_name(config) + "_history.json"
    )


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return default


def _save_json(path: str, data: Any) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _coerce_int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return None


def _empty_item() -> dict[str, Any]:
    return {
        "value": None,
        "precision": None,
        "source": "",
        "last_ok_at": None,
        "text": "",
    }


def _empty_snapshot(config: str, running: bool = False) -> dict[str, Any]:
    return {
        "version": 3,
        "config": config,
        "status": "idle",
        "updated_date": "",
        "captured_at": "",
        "last_attempt_at": "",
        "last_error": "",
        "items": {key: _empty_item() for key in ASSET_KEYS},
        "delta": {},
        "running": running,
        "labels": dict(ASSET_LABELS),
        "order": list(ASSET_KEYS),
        "history": [],
    }


def _read_snapshot(workspace: str, config: str) -> dict[str, Any]:
    data = _load_json(_snapshot_path(workspace, config), {})
    return data if isinstance(data, dict) else {}


def _trim_history(history: Any) -> list[dict[str, Any]]:
    if not isinstance(history, list):
        return []
    by_date: dict[str, dict[str, Any]] = {}
    for entry in history:
        if not isinstance(entry, dict):
            continue
        date = str(entry.get("date") or "")
        if not date:
            continue
        values = entry.get("values")
        if not isinstance(values, dict):
            values = {}
        by_date[date] = {
            "date": date,
            "captured_at": str(entry.get("captured_at") or ""),
            "values": values,
        }
    return [by_date[date] for date in sorted(by_date)][-30:]


def _load_history(workspace: str, config: str) -> list[dict[str, Any]]:
    data = _load_json(_history_path(workspace, config), [])
    if not isinstance(data, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        date = str(entry.get("date") or "")
        if not date:
            continue
        raw_values = entry.get("values")
        values: dict[str, int] = {}
        if isinstance(raw_values, dict):
            for key in ASSET_KEYS:
                value = _coerce_int_or_none(raw_values.get(key))
                if value is not None:
                    values[key] = value
        cleaned.append(
            {
                "date": date,
                "captured_at": str(entry.get("captured_at") or ""),
                "values": values,
            }
        )
    return _trim_history(cleaned)


def _previous_history_entry(
    history: list[dict[str, Any]], today: str
) -> Optional[dict[str, Any]]:
    previous: Optional[dict[str, Any]] = None
    for entry in history:
        if not isinstance(entry, dict):
            continue
        date = str(entry.get("date") or "")
        if not date or date >= today:
            continue
        if previous is None or date > str(previous.get("date") or ""):
            previous = entry
    return previous


def already_updated_today(workspace: str, config: str = "oas1") -> bool:
    """Return True when the snapshot has a valid capture for today."""
    try:
        raw = _read_snapshot(workspace, config)
        if not raw:
            return False
        if str(raw.get("updated_date") or "") != _today():
            return False
        return str(raw.get("status") or "") in ("ok", "partial")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pure parsing functions
# ---------------------------------------------------------------------------


def parse_cn_number(text: str) -> tuple[Optional[int], bool]:
    """Parse a Chinese-formatted number.

    Returns ``(value, is_exact)``.  ``万`` and ``亿`` are treated as
    approximate because the displayed value is rounded by the game UI.
    """
    if text is None:
        return None, False

    raw = str(text).strip()
    if not raw:
        return None, False

    raw = raw.replace("＋", "+").replace("，", ",")
    raw = raw.replace(",", "").replace(" ", "")

    match = _NUM_RE.search(raw)
    if match is None:
        return None, False

    number_text = match.group(1)
    unit = match.group(2)
    try:
        number = float(number_text)
    except (TypeError, ValueError):
        return None, False

    exact = True
    if unit == "万":
        number *= 10_000
        exact = False
    elif unit == "亿":
        number *= 100_000_000
        exact = False

    try:
        return int(round(number)), exact
    except (TypeError, ValueError, OverflowError):
        return None, False


def extract_top_values(rows: list[dict]) -> list[dict]:
    """Extract numeric top-bar values in 1280x720 canonical space."""
    if not isinstance(rows, list):
        return []

    values: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            x = float(row.get("x", 0))
            y = float(row.get("y", 0))
        except (TypeError, ValueError):
            continue
        if y >= 60 or x < 450:
            continue

        text = str(row.get("text") or "")
        value, exact = parse_cn_number(text)
        if value is None:
            continue

        item = dict(row)
        item["text"] = text
        item["value"] = value
        item["exact"] = bool(exact)
        values.append(item)

    values.sort(key=lambda item: float(item.get("x", 0)))
    return values


def looks_courtyard(rows: list[dict]) -> bool:
    """Heuristically decide whether the current screen is the courtyard."""
    if not isinstance(rows, list):
        return False

    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")
        if "探索" in text or "町中" in text:
            return True

    values = extract_top_values(rows)
    if len(values) >= 3:
        first = str(values[0].get("text") or "").strip()
        third = str(values[2].get("text") or "").strip()
        if first.endswith("万") and third.endswith("万"):
            return True

    return False


def extract_stamina_exact(rows: list[dict]) -> Optional[int]:
    """Find the exact stamina value from the 已拥有 popup or adjacent row."""
    if not isinstance(rows, list):
        return None

    anchor: Optional[dict] = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")
        match = _STAMINA_RE.search(text)
        if match is not None:
            try:
                return int(match.group(1).replace(",", ""))
            except (TypeError, ValueError):
                pass
        if ("已拥有" in text or "己拥有" in text or "拥有" in text) and anchor is None:
            anchor = row

    if anchor is None:
        return None

    try:
        anchor_x = float(anchor.get("x", 0))
        anchor_y = float(anchor.get("y", 0))
        anchor_h = float(anchor.get("h", 0))
    except (TypeError, ValueError):
        return None

    candidates: list[tuple[float, int]] = []
    for row in rows:
        if row is anchor or not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if re.fullmatch(r"\d[\d,]*", text) is None:
            continue
        try:
            row_x = float(row.get("x", 0))
            row_y = float(row.get("y", 0))
        except (TypeError, ValueError):
            continue
        if abs(row_y - anchor_y) <= max(40.0, anchor_h * 1.5) and row_x > anchor_x:
            try:
                candidates.append((row_x, int(text.replace(",", ""))))
            except (TypeError, ValueError):
                continue

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def extract_shop_bar(
    rows: list[dict], courtyard_jade: Optional[int]
) -> dict[str, dict]:
    """Extract the four shop currency values from the top bar.

    Expected left-to-right order:
    ``[skin_token, mystery_amulet, s_jade, jade]``.
    """
    values = extract_top_values(rows)
    if len(values) < 4:
        return {}

    if courtyard_jade is not None:
        try:
            expected_jade = int(courtyard_jade)
        except (TypeError, ValueError):
            expected_jade = None

        if expected_jade is not None:
            actual_jade = values[-1].get("value")
            if actual_jade is None:
                return {}
            try:
                actual_int = int(actual_jade)
            except (TypeError, ValueError):
                return {}

            if expected_jade < 10_000:
                if actual_int != expected_jade:
                    return {}
            else:
                if abs(actual_int - expected_jade) > 10_000:
                    return {}

    keys = ["skin_token", "mystery_amulet", "s_jade", "jade"]
    result: dict[str, dict] = {}
    for key, value_row in zip(keys, values[-4:]):
        row_value = value_row.get("value")
        if row_value is None:
            continue
        result[key] = {
            "value": int(row_value),
            "precision": "exact" if value_row.get("exact") else "approx",
            "source": "shop.topbar",
            "text": str(value_row.get("text") or ""),
        }
    return result


# ---------------------------------------------------------------------------
# Snapshot merge
# ---------------------------------------------------------------------------


def merge_snapshot(
    workspace: str, config: str, readings: dict, error: str = ""
) -> dict:
    """Merge new readings into the v3 snapshot and update day history."""
    now = _now_iso()
    today = _today()

    existing = _read_snapshot(workspace, config)
    if not isinstance(existing, dict):
        existing = {}

    raw_items = existing.get("items")
    if not isinstance(raw_items, dict):
        raw_items = {}

    items: dict[str, dict[str, Any]] = {}
    for key in ASSET_KEYS:
        previous = raw_items.get(key)
        items[key] = dict(previous) if isinstance(previous, dict) else _empty_item()

    incoming: dict[str, dict[str, Any]] = {}
    if isinstance(readings, dict):
        for key in ASSET_KEYS:
            if key not in readings:
                continue
            reading = readings[key]
            if isinstance(reading, dict):
                incoming[key] = dict(reading)
            elif isinstance(reading, (int, float)) and not isinstance(reading, bool):
                incoming[key] = {
                    "value": int(reading),
                    "precision": "exact",
                    "source": "manual",
                }

    updated_any = False

    for key in ASSET_KEYS:
        incoming_item = incoming.get(key)
        previous = items[key]
        incoming_value = (
            _coerce_int_or_none(incoming_item.get("value"))
            if incoming_item is not None
            else None
        )

        if incoming_item is not None and incoming_value is not None:
            precision = str(incoming_item.get("precision") or "approx")
            previous_is_exact_today = (
                previous.get("value") is not None
                and str(previous.get("precision") or "") == "exact"
                and str(previous.get("last_ok_at") or "").startswith(today)
            )
            if previous_is_exact_today and precision != "exact":
                # Keep today's exact reading rather than downgrading to approx.
                if incoming_item.get("text"):
                    previous["text"] = str(incoming_item.get("text"))
                items[key] = previous
            else:
                items[key] = {
                    "value": incoming_value,
                    "precision": precision,
                    "source": str(incoming_item.get("source") or "ocr"),
                    "last_ok_at": now,
                    "text": str(incoming_item.get("text") or incoming_value),
                }
                updated_any = True
        elif incoming_item is not None and incoming_item.get("error"):
            previous["error"] = str(incoming_item.get("error"))
            items[key] = previous

    history = _load_history(workspace, config)
    prev_entry = _previous_history_entry(history, today)
    prev_values = prev_entry.get("values", {}) if isinstance(prev_entry, dict) else {}

    delta: dict[str, int] = {}
    current_values: dict[str, int] = {}
    for key in ASSET_KEYS:
        val = _coerce_int_or_none(items[key].get("value"))
        if val is not None:
            current_values[key] = val
            prev_val = _coerce_int_or_none(prev_values.get(key))
            if prev_val is not None:
                delta[key] = val - prev_val

    if updated_any and current_values:
        history = _trim_history(
            history + [{"date": today, "captured_at": now, "values": current_values}]
        )
        _save_json(_history_path(workspace, config), history)

    exact_core = ("jade", "ap", "mystery_amulet", "skin_token", "s_jade")
    all_core = all(items[k].get("value") is not None for k in exact_core)
    status = "ok" if (updated_any and all_core) else ("partial" if updated_any else ("failed" if error else str(existing.get("status") or "idle")))

    snapshot: dict[str, Any] = {
        "version": 3,
        "config": config,
        "status": status,
        "updated_date": today if updated_any else str(existing.get("updated_date") or ""),
        "captured_at": now if updated_any else str(existing.get("captured_at") or ""),
        "last_attempt_at": now,
        "last_error": str(error or ""),
        "items": items,
        "delta": delta,
    }
    _save_json(_snapshot_path(workspace, config), snapshot)
    return load_snapshot(workspace, config)


def load_snapshot(workspace: str, config: str = "oas1") -> dict[str, Any]:
    """Load the latest persisted snapshot enriched with runtime status & labels."""
    raw = _read_snapshot(workspace, config)
    with _LOCK:
        running = bool(_STATUS.get("running") and _STATUS.get("config") == config)
        status_err = str(_STATUS.get("last_error") or "")
        phase = str(_STATUS.get("phase") or "") if running else ""
    if not raw:
        snap = _empty_snapshot(config, running=running)
        if status_err:
            snap["last_error"] = status_err
        snap["phase"] = phase
        snap["history"] = _load_history(workspace, config)[-7:]
        return snap

    items_raw = raw.get("items") if isinstance(raw.get("items"), dict) else {}
    items = {
        key: (dict(items_raw[key]) if isinstance(items_raw.get(key), dict) else _empty_item())
        for key in ASSET_KEYS
    }
    history = _load_history(workspace, config)
    return {
        "version": 3,
        "config": config,
        "status": str(raw.get("status") or "idle"),
        "updated_date": str(raw.get("updated_date") or ""),
        "captured_at": str(raw.get("captured_at") or ""),
        "last_attempt_at": str(raw.get("last_attempt_at") or ""),
        "last_error": str(raw.get("last_error") or status_err),
        "items": items,
        "delta": dict(raw.get("delta") or {}) if isinstance(raw.get("delta"), dict) else {},
        "running": running,
        "phase": phase,
        "labels": dict(ASSET_LABELS),
        "order": list(ASSET_KEYS),
        "history": history[-7:],
    }


# ---------------------------------------------------------------------------
# Live read-only OCR capture on Android virtual display
# ---------------------------------------------------------------------------


def _get_ocr():
    global _OCR_ENGINE
    with _OCR_INIT_LOCK:
        if _OCR_ENGINE is None:
            from ppocronnx.predict_system import TextSystem
            _OCR_ENGINE = TextSystem()
        return _OCR_ENGINE


def _set_phase(text: str) -> None:
    with _LOCK:
        _STATUS["phase"] = text


def _frame_metrics(frame_size: tuple[int, int]) -> tuple[float, int, int]:
    """按高度归一化到 720p 并居中裁剪 16:9，与 module.device.Device 保持一致。"""
    fw, fh = max(1, int(frame_size[0])), max(1, int(frame_size[1]))
    scale = 720.0 / float(fh)
    scaled_w = int(round(fw * scale))
    offset = (scaled_w - 1280) // 2 if scaled_w >= 1280 else -((1280 - scaled_w) // 2)
    return scale, offset, scaled_w


def _normalize_1280(rgb):
    """将任意比例的截图归一化为 1280x720 RGB，避免 20:9 宽屏横向压扁。"""
    import cv2
    import numpy as np

    h, w = rgb.shape[:2]
    if (w, h) == (1280, 720):
        return rgb
    _, offset, scaled_w = _frame_metrics((w, h))
    resized = cv2.resize(rgb, (scaled_w, 720), interpolation=cv2.INTER_AREA)
    if scaled_w >= 1280:
        return resized[:, offset:offset + 1280]
    padded = np.zeros((720, 1280, 3), dtype=resized.dtype)
    left = -offset
    padded[:, left:left + scaled_w] = resized
    return padded


def _capture_rgb(bridge_mod: Any, display: int, retries: int = 1, interval: float = 0.6):
    import cv2
    import numpy as np

    for attempt in range(max(1, int(retries))):
        raw = bridge_mod.display_screenshot(display)
        if raw:
            bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if bgr is not None and bgr.size > 0:
                h, w = bgr.shape[:2]
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                return rgb, (w, h)
        if attempt + 1 < retries:
            time.sleep(interval)
    return None, (1280, 720)


def _ocr_rows_1280(rgb, frame_size: tuple[int, int], roi_1280: Optional[tuple[int, int, int, int]] = None, scale: int = 1) -> list[dict[str, Any]]:
    import cv2

    norm = _normalize_1280(rgb)
    offset_x, offset_y = 0, 0
    img = norm
    if roi_1280 is not None:
        rx, ry, rw, rh = roi_1280
        px0 = max(0, int(rx))
        py0 = max(0, int(ry))
        px1 = min(1280, int(rx + rw))
        py1 = min(720, int(ry + rh))
        crop = norm[py0:py1, px0:px1]
        if crop.size == 0:
            return []
        offset_x, offset_y = px0, py0
        img = crop
    if scale > 1:
        img = cv2.resize(img, (img.shape[1] * scale, img.shape[0] * scale), interpolation=cv2.INTER_CUBIC)

    engine = _get_ocr()
    rows: list[dict[str, Any]] = []
    for item in engine.detect_and_ocr(img):
        box = getattr(item, "box", None)
        try:
            arr = box.tolist() if hasattr(box, "tolist") else box
            if isinstance(arr, (list, tuple)) and arr and isinstance(arr[0], (list, tuple)):
                xs = [float(p[0]) for p in arr]
                ys = [float(p[1]) for p in arr]
                bx, by = min(xs) / scale + offset_x, min(ys) / scale + offset_y
                bw, bh = (max(xs) - min(xs)) / scale, (max(ys) - min(ys)) / scale
            else:
                bx = float(arr[0]) / scale + offset_x
                by = float(arr[1]) / scale + offset_y
                bw = float(arr[2]) / scale
                bh = float(arr[3]) / scale
        except Exception:
            continue
        rows.append({
            "text": str(getattr(item, "ocr_text", "") or ""),
            "x": bx,
            "y": by,
            "w": bw,
            "h": bh,
            "box": [bx, by, bw, bh],
            "score": float(getattr(item, "score", 0.0) or 0.0),
        })
    return rows


def _tap_1280(bridge_mod: Any, display: int, x: int, y: int, frame_size: tuple[int, int]) -> None:
    fw, fh = max(1, int(frame_size[0])), max(1, int(frame_size[1]))
    scale, offset, _ = _frame_metrics((fw, fh))
    tx = min(fw - 1, max(0, int(round((float(x) + offset) / scale))))
    ty = min(fh - 1, max(0, int(round(float(y) / scale))))
    bridge_mod.display_tap(display, tx, ty)


def _ensure_virtual_display(bridge_mod: Any, display: int = -1) -> int:
    """复用后台虚拟屏并把阴阳师搬入虚拟屏；未启动时自动拉起虚拟屏与游戏。"""
    if display >= 0:
        return display

    pkg = (
        bridge_mod.target_package()
        if callable(getattr(bridge_mod, "target_package", None))
        else "com.netease.onmyoji"
    )
    vd_running = callable(getattr(bridge_mod, "vdstream_running", None)) and bool(bridge_mod.vdstream_running())
    if vd_running:
        disp = bridge_mod.vdstream_display() if callable(getattr(bridge_mod, "vdstream_display", None)) else -1
        if disp >= 0:
            info = (
                bridge_mod.vdstream_info()
                if callable(getattr(bridge_mod, "vdstream_info", None))
                else {}
            )
            if isinstance(info, dict) and info.get("target_on_display") is False:
                ensured = (
                    bridge_mod.display_ensure_app(pkg)
                    if callable(getattr(bridge_mod, "display_ensure_app", None))
                    else -1
                )
                if ensured >= 0:
                    return ensured
                if callable(getattr(bridge_mod, "ensure_game_started", None)):
                    bridge_mod.ensure_game_started(pkg)
                    ensured = bridge_mod.display_ensure_app(pkg)
                    if ensured >= 0:
                        return ensured
            return disp

    # 虚拟屏尚未启动（例如刚重开 App）：按 /background/start 同款流程自动建屏并拉起游戏
    started = True
    if callable(getattr(bridge_mod, "vdstream_start", None)):
        started = bool(bridge_mod.vdstream_start(1280, 720))
    if not started:
        return -1

    disp = (
        bridge_mod.display_ensure_app(pkg)
        if callable(getattr(bridge_mod, "display_ensure_app", None))
        else -1
    )
    if disp < 0 and callable(getattr(bridge_mod, "ensure_game_started", None)):
        bridge_mod.ensure_game_started(pkg)
        if callable(getattr(bridge_mod, "display_ensure_app", None)):
            disp = bridge_mod.display_ensure_app(pkg)
    return disp


def _try_return_to_courtyard(bridge_mod: Any, disp: int, rgb, fsize: tuple[int, int], rows: list[dict], max_taps: int = 4):
    """只读方式尝试安全退回庭院（遇到退出确认框自动点取消）。"""
    for _ in range(max_taps):
        if looks_courtyard(rows):
            return rgb, fsize, rows, True
        joined_txt = "".join(str(r.get("text") or "") for r in rows)
        if "退出游戏" in joined_txt or "退出阴阳师" in joined_txt or "确定要退出" in joined_txt:
            cancel_xy = (835, 416)
            for r in rows:
                if "取消" in str(r.get("text") or ""):
                    cancel_xy = (int(r["x"] + r["w"] / 2), int(r["y"] + r["h"] / 2))
                    break
            _tap_1280(bridge_mod, disp, cancel_xy[0], cancel_xy[1], fsize)
        else:
            _tap_1280(bridge_mod, disp, 35, 35, fsize)
        time.sleep(1.0)
        rgb, fsize = _capture_rgb(bridge_mod, disp, retries=3, interval=0.5)
        if rgb is None:
            return None, fsize, [], False
        rows = _ocr_rows_1280(rgb, fsize)
    return rgb, fsize, rows, looks_courtyard(rows)


def refresh_assets(
    workspace: str,
    config: str = "oas1",
    display: int = -1,
    force: bool = False,
) -> dict[str, Any]:
    """Run one safe, read-only OCR pass on the virtual display to update account assets."""
    if not force and already_updated_today(workspace, config):
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_updated_today",
            "snapshot": load_snapshot(workspace, config),
        }

    with _LOCK:
        if _STATUS["running"]:
            return {
                "ok": False,
                "busy": True,
                "error": "资产巡检正在进行中",
                "snapshot": load_snapshot(workspace, config),
            }
        _STATUS.update(running=True, config=config, last_error="", phase="正在检查后台通道...")

    try:
        from . import bridge

        bridge_ready = (
            (callable(getattr(bridge, "shizuku_available", None)) and bridge.shizuku_available())
            or (callable(getattr(bridge, "vdstream_running", None)) and bridge.vdstream_running())
            or (callable(getattr(bridge, "available", None)) and bridge.available())
        )
        if not bridge_ready:
            snap = merge_snapshot(workspace, config, {}, error="Shizuku 桥接未就绪")
            return {"ok": False, "error": "Shizuku 桥接未就绪", "snapshot": snap}

        _set_phase("正在启动后台虚拟屏与游戏...")
        disp = _ensure_virtual_display(bridge, display=display)
        if disp < 0:
            snap = merge_snapshot(workspace, config, {}, error="虚拟屏未就绪，请检查 Shizuku 授权或手动打开一次阴阳师")
            return {"ok": False, "error": "虚拟屏未就绪，请检查 Shizuku 授权或手动打开一次阴阳师", "snapshot": snap}

        # Step 1: 获取虚拟屏画面并确保处于庭院（冷启动/在标题页时自动登录进庭院）
        _set_phase("正在读取虚拟屏画面...")
        rgb, fsize = _capture_rgb(bridge, disp, retries=10, interval=0.6)
        if rgb is None:
            snap = merge_snapshot(workspace, config, {}, error="无法获取虚拟屏截图")
            return {"ok": False, "error": "无法获取虚拟屏截图", "snapshot": snap}

        rows = _ocr_rows_1280(rgb, fsize)
        in_courtyard = looks_courtyard(rows)
        if not in_courtyard:
            # 若刚冷启动停在标题页/加载页，调用统一登录守卫进庭院
            _set_phase("正在等待游戏登录并进入庭院...")
            try:
                from . import login as login_mod

                if callable(getattr(login_mod, "ensure_logged_in", None)):
                    login_mod.ensure_logged_in(disp, timeout=180.0)
                    time.sleep(1.2)
                    rgb, fsize = _capture_rgb(bridge, disp, retries=5, interval=0.6)
                    if rgb is not None:
                        rows = _ocr_rows_1280(rgb, fsize)
                        in_courtyard = looks_courtyard(rows)
            except Exception:  # noqa: BLE001
                pass

        if not in_courtyard and rgb is not None:
            _set_phase("正在安全返回庭院...")
            rgb, fsize, rows, in_courtyard = _try_return_to_courtyard(bridge, disp, rgb, fsize, rows, max_taps=4)

        if not in_courtyard or rgb is None:
            snap = merge_snapshot(workspace, config, {}, error="当前不在庭院，跳过资产巡检")
            return {"ok": False, "error": "当前不在庭院，跳过资产巡检", "snapshot": snap}

        _set_phase("正在识别庭院顶栏资产...")
        readings: dict[str, dict[str, Any]] = {}
        # 优先按庭院顶栏三个固定纯文字槽位 3x 放大裁剪识别，避开图标与加号，大幅提高准确率
        slot_rois = (
            ("coin", (522, 10, 104, 44)),
            ("jade", (710, 10, 90, 44)),
            ("ap", (888, 10, 98, 44)),
        )
        for key, roi in slot_rois:
            slot_rows = _ocr_rows_1280(rgb, fsize, roi_1280=roi, scale=3)
            slot_text = "".join(str(r.get("text") or "") for r in sorted(slot_rows, key=lambda r: float(r.get("x", 0))))
            val, exact = parse_cn_number(slot_text)
            if val is not None:
                readings[key] = {
                    "value": val,
                    "precision": "exact" if exact else "approx",
                    "source": "courtyard.topbar",
                    "text": slot_text.strip(),
                }
        # 若个别槽位未识出，回退到全图顶栏识别结果补齐
        top = extract_top_values(rows)
        if len(top) >= 3:
            for idx, key in enumerate(("coin", "jade", "ap")):
                if key not in readings:
                    readings[key] = {
                        "value": top[idx]["value"],
                        "precision": "exact" if top[idx].get("exact") else "approx",
                        "source": "courtyard.topbar",
                        "text": top[idx].get("text", ""),
                    }
        courtyard_jade: Optional[int] = _coerce_int_or_none((readings.get("jade") or {}).get("value"))

        # Step 2: 点击体力饭团图标 (862, 30) 读取气泡里的精确体力值（绝不碰右侧加号）
        _set_phase("正在读取精确体力...")
        _tap_1280(bridge, disp, 862, 30, fsize)
        time.sleep(0.9)
        rgb2, fsize2 = _capture_rgb(bridge, disp, retries=3, interval=0.4)
        if rgb2 is not None:
            tip_rows = _ocr_rows_1280(rgb2, fsize2, roi_1280=(620, 110, 420, 200), scale=2)
            exact_ap = extract_stamina_exact(tip_rows)
            if exact_ap is None:
                exact_ap = extract_stamina_exact(_ocr_rows_1280(rgb2, fsize2))
            if exact_ap is not None:
                readings["ap"] = {
                    "value": exact_ap,
                    "precision": "exact",
                    "source": "tooltip.stamina",
                    "text": str(exact_ap),
                }
        # 点庭院左侧空白关闭体力说明气泡
        _tap_1280(bridge, disp, 220, 360, fsize)
        time.sleep(0.6)

        # Step 3: 只读进入商店识别皮肤券/神秘符咒/魂玉/勾玉（3x 放大 + 勾玉锚点校验），随后安全退回庭院
        _set_phase("正在识别商店顶栏资产...")
        shop_xy = (700, 645)
        for r in rows:
            if "商店" in str(r.get("text") or ""):
                shop_xy = (int(r["x"] + r["w"] / 2), int(r["y"] + r["h"] / 2))
                break
        _tap_1280(bridge, disp, shop_xy[0], shop_xy[1], fsize)
        time.sleep(1.8)
        for _ in range(2):
            rgb3, fsize3 = _capture_rgb(bridge, disp, retries=3, interval=0.5)
            if rgb3 is None:
                break
            shop_rows = _ocr_rows_1280(rgb3, fsize3, roi_1280=(450, 0, 830, 65), scale=3)
            shop_vals = extract_shop_bar(shop_rows, courtyard_jade)
            if shop_vals:
                readings.update(shop_vals)
                break
            time.sleep(1.0)

        # 安全返回庭院（防误触退出游戏框）
        _set_phase("正在返回庭院...")
        for _ in range(4):
            _tap_1280(bridge, disp, 35, 35, fsize)
            time.sleep(0.9)
            rgb_back, fb_size = _capture_rgb(bridge, disp, retries=2, interval=0.4)
            if rgb_back is None:
                continue
            back_rows = _ocr_rows_1280(rgb_back, fb_size)
            if looks_courtyard(back_rows):
                break
            joined_back = "".join(str(r.get("text") or "") for r in back_rows)
            if "退出游戏" in joined_back or "退出阴阳师" in joined_back:
                _tap_1280(bridge, disp, 835, 416, fb_size)
                time.sleep(0.6)
                break

        snap = merge_snapshot(workspace, config, readings, error="")
        return {"ok": True, "snapshot": snap}
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        with _LOCK:
            _STATUS["last_error"] = err
        snap = merge_snapshot(workspace, config, {}, error=err)
        return {"ok": False, "error": err, "snapshot": snap}
    finally:
        with _LOCK:
            _STATUS["running"] = False
            _STATUS["phase"] = ""


def refresh_async(
    workspace: str,
    config: str = "oas1",
    force: bool = False,
) -> dict[str, Any]:
    """Trigger refresh_assets on a background daemon thread."""
    with _LOCK:
        if _STATUS["running"]:
            return {"ok": True, "started": False, "busy": True}

    def _run() -> None:
        refresh_assets(workspace, config=config, force=force)

    threading.Thread(target=_run, name="oas-asset-refresh", daemon=True).start()
    return {"ok": True, "started": True}


def maybe_refresh_after_daily_tasks(
    workspace: str,
    config: str = "oas1",
) -> dict[str, Any]:
    """Hook called by scheduler once all due tasks finish; runs at most once per day."""
    try:
        if already_updated_today(workspace, config):
            return {"ok": True, "skipped": True, "reason": "already_updated_today"}
        return refresh_assets(workspace, config=config, force=False)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc)}

