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
_STATUS: dict[str, Any] = {"running": False, "config": "", "last_error": ""}
_FRAME_SIZE: tuple[int, int] = (1280, 720)

_OCR_ENGINE: Any = None
_OCR_INIT_LOCK = threading.Lock()

_NUM_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(亿|万)?")
_STAMINA_RE = re.compile(r"已拥有[:：\s]*(\d[\d,]*)")


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
        if "已拥有" in text and anchor is None:
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
    if not raw:
        snap = _empty_snapshot(config, running=running)
        if status_err:
            snap["last_error"] = status_err
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


def _capture_rgb(bridge_mod: Any, display: int):
    import cv2
    import numpy as np

    raw = bridge_mod.display_screenshot(display)
    if not raw:
        return None, (1280, 720)
    bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None or bgr.size == 0:
        return None, (1280, 720)
    h, w = bgr.shape[:2]
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return rgb, (w, h)


def _ocr_rows_1280(rgb, frame_size: tuple[int, int], roi_1280: Optional[tuple[int, int, int, int]] = None, scale: int = 1) -> list[dict[str, Any]]:
    import cv2

    fw, fh = frame_size
    sx, sy = fw / 1280.0, fh / 720.0
    offset_x, offset_y = 0, 0
    img = rgb
    if roi_1280 is not None:
        rx, ry, rw, rh = roi_1280
        px0 = max(0, int(rx * sx))
        py0 = max(0, int(ry * sy))
        px1 = min(fw, int((rx + rw) * sx))
        py1 = min(fh, int((ry + rh) * sy))
        crop = rgb[py0:py1, px0:px1]
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
            "x": bx / sx,
            "y": by / sy,
            "w": bw / sx,
            "h": bh / sy,
            "box": [bx / sx, by / sy, bw / sx, bh / sy],
            "score": float(getattr(item, "score", 0.0) or 0.0),
        })
    return rows


def _tap_1280(bridge_mod: Any, display: int, x: int, y: int, frame_size: tuple[int, int]) -> None:
    fw, fh = frame_size
    tx = int(round(x * fw / 1280.0))
    ty = int(round(y * fh / 720.0))
    bridge_mod.display_tap(display, tx, ty)


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
        _STATUS.update(running=True, config=config, last_error="")

    try:
        from . import bridge

        if not bridge.available():
            snap = merge_snapshot(workspace, config, {}, error="Shizuku 桥接未就绪")
            return {"ok": False, "error": "Shizuku 桥接未就绪", "snapshot": snap}

        disp = display if display >= 0 else bridge.vdstream_display()
        if disp < 0:
            disp = bridge.display_ensure_app(bridge.target_package())
        if disp < 0:
            snap = merge_snapshot(workspace, config, {}, error="虚拟屏未就绪")
            return {"ok": False, "error": "虚拟屏未就绪", "snapshot": snap}

        # Step 1: Ensure we are in courtyard (read-only back taps, max 4)
        rgb, fsize = _capture_rgb(bridge, disp)
        if rgb is None:
            snap = merge_snapshot(workspace, config, {}, error="无法获取虚拟屏截图")
            return {"ok": False, "error": "无法获取虚拟屏截图", "snapshot": snap}

        rows = _ocr_rows_1280(rgb, fsize)
        for _ in range(4):
            if looks_courtyard(rows):
                break
            _tap_1280(bridge, disp, 35, 35, fsize)
            time.sleep(1.0)
            rgb, fsize = _capture_rgb(bridge, disp)
            if rgb is None:
                break
            rows = _ocr_rows_1280(rgb, fsize)

        if not looks_courtyard(rows):
            snap = merge_snapshot(workspace, config, {}, error="当前不在庭院，跳过资产巡检")
            return {"ok": False, "error": "当前不在庭院，跳过资产巡检", "snapshot": snap}

        readings: dict[str, dict[str, Any]] = {}
        top = extract_top_values(rows)
        courtyard_jade: Optional[int] = None
        if len(top) >= 3:
            readings["coin"] = {
                "value": top[0]["value"],
                "precision": "exact" if top[0].get("exact") else "approx",
                "source": "courtyard.topbar",
                "text": top[0].get("text", ""),
            }
            courtyard_jade = _coerce_int_or_none(top[1]["value"])
            readings["jade"] = {
                "value": top[1]["value"],
                "precision": "exact" if top[1].get("exact") else "approx",
                "source": "courtyard.topbar",
                "text": top[1].get("text", ""),
            }
            readings["ap"] = {
                "value": top[2]["value"],
                "precision": "exact" if top[2].get("exact") else "approx",
                "source": "courtyard.topbar",
                "text": top[2].get("text", ""),
            }

        # Step 2: Tap stamina icon for exact AP tooltip
        _tap_1280(bridge, disp, 950, 30, fsize)
        time.sleep(0.8)
        rgb2, fsize2 = _capture_rgb(bridge, disp)
        if rgb2 is not None:
            exact_ap = extract_stamina_exact(_ocr_rows_1280(rgb2, fsize2))
            if exact_ap is not None:
                readings["ap"] = {
                    "value": exact_ap,
                    "precision": "exact",
                    "source": "tooltip.stamina",
                    "text": str(exact_ap),
                }
        _tap_1280(bridge, disp, 1200, 620, fsize)
        time.sleep(0.5)

        # Step 3: Enter Shop (read-only topbar), upscale 3x, anchor-validate jade, then return
        shop_xy = (715, 655)
        for r in rows:
            if "商店" in str(r.get("text") or ""):
                shop_xy = (int(r["x"] + r["w"] / 2), int(r["y"] + r["h"] / 2))
                break
        _tap_1280(bridge, disp, shop_xy[0], shop_xy[1], fsize)
        time.sleep(1.5)
        rgb3, fsize3 = _capture_rgb(bridge, disp)
        if rgb3 is not None:
            shop_rows = _ocr_rows_1280(rgb3, fsize3, roi_1280=(450, 0, 830, 60), scale=3)
            shop_vals = extract_shop_bar(shop_rows, courtyard_jade)
            readings.update(shop_vals)

        # Return cleanly to courtyard
        for _ in range(4):
            _tap_1280(bridge, disp, 35, 35, fsize)
            time.sleep(0.9)
            rgb_back, fb_size = _capture_rgb(bridge, disp)
            if rgb_back is not None and looks_courtyard(_ocr_rows_1280(rgb_back, fb_size)):
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

