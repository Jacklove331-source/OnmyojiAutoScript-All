# This file uses the following encoding: utf-8
"""平安志其余数据域（监督清单第 3 条）的统一读取层。

覆盖：
    inventory_item                      → 游戏资产（按套装/号位/星级的库存明细）
    set_commonness_default              → 套装常用度（PVE/PVP × common/uncommon）
    profile_commonness_override         → 玩家自定义常用度覆盖
    relevance_profile / active_relevance_profile → 相关度画像（哪个"用法"生效）
    yuhun_group / yuhun_group_member    → 御魂分组（含成员）
    analysis_todo                       → 分析待办（该强化的候选清单）
    lineup-catalog-v1/current.json      → 阵容目录（文件，不是 sqlite 表）

所有函数只读，缺库/缺文件时返回 ok=False + reason。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

DEFAULT_DB = os.path.join(os.environ.get("APPDATA", ""), "com.yys.analysis",
                          "current-analysis.sqlite3")
LINEUP_FILE = os.path.join(os.environ.get("APPDATA", ""), "com.yys.analysis",
                           "lineup-catalog-v1", "current.json")


def db_path(db: Optional[str] = None) -> str:
    """库位置：显式参数 > 环境变量 WORKBENCH_PAZ_DB > 平安志默认路径。

    环境变量只用于回归/自检指向临时库或"故意不存在"的路径，正常运行不设置。
    """
    return db or os.environ.get("WORKBENCH_PAZ_DB") or DEFAULT_DB


def _ro(path: str) -> sqlite3.Connection:
    # mode=ro：任何写操作都会直接报错，从连接层保证不碰平安志数据
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    # analysis_todo 的 detail_json 是大 TEXT，放大行要跨溢出页；mmap 后全表扫描
    # 从 ~450ms 降到 ~250ms（只读连接，不会改动库文件）
    try:
        con.execute("pragma query_only=1")
        con.execute("pragma mmap_size=536870912")
        con.execute("pragma cache_size=-16000")
    except sqlite3.Error:
        pass
    return con


def _exists(db: Optional[str] = None) -> Dict[str, Any]:
    p = db_path(db)
    if not os.path.isfile(p):
        return {"ok": False, "reason": "找不到平安志数据库: %s" % p, "path": p}
    return {"ok": True, "path": p}


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute("select 1 from sqlite_master where type='table' and name=?",
                       (name,)).fetchone() is not None


# ---------------------------------------------------------------- 游戏资产
def inventory(db: Optional[str] = None, limit: int = 0) -> Dict[str, Any]:
    """inventory_item：按套装 / 号位 / 星级聚合的库存明细。"""
    chk = _exists(db)
    if not chk["ok"]:
        return chk
    con = _ro(chk["path"])
    by_set, by_slot, by_quality = {}, {}, {}
    rows = 0
    for set_id, slot, quality in con.execute(
            "select s.set_id, s.slot, s.quality from inventory_item i "
            "join snapshot_soul s on s.internal_id = i.soul_internal_id and s.snapshot_id = i.snapshot_id"):
        rows += 1
        by_set[set_id] = by_set.get(set_id, 0) + 1
        by_slot[str(slot)] = by_slot.get(str(slot), 0) + 1
        by_quality[str(quality)] = by_quality.get(str(quality), 0) + 1
    con.close()
    top = sorted(by_set.items(), key=lambda kv: -kv[1])
    if limit:
        top = top[:limit]
    return {"ok": True, "source": "inventory_item", "total": rows,
            "by_set": dict(top), "by_slot": by_slot, "by_quality": by_quality}


# ---------------------------------------------------------------- 常用度
def commonness(db: Optional[str] = None) -> Dict[str, Any]:
    """set_commonness_default + profile_commonness_override。"""
    chk = _exists(db)
    if not chk["ok"]:
        return chk
    con = _ro(chk["path"])
    defaults, overrides = [], []
    try:
        defaults = [{"set": s, "scenario": sc, "value": v}
                    for s, sc, v in con.execute(
                        "select set_id, scenario, value from set_commonness_default")]
    except sqlite3.Error:
        pass
    try:
        overrides = [{"profile": p, "set": s, "scenario": sc, "value": v}
                     for p, s, sc, v in con.execute(
                         "select profile_id, set_id, scenario, value "
                         "from profile_commonness_override")]
    except sqlite3.Error:
        pass
    con.close()
    pve = sum(1 for d in defaults if d["scenario"] == "PVE" and d["value"] == "common")
    pvp = sum(1 for d in defaults if d["scenario"] == "PVP" and d["value"] == "common")
    return {"ok": True, "source": "set_commonness_default", "defaults": len(defaults),
            "pve_common": pve, "pvp_common": pvp, "overrides": len(overrides),
            "rows": defaults[:40], "override_rows": overrides[:20]}


# ---------------------------------------------------------------- 相关度
def relevance(db: Optional[str] = None) -> Dict[str, Any]:
    """relevance_profile + active_relevance_profile。"""
    chk = _exists(db)
    if not chk["ok"]:
        return chk
    con = _ro(chk["path"])
    rows = []
    try:
        for pid, version, title, author, desc, status in con.execute(
                "select id, version, title, author, description, status from relevance_profile"):
            rows.append({"id": pid, "version": version, "title": title,
                         "author": author, "description": (desc or "")[:120],
                         "status": status})
    except sqlite3.Error:
        pass
    active = None
    try:
        r = con.execute("select relevance_profile_id, enabled_at from active_relevance_profile").fetchone()
        active = {"id": r[0], "enabled_at": r[1]} if r else None
    except sqlite3.Error:
        pass
    con.close()
    return {"ok": True, "source": "relevance_profile", "count": len(rows),
            "active": active, "rows": rows[:10]}


# ---------------------------------------------------------------- 御魂分组
def yuhun_groups(db: Optional[str] = None) -> Dict[str, Any]:
    """yuhun_group + yuhun_group_member。"""
    chk = _exists(db)
    if not chk["ok"]:
        return chk
    con = _ro(chk["path"])
    groups = []
    try:
        # 表结构：yuhun_group(id, origin, name, description, parent_id, revision)
        #         yuhun_group_member(group_id, set_id, catalog_version, origin, user_override)
        for gid, name, desc in con.execute("select id, name, description from yuhun_group"):
            members = [{"set": st, "user_override": bool(uo)}
                       for st, uo in con.execute(
                           "select set_id, user_override from yuhun_group_member where group_id=?",
                           (gid,))]
            groups.append({"id": gid, "name": name, "description": desc,
                           "members": len(members), "sets": [m["set"] for m in members]})
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": str(exc)}
    con.close()
    return {"ok": True, "source": "yuhun_group", "groups": len(groups),
            "members": sum(g["members"] for g in groups), "rows": groups}


# ---------------------------------------------------------------- 待办
def todos(db: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    """analysis_todo：待办清单（该强化的候选）。"""
    chk = _exists(db)
    if not chk["ok"]:
        return chk
    con = _ro(chk["path"])
    cols = [r[1] for r in con.execute("pragma table_info(analysis_todo)")]
    rows = []
    try:
        total = con.execute("select count(*) from analysis_todo").fetchone()[0]
        sel = ", ".join(cols[:8])
        for r in con.execute("select %s from analysis_todo limit %d" % (sel, int(limit))):
            rows.append(dict(zip(cols[:8], r)))
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": str(exc)}
    con.close()
    return {"ok": True, "source": "analysis_todo", "total": total,
            "columns": cols, "rows": rows}


# ---------------------------------------------------------------- 阵容目录
def lineup_catalog(path: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    p = path or LINEUP_FILE
    if not os.path.isfile(p):
        return {"ok": False, "reason": "找不到阵容目录: %s" % p}
    try:
        data = json.load(io.open(p, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": str(exc)}
    # 实测结构：{"manifest": {...}, "payload": "<JSON 字符串>"}
    if isinstance(data, dict) and isinstance(data.get("payload"), str):
        try:
            data = json.loads(data["payload"])
        except json.JSONDecodeError:
            pass
    items = (data.get("entries") or data.get("lineups") or data.get("items")
             or data.get("rows") or [])
    if isinstance(items, dict):
        items = list(items.values())
    return {"ok": True, "source": p, "keys": list(data)[:8],
            "total": len(items),
            "samples": [{"name": (x.get("name") or x.get("title") or "")[:24],
                         "kind": x.get("kind") or x.get("usage") or ""}
                        for x in items[:limit] if isinstance(x, dict)]}


# ---------------------------------------------------------------- 单颗御魂详情
def _json(text: Any) -> Dict[str, Any]:
    if isinstance(text, dict):
        return text
    try:
        data = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _labels(row: Any, detail: Dict[str, Any]) -> List[str]:
    """待办标签：分类 / 建议 / 证据等级 / 新增·变更 / 命中规则。"""
    labels = [str(row["category"] or ""), str(row["recommendation"] or ""),
              str(row["evidence_level"] or "")]
    if row["is_new"]:
        labels.append("新增")
    if row["is_changed"]:
        labels.append("有变化")
    rule = ((detail.get("standardScore") or {}).get("matchedRuleName") or "")
    if rule:
        labels.append(rule)
    return [x for x in labels if x]


def _soul_facts(con: sqlite3.Connection, row: Any, soul_key: str) -> Dict[str, Any]:
    """snapshot_soul 一行 + soul_attribute 全部属性 → 引擎可用结构。"""
    from tasks.Workbench.ingest.paz_db import attribute_value

    snapshot_id, iid = row["snapshot_id"], row["internal_id"]
    attrs, subs, rolls, fixed_attrs, fixed_stats = [], {}, {}, [], {}
    for a in con.execute(
            "select attribute_type, value, enhancement_count, fixed_attribute "
            "from soul_attribute where snapshot_id = ? and soul_internal_id = ? "
            "order by attribute_index", (snapshot_id, iid)):
        value = attribute_value(a["attribute_type"], a["value"])
        is_fixed = bool(a["fixed_attribute"])
        attrs.append({"attribute_type": a["attribute_type"], "name": _attr_cn(a["attribute_type"]),
                      "value": value, "is_fixed": is_fixed,
                      "rolls": 0 if is_fixed else int(a["enhancement_count"] or 0)})
        if is_fixed:
            fixed_attrs.append(a["attribute_type"])
            fixed_stats[a["attribute_type"]] = value
        else:
            subs[a["attribute_type"]] = value
            rolls[a["attribute_type"]] = int(a["enhancement_count"] or 0)
    main_type = row["main_attr_type"]
    main_value = attribute_value(main_type, row["main_attr_value"])
    initial = row["initial_substat_count"]
    soul = {"internal_id": iid, "soul_key": soul_key, "set_name": row["set_id"],
            "slot": row["slot"], "quality": row["quality"], "level": row["level"],
            "main_attribute": _attr_cn(main_type), "main_attr_type": main_type,
            "main_value": main_value, "equipped": row["equipped_state"] or "",
            "locked": bool(row["locked_in_source"])}
    raw = {"set_id": row["set_id"], "slot": row["slot"], "quality": row["quality"],
           "level": row["level"], "main_attribute": main_type, "main_value": main_value,
           "substats": subs, "rolls": rolls, "fixed_attrs": fixed_attrs,
           "fixed_stats": fixed_stats,
           "initial_substat_count": len(subs) if initial is None else int(initial),
           "locked": bool(row["locked_in_source"])}
    return {"ok": True, "source": "", "soul": soul, "attributes": attrs, "raw": raw,
            "main": {"attribute_type": main_type, "name": _attr_cn(main_type),
                     "value": main_value}}


def _attr_cn(attr: Any) -> str:
    from tasks.Workbench.engine import ATTR_CN
    return ATTR_CN.get(str(attr), str(attr or ""))


def soul_detail(soul_id: Any, db: Optional[str] = None) -> Dict[str, Any]:
    """一颗御魂：属性明细 + 平安志自己的评分/贡献（analysis_todo.detail_json）。"""
    chk = _exists(db)
    if not chk["ok"]:
        return {**chk, "not_found": False}
    key = str(soul_id or "").strip()
    if not key:
        return {"ok": False, "reason": "缺少御魂编号", "not_found": False}
    con = _ro(chk["path"])
    con.row_factory = sqlite3.Row
    cols = ("snapshot_id, internal_id, set_id, slot, quality, level, main_attr_type, "
            "main_attr_value, initial_substat_count, locked_in_source, equipped_state")
    try:
        row = con.execute("select %s from snapshot_soul where internal_id = ? limit 1" % cols,
                          (key,)).fetchone()
        soul_key = key
        if row is None:
            inv = con.execute("select soul_key, snapshot_id, soul_internal_id from inventory_item "
                              "where soul_key = ? limit 1", (key,)).fetchone()
            if inv is not None:
                soul_key = inv["soul_key"]
                row = con.execute("select %s from snapshot_soul where internal_id = ? "
                                  "and snapshot_id = ? limit 1" % cols,
                                  (inv["soul_internal_id"], inv["snapshot_id"])).fetchone()
        if row is None:
            return {"ok": False, "reason": "找不到御魂: %s" % key, "not_found": True}
        facts = _soul_facts(con, row, soul_key)
        facts["source"] = chk["path"]
        todo = con.execute(
            "select id, standard_score, composite_score, category, recommendation, "
            "evidence_level, data_quality, presence_state, is_new, is_changed, "
            "reason_summary, detail_json, generated_at from analysis_todo "
            "where soul_internal_id = ? order by generated_at desc limit 1",
            (row["internal_id"],)).fetchone()
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": "平安志库读取失败: %s" % exc}
    con.close()
    if todo is None:
        facts["todo"] = {}
    else:
        detail = _json(todo["detail_json"])
        standard = detail.get("standardScore") or {}
        contributions = []
        for c in standard.get("contributions") or []:
            if not isinstance(c, dict):
                continue
            contributions.append({"label": c.get("attributeName") or _attr_cn(c.get("attributeType")),
                                  "value": round(float(c.get("contribution") or 0), 2)})
        facts["todo"] = {
            "id": todo["id"],
            "score": todo["standard_score"], "composite_score": todo["composite_score"],
            "rule": standard.get("matchedRuleName") or "",
            "rule_id": standard.get("matchedRuleId") or "",
            "contributions": contributions, "formula": standard.get("formula") or "",
            "category": todo["category"], "recommendation": todo["recommendation"],
            "evidence_level": todo["evidence_level"], "data_quality": todo["data_quality"],
            "presence_state": todo["presence_state"],
            "is_new": bool(todo["is_new"]), "is_changed": bool(todo["is_changed"]),
            "reason": todo["reason_summary"] or "", "generated_at": todo["generated_at"],
            "labels": _labels(todo, detail),
        }
    return facts


# ---------------------------------------------------------------- 待办清单
TODO_STATUSES = ("open", "done", "ignored")


def todo_items(decisions: Optional[Dict[str, Any]] = None, status: str = "open",
               limit: int = 50, db: Optional[str] = None) -> Dict[str, Any]:
    """analysis_todo + OAS 自己的处置记录 → 待办列表与计数。

    decisions: {todo_id: {"action": done|ignore|restore, "status": done|ignored|open,
                          "note": str, "at": str}}（来自 tasks/Workbench/store.py）
    """
    decisions = decisions or {}
    counts = {"open": 0, "done": 0, "ignored": 0}
    chk = _exists(db)
    if not chk["ok"]:
        return {**chk, "items": [], "counts": counts, "total": 0, "columns": []}
    con = _ro(chk["path"])
    con.row_factory = sqlite3.Row
    try:
        if not _has_table(con, "analysis_todo"):
            con.close()
            return {"ok": False, "reason": "平安志库没有 analysis_todo 表",
                    "items": [], "counts": counts, "total": 0}
        rows = list(con.execute(
            "select id, soul_key, soul_internal_id, snapshot_id, set_id, slot, quality, level, "
            "main_attribute, main_value, category, recommendation, evidence_level, "
            "presence_state, is_new, is_changed, standard_score, generated_at "
            "from analysis_todo order by standard_score desc, id"))
        for row in rows:
            decision = decisions.get(row["id"]) or {}
            counts[_status_of(decision)] += 1
        if status and status != "all":
            rows = [r for r in rows if _status_of(decisions.get(r["id"]) or {}) == status]
        page = rows[:max(0, int(limit))]
        details = {}
        if page:
            marks = ",".join("?" * len(page))
            for r in con.execute("select id, detail_json from analysis_todo where id in (%s)" % marks,
                                 [r["id"] for r in page]):
                details[r["id"]] = _json(r["detail_json"])
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": "平安志库读取失败: %s" % exc,
                "items": [], "counts": counts, "total": 0}
    con.close()
    items = []
    for row in page:
        detail = details.get(row["id"]) or {}
        decision = decisions.get(row["id"]) or {}
        items.append({
            "todo_id": row["id"], "soul_id": row["soul_key"], "set_name": row["set_id"],
            "score": row["standard_score"],
            "rule": ((detail.get("standardScore") or {}).get("matchedRuleName") or ""),
            "status": _status_of(decision), "note": decision.get("note") or "",
            "labels": _labels(row, detail),
            "soul_internal_id": row["soul_internal_id"], "slot": row["slot"],
            "level": row["level"], "quality": row["quality"],
            "main_attribute": _attr_cn(row["main_attribute"]),
            "category": row["category"], "recommendation": row["recommendation"],
            "updated": decision.get("at") or "",
        })
    return {"ok": True, "source": "analysis_todo", "total": len(rows) if status in ("", "all")
            else sum(counts.values()), "items": items, "counts": counts,
            "filtered": len(rows)}


def _status_of(decision: Dict[str, Any]) -> str:
    status = str(decision.get("status") or "")
    return status if status in TODO_STATUSES else "open"


def todo_lookup(todo_id: Any, db: Optional[str] = None) -> Dict[str, Any]:
    """单条待办（处置前校验存在性），返回 {ok, item 或 reason}。"""
    chk = _exists(db)
    if not chk["ok"]:
        return chk
    con = _ro(chk["path"])
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("select id, set_id, standard_score from analysis_todo where id = ? "
                          "limit 1", (str(todo_id),)).fetchone()
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": "平安志库读取失败: %s" % exc}
    con.close()
    if row is None:
        return {"ok": False, "reason": "找不到该待办", "not_found": True}
    return {"ok": True, "item": {"todo_id": row["id"], "set_name": row["set_id"],
                                 "score": row["standard_score"]}}


# ---------------------------------------------------------------- 标准/规则版本
def rule_versions(db: Optional[str] = None) -> Dict[str, Any]:
    """rule_version + rule_preset + active_score_standard（只读）。"""
    chk = _exists(db)
    if not chk["ok"]:
        return {**chk, "items": [], "active": ""}
    con = _ro(chk["path"])
    con.row_factory = sqlite3.Row
    try:
        if not _has_table(con, "rule_version"):
            con.close()
            return {"ok": False, "reason": "平安志库没有 rule_version 表",
                    "items": [], "active": ""}
        rows = list(con.execute(
            "select rv.id, rv.preset_id, rv.version, rv.title, rv.status, rv.created_at, "
            "rv.read_only, rv.canonical_json, rp.title as preset_title "
            "from rule_version rv left join rule_preset rp on rp.id = rv.preset_id "
            "order by rv.id"))
        active = ""
        if _has_table(con, "active_score_standard"):
            hit = con.execute("select rule_version_id from active_score_standard limit 1").fetchone()
            active = hit["rule_version_id"] if hit else ""
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": "平安志库读取失败: %s" % exc, "items": [], "active": ""}
    con.close()
    items = []
    for r in rows:
        canonical = _json(r["canonical_json"])
        items.append({
            "id": r["id"], "name": r["title"] or r["preset_title"] or r["preset_id"],
            "active": r["id"] == active, "rule_count": len(canonical.get("rules") or []),
            "updated": r["created_at"], "version": r["version"], "status": r["status"],
            "preset": r["preset_id"], "read_only": bool(r["read_only"]),
        })
    return {"ok": True, "source": "rule_version", "items": items, "active": active}


# ---------------------------------------------------------------- 式神详情
def shikigami_detail(name: Any, db: Optional[str] = None) -> Dict[str, Any]:
    """shikigami + owned_shikigami + 现有御魂（按 equipped_state 里的实例号匹配）。"""
    chk = _exists(db)
    if not chk["ok"]:
        return {**chk, "not_found": False}
    key = str(name or "").strip()
    if not key:
        return {"ok": False, "reason": "缺少式神名或编号", "not_found": False}
    con = _ro(chk["path"])
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("select * from shikigami where shikigami_id = ? or name = ? limit 1",
                          (key, key)).fetchone()
        if row is None:
            con.close()
            return {"ok": False, "reason": "式神目录里没有: %s" % key, "not_found": True}
        sid = row["shikigami_id"]
        owned = list(con.execute(
            "select instance_id, star, level, awakened, skin_id, skills_json "
            "from owned_shikigami where shikigami_id = ? order by level desc, star desc", (sid,)))
        souls = []
        for inst in owned:
            inst_id = inst["instance_id"]
            for soul in con.execute(
                    "select s.set_id, s.slot, s.quality, s.level, a.standard_score "
                    "from snapshot_soul s left join analysis_todo a "
                    "on a.soul_internal_id = s.internal_id "
                    "where s.equipped_state like ? order by s.slot",
                    ("%" + inst_id + "%",)):
                souls.append({"slot": soul["slot"], "set_name": soul["set_id"],
                              "score": soul["standard_score"], "level": soul["level"],
                              "quality": soul["quality"]})
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": "平安志库读取失败: %s" % exc}
    con.close()
    catalog_skills = _json_list(row["skills_json"])
    levels = {}
    for inst in owned:
        for s in _json_list(inst["skills_json"]):
            if isinstance(s, dict) and s.get("skillId") is not None:
                levels[str(s["skillId"])] = max(int(s.get("level") or 0),
                                                levels.get(str(s["skillId"]), 0))
    skills = []
    for s in catalog_skills:
        if not isinstance(s, dict):
            continue
        skills.append({"id": str(s.get("skillId") or ""), "name": s.get("name") or "",
                       "type": s.get("type") or "",
                       "level": levels.get(str(s.get("skillId")), 0),
                       "energy_cost": s.get("energyCost")})
    if not skills and levels:
        skills = [{"id": sid, "name": "", "type": "", "level": lv, "energy_cost": None}
                  for sid, lv in levels.items()]
    best = dict(owned[0]) if owned else {}
    return {"ok": True, "source": chk["path"],
            "shikigami": {"id": sid, "name": row["name"], "rarity": row["rarity"],
                          "level": best.get("level"), "star": best.get("star"),
                          "awakened": bool(best.get("awakened")),
                          "skin": best.get("skin_id"),
                          "icon": row["icon_asset_id"],
                          "panel": _json(row["panel_json"]),
                          "skills": skills, "souls": souls,
                          "owned": bool(owned), "instances": len(owned)},
            "not_found": False}


def _json_list(text: Any) -> List[Any]:
    if isinstance(text, list):
        return text
    try:
        data = json.loads(text or "[]")
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------- 快照 / 采集 / 备份
def snapshots(db: Optional[str] = None) -> Dict[str, Any]:
    """snapshot + snapshot_summary + snapshot_integrity（缺失表优雅降级）。"""
    chk = _exists(db)
    if not chk["ok"]:
        return {**chk, "items": []}
    con = _ro(chk["path"])
    con.row_factory = sqlite3.Row
    try:
        if not _has_table(con, "snapshot"):
            con.close()
            return {"ok": False, "reason": "平安志库没有 snapshot 表", "items": []}
        has_summary = _has_table(con, "snapshot_summary")
        has_integrity = _has_table(con, "snapshot_integrity")
        sql = ("select s.id, s.captured_at, s.source_kind, s.completeness, s.parser_version, "
               "s.catalog_version, s.created_at, s.parent_snapshot_id")
        if has_summary:
            sql += ", ss.soul_count, ss.inventory_reference_count"
        else:
            sql += ", null as soul_count, null as inventory_reference_count"
        sql += " from snapshot s"
        if has_summary:
            sql += " left join snapshot_summary ss on ss.snapshot_id = s.id"
        sql += " order by s.captured_at desc, s.created_at desc"
        rows = list(con.execute(sql))
        integrity = {}
        if has_integrity:
            for r in con.execute("select snapshot_id, status, message, checked_at "
                                 "from snapshot_integrity"):
                integrity[r["snapshot_id"]] = r
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": "平安志库读取失败: %s" % exc, "items": []}
    con.close()
    items = []
    for r in rows:
        mark = integrity.get(r["id"])
        items.append({
            "snapshot_id": r["id"], "time": r["captured_at"] or r["created_at"],
            "items": r["inventory_reference_count"], "souls": r["soul_count"],
            "source": r["source_kind"],
            "integrity": (mark["status"] if mark else
                          ("unknown" if has_integrity else "未记录")),
            "integrity_message": (mark["message"] if mark else ""),
            "checked_at": (mark["checked_at"] if mark else ""),
            "completeness": r["completeness"],
        })
    return {"ok": True, "source": "snapshot", "items": items,
            "note": "" if has_integrity else "平安志库没有 snapshot_integrity 表，完整性按「未记录」呈现"}


def acquisitions(limit: int = 50, db: Optional[str] = None) -> Dict[str, Any]:
    """acquisition_event：数据采集记录（只读）。"""
    chk = _exists(db)
    if not chk["ok"]:
        return {**chk, "items": []}
    con = _ro(chk["path"])
    con.row_factory = sqlite3.Row
    try:
        if not _has_table(con, "acquisition_event"):
            con.close()
            return {"ok": False, "reason": "平安志库没有 acquisition_event 表", "items": []}
        rows = list(con.execute(
            "select id, profile_id, source_kind, source_format, captured_at, received_at, "
            "completeness, result_kind, snapshot_id, parser_version, adapter_version "
            "from acquisition_event order by received_at desc, captured_at desc limit ?",
            (max(1, int(limit)),)))
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": "平安志库读取失败: %s" % exc, "items": []}
    con.close()
    items = [{"id": r["id"], "time": r["received_at"] or r["captured_at"],
              "source": r["source_kind"], "format": r["source_format"],
              "result": r["result_kind"], "snapshot_id": r["snapshot_id"],
              "completeness": r["completeness"], "parser": r["parser_version"],
              "profile_id": r["profile_id"]} for r in rows]
    return {"ok": True, "source": "acquisition_event", "items": items, "total": len(items)}


def paz_backups(db: Optional[str] = None) -> Dict[str, Any]:
    """backup_record：平安志自己的备份记录（只读展示，绝不触碰其文件）。"""
    chk = _exists(db)
    if not chk["ok"]:
        return {**chk, "items": []}
    con = _ro(chk["path"])
    con.row_factory = sqlite3.Row
    try:
        if not _has_table(con, "backup_record"):
            con.close()
            return {"ok": False, "reason": "平安志库没有 backup_record 表", "items": []}
        rows = list(con.execute("select id, path, total_size, reason, created_at, verified "
                                "from backup_record order by created_at desc limit 50"))
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": "平安志库读取失败: %s" % exc, "items": []}
    con.close()
    return {"ok": True, "source": "backup_record",
            "items": [{"id": r["id"], "time": r["created_at"], "size": r["total_size"],
                       "path": r["path"], "note": r["reason"],
                       "verified": bool(r["verified"])} for r in rows]}


# ---------------------------------------------------------------- 面板 / 速度历史
HISTORY_TABLES = {"panel": "pve_panel_cache", "speed": "speed_preview_cache"}


def history(kind: str = "panel", limit: int = 50, db: Optional[str] = None) -> Dict[str, Any]:
    """pve_panel_cache / speed_preview_cache（本机为空时返回空列表 + 说明，不报错）。"""
    table = HISTORY_TABLES.get(kind)
    if table is None:
        return {"ok": False, "reason": "未知历史类型: %s" % kind, "items": []}
    chk = _exists(db)
    if not chk["ok"]:
        return {**chk, "items": [], "note": "平安志库不可用，历史记录取不到"}
    con = _ro(chk["path"])
    con.row_factory = sqlite3.Row
    try:
        if not _has_table(con, table):
            con.close()
            return {"ok": False, "reason": "平安志库没有 %s 表" % table, "items": [],
                    "note": "平安志没建这张缓存表，历史为空"}
        cols = [r[1] for r in con.execute('pragma table_info("%s")' % table)]
        total = con.execute('select count(*) from "%s"' % table).fetchone()[0]
        rows = [dict(zip(cols, r)) for r in
                con.execute('select * from "%s" limit ?' % table, (max(1, int(limit)),))]
    except sqlite3.Error as exc:
        con.close()
        return {"ok": False, "reason": "平安志库读取失败: %s" % exc, "items": []}
    con.close()
    return {"ok": True, "source": table, "items": rows, "total": total,
            "note": "" if total else "平安志的 %s 还没有记录（表存在但为空），历史为空" % table}


# ---------------------------------------------------------------- 元信息
def meta(db: Optional[str] = None) -> Dict[str, Any]:
    """数据源总览：平安志是否可用、库路径、三类计数、最近同步时间。"""
    p = db_path(db)
    available = os.path.isfile(p)
    counts = {"souls": 0, "shikigami": 0, "todos": 0}
    last_sync = None
    reason = "" if available else "找不到平安志数据库: %s" % p
    if available:
        con = _ro(p)
        con.row_factory = sqlite3.Row
        try:
            for key, table in (("souls", "snapshot_soul"), ("shikigami", "owned_shikigami"),
                               ("todos", "analysis_todo")):
                if _has_table(con, table):
                    counts[key] = con.execute('select count(*) from "%s"' % table).fetchone()[0]
            if _has_table(con, "snapshot"):
                hit = con.execute("select max(captured_at) from snapshot").fetchone()
                last_sync = hit[0] if hit else None
            if not last_sync and _has_table(con, "analysis_todo"):
                hit = con.execute("select max(generated_at) from analysis_todo").fetchone()
                last_sync = hit[0] if hit else None
        except sqlite3.Error as exc:
            con.close()
            return {"ok": False, "reason": "平安志库读取失败: %s" % exc,
                    "data_source": "oas-store",
                    "pinganzhi": {"available": available, "path": p},
                    "counts": counts, "last_sync": None}
        con.close()
    return {"ok": True, "data_source": "paz-db" if available else "oas-store",
            "pinganzhi": {"available": available, "path": p},
            "counts": counts, "last_sync": last_sync, "reason": reason}


# ---------------------------------------------------------------- 汇总
def summary(db: Optional[str] = None) -> Dict[str, Any]:
    """一次拿到全部数据域的状态，供 /workbench/domains 使用。"""
    return {
        "inventory": inventory(db, limit=8),
        "commonness": commonness(db),
        "relevance": relevance(db),
        "yuhun_groups": yuhun_groups(db),
        "todos": todos(db, limit=3),
        "lineup_catalog": lineup_catalog(),
    }


# ---------------------------------------------------------------- 货币余额
DATA_DIR = os.path.join(os.environ.get("APPDATA", ""), "com.yys.analysis")
_CURRENCY_CACHE: Dict[str, Any] = {}


def data_dir() -> str:
    """平安志落盘目录：环境变量 WORKBENCH_PAZ_DATA 只给回归/自检用。"""
    return os.environ.get("WORKBENCH_PAZ_DATA") or DATA_DIR


def _newest_data_file() -> Optional[str]:
    d = data_dir()
    if not os.path.isdir(d):
        return None
    best, best_m = None, -1.0
    try:
        names = os.listdir(d)
    except OSError:
        return None
    for name in names:
        if not name.startswith("current-data-") or not name.endswith(".json"):
            continue
        if name.endswith(".projection.json"):
            continue
        p = os.path.join(d, name)
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if m > best_m:
            best, best_m = p, m
    return best


def currency() -> Dict[str, Any]:
    """勾玉/金币/体力等余额。

    这些余额**不在** sqlite 的 42 张表里，只在采集快照
    `current-data-*.json` 的 `sourcePayload.currency`（`normalizedItems` 同值）里。
    快照 15MB，按 (路径, mtime, size) 缓存，命中时不重复解析。
    """
    p = _newest_data_file()
    if not p:
        return {"ok": False, "reason": "找不到平安志采集快照 current-data-*.json",
                "balances": {}, "source": None}
    try:
        st = os.stat(p)
    except OSError as exc:
        return {"ok": False, "reason": "快照不可读: %s" % exc, "balances": {}, "source": None}
    key = "%s|%s|%s" % (p, st.st_mtime_ns, st.st_size)
    hit = _CURRENCY_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        with io.open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": "快照解析失败: %s" % exc, "balances": {}, "source": None}
    payload = raw.get("sourcePayload")
    payload = payload if isinstance(payload, dict) else {}
    src = payload.get("currency")
    if not isinstance(src, dict):
        src = raw.get("normalizedItems")
    balances = {}
    if isinstance(src, dict):
        for k, v in src.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            balances[str(k)] = int(v)
    out = {"ok": bool(balances), "source": "paz-snapshot-json",
           "file": os.path.basename(p), "captured_at": raw.get("capturedAt"),
           "balances": balances,
           "reason": None if balances else "快照里没有 currency / normalizedItems 字段"}
    if balances:
        _CURRENCY_CACHE.clear()
        _CURRENCY_CACHE[key] = out
    return out


# ---------------------------------------------------------------- 结界 / 余额历史
HISTORY_FILE = os.path.join(os.environ.get("WORKBENCH_STORE") or "log", "workbench",
                            "currency_history.jsonl")


def realm_cards() -> Dict[str, Any]:
    """己方结界 / 寄养结界：来自快照 taskRecords.realmCardStatus。"""
    p = _newest_data_file()
    if not p:
        return {"ok": False, "reason": "找不到合并快照", "own": None, "foster": None}
    try:
        with io.open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": "快照解析失败: %s" % exc, "own": None, "foster": None}
    sp = raw.get("sourcePayload") if isinstance(raw.get("sourcePayload"), dict) else {}
    status = (sp.get("taskRecords") or {}).get("realmCardStatus") or {}
    out = {"ok": False, "captured_at": raw.get("capturedAt"), "own": None, "foster": None,
           "reason": "快照没有结界数据"}
    for slot in ("own", "foster"):
        row = status.get(slot)
        if not isinstance(row, dict):
            continue
        ends = row.get("endsAt")
        out[slot] = {
            "active": bool(row.get("active")),
            "available": bool(row.get("available")),
            "ends_at": ends,
            "started_at": row.get("startedAt"),
            "expected_jade": row.get("expectedJade"),
            "expected_stamina": row.get("expectedStamina"),
            "item_id": row.get("itemId"),
        }
        out["ok"] = True
    if out["ok"]:
        out["reason"] = None
    return out


def _history_path() -> str:
    root = os.environ.get("WORKBENCH_STORE") or ""
    if root:
        return os.path.join(root, "currency_history.jsonl")
    return os.path.join("log", "workbench", "currency_history.jsonl")


def record_currency_history(cur: Optional[Dict[str, Any]] = None) -> int:
    """把当前快照的余额追加进本地历史（按 captured_at 去重），返回历史条数。

    只写 OAS 自己的 log 目录，不碰平安志；一个快照时点只留一行，
    所以重复刷新不会灌水；日/周/月环比就靠这些行算。
    """
    cur = cur if isinstance(cur, dict) else currency()
    at = cur.get("captured_at")
    balances = cur.get("balances") or {}
    if not at or not balances:
        return _count_history()
    path = _history_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        seen = set()
        if os.path.isfile(path):
            with io.open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seen.add(json.loads(line).get("at"))
                    except ValueError:
                        continue
        if at not in seen:
            with io.open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": at, "balances": balances}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return _count_history()


def _read_history() -> List[Dict[str, Any]]:
    path = _history_path()
    rows = []
    if not os.path.isfile(path):
        return rows
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("at") and isinstance(row.get("balances"), dict):
                    rows.append(row)
    except OSError:
        return []
    rows.sort(key=lambda r: str(r.get("at")))
    return rows


def _count_history() -> int:
    return len(_read_history())


_PERIOD_DAYS = {"prev": None, "day": 1, "week": 7, "month": 30}


def currency_delta(cur: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """日/周/月 + 上一次的余额差。历史不足时对应项为 None（不编数）。"""
    cur = cur if isinstance(cur, dict) else currency()
    at = cur.get("captured_at")
    now_bal = cur.get("balances") or {}
    rows = _read_history()
    out = {"points": len(rows), "prev": None, "day": None, "week": None, "month": None,
           "basis": {}}
    if not at or not now_bal or not rows:
        return out

    def parse(ts):
        s = str(ts).replace("Z", "+0000")
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                import datetime as _dt
                return _dt.datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    now = parse(at)
    if now is None:
        return out
    # 只保留早于当前时点的行
    earlier = [r for r in rows if str(r.get("at")) < str(at)]
    if not earlier:
        return out

    def delta_against(ref):
        base = ref.get("balances") or {}
        keys = set(now_bal) | set(base)
        d = {}
        for k in keys:
            if k in now_bal and k in base:
                d[k] = int(now_bal[k]) - int(base[k])
        return d

    out["prev"] = delta_against(earlier[-1])
    out["basis"]["prev"] = earlier[-1].get("at")
    for period, days in _PERIOD_DAYS.items():
        if days is None:
            continue
        target = now - _dt_timedelta(days)
        best = None
        for row in earlier:
            ts = parse(row.get("at"))
            if ts is None:
                continue
            if abs((target - ts).total_seconds()) > 86400 * max(1, days * 0.5):
                continue
            if best is None or abs((target - ts).total_seconds()) < abs(
                    (target - parse(best.get("at"))).total_seconds()):
                best = row
        if best is not None:
            out[period] = delta_against(best)
            out["basis"][period] = best.get("at")
    return out


def _dt_timedelta(days: int):
    import datetime as _dt
    return _dt.timedelta(days=days)


# ---------------------------------------------------------------- 道具 / 加成
def account_resources() -> Dict[str, Any]:
    """快照 sourcePayload.accountComparison：门票类道具 + 经验/金币/御魂加成 + 签到天数。

    余额（勾玉/金币）在 `currency`，这里放的是"资产页下半截"那类道具与 buff，
    平安志自己也是分两个域读的。
    """
    p = _newest_data_file()
    if not p:
        return {"ok": False, "reason": "找不到合并快照", "resources": {}, "buffs": []}
    try:
        with io.open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": "快照解析失败: %s" % exc, "resources": {}, "buffs": []}
    sp = raw.get("sourcePayload") if isinstance(raw.get("sourcePayload"), dict) else {}
    ac = sp.get("accountComparison") if isinstance(sp.get("accountComparison"), dict) else {}
    items = ac.get("items") if isinstance(ac.get("items"), dict) else {}
    res = items.get("resources") if isinstance(items.get("resources"), dict) else {}
    resources = {str(k): v for k, v in res.items()
                 if isinstance(v, int) and not isinstance(v, bool)}
    buffs = []
    raw_buffs = items.get("buffs") if isinstance(items.get("buffs"), dict) else {}
    for name, label in (("experience", "经验加成"), ("gold", "金币加成"), ("soul", "御魂加成")):
        rows = raw_buffs.get(name)
        rows = rows if isinstance(rows, list) else ([rows] if isinstance(rows, dict) else [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            buffs.append({"key": name, "label": label, "factor": row.get("factor"),
                          "paused": bool(row.get("paused")),
                          "remaining_seconds": row.get("remainingSeconds")})
    if isinstance(raw_buffs.get("awake"), dict):
        buffs.append({"key": "awake", "label": "觉醒加成", "factor": raw_buffs["awake"].get("factor"),
                      "paused": bool(raw_buffs["awake"].get("paused")),
                      "remaining_seconds": raw_buffs["awake"].get("remainingSeconds")})
    out = {"ok": bool(resources or buffs), "source": "paz-snapshot-json",
           "captured_at": raw.get("capturedAt"), "resources": resources, "buffs": buffs,
           "sign_days": items.get("signDays"), "graceful_score": items.get("gracefulScore"),
           "reason": None if (resources or buffs) else "快照没有 accountComparison.items"}
    return out
