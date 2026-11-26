# This file uses the following encoding: utf-8
"""Workbench WebUI 接口回归：直接调路由函数，不起服务。

用法：
    .\\toolkit\\python.exe tasks\\Workbench\\selftest_web.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

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


def body(resp):
    try:
        return json.loads(bytes(resp.body).decode("utf-8"))
    except Exception:
        return {}


def main() -> int:
    # 用临时工作目录，避免污染用户配置
    tmp = tempfile.mkdtemp(prefix="wbweb_")
    cwd = os.getcwd()
    os.environ["WORKBENCH_DIR"] = os.path.join(tmp, "wb")
    os.environ["WORKBENCH_BACKUP_DIR"] = os.path.join(tmp, "backups")
    try:
        from tasks.Workbench.store import Store
        st = Store(os.environ["WORKBENCH_DIR"])
        st.add_profile("p1", "主号", "官服")
        st.save_souls([{"set": "破势", "slot": 6, "main": "暴击", "level": 0, "quality": 6,
                        "initial_substat_count": 4,
                        "substats": {"暴击伤害": 20.0, "攻击加成": 10.0}}])
        st.save_owned([
            {"id": "609", "star": 2, "level": 1, "awakened": False,
             "skills": [{"id": "6091", "level": 1}, {"id": "6092", "level": 1}, {"id": "6093", "level": 1}]},
            {"id": "609", "star": 6, "level": 40, "awakened": True,
             "skills": [{"id": "6091", "level": 5}, {"id": "6092", "level": 4}, {"id": "6093", "level": 5}]},
        ])
        st.add_monitor("soul_count", 1)
        st.add_task("strengthen", [0], "draft")

        from module.server import workbench_router as W

        r = W.overview()
        b = body(r)
        check("web.overview.status", r.status_code == 200, str(r.status_code))
        check("web.overview.souls", b.get("assets", {}).get("total") == 1, str(b)[:160])
        check("web.overview.profile", (b.get("profile") or {}).get("id") == "p1", str(b.get("profile")))

        b = body(W.souls())
        check("web.souls.count", b.get("count") == 1, str(b.get("count")))
        check("web.souls.results", len(b.get("results") or []) == 1)

        b = body(W.roster())
        check("web.roster.owned", b.get("owned") == 2, str(b.get("owned")))
        check("web.roster.skills",
              any(row.get("skill_levels") == [5, 4, 5] for row in (b.get("rows") or [])),
              str(b.get("rows")))
        local_det = W._local_roster("609")
        check("web.roster.local_best_and_skills",
              local_det is not None
              and local_det["shikigami"]["level"] == 40
              and local_det["shikigami"]["star"] == 6
              and [s["level"] for s in local_det["shikigami"]["skills"]] == [5, 4, 5],
              str(local_det))

        b = body(W.shards(""))
        check("web.shards.hits", b.get("hits", 0) > 0, str(b.get("hits")))

        r = W.panel("")
        b = body(r)
        check("web.panel.status", r.status_code == 200, str(r.status_code))
        check("web.panel.has_final", "final" in (b.get("result") or {}), str(b)[:160])

        b = body(W.headtail())
        check("web.headtail.keys", set(b) == {"head", "tail", "foot"}, str(list(b)))

        b = body(W.tasks())
        check("web.tasks.total", b.get("total") == 1, str(b.get("total")))

        b = body(W.monitor())
        check("web.monitor.points", b.get("points") == 1, str(b.get("points")))

        r = W.decision({"soul_key": "0", "decision": "keep", "note": "web"})
        check("web.decision.ok", body(r).get("ok") is True, str(body(r)))
        check("web.decision.stored", Store(os.environ["WORKBENCH_DIR"]).decisions().get("0") == "keep")

        r = W.ui()
        check("web.ui.status", r.status_code == 200, str(r.status_code))
        check("web.ui.content", len(bytes(r.body)) > 2000, str(len(bytes(r.body))))

        b = body(W.guild())
        check("web.guild.read", b.get("members") == 0, str(b.get("members")))
        r = W.guild_member({"name": "甲", "role": "会长"})
        check("web.guild.add", body(r).get("members") == 1, str(body(r)))
        r = W.guild_member({"name": "甲", "remove": True})
        check("web.guild.remove", body(r).get("removed") == 1, str(body(r)))
        r = W.guild_event({"kind": "dokan", "value": "S"})
        check("web.guild.event", body(r).get("ok") is True, str(body(r)))
        r = W.guild_event({"kind": "bogus"})
        check("web.guild.event_rejects", r.status_code == 400, str(r.status_code))

        r = W.decision({"soul_key": "1", "decision": "bogus"})
        check("web.decision.rejects", r.status_code == 400, str(r.status_code))

        # 六个数据域：/workbench/domains（汇总）与 /workbench/domains/{name}
        from tasks.Workbench.ingest import paz_db
        b = body(W.domains())
        check("web.domains.six",
              sorted(b) == sorted(["inventory", "commonness", "relevance",
                                   "yuhun_groups", "todos", "lineup_catalog"]), str(sorted(b)))
        if os.path.isfile(paz_db.DEFAULT_DB):
            b = body(W.domain_detail("inventory"))
            # 库存量与具体快照绑定：换一次读取就会变，断言用下界
            check("web.domains.inventory", b.get("total", 0) >= 1000, str(b.get("total")))
            b = body(W.domain_detail("groups"))
            check("web.domains.groups", (b.get("groups"), b.get("members")) == (8, 16),
                  str((b.get("groups"), b.get("members"))))
            b = body(W.domain_detail("lineup"))
            check("web.domains.lineup", b.get("total") == 135, str(b.get("total")))
        r = W.domain_detail("bogus")
        check("web.domains.rejects_unknown", r.status_code == 404, str(r.status_code))

        # 导入同一份快照会更新原档案，切换角色仍可读取旧平铺数据。
        archive = {"profile": {"id": "p2", "name": "副号"},
                   "souls": [{"set_id": "测试御魂", "slot": i, "main": "speed",
                              "main_value": 6, "fixed_stats": {"speed": 1}, "locked": i == 1}
                             for i in range(1, 7)],
                   "owned": [{"id": "609"}],
                   "extras": {"shards": {"609": 12}, "currencies": {"jade": 5}}}
        check("web.archive.import", body(W.profile_import(archive)).get("ok") is True)
        export = body(W.profile_export())
        check("web.archive.export", export.get("profile", {}).get("id") == "p2"
              and export["souls"][0]["fixed_stats"] == {"speed": 1}
              and export.get("extras") == archive["extras"])
        check("web.archive.roundtrip", body(W.profile_import(export)).get("ok") is True
              and len(body(W.profiles()).get("profiles")) == 2)
        check("web.archive.reject_invalid",
              W.profile_import({"profile": {"id": "p2"}, "souls": [{"slot": "无效"}]}).status_code == 400
              and body(W.overview())["assets"]["total"] == 6)
        matrix = body(W.shards_matrix("609"))
        check("web.archive.shard_matrix", {r["profile"]: r["shards"] for r in matrix["rows"]}
              == {"主号": None, "副号": 12}, str(matrix.get("profiles")))
        check("web.archive.shard_unspecified", body(W.shards_matrix(""))["rows"] == [])
        check("web.archive.asset_balance", body(W.overview())["assets"]["currencies"] == {"jade": 5})

        p = body(W.panel_optimize({"base": {"speed": 100}, "metric": "速度"}))
        check("web.panel.active_inventory", p.get("panel", {}).get("speed") == 142, str(p)[:120])
        check("web.panel.invalid_name", W.panel_optimize({"name": "未知式神"}).status_code == 404)
        check("web.panel.invalid_formula", W.panel_optimize({"metric": "2 ** 100"}).status_code == 400)
        check("web.souls.invalid_standard", W.souls("unknown-standard").status_code == 400)
        s = body(W.speed_preview({"speed": 140, "team": [{"name": "队友", "speed": 170}]}))
        check("web.speed.order", [r["speed"] for r in s.get("order", [])] == [170, 140])
        check("web.speed.reject_nan", W.speed_preview({"speed": "NaN"}).status_code == 400)
        check("web.monitor.sample", body(W.monitor_sample({"source": "local"})).get("ok") is True
              and body(W.monitor_trend())["latest"]["soul_count"] == 6)
        check("web.archive.tasks_isolated", body(W.tasks())["total"] == 0)
        draft = body(W.draft_task({"kind": "cleanup"})).get("task", {})
        check("web.tasks.draft_locked_excluded",
              [i["slot"] for i in draft.get("items", [])] == [2, 3, 4, 5, 6])
        check("web.archive.select_legacy", body(W.profile_select({"id": "p1"})).get("ok") is True
              and body(W.overview())["assets"]["total"] == 1 and body(W.tasks())["total"] == 1)

        from tasks.Workbench.ingest import parse_archive
        native = parse_archive({
            "normalizedSouls": [{"set_id": "测试御魂", "slot": 6, "quality": 6, "level": 0,
                                 "main_attr_type": "crit_rate", "main_attr_value": 0.09,
                                 "attributes": [{"attribute_type": "speed", "value": 3,
                                                 "enhancement_count": 1}]}],
            "normalizedShikigami": [{"shikigamiId": 609, "instanceId": "row",
                                     "skills": [{"skillId": 6091, "level": 5}, {"skillId": 6092, "level": 5}]}],
            "normalizedItems": {"coin": 42},
            "sourcePayload": {"heroBookShards": [[609, 13, 0, 60, 0]]},
        })
        check("web.paz_json.format", native["extras"]["shards"] == {"609": 13}
              and native["souls"][0]["main_value"] == 9
              and native["souls"][0]["rolls"] == {"speed": 1}
              and native["owned"][0]["id"] == 609
              and native["owned"][0]["skills"] == [{"id": "6091", "level": 5}, {"id": "6092", "level": 5}])

        # ---------------- 补全的 8 个功能域（新增路由，契约见交付清单） ----------------
        from tasks.Workbench.ingest import paz_extra

        b = body(W.standards_versions())
        check("web.new.standards_versions", b.get("ok") is True and b.get("items")
              and all({"id", "name", "active", "rule_count", "updated"} <= set(i)
                      for i in b["items"]), str(b)[:160])
        pick = b["items"][0]["id"]
        r = W.standards_activate({"version_id": pick})
        check("web.new.standards_activate", body(r).get("ok") is True
              and body(r).get("active") == pick
              and Store(os.environ["WORKBENCH_DIR"]).standards_pref().get("active") == pick)
        check("web.new.standards_reject_unknown",
              W.standards_activate({"version_id": "no.such"}).status_code == 404)
        check("web.new.standards_reject_missing", W.standards_activate({}).status_code == 400)

        check("web.new.todos_reject_status", W.todos("bogus", 5).status_code == 400)
        check("web.new.todos_reject_limit", W.todos("open", 0).status_code == 400)
        check("web.new.soul_reject_missing", W.soul_detail("").status_code == 400)
        check("web.new.roster_reject_unknown", W.roster_detail("不存在的式神").status_code == 404)
        check("web.new.history_reject_limit", W.panel_history(0).status_code == 400
              and W.speed_history(0).status_code == 400)
        check("web.new.acquisitions_reject_limit", W.acquisitions(0).status_code == 400)

        # 待办处置：只写 OAS store，历史可回溯，restore 能收回
        # 有平安志库时用真实 todo_id（路由会校验它存在），没有库时用合成 id（降级模式只记录）
        real_todos = body(W.todos("all", 1)).get("items") or []
        target = real_todos[0]["todo_id"] if real_todos else "todo-selftest-1"
        b = body(W.todo_decision({"todo_id": target, "action": "done", "note": "web"}))
        check("web.new.todo_decision", b.get("ok") is True
              and (b.get("decision") or {}).get("status") == "done", str(b)[:160])
        check("web.new.todo_decision_stored",
              Store(os.environ["WORKBENCH_DIR"]).todo_decisions().get(target, {}).get("note") == "web")
        check("web.new.todo_decision_reject_action",
              W.todo_decision({"todo_id": target, "action": "bogus"}).status_code == 400)
        b = body(W.todo_decision({"todo_id": target, "action": "restore"}))
        check("web.new.todo_restore", (b.get("decision") or {}).get("status") == "open", str(b)[:160])
        hist = body(W.todos_history(10))
        check("web.new.todo_history", hist.get("ok") is True
              and [h.get("action") for h in hist.get("items") or []][:2] == ["restore", "done"],
              str(hist.get("items"))[:160])

        # 备份：落临时目录，不动平安志
        b = body(W.backups())
        check("web.new.backups_empty", b.get("ok") is True and b.get("items") == [], str(b)[:120])
        created = body(W.backups_create({"note": "web 自检"})).get("backup") or {}
        check("web.new.backups_create", {"id", "time", "size", "path"} <= set(created)
              and os.path.isfile(created.get("path") or ""), str(created)[:160])
        check("web.new.backups_dir", os.path.abspath(created["path"]).startswith(
            os.path.abspath(os.environ["WORKBENCH_BACKUP_DIR"])))
        check("web.new.backups_listed",
              [i["id"] for i in body(W.backups()).get("items") or []] == [created.get("id")])

        # 元信息 / 快照 / 采集 / 历史：有库就用真数据核对，没库就要 reason
        b = body(W.meta())
        check("web.new.meta", {"data_source", "pinganzhi", "counts", "last_sync"} <= set(b)
              and isinstance(b["counts"], dict), str(b)[:160])
        if os.path.isfile(paz_extra.db_path()):
            todos = body(W.todos("all", 5))
            check("web.new.todos_real", todos.get("ok") is True and todos.get("items")
                  and set(todos.get("counts") or {}) == {"open", "done", "ignored"},
                  str(todos.get("counts")))
            soul_id = todos["items"][0]["soul_internal_id"]
            detail = body(W.soul_detail(soul_id))
            check("web.new.soul_detail_real", detail.get("ok") is True
                  and len(detail.get("radar") or []) == 8
                  and (detail.get("score") or {}).get("contributions") is not None,
                  str(detail)[:160])
            check("web.new.soul_unknown_404", W.soul_detail("no-such-soul").status_code == 404)
            check("web.new.snapshots_real", body(W.snapshots()).get("ok") is True)
            check("web.new.acquisitions_real", body(W.acquisitions(5)).get("ok") is True)
            roster = body(W.roster_detail("609"))
            check("web.new.roster_real", roster.get("ok") is True
                  and {"name", "rarity", "skills", "souls", "owned"} <= set(roster), str(roster)[:160])
        else:
            b = body(W.todos("open", 5))
            check("web.new.todos_degraded", b.get("ok") is False and b.get("reason")
                  and b.get("items") == [], str(b)[:160])
            b = body(W.snapshots())
            check("web.new.snapshots_degraded", b.get("ok") is False and b.get("items") == [], str(b)[:160])
            b = body(W.acquisitions(5))
            check("web.new.acquisitions_degraded", b.get("ok") is False and b.get("items") == [], str(b)[:160])
            b = body(W.panel_history(5))
            check("web.new.panel_history_degraded", b.get("items") == []
                  and (b.get("reason") or b.get("note")), str(b)[:160])
            roster = body(W.roster_detail("609"))
            check("web.new.roster_local_skills",
                  roster.get("ok") is True and roster.get("source") == "oas-store"
                  and roster.get("level") == 40 and roster.get("star") == 6
                  and roster.get("skill_levels") == [5, 4, 5], str(roster)[:200])

        # /workbench/souls：行标识 + 分页 + 过滤（t4，纯增量）
        b = body(W.souls())
        check("web.new.souls_paging", {"total", "offset", "limit", "returned"} <= set(b)
              and b["total"] == 1 and b["offset"] == 0 and b["limit"] == 200
              and len(b["results"]) == 1,
              str({k: b.get(k) for k in ("total", "offset", "limit", "returned")}))
        row = b["results"][0]
        check("web.new.souls_row_fields",
              {"soul_id", "set_name", "slot", "quality", "level"} <= set(row)
              and {"matched", "score", "decision", "rule_id", "rule_name", "breakdown",
                   "candidates", "idx"} <= set(row), str(sorted(row)))
        check("web.new.souls_row_values", row["set_name"] == "破势" and row["slot"] == 6
              and row["quality"] == 6, str(row)[:200])
        tail = body(W.souls(offset=1))
        check("web.new.souls_offset", tail["offset"] == 1 and tail["results"] == [],
              str(tail)[:160])
        check("web.new.souls_filter_set", body(W.souls(set_name="破势"))["total"] == 1
              and body(W.souls(set_name="不存在"))["total"] == 0)
        check("web.new.souls_filter_slot", body(W.souls(slot=6))["total"] == 1
              and body(W.souls(slot=1))["total"] == 0)
        check("web.new.souls_bad_params",
              W.souls(limit=0).status_code == 400 and W.souls(limit=1001).status_code == 400
              and W.souls(offset=-1).status_code == 400 and W.souls(slot=9).status_code == 400)

        # 不新增藏宝阁(cbg)路由
        paths = {getattr(route, "path", "") for route in W.app.routes}
        check("web.new.no_cbg", not any("cbg" in p.lower() or "cangbao" in p.lower() for p in paths))
    finally:
        os.environ.pop("WORKBENCH_DIR", None)
        os.environ.pop("WORKBENCH_BACKUP_DIR", None)
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nWORKBENCH WEB SELFTEST: %s   TOTAL %d PASS %d FAIL %d" % (
        "PASSED" if FAIL == 0 else "FAILED", PASS + FAIL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
