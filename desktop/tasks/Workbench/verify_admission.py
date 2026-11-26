# -*- coding: utf-8 -*-
"""准入路径验收：用平安志自己的 admission.rules[].matched 当答案，逐条比对。"""
import io
import json
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tasks.Workbench.engine import RuleEngine, Catalog, Standards, normalize_soul

DB = os.path.join(os.environ["APPDATA"], "com.yys.analysis", "current-analysis.sqlite3")
RATE = {"attack_rate", "defense_rate", "hp_rate", "crit_rate", "crit_damage",
        "effect_hit", "effect_resist"}


def soul_from_detail(d: dict, main_attr=None, main_val=None) -> dict:
    facts = d.get("facts") or {}
    attrs = facts.get("attributes") or []
    mt, mv = None, float(main_val or 0.0)
    subs, rolls, fixed = {}, {}, []
    for a in attrs:
        t = a.get("attributeType")
        v = float(a.get("value") or a.get("effectiveValue") or 0.0)
        # facts.attributes 里主属性往往不带 isMain 标记，用 main_attribute 兜底排除
        if a.get("isMain") or a.get("main") or (main_attr and t == main_attr and abs(v - mv) < 1e-6):
            mt, mv = t, v
            continue
        # enhancementCount 缺失 = 固有属性（不计入腿数）；fixedAttribute 标记同样处理
        if a.get("enhancementCount") is None or int(a.get("fixedAttribute") or a.get("isFixed") or 0) == 1:
            fixed.append(t)
            continue
        if t in subs:
            continue
        subs[t] = round(v * 100, 3) if t in RATE else round(v, 3)
        rolls[t] = int(a.get("enhancementCount") or 0)
    if mt is None:
        mt = main_attr or facts.get("mainAttribute") or ""
        mv = float(main_val or facts.get("mainValue") or 0.0)
    return normalize_soul({
        "set": facts.get("setId") or facts.get("set") or d.get("facts", {}).get("setId"),
        "slot": facts.get("slot") or 0,
        "level": facts.get("level", 15),
        "quality": facts.get("quality", 6),
        "main": mt, "main_value": mv,
        "substats": subs, "rolls": rolls, "fixed_attrs": fixed,
    })


def main() -> int:
    con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    rows = list(con.execute(
        "select set_id, slot, level, quality, main_attribute, main_value, detail_json "
        "from analysis_todo where detail_json is not null"))
    con.close()
    eng = RuleEngine(catalog=Catalog(), standards=Standards(),
                     standard_id="builtin.owner.default-manual")
    by_id = {r.id: r for r in eng.rules}

    agree = disagree = 0
    diffs = []
    for set_id, slot, level, quality, main_attr, main_val, dj in rows:
        d = json.loads(dj)
        facts = dict(d.get("facts") or {})
        facts.setdefault("setId", set_id)
        facts.setdefault("slot", slot)
        facts.setdefault("level", level)
        facts.setdefault("quality", quality)
        facts.setdefault("mainAttribute", main_attr)
        facts.setdefault("mainValue", main_val)
        soul = soul_from_detail({"facts": facts}, main_attr, main_val)
        for r in (((d.get("admission") or {}).get("rules")) or []):
            rule = by_id.get(r.get("ruleId"))
            if rule is None:
                continue
            app_matched = bool(r.get("matched"))
            ours = rule.matches_admission(soul)
            if ours == app_matched:
                agree += 1
            else:
                disagree += 1
                if len(diffs) < 20:
                    checks = [(c.get("label"), c.get("status"), (c.get("detail") or "")[:50])
                              for c in ((r.get("selector") or {}).get("checks") or [])]
                    diffs.append("%s %s位 %s app=%s ours=%s" % (
                        set_id, slot, r.get("ruleId"), app_matched, ours))
                    for c in checks:
                        diffs.append("      %-8s %-8s %s" % c)
    total = agree + disagree
    out = ["准入路径比对：%d 对规则-御魂组合" % total,
           "一致 %d (%.2f%%)，不一致 %d" % (agree, 100.0 * agree / max(total, 1), disagree), ""] + diffs
    io.open(os.path.join("log", "workbench", "admission_parity.txt"), "w", encoding="utf-8").write("\n".join(out))
    print(out[1])
    return 0 if disagree == 0 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
