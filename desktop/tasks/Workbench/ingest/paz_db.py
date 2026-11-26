# This file uses the following encoding: utf-8
"""从平安志 sqlite 直接读御魂/式神（权威口径，含固有属性）。

比解析 raw 位置数组更可靠：
  * set_id 已是中文套装名
  * 属性已是语义名（attribute_type）
  * **固有属性**在 soul_attribute.fixed_attribute=1 里，raw 数组里看不出来
  * 等级、槽位、星级都是规范化字段

用法：
    from tasks.Workbench.ingest import paz_db
    data = paz_db.load()            # {"souls": [...], "owned": [...], "source": "<db path>"}
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_DB = os.path.join(os.environ.get("APPDATA", ""), "com.yys.analysis",
                          "current-analysis.sqlite3")

RATE_ATTRS = {"attack_rate", "defense_rate", "hp_rate", "crit_rate", "crit_damage",
              "effect_hit", "effect_resist"}


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def set_groups(path: Optional[str] = None) -> Dict[str, set]:
    """set_id → {group_id}（来自 yuhun_group_member），供规则组级回退使用。"""
    db = path or DEFAULT_DB
    if not os.path.isfile(db):
        return {}
    out: Dict[str, set] = {}
    try:
        with closing(_ro(db)) as con:
            for gid, sid in con.execute("select group_id, set_id from yuhun_group_member"):
                out.setdefault(sid, set()).add(gid)
    except sqlite3.Error:
        pass
    return out


def _profile_rows(con: sqlite3.Connection):
    return con.execute(
        "select p.id, p.display_name, p.server_label, s.id, s.captured_at "
        "from game_profile p left join profile_current_snapshot c on c.profile_id = p.id "
        "left join snapshot s on s.id = c.snapshot_id and s.profile_id = p.id "
        "where p.archived_at is null "
        "order by s.id is null, c.selected_at desc, p.updated_at desc, p.id"
    )


def profiles(path: Optional[str] = None) -> Dict[str, Any]:
    db = path or DEFAULT_DB
    try:
        with closing(_ro(db)) as con:
            rows = [{"id": pid, "name": name or pid, "server": server or "",
                     "captured_at": captured, "has_snapshot": sid is not None}
                    for pid, name, server, sid, captured in _profile_rows(con)]
    except sqlite3.Error:
        return {"ok": False, "reason": "无法读取平安志数据库", "profiles": []}
    return {"ok": True, "profiles": rows}


def attribute_value(attr: str, value: Any) -> float:
    return round(float(value) * (100 if attr in RATE_ATTRS else 1), 3)


def load(path: Optional[str] = None, profile_id: Optional[str] = None, *,
         include_extras: bool = False) -> Dict[str, Any]:
    """读取所选角色的当前快照，默认取最近选中的角色。"""
    db = path or DEFAULT_DB
    if not os.path.isfile(db):
        return {"ok": False, "reason": "找不到平安志数据库", "souls": [], "owned": []}

    souls: Dict[str, Dict[str, Any]] = {}
    try:
        with closing(_ro(db)) as con:
            con.execute("begin")
            row = next((r for r in _profile_rows(con) if profile_id is None or r[0] == profile_id), None)
            if row is None or row[3] is None:
                return {"ok": False, "reason": "所选角色没有当前快照", "souls": [], "owned": []}
            pid, name, server, snapshot_id, captured_at = row
            for iid, set_id, slot, quality, level, mtype, mval, locked, initial in con.execute(
                    "select internal_id, set_id, slot, quality, level, main_attr_type, "
                    "main_attr_value, locked_in_source, initial_substat_count "
                    "from snapshot_soul where snapshot_id = ?", (snapshot_id,)):
                souls[iid] = {
                    "internal_id": iid, "set": set_id, "slot": slot, "quality": quality,
                    "level": level, "main": mtype, "main_value": attribute_value(mtype, mval),
                    "substats": {}, "rolls": {}, "fixed_attrs": [], "fixed_stats": {},
                    "locked": bool(locked), "initial_substat_count": initial,
                }

            for iid, atype, val, enh, fixed in con.execute(
                    "select soul_internal_id, attribute_type, value, enhancement_count, fixed_attribute "
                    "from soul_attribute where snapshot_id = ? order by soul_internal_id, attribute_index",
                    (snapshot_id,)):
                s = souls.get(iid)
                if s is None:
                    continue
                if fixed:
                    s["fixed_attrs"].append(atype)
                    s["fixed_stats"][atype] = attribute_value(atype, val)
                else:
                    s["substats"][atype] = attribute_value(atype, val)
                    s["rolls"][atype] = int(enh or 0)

            from tasks.Workbench.ingest import norm_skills

            cols = {r[1] for r in con.execute("pragma table_info(owned_shikigami)")}
            if "skills_json" in cols:
                owned = [{"id": sid, "instance_id": iid, "star": star, "level": level,
                          "awakened": bool(awakened), "skin": skin,
                          "skills": norm_skills(skills_json)}
                         for iid, sid, star, level, awakened, skin, skills_json in con.execute(
                             "select instance_id, shikigami_id, star, level, awakened, skin_id, skills_json "
                             "from owned_shikigami where profile_id = ?", (pid,))]
            else:
                owned = [{"id": sid, "instance_id": iid, "star": star, "level": level,
                          "awakened": bool(awakened), "skin": skin, "skills": []}
                         for iid, sid, star, level, awakened, skin in con.execute(
                             "select instance_id, shikigami_id, star, level, awakened, skin_id "
                             "from owned_shikigami where profile_id = ?", (pid,))]
    except (sqlite3.Error, ValueError, TypeError):
        return {"ok": False, "reason": "无法读取平安志角色快照", "souls": [], "owned": []}

    for s in souls.values():
        if s["initial_substat_count"] is None:
            s["initial_substat_count"] = len(s["substats"])
    result = {"ok": True, "source": db, "souls": list(souls.values()), "owned": owned,
              "profile": {"id": pid, "name": name or pid, "server": server or "",
                          "captured_at": captured_at}}
    if include_extras and all(re.fullmatch(r"[\w-]+", k) for k in (pid, snapshot_id)):
        result["extras"] = _snapshot_extras(Path(db).parent, pid, snapshot_id)
    return result


def _snapshot_extras(directory: Path, profile_id: str, snapshot_id: str) -> Dict[str, Any]:
    import json
    from tasks.Workbench.ingest import paz_export_extras

    for name in (f"current-data-{profile_id}-snapshot-{snapshot_id}.json",
                 f"current-data-{profile_id}.json"):
        try:
            raw = json.loads((directory / name).read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("snapshotId") == snapshot_id:
                return paz_export_extras(raw)
        except (OSError, ValueError):
            continue
    return {}
