# This file uses the following encoding: utf-8
"""Workbench 功能层回归：覆盖 12 项移植功能（藏宝阁除外）。

用法（OAS 仓库根目录）：
    .\\toolkit\\python.exe tasks\\Workbench\\selftest_features.py
退出码 0 = 全绿。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tasks.Workbench import features as F  # noqa: E402
from tasks.Workbench.engine import Catalog, RuleEngine  # noqa: E402
from tasks.Workbench.store import Store  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


SOULS = [
    {"set": "破势", "slot": 6, "main": "暴击", "level": 0, "quality": 6,
     "initial_substat_count": 4,
     "substats": {"暴击伤害": 22.0, "攻击加成": 15.0, "速度": 8.0, "生命加成": 6.0}},
    {"set": "破势", "slot": 2, "main": "速度", "level": 0, "quality": 6,
     "initial_substat_count": 4,
     "substats": {"暴击": 9.0, "攻击加成": 12.0, "速度": 6.0}},
    {"set": "针女", "slot": 4, "main": "攻击加成", "level": 15, "quality": 6,
     "initial_substat_count": 3,
     "substats": {"暴击": 5.0, "生命加成": 4.0}},
    {"set": "心眼", "slot": 3, "main": "防御", "level": 0, "quality": 5,
     "initial_substat_count": 2, "substats": {"防御加成": 3.0}},
]

OWNED = [
    {"id": "609", "star": 6, "level": 40, "awakened": True,
     "skills": [{"id": "6091", "level": 5}, {"id": "6092", "level": 5}, {"id": "6093", "level": 5}]},
    {"id": "605", "star": 5, "level": 35, "awakened": True,
     "skills": [{"id": "6051", "level": 1}, {"id": "6052", "level": 4}, {"id": "6053", "level": 5}]},
]


def main() -> int:
    cat = Catalog()

    # 1) 式神录
    r = F.roster(OWNED, cat)
    check("roster.owned", r["owned"] == 2, str(r["owned"]))
    check("roster.catalog_total", r["catalog_total"] >= 250, str(r["catalog_total"]))
    check("roster.rows_have_name", all(x["name"] for x in r["rows"]))
    check("roster.rows_have_skills",
          r["rows"][0]["skill_levels"] == [5, 5, 5] and r["rows"][1]["skill_levels"] == [1, 4, 5],
          str(r["rows"]))

    # 2) 碎片查询
    sh = F.shard_query("", OWNED, cat)
    check("shard.all", sh["hits"] > 0, str(sh["hits"]))
    check("shard.owned_flag", any(x["owned"] for x in sh["rows"]), "无已拥有标记")
    sh2 = F.shard_query("大蛇", OWNED, cat)
    check("shard.keyword_subset", sh2["hits"] <= sh["hits"], "%s vs %s" % (sh2["hits"], sh["hits"]))

    # 3) 游戏资产
    a = F.asset_summary(SOULS)
    check("assets.total", a["total"] == 4, str(a["total"]))
    check("assets.by_slot", set(a["by_slot"]) == {2, 3, 4, 6}, str(a["by_slot"]))
    check("assets.maturity", a["maturity"] == "starter", a["maturity"])

    # 4) 练度档
    check("maturity.growth", F.maturity_of(600) == "growth")
    check("maturity.formed", F.maturity_of(1600) == "formed")
    check("maturity.deep", F.maturity_of(9999) == "deep")

    # 5) 面板计算
    pref = cat.panel_of("609")
    check("panel.catalog_row", pref is not None)
    pc = F.panel_calc(pref, SOULS[:3])
    check("panel.souls_used", pc["souls_used"] == 3, str(pc["souls_used"]))
    check("panel.attack_up", pc["final"]["attack"] > pc["base"]["attack"],
          "%s -> %s" % (pc["base"]["attack"], pc["final"]["attack"]))
    check("panel.speed_up", pc["final"]["speed"] > pc["base"]["speed"],
          "%s -> %s" % (pc["base"]["speed"], pc["final"]["speed"]))
    check("panel.crit_ge_base", pc["final"]["crit_rate"] >= pc["base"]["crit_rate"])

    # 6) 模拟玩法：速度排序
    sp = F.speed_preview(pref, SOULS[:2],
                         team=[{"name": "队友A", "speed": 210}, {"name": "队友B", "speed": 128}])
    check("speed.order_desc", [x["speed"] for x in sp["order"]] ==
          sorted([x["speed"] for x in sp["order"]], reverse=True), str(sp["order"]))

    # 7) 头/尾/脚
    ht = F.head_tail(SOULS, RuleEngine(catalog=cat))
    check("headtail.keys", set(ht) == {"head", "tail", "foot"}, str(list(ht)))
    check("headtail.head_slot2", ht["head"]["candidates"] == 1, str(ht["head"]["candidates"]))
    check("headtail.foot_slot6", ht["foot"]["candidates"] == 1, str(ht["foot"]["candidates"]))

    # 8) 御魂分析
    rep = F.soul_report(SOULS, catalog=cat)
    check("souls.count", rep["count"] == 4, str(rep["count"]))
    check("souls.decisions", sum(rep["by_decision"].values()) == 4, str(rep["by_decision"]))
    check("souls.action_batch", "cleanup" in rep["action_batch"])

    # 9-12) 存储类：任务记录 / 监控 / 寮 / 档案
    tmp = tempfile.mkdtemp(prefix="wb_")
    try:
        st = Store(tmp)
        st.add_profile("p1", "主号", "官服")
        check("profile.current", (st.current_profile() or {}).get("id") == "p1")
        st.save_souls(SOULS)
        check("store.souls_roundtrip", len(st.souls()) == 4)
        st.save_owned(OWNED)
        check("store.owned_roundtrip", len(st.owned()) == 2)
        st.set_decision("0", "keep", "测试")
        check("store.decision", st.decisions().get("0") == "keep")
        st.add_task("strengthen", [0, 1], "draft")
        ts = F.task_summary(st.tasks())
        check("tasklog.total", ts["total"] == 1, str(ts["total"]))
        check("tasklog.kind", ts["by_kind"].get("strengthen") == 1, str(ts["by_kind"]))
        st.add_monitor("soul_count", 4)
        st.add_monitor("soul_count", 5)
        ms = F.monitor_summary(st.monitor())
        check("monitor.points", ms["points"] == 2, str(ms["points"]))
        st.set_guild({"name": "寮", "members": [{"name": "甲", "role": "会长"},
                                               {"name": "乙", "role": "成员"}]})
        gs = F.guild_summary(st.guild())
        check("guild.members", gs["members"] == 2, str(gs["members"]))
        check("guild.roles", gs["by_role"].get("会长") == 1, str(gs["by_role"]))

        from tasks.Workbench import guild_monitor as GM
        m = GM.collect_metrics(st, {"standard": "builtin.score.low", "by_decision": {"keep": 1},
                                    "count": 4}, note="t")
        check("monitor.collect", m.get("soul_count") == 4, str(m))
        trend = GM.metric_trend(st.monitor(), key="value")
        check("monitor.trend_points", trend["points"] >= 1, str(trend))
        check("guild.add", GM.guild_add_member(st, "乙", "成员")["ok"] is True)
        check("guild.add_dup_updates", GM.guild_add_member(st, "乙", "长老")["updated"] is True)
        check("guild.log_event", GM.guild_log_event(st, "dokan", "A")["ok"] is True)
        check("guild.log_event_bad", GM.guild_log_event(st, "nope")["ok"] is False)
        gr = GM.guild_report(st)
        check("guild.report_roles", gr["by_role"].get("长老") == 1, str(gr["by_role"]))
        check("guild.report_events", gr["events"] == 1, str(gr["events"]))
        check("guild.remove", GM.guild_remove_member(st, "乙")["removed"] == 1)

        from tasks.Workbench.ingest import activities as A
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone(timedelta(hours=8)))
        fake = {"version": "t", "source": "test", "announcements": [
            {"id": "n1", "title": "公告", "publishedAt": "2026-11-19T10:00:00+08:00", "body": "x" * 50}],
            "activities": [
                {"id": "a1", "title": "快结束", "endsAt": "2026-11-21T00:00:00+08:00",
                 "tags": ["资源"], "rewards": {"items": [{"name": "x"}]}},
                {"id": "a2", "title": "长期", "endsAt": "2026-12-31T23:59:00+08:00", "tags": ["活动"]},
                {"id": "a3", "title": "已过期", "endsAt": "2026-11-01T00:00:00+08:00"},
            ]}
        rep = A.activity_report(fake, now=now)
        check("act.total", rep["total"] == 3, str(rep["total"]))
        check("act.order_ending_first", rep["rows"][0]["id"] == "a1", str(rep["rows"][0]["id"]))
        check("act.status_counts", rep["by_status"].get("ending") == 1 and
              rep["by_status"].get("expired") == 1, str(rep["by_status"]))
        check("act.hours_left", rep["rows"][0]["hours_left"] == 12.0, str(rep["rows"][0]["hours_left"]))
        rep2 = A.activity_report(fake, now=now, tag="活动")
        check("act.tag_filter", rep2["total"] == 1, str(rep2["total"]))
        ann = A.announcement_report(fake)
        check("act.announcements", ann["total"] == 1 and ann["rows"][0]["id"] == "n1", str(ann))

        adm = F.admission_report(SOULS, standard_id="builtin.owner.default-manual", catalog=cat)
        check("admission.total", adm["total"] == 4, str(adm["total"]))
        check("admission.has_fields", "admitted" in adm and "not_admitted" in adm, str(adm))
        check("admission.rows_len", len(adm["rows"]) == 4, str(len(adm["rows"])))

        mtx = F.shard_matrix("609", {"A": [{"id": "609"}], "B": [{"id": "605"}]}, cat)
        check("shard.matrix_profiles", mtx["profiles"] == 2, str(mtx["profiles"]))
        check("shard.matrix_owned", mtx["owned_profiles"] == 1, str(mtx["owned_profiles"]))
        check("shard.matrix_missing", mtx["missing_profiles"] == 1, str(mtx["missing_profiles"]))
        check("shard.matrix_name", bool(mtx["target"]), str(mtx["target"]))

        from tasks.Workbench import monitor as M
        s1 = M.sample(st, SOULS, OWNED, standard_id="builtin.owner.default-manual", note="t")
        check("monitor.sample_fields",
              s1.get("soul_count") == 4 and "by_decision" in s1, str(s1)[:160])
        check("monitor.high_score", isinstance(s1.get("high_score"), int), str(s1.get("high_score")))
        tr = M.trend(st)
        check("monitor.trend_days", tr["days"] >= 1, str(tr["days"]))
        check("monitor.trend_series", isinstance(tr["soul_count"], list), str(tr["soul_count"]))

        ov = F.overview(st, catalog=cat, activities_data=fake)
        check("overview.keys", set(ov) >= {"profile", "roster", "assets", "recent_tasks"}, str(list(ov)))
        check("overview.assets_total", ov["assets"]["total"] == 4, str(ov["assets"]["total"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nWORKBENCH FEATURES SELFTEST: %s   TOTAL %d PASS %d FAIL %d" % (
        "PASSED" if FAIL == 0 else "FAILED", PASS + FAIL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
