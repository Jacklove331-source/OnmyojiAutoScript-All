# This file uses the following encoding: utf-8
"""监控：定时采样本地关键指标，供趋势页使用。

对应平安志侧栏「监控」。与"实时读屏监控"不同，这里只采**本机数据快照**：
    御魂总数 / 各决策数量 / 练度档 / 高分御魂数 / 即将结束的活动数 / 式神拥有数

采样入口：
    sample(store, souls=..., owned=...)      —— 由 Workbench 任务在每次分析后调用
    sample_from_db()                          —— 直接读平安志 sqlite（paz-db 通道）
趋势查询：
    trend(rows, days=30)                      —— 按天聚合，返回首末值与增量
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from tasks.Workbench.features import maturity_of, soul_report


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def sample(store, souls: Optional[List[Dict[str, Any]]] = None,
           owned: Optional[List[Dict[str, Any]]] = None,
           standard_id: Optional[str] = None, activities: Optional[Dict[str, Any]] = None,
           note: str = "", report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """采一次样并落盘（monitor.jsonl 一行）。"""
    if souls is None or owned is None:
        inventory = store.inventory()
        souls = inventory["souls"] if souls is None else souls
        owned = inventory["owned"] if owned is None else owned

    metrics: Dict[str, Any] = {
        "date": _today(),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "soul_count": len(souls),
        "owned_count": len(owned),
        "maturity": maturity_of(len(souls)),
        "standard": standard_id,
    }
    try:
        report = report if report is not None else soul_report(souls, standard_id)
        scored = report["results"]
        metrics["standard"] = report["standard"]
        metrics["by_decision"] = report["by_decision"]
        metrics["high_score"] = sum(1 for r in scored if (r.get("score") or 0) >= 70)
        metrics["scored"] = sum(1 for r in scored if r.get("matched"))
    except Exception as exc:  # 数据不完整时不阻塞采样
        metrics["score_error"] = str(exc)

    if activities:
        acts = activities.get("activities") or []
        metrics["activities_total"] = len(acts)
        metrics["activities_ending"] = sum(
            1 for days in (_days_left(a.get("endsAt")) for a in acts) if days is not None and 0 < days <= 3)

    store.add_monitor("workbench_sample", metrics, note)
    return metrics


def _days_left(ends_at: Optional[str]) -> Optional[float]:
    if not ends_at:
        return None
    try:
        from datetime import datetime
        end = datetime.fromisoformat(str(ends_at).replace("Z", "+00:00"))
        now = datetime.now(end.tzinfo)
        return (end - now).total_seconds() / 86400
    except (ValueError, TypeError):
        return None


def sample_from_db(store, standard_id: Optional[str] = None) -> Dict[str, Any]:
    """paz-db 通道：直接读平安志 sqlite 采样。"""
    from tasks.Workbench.ingest import paz_db
    data = paz_db.load(profile_id=getattr(store, "profile_id", None))
    if not data.get("ok"):
        return {"ok": False, "reason": data.get("reason")}
    m = sample(store, data["souls"], data["owned"], standard_id=standard_id,
               note="paz-db")
    m["source"] = "paz-db"
    return m


def trend(store, days: int = 30) -> Dict[str, Any]:
    """按天汇总监控点：每天取最后一条，返回关键指标的走势与增量。"""
    rows = [r for r in store.monitor(1000)
            if isinstance(r.get("value"), dict) and r["value"].get("date")]
    by_day: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        by_day[r["value"]["date"]] = r["value"]
    days_sorted = sorted(by_day)[-days:]
    series = [by_day[d] for d in days_sorted]

    def _pick(key: str) -> List[Any]:
        return [d.get(key) for d in series]

    soul_series = _pick("soul_count")
    delta = None
    if len(soul_series) >= 2 and isinstance(soul_series[0], int) and isinstance(soul_series[-1], int):
        delta = soul_series[-1] - soul_series[0]
    return {"days": len(series), "dates": days_sorted,
            "soul_count": soul_series, "owned_count": _pick("owned_count"),
            "high_score": _pick("high_score"), "soul_delta": delta,
            "latest": series[-1] if series else None}
