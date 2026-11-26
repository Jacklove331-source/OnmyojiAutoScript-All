# This file uses the following encoding: utf-8
"""平安志快照解析回归。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tasks.Workbench.ingest import paz_snapshot as P

PASS = 0
FAIL = 0
REAL = r"D:\3D\PAZ\_reverse\snap_result.json"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def synthetic():
    raw = [None] * 16
    raw[0] = [105585081, 10037, "测试", 60, 0, {"name": "测试", "level": 60, "headIcon": 1}]
    raw[2] = [["u1", 224, [], 40, 0, "", 1747789705, True, 2, [[2241, 5]], 1, 6]]
    raw[3] = [
        ["s1", 300004, 6, 0, 150006, 15, 1743911441, False, False,
         [[1, 4.5796, 0], [4, 0.0296, 1], [5, 0.0264, 0], [7, 0.0243, 2]], [2, 486.0], [], [], []],
        ["s2", 300036, 6, 5, 150006, 15, 1743911441, True, False,
         [[8, 0.89, 0], [6, 2.78, 1]], [8, 0.89], [], [], []],
    ]
    raw[12] = {"schemaVersion": 1, "daily": {}, "weekly": {}}
    return raw


def main() -> int:
    # 合成样本
    tmp = tempfile.mkdtemp(prefix="paz_")
    p = os.path.join(tmp, "snap.json")
    json.dump(synthetic(), open(p, "w", encoding="utf-8"), ensure_ascii=False)
    d = P.load_file(p)
    check("synthetic.player", d["player"]["short_id"] == 105585081, str(d["player"]))
    check("synthetic.souls", len(d["souls"]) == 2, str(len(d["souls"])))
    check("synthetic.slot_1based", [s["slot"] for s in d["souls"]] == [1, 6], str([s["slot"] for s in d["souls"]]))
    check("synthetic.level", d["souls"][0]["level"] == 15, str(d["souls"][0]["level"]))
    check("synthetic.main_flat", d["souls"][0]["main"] == "attack_flat", d["souls"][0]["main"])
    check("synthetic.main_crit_dmg", d["souls"][1]["main"] == "crit_damage", d["souls"][1]["main"])
    check("synthetic.substats", "crit_rate" in d["souls"][0]["substats"], str(d["souls"][0]["substats"]))
    check("synthetic.owned", len(d["owned"]) == 1, str(d["owned"]))
    check("synthetic.owned_star", d["owned"][0]["star"] == 6, str(d["owned"][0]))
    check("synthetic.owned_skills",
          d["owned"][0].get("skills") == [{"id": "2241", "level": 5}],
          str(d["owned"][0]))
    check("synthetic.weekly", isinstance(d["weekly"], dict))
    v = P.validate(d["souls"])
    check("synthetic.validate", v["ok"] is True, str(v))

    # 货币余额：只在采集快照 JSON 里，sqlite 42 张表没有货币表
    from tasks.Workbench.ingest import paz_extra
    snap_dir = tempfile.mkdtemp(prefix="paz_cur_")
    snap = os.path.join(snap_dir, "current-data-test.json")
    json.dump({"capturedAt": "2026-01-02T03:04:05Z",
               "sourcePayload": {"currency": {"jade": 123, "coin": 456789}},
               "normalizedItems": {"jade": 123, "coin": 456789}},
              open(snap, "w", encoding="utf-8"), ensure_ascii=False)
    old_dir = os.environ.get("WORKBENCH_PAZ_DATA")
    os.environ["WORKBENCH_PAZ_DATA"] = snap_dir
    try:
        cur = paz_extra.currency()
        check("currency.ok", cur.get("ok") is True, str(cur.get("reason")))
        check("currency.jade", (cur.get("balances") or {}).get("jade") == 123, str(cur.get("balances")))
        check("currency.captured_at", cur.get("captured_at") == "2026-01-02T03:04:05Z",
              str(cur.get("captured_at")))
        empty = tempfile.mkdtemp(prefix="paz_cur_empty_")
        os.environ["WORKBENCH_PAZ_DATA"] = empty
        cur2 = paz_extra.currency()
        check("currency.degrade", cur2.get("ok") is False and bool(cur2.get("reason")), str(cur2))
        # 余额历史：按快照时点去重，攒到第 2 个点才出环比
        os.environ["WORKBENCH_PAZ_DATA"] = snap_dir
        store_dir = tempfile.mkdtemp(prefix="paz_hist_")
        old_store = os.environ.get("WORKBENCH_STORE")
        os.environ["WORKBENCH_STORE"] = store_dir
        try:
            c1 = paz_extra.currency()
            n1 = paz_extra.record_currency_history(c1)
            n2 = paz_extra.record_currency_history(c1)
            check("history.dedupe", n1 == n2 == 1, "%s/%s" % (n1, n2))
            d0 = paz_extra.currency_delta(c1)
            check("history.no_delta_with_one_point", d0.get("prev") is None and d0.get("points") == 1, str(d0))
            snap2 = dict(c1)
            snap2["captured_at"] = "2026-01-03T03:04:05Z"
            snap2["balances"] = {"jade": 200, "coin": 400000}
            paz_extra.record_currency_history(snap2)
            d1 = paz_extra.currency_delta(snap2)
            check("history.prev_delta", (d1.get("prev") or {}).get("jade") == 77, str(d1.get("prev")))
            check("history.day_delta", (d1.get("day") or {}).get("coin") == -56789, str(d1.get("day")))
        finally:
            if old_store is None:
                os.environ.pop("WORKBENCH_STORE", None)
            else:
                os.environ["WORKBENCH_STORE"] = old_store
    finally:
        if old_dir is None:
            os.environ.pop("WORKBENCH_PAZ_DATA", None)
        else:
            os.environ["WORKBENCH_PAZ_DATA"] = old_dir

    # 离线 sqlite 回归：验证 paz_db.load() 读取 owned_shikigami.skills_json
    import sqlite3 as _sqlite3
    from tasks.Workbench.ingest import paz_db
    mock_db = os.path.join(tmp, "mock_paz.sqlite3")
    with _sqlite3.connect(mock_db) as mcon:
        mcon.executescript("""
            create table game_profile(id text, display_name text, server_label text, archived_at text, updated_at text);
            create table profile_current_snapshot(profile_id text, snapshot_id text, selected_at text);
            create table snapshot(id text, profile_id text, captured_at text);
            create table snapshot_soul(snapshot_id text, internal_id text, set_id text, slot int, quality int, level int,
                                       main_attr_type text, main_attr_value real, locked_in_source int, initial_substat_count int);
            create table soul_attribute(snapshot_id text, soul_internal_id text, attribute_index int,
                                        attribute_type text, value real, enhancement_count int, fixed_attribute int);
            create table owned_shikigami(profile_id text, instance_id text, shikigami_id text, star int, level int,
                                         awakened int, skin_id text, skills_json text);
            insert into game_profile values ('p1', '测试号', '官服', null, '2026-11-25');
            insert into profile_current_snapshot values ('p1', 's1', '2026-11-25');
            insert into snapshot values ('s1', 'p1', '2026-11-25T00:00:00Z');
            insert into owned_shikigami values ('p1', 'inst1', '200', 6, 40, 1, null,
                '[{"skillId":2001,"level":5},{"skillId":2002,"level":5},{"skillId":2003,"level":5}]');
        """)
    mock_res = paz_db.load(path=mock_db)
    check("mock_db.ok", mock_res.get("ok") is True and len(mock_res["owned"]) == 1, str(mock_res))
    check("mock_db.owned_skills",
          mock_res["owned"][0].get("skills") == [
              {"id": "2001", "level": 5}, {"id": "2002", "level": 5}, {"id": "2003", "level": 5}
          ], str(mock_res["owned"][0]))

    # 平安志 sqlite（存在才跑）：权威口径，含固有属性
    db = paz_db.load()
    if db.get("ok") and db["souls"]:
        check("db.souls_gt_1000", len(db["souls"]) > 1000, str(len(db["souls"])))
        check("db.fixed_attrs", any(s["fixed_attrs"] for s in db["souls"]),
              str(sum(1 for s in db["souls"] if s["fixed_attrs"])))
        dv = P.validate(db["souls"])
        check("db.slot_rule_no_violation", dv["violations"] == 0, str(dv["samples"]))
        from tasks.Workbench.engine import RuleEngine, Catalog, Standards
        eng = RuleEngine(catalog=Catalog(), standards=Standards(),
                         standard_id="builtin.owner.default-manual")
        scored = [eng.score(s) for s in db["souls"][:200]]
        check("db.scoring_matched", sum(1 for r in scored if r["matched"]) > 100,
              str(sum(1 for r in scored if r["matched"])))
        # 评分等价性：平安志自身落盘的 standardScore（按 soul_internal_id 直连，无需配对）
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % paz_db.DEFAULT_DB.replace("\\", "/"), uri=True)
        oracle = {}
        for iid, detail in con.execute(
                "select soul_internal_id, detail_json from analysis_todo"):
            st = (json.loads(detail) or {}).get("standardScore") or {}
            oracle[iid] = (float(st.get("score") or 0), st.get("matchedRuleId"))
        con.close()
        bad_score = bad_label = 0
        for s in db["souls"]:
            exp = oracle.get(s["internal_id"])
            if exp is None:
                continue
            got = eng.score(s)
            if abs(exp[0] - got["score"]) > 0.01:
                bad_score += 1
            exp_label = None if exp[1] in (None, "unmatched") else exp[1]
            if exp_label != got.get("rule_id"):
                bad_label += 1
        check("db.score_parity", bad_score == 0, "%d mismatches" % bad_score)
        check("db.rule_parity", bad_label == 0, "%d mismatches" % bad_label)
        print("INFO  db parity: oracle=%d score_bad=%d label_bad=%d" %
              (len(oracle), bad_score, bad_label))
        print("INFO  db: souls=%d owned=%d fixed=%d" %
              (len(db["souls"]), len(db["owned"]),
               sum(1 for s in db["souls"] if s["fixed_attrs"])))
    else:
        print("INFO  平安志 sqlite 不可用，跳过 db 用例")

    # 真实快照（存在才跑）
    if os.path.isfile(REAL):
        r = P.load_file(REAL)
        check("real.souls_gt_1000", len(r["souls"]) > 1000, str(len(r["souls"])))
        check("real.owned_gt_1000", len(r["owned"]) > 1000, str(len(r["owned"])))
        check("real.owned_skills", any(o.get("skills") for o in r["owned"]), str(r["owned"][:2]))
        check("real.owned_star", any(isinstance(o.get("star"), int) and 1 <= o["star"] <= 6 for o in r["owned"]),
              str(r["owned"][:2]))
        rv = P.validate(r["souls"])
        check("real.slot_rule_no_violation", rv["violations"] == 0, str(rv["samples"]))
        check("real.levels_in_range",
              all(0 <= s["level"] <= 15 for s in r["souls"]),
              "存在越界等级")
        print("INFO  real: player=%s souls=%d owned=%d" %
              (r["player"].get("name"), len(r["souls"]), len(r["owned"])))
    else:
        print("INFO  real snapshot not found, skipped")

    print("\nWORKBENCH PAZ SELFTEST: %s   TOTAL %d PASS %d FAIL %d" %
          ("PASSED" if FAIL == 0 else "FAILED", PASS + FAIL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
