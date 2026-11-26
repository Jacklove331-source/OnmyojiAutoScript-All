"""账号资产快照 v3：**按 OAS 配置分开存**（assets/<config>.json），不共用全局快照。

每项记录：value / precision(exact|approx|unknown) / source / last_ok_at / last_attempt_at / error / stale
规则：
  * 读不到就不写 value（保留该账号上次成功值），只更新 last_attempt_at + error + stale=True。
  * gold_ofuda / honor / medal / contrib / scale / demon_soul 目前 unsupported（没找到可读界面）。
  * complete 只认 5 项精确值：jade / ap / skin_token / mystery_amulet / s_jade。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ASSET_KEYS = [
    "jade", "coin", "ap", "s_jade", "mystery_amulet", "gold_ofuda",
    "honor", "medal", "contrib", "skin_token", "scale", "demon_soul",
]
EXACT_REQUIRED = ["jade", "ap", "skin_token", "mystery_amulet", "s_jade"]
# 平安志（PAZ）快照能一次给全 12 项精确值：来自 PAZ 的轮次就用全套做完成度判据
PAZ_REQUIRED = list(ASSET_KEYS)
APPROX_KEYS: set = set()
UNSUPPORTED_KEYS: list = []          # PAZ 快照已覆盖全部 12 项（金币/六项/结界卡都有）

STALE_AFTER_SEC = 1800


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def path_for(base: Path, config: str) -> Path:
    return base / "assets" / f"{config}.json"


def load(base: Path, config: str) -> Dict[str, Any]:
    p = path_for(base, config)
    if not p.exists():
        return {"version": 3, "config": config, "items": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 3, "config": config, "items": {}}
    data.setdefault("items", {})
    data.setdefault("config", config)
    return data


def _age_sec(ts: Optional[str]) -> Optional[int]:
    if not ts:
        return None
    try:
        return int(time.time() - time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S")))
    except Exception:
        return None


def merge(base: Path, config: str, *, serial: str = "", profile: Optional[str] = None,
          readings: Optional[Dict[str, Dict[str, Any]]] = None,
          raw: Optional[Dict[str, Any]] = None,
          meta: Optional[Dict[str, Any]] = None,
          required: Optional[List[str]] = None,
          error: Optional[str] = None, source: str = "") -> Dict[str, Any]:
    prev = load(base, config)
    at = now()
    items: Dict[str, Any] = dict(prev.get("items") or {})
    readings = readings or {}
    req = list(required or EXACT_REQUIRED)

    for key in ASSET_KEYS:
        cur = dict(items.get(key) or {})
        cur.setdefault("value", None)
        cur.setdefault("precision", "unsupported" if key in UNSUPPORTED_KEYS else "unknown")
        cur.setdefault("source", None)
        cur.setdefault("last_ok_at", None)
        cur.setdefault("last_attempt_at", None)
        cur.setdefault("error", None)

        r = readings.get(key)
        if r is not None:
            if r.get("touch") and r.get("value") is None and not r.get("error"):
                # 只更新"尝试过"的时间：实时帧读到简写但该账号已有精确值时不降级
                cur["last_attempt_at"] = at
                items[key] = cur
                continue
            cur["last_attempt_at"] = at
            incoming_precision = r.get("precision", "exact")
            old_age = _age_sec(cur.get("last_ok_at"))
            if (r.get("value") is not None and incoming_precision != "exact"
                    and cur.get("precision") == "exact"
                    and cur.get("value") is not None
                    and old_age is not None and old_age <= STALE_AFTER_SEC):
                # 精确值还新鲜：近似读数（如实时帧的 "3927万"）不覆盖，只留文本备查
                if r.get("text"):
                    cur["text"] = r["text"]
                items[key] = cur
                continue
            if r.get("value") is not None:
                cur.update({"value": r["value"],
                            "precision": incoming_precision,
                            "source": r.get("source", source),
                            "last_ok_at": at, "error": None})
                if r.get("text"):
                    cur["text"] = r["text"]
                else:
                    cur.pop("text", None)
            else:
                cur["error"] = r.get("error") or "本轮未读到"
                if r.get("precision"):
                    cur["precision"] = r["precision"]
        items[key] = cur

    for key, it in items.items():
        age = _age_sec(it.get("last_ok_at"))
        it["age_sec"] = age
        it["stale"] = True if (age is None or age > STALE_AFTER_SEC) else False

    # 只认"本轮真的读到"的成功：last_ok_at 必须等于本次时间
    this_run = {k: (items.get(k) or {}).get("last_ok_at") == at for k in req}
    complete = all(this_run.values()) and all(
        (items.get(k) or {}).get("value") is not None for k in req)
    any_exact = any((items.get(k) or {}).get("value") is not None for k in req)

    # 不完整的一轮（例如只写了几项实时帧）不应该把上一轮的 ok/complete 打回 partial
    prev_age = _age_sec(prev.get("captured_at"))
    keep_prev = (not complete and prev.get("complete")
                 and prev_age is not None and prev_age <= STALE_AFTER_SEC
                 and (prev.get("status") or "") == "ok")
    if keep_prev:
        complete = True

    state = {
        "version": 3,
        "config": config,
        "serial": serial or prev.get("serial"),
        "profile": profile if profile is not None else prev.get("profile"),
        "profile_unknown": (profile if profile is not None else prev.get("profile")) is None,
        "status": "ok" if complete else ("partial" if any_exact else "failed"),
        "complete": complete,
        "captured_at": (prev.get("captured_at") if keep_prev else (at if complete else prev.get("captured_at"))),
        "last_attempt_at": at,
        "last_error": error,
        "last_source": source or prev.get("last_source"),
        "required": req,
        "items": items,
        "meta": meta if meta is not None else prev.get("meta"),
        "raw": raw if raw is not None else prev.get("raw"),
    }
    p = path_for(base, config)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return state


def currencies(state: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for k, it in (state.get("items") or {}).items():
        if it.get("precision") == "exact" and isinstance(it.get("value"), int):
            out[k] = it["value"]
    return out


def supported_summary() -> Dict[str, List[str]]:
    return {
        "exact": [k for k in ASSET_KEYS if k not in UNSUPPORTED_KEYS and k not in APPROX_KEYS],
        "approx": sorted(APPROX_KEYS),
        "unsupported": UNSUPPORTED_KEYS,
    }
