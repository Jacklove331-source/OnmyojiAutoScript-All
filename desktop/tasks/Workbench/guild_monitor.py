# This file uses the following encoding: utf-8
"""监控与寮管理：把「随时间变的东西」落到本地日志。

监控（对应平安志侧栏「监控」）：
    每次跑 Workbench 任务时记一条指标快照：御魂总数、各决策数量、练度档、报表路径。
    与实时读屏的「监控」不同，这里只记"分析结果随时间的变化"，零设备依赖。

寮管理（对应「寮管理」）：
    成员名单 + 角色 + 备注；以及活动记录（道馆 / 退治 / 宴会）的追加日志。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

GUILD_EVENTS = ("dokan", "abyss_shadows", "guild_banquet", "demon_retreat")


def collect_metrics(store, report: Optional[Dict[str, Any]] = None,
                    note: str = "") -> Dict[str, Any]:
    """把一次分析的结论写成监控点。report 为空时只用本地档案。"""
    souls = store.souls()
    row = {"soul_count": len(souls)}
    if report:
        row.update({
            "standard": report.get("standard"),
            "by_decision": report.get("by_decision") or {},
            "count": report.get("count"),
        })
    store.add_monitor("workbench_metrics", row, note)
    return row


def metric_trend(rows: List[Dict[str, Any]], key: str = "value") -> Dict[str, Any]:
    """取监控点的走势：首值/末值/增量/点数。"""
    points = []
    for r in rows:
        v = r.get(key)
        if isinstance(v, dict):
            v = v.get("soul_count")
        if isinstance(v, (int, float)):
            points.append(float(v))
    if not points:
        return {"points": 0, "first": None, "last": None, "delta": None}
    return {"points": len(points), "first": points[0], "last": points[-1],
            "delta": points[-1] - points[0]}


# ---------------------------------------------------------------- 寮管理
def guild_add_member(store, name: str, role: str = "成员", note: str = "") -> Dict[str, Any]:
    with store.lock("guild.json"):
        g = store.guild()
        members = g.setdefault("members", [])
        for member in members:
            if member.get("name") == name:
                member.update({"role": role, "note": note})
                store.set_guild(g)
                return {"ok": True, "updated": True, "members": len(members)}
        members.append({"name": name, "role": role, "note": note})
        store.set_guild(g)
        return {"ok": True, "updated": False, "members": len(members)}


def guild_remove_member(store, name: str) -> Dict[str, Any]:
    with store.lock("guild.json"):
        g = store.guild()
        before = len(g.get("members") or [])
        g["members"] = [m for m in (g.get("members") or []) if m.get("name") != name]
        store.set_guild(g)
        return {"ok": True, "removed": before - len(g["members"]), "members": len(g["members"])}


def guild_log_event(store, kind: str, value: Any = "", note: str = "") -> Dict[str, Any]:
    if kind not in GUILD_EVENTS:
        return {"ok": False, "reason": "unknown kind: %s (allowed: %s)"
                % (kind, ",".join(GUILD_EVENTS))}
    row = {"kind": kind, "value": value, "note": note,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    store.append_jsonl("guild_events.jsonl", row)
    return {"ok": True, **row}


def guild_report(store) -> Dict[str, Any]:
    g = store.guild()
    members = g.get("members") or []
    by_role: Dict[str, int] = {}
    for m in members:
        by_role[m.get("role", "成员")] = by_role.get(m.get("role", "成员"), 0) + 1
    events = store.read_jsonl("guild_events.jsonl", 200)
    by_kind: Dict[str, int] = {}
    for e in events:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
    return {"name": g.get("name", ""), "members": len(members), "by_role": by_role,
            "events": len(events), "events_by_kind": by_kind,
            "last_event": events[-1] if events else None,
            "member_rows": members, "event_rows": events[-10:]}
