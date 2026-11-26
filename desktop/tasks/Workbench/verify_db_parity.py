# This file uses the following encoding: utf-8
"""与平安志自身落盘的评分结论逐条对照（第三口径，比界面抓表更硬）。

为什么还需要这一份：
  * 界面抓表（verify_score_parity.py）要按「套装+号位+副属性」反查御魂，配对有歧义风险；
  * 平安志把每个御魂的评分结论直接写在 `analysis_todo.detail_json.standardScore`
    （matchedRuleId / matchedRuleName / score / contributions / formula），
    并用 `soul_internal_id` 一一对应，**不需要配对**。

本脚本按 `soul_internal_id` 直连比对，并交叉核对 `analysis_todo.standard_score` 列，
所以能排除「配对错误」与「两个分数列不同口径」两种质疑。

用法：.\\toolkit\\python.exe tasks/Workbench/verify_db_parity.py
输出：log/workbench/db_parity.txt
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tasks.Workbench.engine import RuleEngine, Catalog, Standards
from tasks.Workbench.ingest import paz_db

OUT = os.path.join("log", "workbench", "db_parity.txt")
DB = paz_db.DEFAULT_DB


def _rows(db_path: str):
    con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
    try:
        for (iid, col_score, detail) in con.execute(
                "select soul_internal_id, standard_score, detail_json from analysis_todo"):
            yield iid, col_score, json.loads(detail)
    finally:
        con.close()


def main() -> int:
    if not os.path.isfile(DB):
        print("SKIP: 找不到平安志数据库 %s" % DB)
        return 0

    souls = {s["internal_id"]: s for s in paz_db.load()["souls"]}
    eng = RuleEngine(catalog=Catalog(), standards=Standards(),
                     standard_id="builtin.owner.default-manual")

    seen = set()
    dup = unpaired = 0
    total = same_score = same_label = 0
    col_eq_detail = 0
    label_diffs = []
    for iid, col_score, detail in _rows(DB):
        if iid in seen:
            dup += 1
            continue
        seen.add(iid)
        total += 1
        soul = souls.get(iid)
        if soul is None:
            unpaired += 1
            continue
        st = detail.get("standardScore") or {}
        app_score = float(st.get("score") or 0)
        app_rule = st.get("matchedRuleId")
        col = float(col_score or 0)
        if abs(col - app_score) <= 0.01:
            col_eq_detail += 1
        ours = eng.score(soul)
        if abs(app_score - ours["score"]) <= 0.01:
            same_score += 1
        if app_score == 0.0 and app_rule == "unmatched" and ours.get("rule_id") is None:
            same_label += 1      # 双方都判定「无命中」：界面显示占位符，本引擎返回 None
        elif app_rule == ours.get("rule_id"):
            same_label += 1
        else:
            label_diffs.append((iid, app_rule, ours.get("rule_id"), app_score, ours["score"]))

    out = [
        "与平安志自身落盘结论逐条对照（按 soul_internal_id 直连，无需配对）",
        "总行数 %d，唯一 %d，重复 %d，未配对 %d" % (total, len(seen), dup, unpaired),
        "两个分数列一致（standard_score == detail.standardScore.score）: %d/%d" % (col_eq_detail, total),
        "分数一致 %d/%d = %.2f%%" % (same_score, total, 100.0 * same_score / max(total, 1)),
        "规则/判定一致 %d/%d = %.2f%%（含「双方均未命中」归一为一致）" % (
            same_label, total, 100.0 * same_label / max(total, 1)),
        "",
        "标签差异 %d 条：" % len(label_diffs),
    ]
    for x in label_diffs[:30]:
        out.append("  %s 平安志=%s(%s) 本引擎=%s(%s)" % (x[0][:8], x[1], x[3], x[2], x[4]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write("\n".join(out))
    print("\n".join(out[:5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
