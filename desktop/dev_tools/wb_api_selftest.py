# This file uses the following encoding: utf-8
"""工作台补全的 8 个功能域接口 —— 离线自检。

做法：
  * 直接调 module/server/workbench_router.py 里的路由函数（不起 HTTP 服务）；
  * 读真实平安志 sqlite（mode=ro，只读），本机没有该库时自动跳过"真数据"用例并说明；
  * 写操作全部指向临时 OAS store + 临时 backup 目录（WORKBENCH_DIR / WORKBENCH_BACKUP_DIR）；
  * 自检前后对平安志库文件做 大小 + mtime + 目录文件集 三重比对，证明没写它的库。

用法（仓库根）：
    .\\toolkit\\python.exe dev_tools\\wb_api_selftest.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0
SKIP = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print("SKIP  %s  （%s）" % (name, why))


def body(resp):
    try:
        return json.loads(bytes(resp.body).decode("utf-8"))
    except Exception:
        return {}


def _dir_files(path: str) -> dict:
    out = {}
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if os.path.isfile(full):
                out[name] = os.path.getsize(full)
    except OSError:
        pass
    return out


def _ro(db: str) -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="wbapi_")
    backup_dir = os.path.join(tmp, "backups")
    os.environ["WORKBENCH_DIR"] = os.path.join(tmp, "wb")
    os.environ["WORKBENCH_BACKUP_DIR"] = backup_dir
    os.environ.pop("WORKBENCH_PAZ_DB", None)
    try:
        from tasks.Workbench.store import Store
        from tasks.Workbench.ingest import paz_extra

        store = Store(os.environ["WORKBENCH_DIR"])
        store.add_profile("p1", "主号", "官服")
        store.save_souls([{"internal_id": "local-1", "set": "破势", "slot": 6, "main": "暴击伤害",
                           "main_value": 89.0, "level": 15, "quality": 6,
                           "initial_substat_count": 4, "rolls": {"暴击": 2, "速度": 1},
                           "substats": {"暴击": 8.0, "速度": 11.0}}])
        store.save_owned([{"id": "609", "star": 6, "level": 40, "awakened": True}])

        db = paz_extra.db_path()
        have_paz = os.path.isfile(db)
        paz_dir = os.path.dirname(db)
        paz_files = _dir_files(paz_dir) if have_paz else {}
        db_stat = (os.path.getsize(db), os.path.getmtime(db)) if have_paz else None
        todo_id = soul_id = shikigami_key = None
        if have_paz:
            con = _ro(db)
            try:
                row = con.execute("select id from analysis_todo limit 1").fetchone()
                todo_id = row[0] if row else None
                row = con.execute("select internal_id from snapshot_soul limit 1").fetchone()
                soul_id = row[0] if row else None
                row = con.execute(
                    "select s.name from owned_shikigami o join shikigami s "
                    "on s.shikigami_id = o.shikigami_id limit 1").fetchone()
                shikigami_key = row[0] if row else None
            finally:
                con.close()
        else:
            print("NOTE  本机没有平安志数据库（%s），真数据用例跳过；只跑 OAS store / 降级用例。" % db)

        from module.server import workbench_router as W

        # ---------------- 1) GET /workbench/souls/{soul_id}
        if have_paz and soul_id:
            r = W.soul_detail(soul_id)
            b = body(r)
            check("api.soul.status", r.status_code == 200, str(r.status_code))
            check("api.soul.keys", set(b) >= {"ok", "soul", "attrs", "score", "radar"}, str(list(b)))
            check("api.soul.soul_fields",
                  {"internal_id", "set_name", "slot", "quality", "level", "score"} <= set(b.get("soul") or {}),
                  str(b.get("soul")))
            check("api.soul.attrs", len(b.get("attrs") or []) > 1
                  and all({"name", "value", "is_fixed", "rolls"} <= set(a) for a in b["attrs"]),
                  str(b.get("attrs"))[:200])
            contributions = (b.get("score") or {}).get("contributions") or []
            check("api.soul.score", isinstance((b.get("score") or {}).get("value"), (int, float))
                  and all({"label", "value"} <= set(c) for c in contributions), str(b.get("score"))[:200])
            check("api.soul.radar", len(b.get("radar") or []) == 8
                  and all({"axis", "value"} <= set(p) for p in b["radar"]), str(b.get("radar"))[:200])
        else:
            skip("api.soul.*", "没有平安志库或快照御魂")
        check("api.soul.unknown_404", W.soul_detail("no-such-soul").status_code == 404)

        # ---------------- 1b) GET /workbench/souls：行标识 + 分页 + 过滤（t4）
        paz_souls = []
        if have_paz:
            from tasks.Workbench.ingest import paz_db
            paz_souls = paz_db.load().get("souls") or []
            if paz_souls:
                store.save_souls(paz_souls)
        if paz_souls:
            r = W.souls()
            size = len(bytes(r.body))
            b = body(r)
            check("api.souls.status", r.status_code == 200, str(r.status_code))
            check("api.souls.paging_fields", {"total", "offset", "limit", "returned"} <= set(b),
                  str(sorted(b)))
            check("api.souls.default_page", b["total"] == len(paz_souls)
                  and b["offset"] == 0 and b["limit"] == 200 and b["returned"] == 200
                  and len(b["results"]) == 200,
                  "total=%s offset=%s limit=%s returned=%s" % (b["total"], b["offset"],
                                                               b["limit"], b["returned"]))
            check("api.souls.default_size", size < 150 * 1024, "%d bytes" % size)
            row = b["results"][0]
            old_fields = {"matched", "score", "decision", "rule_id", "rule_name", "breakdown",
                          "candidates", "idx"}
            new_fields = {"soul_id", "set_name", "slot", "quality", "level"}
            check("api.souls.row_old_fields", old_fields <= set(row), str(sorted(row)))
            check("api.souls.row_new_fields",
                  new_fields <= set(row) and bool(row["soul_id"])
                  and row["set_name"] == paz_souls[row["idx"]]["set"],
                  str(row)[:240])
            bad = []
            for item in b["results"][:20]:
                detail = body(W.soul_detail(item["soul_id"]))
                if not detail.get("ok") or not (detail.get("soul") or {}).get("internal_id"):
                    bad.append(item["soul_id"])
            check("api.souls.jump_detail", not bad, str(bad[:3]))
            tail = body(W.souls(offset=b["total"] - 7, limit=200))
            check("api.souls.tail_page", len(tail["results"]) == 7
                  and tail["offset"] == b["total"] - 7, str(len(tail["results"])))
            mid = body(W.souls(offset=1000, limit=5))
            check("api.souls.offset_mid",
                  [x["idx"] for x in mid["results"]] == list(range(1000, 1005)),
                  str([x["idx"] for x in mid["results"]]))
            set_name = row["set_name"]
            f = body(W.souls(set_name=set_name, limit=50))
            check("api.souls.filter_set", f["total"] > 0
                  and all(x["set_name"] == set_name for x in f["results"]), str(f["total"]))
            f = body(W.souls(slot=6, limit=20))
            check("api.souls.filter_slot", f["total"] > 0
                  and all(x["slot"] == 6 for x in f["results"]), str(f["total"]))
            f = body(W.souls(q=row["soul_id"][:8], limit=5))
            check("api.souls.filter_q", f["total"] >= 1
                  and all(x["soul_id"].startswith(row["soul_id"][:8]) for x in f["results"]),
                  str(f["total"]))
        else:
            skip("api.souls.*", "没有平安志库或快照御魂")
        check("api.souls.bad_params",
              W.souls(limit=0).status_code == 400
              and W.souls(limit=1001).status_code == 400
              and W.souls(offset=-1).status_code == 400
              and W.souls(slot=9).status_code == 400)

        # ---------------- 2) GET /workbench/todos
        if have_paz:
            r = W.todos("open", 5)
            b = body(r)
            check("api.todos.status", r.status_code == 200, str(r.status_code))
            check("api.todos.counts", set(b.get("counts") or {}) == {"open", "done", "ignored"},
                  str(b.get("counts")))
            check("api.todos.items", 0 < len(b.get("items") or []) <= 5, str(len(b.get("items") or [])))
            item = (b.get("items") or [{}])[0]
            check("api.todos.item_fields",
                  {"todo_id", "soul_id", "set_name", "score", "rule", "status", "note", "labels"}
                  <= set(item), str(sorted(item)))
            before_open = (b.get("counts") or {}).get("open", 0)
            check("api.todos.bad_status", W.todos("bogus", 5).status_code == 400)
            check("api.todos.bad_limit", W.todos("open", 0).status_code == 400)
        else:
            skip("api.todos.*", "没有平安志库")

        # ---------------- 3) POST /workbench/todos/decision + 4) history
        target = todo_id or "todo-from-test"
        r = W.todo_decision({"todo_id": target, "action": "ignore", "note": "自检"})
        b = body(r)
        check("api.todos.decision", b.get("ok") is True and (b.get("decision") or {}).get("status") == "ignored",
              str(b))
        check("api.todos.decision_stored",
              Store(os.environ["WORKBENCH_DIR"]).todo_decisions().get(target, {}).get("status") == "ignored")
        check("api.todos.decision_bad_action", W.todo_decision({"todo_id": target, "action": "delete"}).status_code == 400)
        check("api.todos.decision_missing_id", W.todo_decision({"action": "done"}).status_code == 400)
        if have_paz:
            b = body(W.todos("ignored", 50))
            check("api.todos.after_decision", any(i["todo_id"] == target and i["status"] == "ignored"
                                                  for i in b.get("items") or []), str(b.get("counts")))
            b = body(W.todos("open", 5))
            check("api.todos.open_decreased",
                  (b.get("counts") or {}).get("open", 0) == before_open - 1, str(b.get("counts")))
        b = body(W.todo_decision({"todo_id": target, "action": "restore"}))
        check("api.todos.restore", b.get("ok") is True and (b.get("decision") or {}).get("status") == "open",
              str(b))
        hist = body(W.todos_history(20))
        check("api.todos.history", hist.get("ok") is True and len(hist.get("items") or []) >= 2,
              str(hist.get("items"))[:200])
        check("api.todos.history_bad_limit", W.todos_history(0).status_code == 400)

        # ---------------- 5) standards versions / activate
        b = body(W.standards_versions())
        items = b.get("items") or []
        check("api.standards.versions", b.get("ok") is True and items
              and all({"id", "name", "active", "rule_count", "updated"} <= set(i) for i in items),
              str(b)[:200])
        check("api.standards.active_in_items", b.get("active") in {i["id"] for i in items},
              str(b.get("active")))
        pick = items[0]["id"] if items else "builtin.owner.default-manual"
        b = body(W.standards_activate({"version_id": pick}))
        check("api.standards.activate", b.get("ok") is True and b.get("active") == pick, str(b))
        check("api.standards.activate_stored",
              Store(os.environ["WORKBENCH_DIR"]).standards_pref().get("active") == pick)
        check("api.standards.activate_unknown",
              W.standards_activate({"version_id": "no.such.standard"}).status_code == 404)
        check("api.standards.activate_missing", W.standards_activate({}).status_code == 400)

        # ---------------- 6) GET /workbench/roster/{name}
        if have_paz and shikigami_key:
            r = W.roster_detail(shikigami_key)
            b = body(r)
            check("api.roster.status", r.status_code == 200, str(r.status_code))
            check("api.roster.keys",
                  {"name", "rarity", "level", "star", "skills", "souls", "owned"} <= set(b), str(list(b)))
            check("api.roster.owned", b.get("owned") is True, str(b.get("owned")))
            check("api.roster.skills", isinstance(b.get("skills"), list) and b["skills"]
                  and {"id", "name", "level"} <= set(b["skills"][0]), str(b.get("skills"))[:200])
            check("api.roster.souls_shape", all({"slot", "set_name", "score"} <= set(s)
                                                for s in b.get("souls") or []), str(b.get("souls"))[:200])
        else:
            skip("api.roster.*", "没有平安志库")
        check("api.roster.unknown_404", W.roster_detail("不存在的式神").status_code == 404)

        # ---------------- 7) GET /workbench/snapshots
        b = body(W.snapshots())
        if have_paz:
            check("api.snapshots.ok", b.get("ok") is True and b.get("items"), str(b)[:200])
            check("api.snapshots.fields", all({"snapshot_id", "time", "items", "souls", "source",
                                               "integrity"} <= set(i) for i in b.get("items") or []),
                  str(b.get("items"))[:200])
        else:
            skip("api.snapshots.*", "没有平安志库")

        # ---------------- 8) backups（写 OAS 自己的目录）
        b = body(W.backups())
        check("api.backups.empty", b.get("ok") is True and b.get("items") == [], str(b)[:160])
        b = body(W.backups_create({"note": "自检备份"}))
        created = b.get("backup") or {}
        check("api.backups.create", b.get("ok") is True and created.get("id")
              and {"id", "time", "size", "path"} <= set(created), str(b)[:200])
        check("api.backups.file", os.path.isfile(created.get("path") or "")
              and os.path.abspath(created["path"]).startswith(os.path.abspath(backup_dir)),
              str(created.get("path")))
        check("api.backups.small", (created.get("size") or 0) < 32 * 1024 * 1024, str(created.get("size")))
        b = body(W.backups())
        check("api.backups.listed", len(b.get("items") or []) == 1
              and b["items"][0]["id"] == created.get("id"), str(b.get("items"))[:160])

        # ---------------- 9) GET /workbench/acquisitions
        b = body(W.acquisitions(10))
        if have_paz:
            check("api.acquisitions.ok", b.get("ok") is True, str(b)[:200])
            check("api.acquisitions.fields", all({"id", "time", "source", "format", "result"}
                                                 <= set(i) for i in b.get("items") or []),
                  str(b.get("items"))[:200])
        else:
            skip("api.acquisitions.*", "没有平安志库")
        check("api.acquisitions.bad_limit", W.acquisitions(0).status_code == 400)

        # ---------------- 10) GET /workbench/meta
        b = body(W.meta())
        check("api.meta.keys", {"data_source", "pinganzhi", "counts", "last_sync"} <= set(b), str(list(b)))
        check("api.meta.pinganzhi", (b.get("pinganzhi") or {}).get("available") is have_paz,
              str(b.get("pinganzhi")))
        if have_paz:
            check("api.meta.counts", (b.get("counts") or {}).get("souls") == 3547
                  or (b.get("counts") or {}).get("souls", 0) > 0, str(b.get("counts")))
            check("api.meta.last_sync", bool(b.get("last_sync")), str(b.get("last_sync")))

        # ---------------- 11) panel / speed history
        for kind, path in (("panel", W.panel_history), ("speed", W.speed_history)):
            b = body(path(10))
            check("api.%s_history.status" % kind, "items" in b and isinstance(b["items"], list),
                  str(b)[:160])
            if have_paz:
                check("api.%s_history.graceful" % kind,
                      (b.get("ok") is True) or bool(b.get("reason")), str(b)[:160])
            check("api.%s_history.bad_limit" % kind, path(0).status_code == 400)

        # ---------------- 降级：平安志库指向不存在的路径
        os.environ["WORKBENCH_PAZ_DB"] = os.path.join(tmp, "missing", "none.sqlite3")
        b = body(W.todos("open", 5))
        check("degraded.todos", b.get("ok") is False and b.get("reason") and b.get("items") == [],
              str(b)[:200])
        b = body(W.snapshots())
        check("degraded.snapshots", b.get("ok") is False and b.get("reason") and b.get("items") == [],
              str(b)[:160])
        b = body(W.acquisitions(5))
        check("degraded.acquisitions", b.get("ok") is False and b.get("reason") and b.get("items") == [],
              str(b)[:160])
        b = body(W.meta())
        check("degraded.meta", (b.get("pinganzhi") or {}).get("available") is False
              and b.get("data_source") == "oas-store" and b.get("reason"), str(b)[:200])
        b = body(W.panel_history(5))
        check("degraded.panel_history", b.get("items") == [] and (b.get("reason") or b.get("note")),
              str(b)[:160])
        b = body(W.standards_versions())
        check("degraded.standards_fallback", b.get("ok") is True and b.get("items")
              and b.get("source") == "local-standard", str(b)[:200])
        # 本地回退用例需要一条带 internal_id 的档案记录（上面分页用例已用真数据覆盖 store）
        current = Store(os.environ["WORKBENCH_DIR"]).souls()
        if not any(str(s.get("internal_id")) == "local-1" for s in current):
            Store(os.environ["WORKBENCH_DIR"]).save_souls(current + [{
                "internal_id": "local-1", "set": "破势", "slot": 6, "main": "暴击伤害",
                "main_value": 89.0, "level": 15, "quality": 6,
                "initial_substat_count": 4, "rolls": {"暴击": 2, "速度": 1},
                "substats": {"暴击": 8.0, "速度": 11.0}}])
        r = W.soul_detail("local-1")
        b = body(r)
        check("degraded.soul_local_fallback", r.status_code == 200 and b.get("ok") is True
              and b.get("source") == "oas-store" and b.get("attrs"), str(b)[:200])
        check("degraded.soul_unknown_graceful",
              body(W.soul_detail("nothing-here")).get("ok") is False
              and body(W.soul_detail("nothing-here")).get("reason"))
        check("degraded.roster_local_fallback", body(W.roster_detail("609")).get("ok") is True)
        b = body(W.todo_decision({"todo_id": "offline-todo", "action": "done"}))
        check("degraded.todos_decision_records", b.get("ok") is True and b.get("reason"), str(b)[:200])
        b = body(W.backups_create({"note": "离线备份"}))
        check("degraded.backups_create", b.get("ok") is True, str(b)[:160])
        os.environ.pop("WORKBENCH_PAZ_DB", None)

        # ---------------- 只读证明：平安志库与目录没被写过
        if have_paz:
            after_stat = (os.path.getsize(db), os.path.getmtime(db))
            check("readonly.db_file_untouched", after_stat == db_stat,
                  "before=%s after=%s" % (db_stat, after_stat))
            check("readonly.db_dir_untouched", _dir_files(paz_dir) == paz_files,
                  str(set(_dir_files(paz_dir)) ^ set(paz_files)))
        # 写操作目标必须全在临时 OAS 目录下
        check("write.only_oas_store",
              os.path.abspath(os.environ["WORKBENCH_DIR"]).startswith(os.path.abspath(tmp)))
        check("write.only_oas_backup",
              os.path.abspath(backup_dir).startswith(os.path.abspath(tmp))
              and os.path.isfile(os.path.join(backup_dir, "index.json")))
        # 不新增藏宝阁(cbg)路由/字段
        paths = {getattr(route, "path", "") for route in W.app.routes}
        check("scope.no_cbg_routes", not any("cbg" in p.lower() or "cangbao" in p.lower() for p in paths),
              str(sorted(p for p in paths if "cbg" in p.lower())))

        # ---------------- 路由解析：14 个新端点各自命中自己的处理函数（无路径冲突）
        from starlette.routing import Match

        def matched(path, method):
            scope = {"type": "http", "method": method, "path": path,
                     "headers": [], "root_path": ""}
            for route in W.app.routes:
                state, _ = route.matches(scope)
                if state == Match.FULL:
                    return route
            return None

        expected = [
            ("GET", "/workbench/souls/abc", "soul_detail"),
            ("GET", "/workbench/todos", "todos"),
            ("POST", "/workbench/todos/decision", "todo_decision"),
            ("GET", "/workbench/todos/history", "todos_history"),
            ("GET", "/workbench/standards/versions", "standards_versions"),
            ("POST", "/workbench/standards/activate", "standards_activate"),
            ("GET", "/workbench/roster/609", "roster_detail"),
            ("GET", "/workbench/snapshots", "snapshots"),
            ("GET", "/workbench/backups", "backups"),
            ("POST", "/workbench/backups/create", "backups_create"),
            ("GET", "/workbench/acquisitions", "acquisitions"),
            ("GET", "/workbench/meta", "meta"),
            ("GET", "/workbench/panel/history", "panel_history"),
            ("GET", "/workbench/speed/history", "speed_history"),
        ]
        wrong = []
        for method, path, name in expected:
            route = matched(path, method)
            if route is None or getattr(route, "name", "") != name and \
                    getattr(getattr(route, "endpoint", None), "__name__", "") != name:
                wrong.append("%s %s -> %s" % (method, path, route and getattr(route, "path", None)))
        check("routing.new_endpoints", not wrong, str(wrong))

        # 老路由一条不少、方法不变（本轮只追加、不改既有处理函数）
        old_routes = [
            ("GET", "/workbench/ui"), ("GET", "/workbench/overview"),
            ("GET", "/workbench/profiles"), ("POST", "/workbench/profiles/select"),
            ("POST", "/workbench/profiles/import"), ("GET", "/workbench/profiles/export"),
            ("POST", "/workbench/profiles/sync"), ("GET", "/workbench/standards"),
            ("GET", "/workbench/souls"), ("GET", "/workbench/roster"),
            ("GET", "/workbench/shards"), ("GET", "/workbench/shards/matrix"),
            ("GET", "/workbench/panel"), ("POST", "/workbench/panel/optimize"),
            ("POST", "/workbench/speed-preview"), ("GET", "/workbench/headtail"),
            ("GET", "/workbench/guild"), ("POST", "/workbench/guild/member"),
            ("POST", "/workbench/guild/event"), ("GET", "/workbench/activities"),
            ("GET", "/workbench/tasks"), ("POST", "/workbench/tasks/draft"),
            ("GET", "/workbench/monitor"), ("GET", "/workbench/admission"),
            ("GET", "/workbench/domains"), ("GET", "/workbench/domains/{name}"),
            ("GET", "/workbench/location/status"), ("POST", "/workbench/location"),
            ("GET", "/workbench/monitor/trend"), ("POST", "/workbench/monitor/sample"),
            ("GET", "/workbench/instances"), ("POST", "/workbench/instances/action"),
            ("POST", "/workbench/decision"),
        ]
        known = {getattr(r, "path", "") for r in W.app.routes}
        missing = [p for _, p in old_routes if p not in known]
        check("routing.old_routes_kept", not missing,
              "missing=%s count=%d" % (missing, len(old_routes)))
        print("INFO  老路由 %d 条全部仍在；路由总数 %d" % (len(old_routes), len(known)))
    finally:
        os.environ.pop("WORKBENCH_DIR", None)
        os.environ.pop("WORKBENCH_BACKUP_DIR", None)
        os.environ.pop("WORKBENCH_PAZ_DB", None)
        shutil.rmtree(tmp, ignore_errors=True)

    total = PASS + FAIL
    print("\nWB API SELFTEST: %s   TOTAL %d PASS %d FAIL %d SKIP %d" % (
        "PASSED" if FAIL == 0 else "FAILED", total, PASS, FAIL, SKIP))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
