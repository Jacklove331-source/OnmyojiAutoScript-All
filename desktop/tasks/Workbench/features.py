# This file uses the following encoding: utf-8
"""Workbench 功能层：把平安志的侧栏功能实现为纯函数。

对应关系（藏宝阁已剔除）：
  monitor        监控         -> monitor_summary
  workbench      工作台首页   -> overview
  ingest         数据读取     -> 由 ingest/* 提供，这里只做规范化入口
  profile        角色档案     -> profile_summary
  tasklog        任务记录     -> task_summary
  souls          御魂分析     -> soul_report（走 engine.RuleEngine）
  panel          面板计算/提升 -> panel_calc / panel_upgrade
  simulate       模拟玩法     -> speed_preview
  roster         式神录       -> roster
  shards         碎片查询     -> shard_query
  assets         游戏资产     -> asset_summary
  guild          寮管理       -> guild_summary
  tools          小工具       -> 由 tools.py 提供（多开 / 定位）

所有函数只读本地数据，不碰设备。
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Iterable, List, Optional

from tasks.Workbench.engine import (ATTR_CN, Catalog, RuleEngine, Standards, normalize_attr,
                                    normalize_soul)
from tasks.Workbench.ingest.activities import activity_report

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 练度阈值来自 current-analysis.sqlite3 的 maturity_threshold（system 作用域）
MATURITY_THRESHOLDS = [("starter", 0), ("growth", 500), ("formed", 1500), ("deep", 3000)]

# 头 / 尾 / 脚：社区口语，这里按"二号位速度=头、四号位=尾、六号位=脚"建模，
# 具体口径可在 config 的 workbench.standard 之外用 calibration 覆盖（见 head_tail 的 positions 参数）。
DEFAULT_POSITIONS = {"head": (2, "speed"), "tail": (4, None), "foot": (6, None)}

MAIN_ATTRS = ("attack_flat", "attack_rate", "defense_flat", "defense_rate",
              "hp_flat", "hp_rate", "speed", "crit_rate", "crit_damage",
              "effect_hit", "effect_resist")


def _load(name: str) -> Any:
    with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def maturity_of(soul_count: int) -> str:
    level = "starter"
    for name, threshold in MATURITY_THRESHOLDS:
        if soul_count >= threshold:
            level = name
    return level


# ---------------------------------------------------------------- 目录类
def roster(owned: List[Dict[str, Any]], catalog: Optional[Catalog] = None) -> Dict[str, Any]:
    """式神录：拥有列表 + 图鉴总数 + 稀有度分布。"""
    from tasks.Workbench.ingest import norm_skills

    catalog = catalog or Catalog()
    rows = []
    for item in owned:
        key = item.get("id") or item.get("name")
        ref = catalog.shikigami_of(str(key)) if key is not None else None
        raw_skills = item.get("skills")
        if raw_skills is None:
            raw_skills = item.get("skills_json") if item.get("skills_json") is not None else item.get("skill_levels")
        skills = norm_skills(raw_skills)
        rows.append({
            "id": (ref or {}).get("shikigami_id", key),
            "name": (ref or {}).get("name") or item.get("name") or str(key or ""),
            "rarity": (ref or {}).get("rarity", ""),
            "star": item.get("star"),
            "level": item.get("level"),
            "awakened": bool(item.get("awakened")),
            "skin": item.get("skin"),
            "skills": skills,
            "skill_levels": [s["level"] for s in skills],
        })
    dist: Dict[str, int] = {}
    for r in rows:
        dist[r["rarity"] or "?"] = dist.get(r["rarity"] or "?", 0) + 1
    return {"owned": len(rows), "catalog_total": len(catalog.shikigami),
            "owned_types": len({r["id"] for r in rows if r["rarity"]}),
            "by_rarity": dist, "rows": rows}


def shard_query(keyword: str, owned: Optional[List[Dict[str, Any]]] = None,
                catalog: Optional[Catalog] = None,
                counts: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """碎片查询：按关键词在目录里找式神，并标出是否已拥有（= 还缺多少图鉴）。"""
    catalog = catalog or Catalog()
    owned_keys = set()
    for item in owned or []:
        for k in (item.get("id"), item.get("name")):
            if k is not None:
                owned_keys.add(str(k))
    kw = (keyword or "").strip()
    hits = []
    for row in catalog.shikigami:
        name = row.get("name") or ""
        if kw and kw not in name and kw not in str(row.get("shikigami_id")):
            continue
        hits.append({"id": row.get("shikigami_id"), "name": name,
                     "rarity": row.get("rarity"),
                     "shards": (counts or {}).get(str(row.get("shikigami_id"))),
                     "owned": str(row.get("shikigami_id")) in owned_keys or name in owned_keys})
        if len(hits) >= 50:
            break
    return {"keyword": kw, "hits": len(hits), "rows": hits,
            "missing": sum(1 for h in hits if not h["owned"])}


def shard_matrix(target: str, profiles: Dict[str, List[Dict[str, Any]]],
                 catalog: Optional[Catalog] = None,
                 counts_by_profile: Optional[Dict[str, Dict[str, int]]] = None) -> Dict[str, Any]:
    """跨角色碎片/拥有矩阵。

    profiles: {档案名: [owned 项]}；target: 式神名或 id。
    返回每个档案是否拥有该式神，以及"未拥有档案数"（= 需要碎片的角色数）。
    """
    catalog = catalog or Catalog()
    ref = catalog.shikigami_of(target.strip())
    if ref is None:
        matches = shard_query(target, catalog=catalog)["rows"] if target.strip() else []
        if len(matches) != 1:
            return {"target": target, "rows": [], "profiles": 0,
                    "reason": "请选定具体式神"}
        ref = catalog.shikigami_of(str(matches[0]["id"]))
    name = ref.get("name") or str(target)
    rows = []
    for pname, owned in (profiles or {}).items():
        keys = set()
        for item in owned or []:
            for k in (item.get("id"), item.get("name")):
                if k is not None:
                    keys.add(str(k))
        rows.append({"profile": pname,
                     "shards": (counts_by_profile or {}).get(pname, {}).get(str(ref.get("shikigami_id"))),
                     "owned": name in keys or str(target) in keys or
                     str(ref.get("shikigami_id")) in keys})
    owned_n = sum(1 for r in rows if r["owned"])
    return {"target": name, "id": ref.get("shikigami_id"),
            "rarity": ref.get("rarity"), "profiles": len(rows),
            "owned_profiles": owned_n, "missing_profiles": len(rows) - owned_n,
            "rows": rows}


def asset_summary(souls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """游戏资产：御魂总量 / 按号位 / 按套装 / 按星级 / 练度档。"""
    by_slot: Dict[int, int] = {}
    by_set: Dict[str, int] = {}
    by_quality: Dict[int, int] = {}
    for raw in souls:
        s = raw if "set_id" in raw else normalize_soul(raw)
        by_slot[s["slot"]] = by_slot.get(s["slot"], 0) + 1
        by_set[s["set_id"]] = by_set.get(s["set_id"], 0) + 1
        by_quality[s["quality"]] = by_quality.get(s["quality"], 0) + 1
    total = len(souls)
    return {"total": total, "by_slot": by_slot, "by_set": by_set,
            "by_quality": by_quality, "maturity": maturity_of(total)}


# ---------------------------------------------------------------- 计算类
def panel_calc(shikigami: Dict[str, Any],
               souls: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """面板计算：基础面板 + 御魂主/副属性汇总。

    shikigami 可以是 catalog_panel.json 的一条，也可以是 {panel:{...}} 结构。
    御魂加成按阴阳师口径：flat 直接相加，rate 作用于基础值，暴击/暴伤/命抗按百分点累加。
    """
    from tasks.Workbench.analyze.panel_opt import combine

    panel = shikigami.get("panel") or shikigami
    base = {
        "attack": float(panel.get("attack") or 0),
        "hp": float(panel.get("hp") or 0),
        "defense": float(panel.get("defense") or 0),
        "speed": float(panel.get("speed") or 0),
        "crit_rate": float(panel.get("critRate") or panel.get("crit_rate") or 0),
        "crit_damage": float(panel.get("critDamage") or panel.get("crit_damage") or 0),
        "effect_hit": float(panel.get("effectHit") or panel.get("effect_hit") or 0),
        "effect_resist": float(panel.get("effectResist") or panel.get("effect_resist") or 0),
    }
    equipment = [_panel_soul(raw) for raw in souls or []]
    final = combine(base, equipment)
    return {"base": {k: round(v, 4) for k, v in base.items()}, "final": final,
            "souls_used": len(equipment),
            "delta": {k: round(final[k] - base[k], 4) for k in final}}


def _panel_soul(raw: Dict[str, Any]) -> Dict[str, Any]:
    s = normalize_soul(raw)
    return {**s, "set": s["set_id"], "main": s["main_attribute"],
            "main_value": _main_value(s)}


def _main_value(soul: Dict[str, Any]) -> float:
    """主属性数值：用户没给 probs（强化档位）时按 6 星满级常见值兜底。"""
    v = soul.get("main_value")
    if v is not None:
        return float(v)
    return {"speed": 57.0, "crit_rate": 55.0, "crit_damage": 89.0,
            "attack_rate": 55.0, "hp_rate": 55.0, "defense_rate": 55.0,
            "effect_hit": 55.0, "effect_resist": 55.0,
            "attack_flat": 486.0, "hp_flat": 2052.0, "defense_flat": 104.0}.get(
        soul["main_attribute"], 0.0)


def speed_preview(shikigami: Dict[str, Any], souls: Iterable[Dict[str, Any]],
                  team: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """模拟玩法：速度预览（单卡 + 队伍排序）。"""
    me = panel_calc(shikigami, souls)
    rows = [{"name": shikigami.get("name") or "本卡", "speed": me["final"]["speed"]}]
    for mate in team or []:
        rows.append({"name": mate.get("name"), "speed": mate.get("speed", 0)})
    rows.sort(key=lambda r: -r["speed"])
    return {"self": me["final"], "order": rows}


def head_tail(souls: List[Dict[str, Any]], engine: Optional[RuleEngine] = None,
              positions: Optional[Dict[str, Any]] = None,
              top_n: int = 5) -> Dict[str, Any]:
    """头/尾/脚排名：按号位（+主属性）挑候选，用评分引擎排序。

    默认口径：head=2 号位速度主属性，tail=4 号位，foot=6 号位。positions 可覆盖。
    """
    engine = engine or RuleEngine()
    positions = positions or DEFAULT_POSITIONS
    out: Dict[str, Any] = {}
    for pos_name, (slot, main_attr) in positions.items():
        pool = []
        for raw in souls:
            s = raw if "set_id" in raw else normalize_soul(raw)
            if s["slot"] != slot:
                continue
            if main_attr and s["main_attribute"] != main_attr:
                continue
            scored = engine.score(s)
            pool.append({"set_id": s["set_id"], "slot": s["slot"],
                         "main": s["main_attribute"], "level": s["level"],
                         "score": scored.get("score", 0.0),
                         "decision": scored.get("decision")})
        pool.sort(key=lambda r: -r["score"])
        out[pos_name] = {"slot": slot, "main": main_attr,
                         "candidates": len(pool), "top": pool[:top_n]}
    return out


def soul_report(souls: List[Dict[str, Any]], standard_id: Optional[str] = None,
                catalog: Optional[Catalog] = None,
                standards: Optional[Standards] = None, *,
                offset: int = 0, limit: int = 0,
                q: str = "", set_name: str = "",
                slot: Optional[int] = None) -> Dict[str, Any]:
    """御魂分析：逐条打分 + 决策分布 + 可执行批次建议。

    分页/过滤（纯增量，老字段与老语义不变）：
      * ``offset``/``limit`` 只裁 ``results`` 列表；``count``/``by_decision``/
        ``action_batch`` 仍按"参与打分的条数"统计（不传过滤条件时与旧版一致）。
      * ``limit=0`` 表示不分页（内部调用，如 /workbench/tasks/draft 依赖全量）。
      * 过滤：``q``（套装名或 soul_id 子串）、``set_name``（套装名精确）、
        ``slot``（1..6）。
      * 每行增量字段：``soul_id``（= 档案里的 internal_id/soul_key，可直接喂
        /workbench/souls/{id}）、``set_name``、``slot``、``quality``、``level``；
        ``idx`` 仍是该御魂在原库存数组里的下标（action_batch 也用它）。
    """
    # 组级回退（set_groups）实测会让一致率从 98.3% 掉到 95.6%，默认关闭；
    # 能力保留在 RuleEngine(set_groups=...) 里，需要时可显式打开做实验。
    engine = RuleEngine(catalog=catalog, standards=standards, standard_id=standard_id)
    keyword = str(q or "").strip().lower()
    set_filter = str(set_name or "").strip()
    slot_filter = int(slot) if slot not in (None, "", 0) else None
    results = []
    for idx, raw in enumerate(souls):
        soul = raw if "set_id" in raw else normalize_soul(raw)
        soul_id = str(raw.get("internal_id") or raw.get("soul_key") or "")
        if set_filter and soul["set_id"] != set_filter:
            continue
        if slot_filter is not None and soul["slot"] != slot_filter:
            continue
        if keyword and keyword not in str(soul["set_id"]).lower() \
                and keyword not in soul_id.lower():
            continue
        r = engine.score(soul)
        r["idx"] = idx
        r["soul_id"] = soul_id
        r["set_name"] = soul["set_id"]
        r["slot"] = soul["slot"]
        r["quality"] = soul["quality"]
        r["level"] = soul["level"]
        results.append(r)
    by_decision: Dict[str, int] = {}
    for r in results:
        by_decision[r["decision"]] = by_decision.get(r["decision"], 0) + 1
    recycle = [r["idx"] for r in results
               if r["decision"] == "discard" and not souls[r["idx"]].get("locked")]
    strengthen = [r["idx"] for r in results if r["decision"] in ("observe", "keep")]
    start = max(0, int(offset or 0))
    size = int(limit or 0)
    page = results[start:start + size] if size > 0 else results[start:]
    return {"standard": engine.standard.get("id"), "count": len(results),
            "by_decision": by_decision, "results": page,
            "action_batch": {"cleanup": recycle[:50], "strengthen": strengthen[:50]},
            "total": len(results), "offset": start, "limit": size,
            "returned": len(page)}


# ---------------------------------------------------------------- 胚子准入（严格路径）
def admission_report(souls: List[Dict[str, Any]], standard_id: Optional[str] = None,
                     catalog: Optional[Catalog] = None,
                     standards: Optional[Standards] = None) -> Dict[str, Any]:
    """严格准入：列出每件御魂命中了哪些规则（对应平安志 admission 路径）。"""
    engine = RuleEngine(catalog=catalog, standards=standards, standard_id=standard_id)
    engine.count_fixed = True
    rows = []
    for idx, raw in enumerate(souls):
        soul = raw if "set_id" in raw else normalize_soul(raw)
        hits = [r.id for r in engine.rules if r.matches_admission(soul)]
        rows.append({"idx": idx, "set": soul["set_id"], "slot": soul["slot"],
                     "matched": len(hits), "rules": hits[:5]})
    admitted = sum(1 for r in rows if r["matched"])
    return {"standard": engine.standard.get("id"), "total": len(rows),
            "admitted": admitted, "not_admitted": len(rows) - admitted, "rows": rows}


# ---------------------------------------------------------------- 面板优化
def panel_optimize(souls: List[Dict[str, Any]], base: Dict[str, float], metric: str,
                   slots_allowed: Optional[Dict[int, List[str]]] = None,
                   constraints: Optional[Dict[str, Any]] = None,
                   top_k: int = 10, beam: int = 300,
                   recipe: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """在库存里搜一套满足目标指标的 6 件配装，并给出提升方向。"""
    from tasks.Workbench.analyze import panel_opt as O
    cons = None
    if constraints:
        cons = {}
        for k, v in constraints.items():
            key = O.ALIAS.get(k, k)
            if key not in O.ATTR_KEYS or not isinstance(v, (list, tuple)) or len(v) != 2:
                raise ValueError("无效的面板约束")
            lo, hi = (None if x is None else float(x) for x in v)
            if any(x is not None and not math.isfinite(x) for x in (lo, hi)) or (
                    lo is not None and hi is not None and lo > hi):
                raise ValueError("面板约束的数值或范围无效")
            cons[key] = (lo, hi)
    allowed = {}
    for k, values in (slots_allowed or {}).items():
        slot = int(k)
        if slot not in range(1, 7) or not isinstance(values, (list, tuple)):
            raise ValueError("无效的号位约束")
        allowed[slot] = [normalize_attr(a) for a in values]
    base_panel = panel_calc(base)["base"]
    if not all(math.isfinite(value) for value in base_panel.values()):
        raise ValueError("基础面板数值无效")
    return O.optimize([{**_panel_soul(s), "idx": idx} for idx, s in enumerate(souls)],
                      base_panel, metric, slots_allowed=allowed,
                      constraints=cons, top_k=top_k, beam=beam, recipe=recipe)


# ---------------------------------------------------------------- 记录类
def task_summary(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_kind: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for t in tasks:
        by_kind[t.get("kind", "?")] = by_kind.get(t.get("kind", "?"), 0) + 1
        by_status[t.get("status", "?")] = by_status.get(t.get("status", "?"), 0) + 1
    return {"total": len(tasks), "by_kind": by_kind, "by_status": by_status,
            "recent": tasks[-10:]}


def monitor_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_kind: Dict[str, int] = {}
    for r in rows:
        by_kind[r.get("kind", "?")] = by_kind.get(r.get("kind", "?"), 0) + 1
    return {"points": len(rows), "by_kind": by_kind, "first": rows[0] if rows else None,
            "last": rows[-1] if rows else None}


def guild_summary(guild: Dict[str, Any]) -> Dict[str, Any]:
    members = guild.get("members") or []
    by_role: Dict[str, int] = {}
    for m in members:
        by_role[m.get("role", "成员")] = by_role.get(m.get("role", "成员"), 0) + 1
    return {"name": guild.get("name", ""), "members": len(members), "by_role": by_role}


# ---------------------------------------------------------------- 首页聚合
def overview(store, **kwargs) -> Dict[str, Any]:
    """工作台首页：一次拿全部卡片数据。"""
    inventory = store.inventory()
    souls = inventory["souls"]
    owned = inventory["owned"]
    catalog = kwargs.get("catalog") or Catalog()
    prof = store.current_profile()
    # 货币余额：档案 JSON 自带优先；没有就走平安志采集快照（sqlite 没有货币表）
    currencies = (inventory.get("extras") or {}).get("currencies") or {}
    currencies_source = "archive" if currencies else None
    currencies_at = inventory.get("captured_at") if currencies else None
    currencies_delta = None
    realm_cards = None
    account_resources = None
    try:
        from tasks.Workbench.ingest import paz_extra

        if not currencies:
            cur = paz_extra.currency()
            if cur.get("balances"):
                currencies = cur["balances"]
                currencies_source = cur.get("source")
                currencies_at = cur.get("captured_at")
        if currencies:
            # 每次读首页顺手记一条历史（按快照时点去重），攒够了日/周/月环比才有数
            snapshot = paz_extra.currency()
            paz_extra.record_currency_history(snapshot)
            currencies_delta = paz_extra.currency_delta(snapshot)
        realm_cards = paz_extra.realm_cards()
        account_resources = paz_extra.account_resources()
    except Exception:  # 取不到余额/结界/道具不该拖垮首页
        pass
    return {
        "profile": prof,
        "roster": {k: v for k, v in roster(owned, catalog).items() if k != "rows"},
        "assets": {**asset_summary(souls), "currencies": currencies or None,
                   "currencies_source": currencies_source,
                   "currencies_at": currencies_at,
                   "currencies_delta": currencies_delta,
                   "realm_cards": realm_cards,
                   "account_resources": account_resources},
        "recent_tasks": task_summary(store.tasks(limit=0)),
        "monitor": monitor_summary(store.monitor()),
        "guild": guild_summary(store.guild()),
        "activities": _activities_card(kwargs.get("activities_data")),
        "inventory_source": inventory.get("source"),
        "captured_at": inventory.get("captured_at"),
    }


def _activities_card(data):
    """首页只放计数，详情走 /workbench/activities。"""
    if not data:
        try:
            from tasks.Workbench.ingest import activities as A
            data = A.load()
        except Exception:
            return {"total": 0, "by_status": {}}
    rep = activity_report(data)
    return {"total": rep["total"], "by_status": rep["by_status"],
            "announcements": rep["announcements"]}


# ---------------------------------------------------------------- 单颗御魂详情
# 雷达只取"率类 + 速度"这 8 个轴：flat 属性（攻击/生命/防御）数值量级差 2 个数量级，
# 混在同一张雷达上会把率类全部压平。数值 = 该御魂这条属性的主属性 + 副属性 + 固有属性之和。
RADAR_AXES = ("attack_rate", "crit_rate", "crit_damage", "speed",
              "effect_hit", "effect_resist", "hp_rate", "defense_rate")


def soul_detail(facts: Dict[str, Any], catalog: Optional[Catalog] = None,
                standards: Optional[Standards] = None) -> Dict[str, Any]:
    """把 paz_extra.soul_detail 的原始事实拼成接口契约结构。

    评分优先用平安志自己的 analysis_todo（standard_score / matchedRuleName /
    contributions）；它没有对应待办时回退到本引擎算，并在 score.source 里标明来源。
    """
    soul = facts["soul"]
    todo = facts.get("todo") or {}
    engine = RuleEngine(catalog=catalog, standards=standards)
    scored = engine.score(facts["raw"])
    contributions = list(todo.get("contributions") or [])
    if not contributions:
        contributions = [{"label": ATTR_CN.get(attr, attr), "value": round(float(pts), 2)}
                         for attr, pts in (scored.get("breakdown") or {}).items()]
    value = todo.get("score")
    if value is None:
        value = scored.get("score", 0.0)
    value = round(float(value or 0.0), 2)
    score = {"value": value,
             "rule": todo.get("rule") or scored.get("rule_name") or "",
             "contributions": contributions,
             "formula": todo.get("formula") or "",
             "source": "analysis_todo" if todo else "engine"}
    attrs = []
    main = facts.get("main") or {}
    if main:
        attrs.append({"name": main.get("name"), "value": main.get("value"),
                      "is_fixed": False, "rolls": 0, "kind": "main"})
    for a in facts.get("attributes") or []:
        attrs.append({"name": a.get("name"), "value": a.get("value"),
                      "is_fixed": bool(a.get("is_fixed")),
                      "rolls": int(a.get("rolls") or 0),
                      "kind": "fixed" if a.get("is_fixed") else "sub"})
    return {
        "soul": {"internal_id": soul.get("internal_id"), "soul_key": soul.get("soul_key"),
                 "set_name": soul.get("set_name"), "slot": soul.get("slot"),
                 "quality": soul.get("quality"), "level": soul.get("level"),
                 "score": value, "main_attribute": soul.get("main_attribute"),
                 "main_value": soul.get("main_value"), "equipped": soul.get("equipped"),
                 "locked": bool(soul.get("locked")), "recommendation": todo.get("recommendation"),
                 "category": todo.get("category")},
        "attrs": attrs, "score": score, "radar": _radar(facts),
    }


def _radar(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    totals: Dict[str, float] = {}
    main = facts.get("main") or {}
    if main.get("attribute_type"):
        totals[main["attribute_type"]] = float(main.get("value") or 0.0)
    for a in facts.get("attributes") or []:
        key = a.get("attribute_type")
        if key:
            totals[key] = totals.get(key, 0.0) + float(a.get("value") or 0.0)
    return [{"axis": ATTR_CN.get(axis, axis), "value": round(totals.get(axis, 0.0), 3)}
            for axis in RADAR_AXES]


# ---------------------------------------------------------------- 式神详情
def roster_detail(data: Dict[str, Any]) -> Dict[str, Any]:
    """paz_extra.shikigami_detail → roster/{name} 契约结构。"""
    sh = data["shikigami"]
    skills = sh.get("skills") or []
    return {"name": sh.get("name"), "rarity": sh.get("rarity"),
            "level": sh.get("level"), "star": sh.get("star"),
            "skills": skills,
            "skill_levels": [int(s.get("level") or 0) for s in skills if isinstance(s, dict)],
            "souls": sh.get("souls") or [],
            "owned": bool(sh.get("owned")), "id": sh.get("id"),
            "awakened": bool(sh.get("awakened")), "skin": sh.get("skin"),
            "icon": sh.get("icon"), "panel": sh.get("panel") or {},
            "instances": sh.get("instances")}


# ---------------------------------------------------------------- 标准版本（本地兜底）
def standards_catalog() -> List[Dict[str, Any]]:
    """平安志库不可用时，用本模块自带的评分标准包列出可激活项。"""
    return [{"id": s["id"], "name": s.get("title") or s["id"],
             "rule_count": len(s.get("rules") or []), "updated": "",
             "version": s.get("version") or "", "preset": s["id"], "status": "local"}
            for s in Standards().standards]
