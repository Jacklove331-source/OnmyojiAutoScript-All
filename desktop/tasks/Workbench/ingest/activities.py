# This file uses the following encoding: utf-8
"""活动/每周任务数据源（对应平安志侧栏「阴阳师工作台」）。

数据形态（2026-11 实测）：`%APPDATA%\\com.yys.analysis\\workbench\\activities.json`
    {"packet": {"activities": [...], "announcements": [...], "version": …}, "raw": "...", …}
    activities[*] = {id, title, endsAt, tags[], server, summary,
                     rewards{conditions, items[{group,name,quantityText}], sourceText, status},
                     announcementIds[]}
    announcements[*] = {id, title, body, publishedAt, server}

本模块**不打包别人的数据**：运行时优先读本机 平安志 缓存（它自己会刷新），
读不到就用调用方给的路径/列表。只做解析与筛选，不做下载。
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

CANDIDATE_PATHS = [
    os.path.join(os.environ.get("APPDATA", ""), "com.yys.analysis", "workbench", "activities.json"),
]

CST = timezone(timedelta(hours=8))


def _parse_time(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        txt = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt)
        return dt if dt.tzinfo else dt.replace(tzinfo=CST)
    except ValueError:
        return None


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """返回 {'activities': [...], 'announcements': [...], 'version': …}，找不到返回空表。"""
    paths = [path] if path else list(CANDIDATE_PATHS)
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        try:
            data = json.load(io.open(p, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        packet = data.get("packet") if isinstance(data, dict) else None
        if not isinstance(packet, dict):
            packet = data if isinstance(data, dict) else {}
        if "activities" in packet or "announcements" in packet:
            return {"activities": packet.get("activities") or [],
                    "announcements": packet.get("announcements") or [],
                    "version": packet.get("version") or data.get("manifest", {}).get("version"),
                    "source": p}
    return {"activities": [], "announcements": [], "version": None, "source": None}


def classify(activity: Dict[str, Any], now: Optional[datetime] = None) -> str:
    now = now or datetime.now(CST)
    end = _parse_time(activity.get("endsAt"))
    if end is None:
        return "unknown"
    if end < now:
        return "expired"
    if (end - now).total_seconds() <= 72 * 3600:
        return "ending"
    return "active"


def activity_report(data: Dict[str, Any], now: Optional[datetime] = None,
                    tag: str = "", limit: int = 0) -> Dict[str, Any]:
    """活动清单：按状态分组 + 剩余时间，可按 tag 过滤。"""
    now = now or datetime.now(CST)
    rows = []
    for a in data.get("activities") or []:
        tags = a.get("tags") or []
        if tag and not any(tag in str(t) for t in tags):
            continue
        end = _parse_time(a.get("endsAt"))
        rows.append({
            "id": a.get("id"),
            "title": a.get("title"),
            "tags": tags,
            "server": a.get("server"),
            "endsAt": a.get("endsAt"),
            "status": classify(a, now),
            "hours_left": round((end - now).total_seconds() / 3600, 1) if end else None,
            "rewards": (a.get("rewards") or {}).get("items") or [],
        })
    rows.sort(key=lambda r: ({"ending": 0, "active": 1, "unknown": 2, "expired": 3}[r["status"]],
                             r["hours_left"] if r["hours_left"] is not None else 1e9))
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    # total 必须在截断前取：by_status 是按全量统计的，若 total 取截断后的 len(rows)，
    # 界面会显示「共 40 条 · active 2 / unknown 8 / expired 71」（2+8+71=81），自相矛盾。
    total = len(rows)
    if limit:
        rows = rows[:limit]
    return {"version": data.get("version"), "source": data.get("source"),
            "total": total, "returned": len(rows), "limit": limit or None,
            "by_status": by_status, "rows": rows,
            "announcements": len(data.get("announcements") or [])}


def announcement_report(data: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
    rows = []
    for a in data.get("announcements") or []:
        rows.append({"id": a.get("id"), "title": a.get("title"),
                     "publishedAt": a.get("publishedAt"), "server": a.get("server"),
                     "body_len": len(a.get("body") or "")})
    rows.sort(key=lambda r: str(r.get("publishedAt") or ""), reverse=True)
    return {"total": len(rows), "rows": rows[:limit]}
