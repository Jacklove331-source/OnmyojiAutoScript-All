"""AI 自愈历史（/ai/recovery 的数据层）。

纯标准库，host 侧可直接单测：`build_payload(root, limit)`，root = 工作目录
（里面应有 log/ai_recovery/incidents/<YYYYMMDD_HHMMSS>_<incident_id>/{anomaly,result}.json）。

原则：**单条记录坏了只跳过它**；summary 统计全量目录（`summary.scope == 'all'`），
`entries` 才按 limit 截断，两者口径在返回里写清楚。
"""
import glob
import json
import os

MAX_SCAN = 500  # summary 最多扫这么多目录，避免 SD 卡上几千条时卡住


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


def _to_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_str(value):
    return "" if value is None else str(value)


def _incident_id(name, anomaly):
    raw = anomaly.get("incident_id")
    if raw:
        return _to_str(raw)
    parts = name.split("_", 2)
    return parts[2] if len(parts) == 3 else name


def _entry(folder):
    name = os.path.basename(folder)
    anomaly = _load(os.path.join(folder, "anomaly.json"))
    result = _load(os.path.join(folder, "result.json"))

    diagnosis = result.get("diagnosis")
    if not isinstance(diagnosis, dict):
        diagnosis = {}
    actions = result.get("actions_done")
    if not isinstance(actions, list):
        actions = []

    ok_actions = [a for a in actions if isinstance(a, dict) and a.get("ok")]
    return {
        "id": _incident_id(name, anomaly),
        "dir": name,
        "at": _to_str(anomaly.get("created_at_iso")),
        "type": _to_str(anomaly.get("type")),
        "task": _to_str(anomaly.get("task")),
        "page": _to_str(anomaly.get("page")),
        "severity": _to_str(anomaly.get("severity")),
        "detail": _to_str(anomaly.get("detail")),
        "outcome": _to_str(result.get("outcome")) or "unknown",
        "message": _to_str(result.get("message")),
        "ai_called": bool(result.get("ai_called")),
        "verified": bool(result.get("verified")),
        "tokens": _to_int(result.get("tokens")),
        "cost_cny": _to_float(result.get("cost_cny")),
        "cause": _to_str(diagnosis.get("cause")),
        "page_now": _to_str(diagnosis.get("page")),
        "actions": len(actions),
        "actions_ok": len(ok_actions),
    }


def _summarise(entries, total, scope):
    return {
        "scope": scope,
        "total": total,
        "scanned": len(entries),
        "recovered": len([e for e in entries if e["outcome"] == "recovered"]),
        "needs_human": len([e for e in entries if e["outcome"] == "needs_human"]),
        "stopped": len([e for e in entries if e["outcome"] == "stopped"]),
        "ai_calls": len([e for e in entries if e["ai_called"]]),
        "tokens": sum(e["tokens"] for e in entries),
        "cost_cny": round(sum(e["cost_cny"] for e in entries), 4),
    }


def build_payload(root, limit=50, config=""):
    """返回 {ok, config, count, total, summary, entries}；任何异常都退化成空列表。"""
    limit = max(1, min(_to_int(limit, 50), 200))
    base = os.path.join(root, "log", "ai_recovery", "incidents")
    try:
        all_dirs = [d for d in glob.glob(os.path.join(base, "*")) if os.path.isdir(d)]
    except OSError:
        all_dirs = []
    all_dirs.sort(reverse=True)
    total = len(all_dirs)

    scanned = []
    for folder in all_dirs[:MAX_SCAN]:
        try:
            scanned.append(_entry(folder))
        except Exception:  # noqa: BLE001 —— 单条坏记录不影响整页
            continue

    summary = _summarise(scanned, total, "all" if total <= MAX_SCAN else "capped")
    return {
        "ok": True,
        "config": _to_str(config),
        "count": len(scanned[:limit]),
        "total": total,
        "summary": summary,
        "entries": scanned[:limit],
    }
