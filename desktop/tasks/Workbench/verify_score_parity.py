# This file uses the following encoding: utf-8
"""严格配对的全量评分对照（修正版）。

配对键 = 套装 + 号位 + 副属性名集合 + 每条手数 + 每条数值（容差 max(0.02, 1%)）。

**为什么不能只用手数配对**：手数全 0 的御魂有几百颗，只比 rolls 会拿错御魂，
凭空造出"规则/分数不一致"。

注意单位：界面显示 `暴击 7.80%`，DB `soul_attribute.value` 存的就是 `7.805`，
**同尺度，不要除以 100**。

用法：.\\toolkit\\python.exe tasks/Workbench/verify_score_parity.py
输出：log/workbench/score_parity_strict.txt
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tasks.Workbench.engine import RuleEngine, Catalog, Standards

SCRAPE = os.environ.get("WB_SCRAPE", r"D:\3D\PAZ\scores_all.json")
OUT = os.path.join("log", "workbench", "score_parity_strict.txt")

CN = {"攻击": "attack_flat", "攻击加成": "attack_rate", "防御": "defense_flat",
      "防御加成": "defense_rate", "生命": "hp_flat", "生命加成": "hp_rate",
      "速度": "speed", "暴击": "crit_rate", "暴伤": "crit_damage",
      "效果命中": "effect_hit", "效果抵抗": "effect_resist"}
A = "攻击加成|防御加成|生命加成|攻击|防御|生命|速度|暴击|暴伤|效果命中|效果抵抗"
SUB_RE = re.compile(r"(%s)\s+([\d.]+)%%?\s+\+(\d+)" % A)


def parse_row(c):
    """→ (set, slot, {attr: (value, rolls)}, score, rule_name) 或 None"""
    if len(c) < 7:
        return None
    m = re.match(r"^(\S+?)\s.*?(\d)号位", c[1])
    sm = re.match(r"([\d.]+)\s+(\S+)", c[6])
    if not (m and sm):
        return None
    subs = {}
    for name, val, rolls in SUB_RE.findall(c[5]):
        subs[CN[name]] = (float(val), int(rolls))
    return m.group(1), int(m.group(2)), subs, float(sm.group(1)), sm.group(2)


def close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.02, 0.01 * max(abs(a), abs(b)))


def key_of_subs(subs) -> tuple:
    return tuple(sorted(subs))


def main() -> int:
    from tasks.Workbench.ingest import paz_db

    rows = json.load(io.open(SCRAPE, encoding="utf-8"))["rows"]
    souls = paz_db.load()["souls"]
    eng = RuleEngine(catalog=Catalog(), standards=Standards(),
                     standard_id="builtin.owner.default-manual")

    idx = {}
    for s in souls:
        idx.setdefault((s["set"], s["slot"]), []).append(s)

    paired = multi = unpaired = 0
    same_score = same_rule = 0
    label_diffs = []

    for c in rows:
        r = parse_row(c)
        if not r:
            continue
        set_id, slot, subs, score, rule_name = r
        cands = [s for s in idx.get((set_id, slot), [])
                 if key_of_subs(s["substats"]) == key_of_subs(subs)
                 and all(int(s["rolls"].get(a, 0)) == subs[a][1] for a in subs)
                 and all(close(float(s["substats"][a]), subs[a][0]) for a in subs)]
        if not cands:
            unpaired += 1
            continue
        if len(cands) > 1:
            multi += 1
        paired += 1
        ours = eng.score(cands[0])
        if abs(score - ours["score"]) < 0.01:
            same_score += 1
        if rule_name == (ours.get("rule_name") or "None"):
            same_rule += 1
        elif abs(score - ours["score"]) < 0.01:
            label_diffs.append((set_id, slot, rule_name, ours.get("rule_name"), score))

    out = ["严格配对对照（套装+号位+副属性名集合+每条手数+每条数值±max(0.02,1%)）",
           "界面行数 %d，唯一配对 %d，多候选 %d，未配对 %d" % (len(rows), paired, multi, unpaired),
           "分数一致 %d/%d = %.2f%%" % (same_score, paired, 100.0 * same_score / max(paired, 1)),
           "规则名一致 %d/%d = %.2f%%" % (same_rule, paired, 100.0 * same_rule / max(paired, 1)),
           "",
           "等分但标签不同 %d 条（不影响分数）：" % len(label_diffs),
           "  其中 双方均未命中（界面占位符「未匹配评分规则卡片」 vs 本引擎 None）: %d" % sum(
               1 for d in label_diffs if d[2] == "未匹配评分规则卡片" and d[3] is None),
           "  其中 双方都有规则名但标签不同: %d" % sum(
               1 for d in label_diffs if d[2] != "未匹配评分规则卡片" and d[3] is not None),
           "  其中 界面未命中但本引擎有规则（或反之）: %d" % sum(
               1 for d in label_diffs if (d[2] == "未匹配评分规则卡片") != (d[3] is None))]
    for x in label_diffs[:30]:
        out.append("  %s %s位 界面=%s 本引擎=%s 分数=%s" % x)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write("\n".join(out))
    print("\n".join(out[:4]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
