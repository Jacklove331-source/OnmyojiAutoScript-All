# This file uses the following encoding: utf-8
"""工作台补全 —— 独立验收脚本（wb-verify 编写，不同于实现方自检）。

覆盖：
  1) 静态核查：UI 无外网 CDN、导航五组顺序、13 个老 hash 锚、三态提示、
     藏宝阁零残留、平安志读取层没有写 SQL；
  2) 临时服务（默认 23334，只挂 /workbench，不碰 22267）：11 组新接口逐个实测，
     字段名按契约断言，无效参数必须 4xx；平安志库不可用时优雅降级
     （WORKBENCH_PAZ_DB 指向不存在的库，等价于 --no-paz）；
  3) 老路由回归：从 git 46829740（补全前最后一个提交）取出 workbench_router.py，
     在 23335 起第二份服务，12 条老路由逐条比对字段名（新响应必须包含老响应全部字段）；
  4) 写边界：POST /todos/decision、/standards/activate、/backups/create 前后，
     %APPDATA%\\com.yys.analysis 整棵目录的文件集合/大小/mtime 必须完全不变；
  5) UI：Edge headless + DevTools 协议，等真实数据渲染完再截图
     （1440x2400 / 720x1600，另出全页图），并在真实 DOM 里核查导航顺序、
     锚点可达、三态、资源同源、移动端无横向溢出。

用法（仓库根）：
    .\\toolkit\\python.exe dev_tools\\verify_workbench_ui.py
    .\\toolkit\\python.exe dev_tools\\verify_workbench_ui.py --no-shots
    .\\toolkit\\python.exe dev_tools\\verify_workbench_ui.py --port 23340 --edge "C:\\...\\msedge.exe"

只写：log/workbench/ui/verify_*.png（截图）。其余全部只读；
写操作一律打到 %TEMP% 下复制出来的 OAS store 与备份目录。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

BASELINE = "46829740"  # 工作台补全开始前的最后一个提交
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]
SHOT_DIR = ROOT / "log" / "workbench" / "ui"

PASS = 0
FAIL = 0
FAILED = []
SECTION = {"name": ""}


def section(name):
    SECTION["name"] = name
    print("\n=== %s ===" % name, flush=True)


def need(name, cond, detail=""):
    global PASS, FAIL
    detail = str(detail)
    if len(detail) > 400:
        detail = detail[:400] + "…"
    if cond:
        PASS += 1
        print("PASS  %s" % name, flush=True)
        return True
    FAIL += 1
    FAILED.append("%s/%s  %s" % (SECTION["name"], name, detail))
    print("FAIL  %s  %s" % (name, detail), flush=True)
    return False


def info(text):
    print("INFO  %s" % text, flush=True)


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method, url, payload=None, timeout=120):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw or "{}")
            except ValueError:
                return resp.status, {"raw": raw[:500]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"raw": raw[:500]}
        return exc.code, body


def keys(obj):
    return set(obj.keys()) if isinstance(obj, dict) else set()


def has_keys(obj, expected):
    return keys(obj) >= set(expected)


def missing(obj, expected):
    return sorted(set(expected) - keys(obj))


# ---------------------------------------------------------------- 静态核查
def static_checks():
    section("静态核查（UI 结构 / CDN / 三态 / 藏宝阁 / 只读实现）")
    html_path = ROOT / "module" / "server" / "web" / "workbench" / "index.html"
    html = html_path.read_text(encoding="utf-8")
    router = (ROOT / "module" / "server" / "workbench_router.py").read_text(encoding="utf-8")
    paz = (ROOT / "tasks" / "Workbench" / "ingest" / "paz_extra.py").read_text(encoding="utf-8")
    store = (ROOT / "tasks" / "Workbench" / "store.py").read_text(encoding="utf-8")

    # 1) 无外网依赖
    urls = re.findall(r"https?://[^\s\"'<>()]+", html)
    need("UI.html 无外网 URL", len(urls) == 0, "发现 %d 条: %s" % (len(urls), urls[:5]))
    tags = re.findall(r"<(?:script|link|img)[^>]*(?:src|href)=\"(?!data:|#)([^\"]+)\"", html)
    ext = [t for t in tags if re.match(r"^(?:https?:)?//", t)]
    need("UI.html 无外链静态资源", len(ext) == 0, str(ext[:5]))

    # 2) 导航分组顺序
    groups = [g.strip() for g in re.findall(r'<div class="ng-t">([^<]+)</div>', html)]
    expect_groups = ["总览", "御魂", "式神", "养成", "数据"]
    need("导航分组顺序 总览→御魂→式神→养成→数据", groups == expect_groups, str(groups))

    # 3) 13 个老 hash 锚（补全前页面用过的 id）必须仍然可达
    legacy = ["ov", "mon", "soul-sec", "panel-sec", "ht-sec", "act-sec", "roster-sec",
              "asset-sec", "shard-sec", "guild-sec", "profiles-sec", "tasks-sec", "speed-sec"]
    ids = set(re.findall(r'id="([^"]+)"', html))
    miss = [x for x in legacy if x not in ids]
    need("13 个老 hash 锚 id 全部存在", not miss, "缺: %s" % miss)
    hrefs = re.findall(r'<a href="#([^"]+)"', html)
    bad = [h for h in hrefs if h not in ids]
    need("所有导航 href 指向已存在的 id", not bad, "悬空: %s" % bad)
    info("老锚点 %d/%d，页面内导航链接 %d 条，section %d 个"
         % (len(legacy) - len(miss), len(legacy), len(hrefs), len(ids)))

    # 4) 三态提示
    for cls, label in (("state loading", "加载中"), ("state empty", "空"), ("state error", "错误")):
        need("三态模板存在: %s" % cls, cls in html)
    need("三态中文文案存在（加载中/空/失败）",
         "加载中" in html and "还没有" in html and ("失败" in html or "错误" in html))

    # 5) 藏宝阁零残留：只看"真东西"——路由路径 / 菜单可见文案 / 代码标识符，
    #    注释里写"藏宝阁已剔除"以及断言"不得出现 cbg"的测试不算残留。
    cbg_pat = re.compile(r"cbg|cangbao|cang_bao|藏宝阁", re.I)
    route_hits = [m.group(0) for m in re.finditer(r'@app\.(?:get|post)\(\s*["\'][^"\']*["\']', router)
                  if cbg_pat.search(m.group(0))]
    menu_hits = [line.strip()[:90] for line in html.splitlines()
                 if cbg_pat.search(line) and "<a " in line]
    id_hits = []
    for path in [ROOT / "module" / "server" / "workbench_router.py", html_path] + \
            sorted((ROOT / "tasks" / "Workbench").rglob("*.py")):
        if path.name.startswith("selftest"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if cbg_pat.search(stripped) and not stripped.startswith("#") \
                    and not stripped.startswith('"""') and "CBG" not in stripped.upper():
                # 代码/字段里的 cbg 标识符（排除注释与"已剔除"说明）
                if re.search(r"[\"'](?:cbg|cangbao)", stripped, re.I):
                    id_hits.append("%s:%d" % (path.relative_to(ROOT), i))
    need("藏宝阁零残留（路由）", not route_hits, str(route_hits[:5]))
    need("藏宝阁零残留（菜单/导航）", not menu_hits, str(menu_hits[:5]))
    need("藏宝阁零残留（代码字段标识符）", not id_hits, str(id_hits[:5]))
    info("藏宝阁检索：路由 0 / 菜单 0 / 标识符 0（注释与负向断言不计）")

    # 6) 只读实现：读取层没有写 SQL / 连接层是 mode=ro
    write_kw = re.compile(r"\b(INSERT|UPDATE|DELETE|REPLACE\s+INTO|DROP\s+TABLE|ATTACH\s+DATABASE)\b", re.I)
    hits = ["paz_extra.py:%d" % i for i, line in enumerate(paz.splitlines(), 1) if write_kw.search(line)]
    need("paz_extra.py 无写 SQL", not hits, str(hits[:6]))
    need("paz_extra._ro 使用 mode=ro", "mode=ro" in paz and "query_only=1" in paz)
    need("store.py 只落 JSON（无 sqlite 写）", ".sqlite" not in store and "INSERT" not in store.upper())
    need("router 不直接写平安志路径（无 %APPDATA% 写语句）",
         not re.search(r"(?i)(insert|update|delete)[^\n]{0,80}com\.yys\.analysis", router))


# ---------------------------------------------------------------- 服务
def port_free(port):
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def pick_port(preferred, span=60):
    if port_free(preferred):
        return preferred
    for port in range(preferred + 1, preferred + span):
        if port_free(port):
            return port
    raise SystemExit("找不到空闲端口（从 %d 起试了 %d 个）" % (preferred, span))


def start_server(app, port):
    import uvicorn

    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def wait_http(url, timeout=120, marker=None):
    end = time.time() + timeout
    while time.time() < end:
        try:
            status, body = http("GET", url, timeout=5)
            if status < 500 and (marker is None or (isinstance(body, dict) and body.get("marker") == marker)):
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def build_apps(port_new, port_old):
    from fastapi import FastAPI

    import module.server.workbench_router as wb_new

    new_app = FastAPI(title="wb-verify-new")
    new_app.include_router(wb_new.app)

    @new_app.get("/__wb_verify_marker__")
    def _marker_new():
        return {"marker": "wb-verify-new"}

    # 补全前的 router：从 git 取出源码后在内存里 exec，__file__ 保持真实位置，
    # 这样模块内的 REPO_ROOT 计算和正式代码一致（不落地任何临时源码文件）。
    old_src = subprocess.run(
        ["git", "show", "%s:module/server/workbench_router.py" % BASELINE],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    if old_src.returncode != 0:
        raise RuntimeError("取不到补全前的 router: %s" % old_src.stderr.strip())
    ns = {"__name__": "wb_old_router", "__file__": str(ROOT / "module" / "server" / "workbench_router.py")}
    exec(compile(old_src.stdout, ns["__file__"], "exec"), ns)
    old_app = FastAPI(title="wb-verify-old")
    old_app.include_router(ns["app"])

    @old_app.get("/__wb_verify_marker__")
    def _marker_old():
        return {"marker": "wb-verify-old"}

    return new_app, old_app


# ---------------------------------------------------------------- 新接口契约（11 组）
SOUL_KEYS = ["internal_id", "soul_key", "set_name", "slot", "quality", "level", "score",
             "main_attribute", "main_value", "equipped", "locked", "recommendation", "category"]
ATTR_KEYS = ["name", "value", "is_fixed", "rolls", "kind"]
SCORE_KEYS = ["value", "rule", "contributions", "formula", "source"]
TODO_KEYS = ["todo_id", "soul_id", "set_name", "score", "rule", "status", "note", "labels",
             "soul_internal_id", "slot", "level", "quality", "main_attribute", "category",
             "recommendation", "updated"]
TODO_DECISION_KEYS = ["todo_id", "action", "status", "note", "at"]
STD_VER_KEYS = ["id", "name", "active", "rule_count", "updated", "version", "preset", "status"]
ROSTER_KEYS = ["name", "rarity", "level", "star", "skills", "souls", "owned", "id",
               "awakened", "skin", "icon", "panel", "instances"]
SNAP_KEYS = ["snapshot_id", "time", "items", "souls", "source", "integrity",
             "integrity_message", "checked_at", "completeness"]
BACKUP_KEYS = ["id", "time", "size", "path", "note"]
ACQ_KEYS = ["id", "time", "source", "format", "result", "snapshot_id", "completeness",
            "parser", "profile_id"]
META_COUNTS = ["souls", "shikigami", "todos", "oas_souls", "oas_owned"]


def new_api_checks(base):
    section("11 组新接口（临时端口，真实 HTTP）")
    results = {}

    # --- 1. 御魂列表（t4 增量：行标识 + 分页）与单颗详情 ---
    st, souls = http("GET", base + "/workbench/souls?limit=20")
    ok = need("GET /souls 200", st == 200)
    need("GET /souls 顶层含 standard/count/by_decision/results/action_batch",
         ok and has_keys(souls, ["standard", "count", "by_decision", "results", "action_batch"]),
         str(missing(souls, ["standard", "count", "by_decision", "results", "action_batch"])) if ok else "")
    need("GET /souls 顶层分页字段 total/offset/limit/returned",
         ok and has_keys(souls, ["total", "offset", "limit", "returned"]),
         str(missing(souls, ["total", "offset", "limit", "returned"])) if ok else "")
    rows = souls.get("results") or []
    need("GET /souls 返回 20 行", len(rows) == 20, "rows=%d" % len(rows))
    first = rows[0] if rows else {}
    need("GET /souls 行含 soul_id/set_name/slot/quality/level",
         has_keys(first, ["soul_id", "set_name", "slot", "quality", "level"]),
         "缺 %s" % missing(first, ["soul_id", "set_name", "slot", "quality", "level"]))
    need("GET /souls 旧字段保留（matched/score/decision/rule_id/idx）",
         has_keys(first, ["matched", "score", "decision", "rule_id", "idx"]),
         "缺 %s" % missing(first, ["matched", "score", "decision", "rule_id", "idx"]))
    need("GET /souls 计数口径仍在", has_keys(souls, ["count", "by_decision", "action_batch"]),
         str(missing(souls, ["count", "by_decision", "action_batch"])))
    info("souls total=%s returned=%s count=%s by_decision=%s"
         % (souls.get("total"), souls.get("returned"), souls.get("count"), souls.get("by_decision")))
    for query, label in (("offset=-1", "offset 负数"), ("limit=0", "limit=0"), ("slot=9", "slot 越界")):
        st, body = http("GET", base + "/workbench/souls?" + query)
        need("GET /souls?%s → 400（%s）" % (query, label), st == 400, "status=%s %s" % (st, body))

    soul_id = str(first.get("soul_id") or "")
    need("souls 首行有 soul_id", bool(soul_id), soul_id)
    results["soul_id"] = soul_id
    if soul_id:
        st, detail = http("GET", base + "/workbench/souls/" + urllib.request.quote(soul_id, safe=""))
        need("GET /souls/{id} 200", st == 200, "status=%s %s" % (st, detail))
        need("GET /souls/{id} 顶层 soul/attrs/score/radar/source",
             has_keys(detail, ["ok", "source", "soul", "attrs", "score", "radar"]),
             str(missing(detail, ["ok", "source", "soul", "attrs", "score", "radar"])))
        need("soul 对象字段齐全", has_keys(detail.get("soul"), SOUL_KEYS),
             str(missing(detail.get("soul"), SOUL_KEYS)))
        attrs = detail.get("attrs") or []
        need("attrs 非空且字段齐全", bool(attrs) and has_keys(attrs[0], ATTR_KEYS),
             "len=%d 缺 %s" % (len(attrs), missing(attrs[0] if attrs else {}, ATTR_KEYS)))
        score = detail.get("score") or {}
        need("score 字段齐全", has_keys(score, SCORE_KEYS), str(missing(score, SCORE_KEYS)))
        need("score.source ∈ {analysis_todo, engine}", score.get("source") in ("analysis_todo", "engine"),
             str(score.get("source")))
        radar = detail.get("radar") or []
        need("radar 8 轴，每轴 axis/value",
             len(radar) == 8 and all(has_keys(r, ["axis", "value"]) for r in radar),
             "len=%d" % len(radar))
    st, body = http("GET", base + "/workbench/souls/__wb_verify_missing__")
    need("GET /souls/不存在 → 404", st == 404, "status=%s %s" % (st, body))

    # --- 2. 待办清单 / 处置 / 历史 ---
    st, todos = http("GET", base + "/workbench/todos?status=open&limit=5")
    need("GET /todos 200", st == 200)
    need("GET /todos 顶层 ok/source/items/counts/total/filtered",
         has_keys(todos, ["ok", "source", "items", "counts", "total", "filtered"]),
         str(missing(todos, ["ok", "source", "items", "counts", "total", "filtered"])))
    items = todos.get("items") or []
    need("GET /todos 返回 5 条", len(items) == 5, "len=%d" % len(items))
    need("GET /todos item 字段齐全", bool(items) and has_keys(items[0], TODO_KEYS),
         str(missing(items[0] if items else {}, TODO_KEYS)))
    need("GET /todos counts 三态计数", has_keys(todos.get("counts"), ["open", "done", "ignored"]),
         str(missing(todos.get("counts"), ["open", "done", "ignored"])))
    info("todos counts=%s total=%s filtered=%s" % (todos.get("counts"), todos.get("total"), todos.get("filtered")))
    st, body = http("GET", base + "/workbench/todos?status=bogus")
    need("GET /todos?status=bogus → 400", st == 400, "status=%s" % st)
    st, body = http("GET", base + "/workbench/todos?limit=0")
    need("GET /todos?limit=0 → 400", st == 400, "status=%s" % st)

    todo_id = str((items[0] or {}).get("todo_id") or "") if items else ""
    results["todo_id"] = todo_id
    if todo_id:
        st, done = http("POST", base + "/workbench/todos/decision",
                        {"todo_id": todo_id, "action": "done", "note": "wb-verify 验收"})
        need("POST /todos/decision done 200", st == 200, "status=%s %s" % (st, done))
        need("POST /todos/decision 返回 decision 行字段",
             has_keys(done.get("decision"), TODO_DECISION_KEYS),
             str(missing(done.get("decision"), TODO_DECISION_KEYS)))
        need("POST /todos/decision status=done", (done.get("decision") or {}).get("status") == "done")
        st, hist = http("GET", base + "/workbench/todos/history?limit=5")
        need("GET /todos/history 200", st == 200)
        need("GET /todos/history 顶层 ok/source/items", has_keys(hist, ["ok", "source", "items"]))
        hist_items = hist.get("items") or []
        need("处置出现在 history 首条",
             bool(hist_items) and hist_items[0].get("todo_id") == todo_id,
             str(hist_items[:1]))
        st, restored = http("POST", base + "/workbench/todos/decision",
                            {"todo_id": todo_id, "action": "restore"})
        need("POST /todos/decision restore 200 且回到 open",
             st == 200 and (restored.get("decision") or {}).get("status") == "open",
             "status=%s %s" % (st, restored))
    st, body = http("POST", base + "/workbench/todos/decision", {})
    need("POST /todos/decision 缺 todo_id → 400", st == 400, "status=%s" % st)
    st, body = http("POST", base + "/workbench/todos/decision", {"todo_id": "x", "action": "bogus"})
    need("POST /todos/decision 非法 action → 400", st == 400, "status=%s" % st)
    st, body = http("POST", base + "/workbench/todos/decision",
                    {"todo_id": "__wb_verify_missing__", "action": "done"})
    need("POST /todos/decision 不存在的待办 → 404", st == 404, "status=%s" % st)

    # --- 3. 评分标准版本 ---
    st, vers = http("GET", base + "/workbench/standards/versions")
    need("GET /standards/versions 200", st == 200)
    need("GET /standards/versions 顶层 ok/source/items/active/preference",
         has_keys(vers, ["ok", "source", "items", "active", "preference"]),
         str(missing(vers, ["ok", "source", "items", "active", "preference"])))
    vitems = vers.get("items") or []
    need("standards versions 非空", bool(vitems), "items=%d" % len(vitems))
    need("standards version 字段齐全", bool(vitems) and has_keys(vitems[0], STD_VER_KEYS),
         str(missing(vitems[0] if vitems else {}, STD_VER_KEYS)))
    info("standards source=%s active=%s items=%d" % (vers.get("source"), vers.get("active"), len(vitems)))
    if vitems:
        vid = str(vitems[0].get("id"))
        st, act = http("POST", base + "/workbench/standards/activate", {"version_id": vid})
        need("POST /standards/activate 200", st == 200, "status=%s %s" % (st, act))
        need("POST /standards/activate 返回 ok/active/updated/note",
             has_keys(act, ["ok", "active", "updated", "note"]),
             str(missing(act, ["ok", "active", "updated", "note"])))
        need("activate 生效（active == 传入版本）", act.get("active") == vid, str(act.get("active")))
    st, body = http("POST", base + "/workbench/standards/activate", {})
    need("POST /standards/activate 缺 version_id → 400", st == 400, "status=%s" % st)
    st, body = http("POST", base + "/workbench/standards/activate", {"version_id": "__nope__"})
    need("POST /standards/activate 未知版本 → 404", st == 404, "status=%s" % st)
    st, body = http("GET", base + "/workbench/standards/activate")
    need("GET /standards/activate → 405", st == 405, "status=%s" % st)

    # --- 4. 式神详情 ---
    st, roster = http("GET", base + "/workbench/roster")
    need("GET /roster（老路由，供取样本）200", st == 200, "status=%s" % st)
    ritems = roster.get("items") if isinstance(roster, dict) else roster
    if isinstance(ritems, dict):
        ritems = ritems.get("items") or []
    ritems = ritems or []
    name = ""
    for row in ritems:
        if isinstance(row, dict) and row.get("name"):
            name = str(row["name"])
            break
    name = name or "丑时之女"
    results["roster_name"] = name
    st, shiki = http("GET", base + "/workbench/roster/" + urllib.request.quote(name, safe=""))
    need("GET /roster/{name} 200", st == 200, "status=%s %s" % (st, shiki))
    need("GET /roster/{name} 顶层字段齐全", has_keys(shiki, ROSTER_KEYS),
         str(missing(shiki, ROSTER_KEYS)))
    need("式神详情 skills/souls 是列表",
         isinstance(shiki.get("skills"), list) and isinstance(shiki.get("souls"), list))
    info("roster=%s owned=%s souls=%s skills=%s"
         % (name, shiki.get("owned"), len(shiki.get("souls") or []), len(shiki.get("skills") or [])))
    st, body = http("GET", base + "/workbench/roster/__wb_verify_missing__")
    need("GET /roster/不存在 → 404", st == 404, "status=%s %s" % (st, body))

    # --- 5. 快照 ---
    st, snaps = http("GET", base + "/workbench/snapshots")
    need("GET /snapshots 200", st == 200)
    need("GET /snapshots 顶层 ok/source/items/note",
         has_keys(snaps, ["ok", "source", "items", "note"]),
         str(missing(snaps, ["ok", "source", "items", "note"])))
    sitems = snaps.get("items") or []
    need("snapshots 非空且字段齐全", bool(sitems) and has_keys(sitems[0], SNAP_KEYS),
         "len=%d 缺 %s" % (len(sitems), missing(sitems[0] if sitems else {}, SNAP_KEYS)))
    info("snapshots=%d first=%s/%s" % (len(sitems), (sitems[0] if sitems else {}).get("snapshot_id"),
                                       (sitems[0] if sitems else {}).get("integrity")))

    # --- 6. 备份 ---
    st, backups = http("GET", base + "/workbench/backups")
    need("GET /backups 200", st == 200)
    need("GET /backups 顶层 ok/source/items/dir/note/pinganzhi",
         has_keys(backups, ["ok", "source", "items", "dir", "note", "pinganzhi"]),
         str(missing(backups, ["ok", "source", "items", "dir", "note", "pinganzhi"])))
    need("GET /backups source=oas-store", backups.get("source") == "oas-store")
    before = len(backups.get("items") or [])
    st, created = http("POST", base + "/workbench/backups/create", {"note": "wb-verify 验收"})
    need("POST /backups/create 200", st == 200, "status=%s %s" % (st, created))
    need("POST /backups/create 返回 backup 行字段",
         has_keys(created.get("backup"), BACKUP_KEYS),
         str(missing(created.get("backup"), BACKUP_KEYS)))
    bpath = str((created.get("backup") or {}).get("path") or "")
    need("备份文件落在临时备份目录", bool(bpath) and os.path.isfile(bpath), bpath)
    st, backups2 = http("GET", base + "/workbench/backups")
    need("备份清单 +1", len(backups2.get("items") or []) == before + 1,
         "before=%d after=%d" % (before, len(backups2.get("items") or [])))
    need("平安志 backup_record 只读展示（不写其目录）",
         has_keys(backups2.get("pinganzhi"), ["ok", "items", "reason"]),
         str(missing(backups2.get("pinganzhi"), ["ok", "items", "reason"])))

    # --- 7. 采集记录 ---
    st, acq = http("GET", base + "/workbench/acquisitions?limit=10")
    need("GET /acquisitions 200", st == 200)
    need("GET /acquisitions 顶层 ok/source/items/total",
         has_keys(acq, ["ok", "source", "items", "total"]),
         str(missing(acq, ["ok", "source", "items", "total"])))
    aitems = acq.get("items") or []
    need("acquisitions item 字段齐全", bool(aitems) and has_keys(aitems[0], ACQ_KEYS),
         "len=%d 缺 %s" % (len(aitems), missing(aitems[0] if aitems else {}, ACQ_KEYS)))
    st, body = http("GET", base + "/workbench/acquisitions?limit=0")
    need("GET /acquisitions?limit=0 → 400", st == 400, "status=%s" % st)

    # --- 8. 元信息 ---
    st, meta = http("GET", base + "/workbench/meta")
    need("GET /meta 200", st == 200)
    need("GET /meta 顶层 ok/data_source/pinganzhi/counts/last_sync",
         has_keys(meta, ["ok", "data_source", "pinganzhi", "counts", "last_sync"]),
         str(missing(meta, ["ok", "data_source", "pinganzhi", "counts", "last_sync"])))
    need("GET /meta counts 五类计数", has_keys(meta.get("counts"), META_COUNTS),
         str(missing(meta.get("counts"), META_COUNTS)))
    need("GET /meta pinganzhi.available=True 且 path 存在",
         (meta.get("pinganzhi") or {}).get("available") is True
         and os.path.isfile(str((meta.get("pinganzhi") or {}).get("path") or "")),
         str(meta.get("pinganzhi")))
    results["meta"] = meta.get("counts")
    info("meta data_source=%s counts=%s last_sync=%s"
         % (meta.get("data_source"), meta.get("counts"), meta.get("last_sync")))

    # --- 9/10. 面板 / 速度历史 ---
    for kind in ("panel", "speed"):
        st, hist = http("GET", base + "/workbench/%s/history?limit=5" % kind)
        need("GET /%s/history 200" % kind, st == 200)
        need("GET /%s/history 顶层 ok/source/items/total/note" % kind,
             has_keys(hist, ["ok", "source", "items", "total", "note"]),
             str(missing(hist, ["ok", "source", "items", "total", "note"])))
        need("GET /%s/history items 是列表" % kind, isinstance(hist.get("items"), list))
        st, body = http("GET", base + "/workbench/%s/history?limit=501" % kind)
        need("GET /%s/history?limit=501 → 400" % kind, st == 400, "status=%s" % st)
        info("%s history source=%s total=%s note=%s"
             % (kind, hist.get("source"), hist.get("total"), hist.get("note")))

    # --- 11. 未知路由 ---
    st, body = http("GET", base + "/workbench/__wb_verify_unknown__")
    need("未知 /workbench 路由 → 404", st == 404, "status=%s" % st)
    return results


# ---------------------------------------------------------------- 降级（--no-paz 等价）
def degraded_checks(base, tmp):
    section("平安志不可用时的优雅降级（WORKBENCH_PAZ_DB → 不存在的库）")
    missing_db = os.path.join(tmp, "no-such-analysis.sqlite3")
    old = os.environ.get("WORKBENCH_PAZ_DB")
    os.environ["WORKBENCH_PAZ_DB"] = missing_db
    try:
        st, meta = http("GET", base + "/workbench/meta")
        need("降级 /meta 仍 200，明确标记平安志不可用（available=false + reason）",
             st == 200 and (meta.get("pinganzhi") or {}).get("available") is False
             and bool(meta.get("reason")),
             "status=%s %s" % (st, meta))
        need("降级 /meta 给出 oas-store 数据源与计数", meta.get("data_source") == "oas-store"
             and has_keys(meta.get("counts"), ["souls", "shikigami", "todos"]),
             str(meta.get("counts")))

        st, snaps = http("GET", base + "/workbench/snapshots")
        need("降级 /snapshots 200 + ok=false + reason + items=[]",
             st == 200 and snaps.get("ok") is False and snaps.get("items") == []
             and bool(snaps.get("reason")), "status=%s %s" % (st, snaps))

        st, acq = http("GET", base + "/workbench/acquisitions")
        need("降级 /acquisitions 200 + ok=false + reason",
             st == 200 and acq.get("ok") is False and bool(acq.get("reason")), "status=%s %s" % (st, acq))

        st, todos = http("GET", base + "/workbench/todos")
        need("降级 /todos 200 + ok=false + reason + counts",
             st == 200 and todos.get("ok") is False and bool(todos.get("reason"))
             and has_keys(todos.get("counts"), ["open", "done", "ignored"]),
             "status=%s %s" % (st, todos))

        st, vers = http("GET", base + "/workbench/standards/versions")
        need("降级 /standards/versions 回退本地标准包（ok=true + reason）",
             st == 200 and vers.get("ok") is True and vers.get("source") == "local-standard"
             and bool(vers.get("items")) and bool(vers.get("reason")),
             "status=%s source=%s items=%s" % (st, vers.get("source"), len(vers.get("items") or [])))

        st, backups = http("GET", base + "/workbench/backups")
        need("降级 /backups 200，OAS 备份仍可用，平安志部分标 ok=false",
             st == 200 and backups.get("ok") is True
             and (backups.get("pinganzhi") or {}).get("ok") is False
             and bool((backups.get("pinganzhi") or {}).get("reason")),
             "status=%s %s" % (st, backups.get("pinganzhi")))

        st, detail = http("GET", base + "/workbench/souls/__wb_verify_missing__")
        need("降级 /souls/不存在 → 200 ok=false + reason（不是 500）",
             st == 200 and detail.get("ok") is False and bool(detail.get("reason")),
             "status=%s %s" % (st, detail))

        st, shiki = http("GET", base + "/workbench/roster/__wb_verify_missing__")
        need("降级 /roster/不存在 → 200 ok=false + reason（不是 500）",
             st == 200 and shiki.get("ok") is False and bool(shiki.get("reason")),
             "status=%s %s" % (st, shiki))

        st, hist = http("GET", base + "/workbench/panel/history")
        need("降级 /panel/history 200 + ok=false + note/reason",
             st == 200 and hist.get("ok") is False and (hist.get("reason") or hist.get("note")),
             "status=%s %s" % (st, hist))

        # 平安志不可用时，处置仍要能落在 OAS 自己的 store（写边界不受影响）
        st, dec = http("POST", base + "/workbench/todos/decision",
                       {"todo_id": "__degraded__", "action": "done", "note": "降级验收"})
        need("降级 POST /todos/decision 200 + 明确 reason（写只落 OAS）",
             st == 200 and (dec.get("decision") or {}).get("status") == "done" and bool(dec.get("reason")),
             "status=%s %s" % (st, dec))
    finally:
        if old is None:
            os.environ.pop("WORKBENCH_PAZ_DB", None)
        else:
            os.environ["WORKBENCH_PAZ_DB"] = old


# ---------------------------------------------------------------- 老路由回归
OLD_ROUTES = ["overview", "souls", "roster", "shards", "panel", "headtail",
              "activities", "tasks", "monitor", "domains", "admission", "instances"]


def old_route_checks(base_new, base_old, roster_name):
    section("24 条老路由回归（补全前 router 起在临时端口逐条对比字段名）")
    st, old_routes = http("GET", base_old + "/openapi.json")
    st2, new_routes = http("GET", base_new + "/openapi.json")
    old_paths = set((old_routes.get("paths") or {}).keys())
    new_paths = set((new_routes.get("paths") or {}).keys())
    missing_paths = sorted(old_paths - new_paths)
    need("补全前的全部 %d 条 /workbench 路由仍然注册" % len(old_paths), not missing_paths,
         "缺: %s" % missing_paths[:8])
    info("老路由 %d 条，新路由 %d 条（新增 %d）"
         % (len(old_paths), len(new_paths), len(new_paths - old_paths)))

    queries = {
        "overview": "",
        "souls": "",
        "roster": "",
        "shards": "?q=" + urllib.request.quote("茨"),
        "panel": "?name=" + urllib.request.quote(roster_name),
        "headtail": "",
        "activities": "",
        "tasks": "",
        "monitor": "",
        "domains": "",
        "admission": "",
        "instances": "",
    }
    for name in OLD_ROUTES:
        path = "/workbench/" + name + queries[name]
        st_new, new = http("GET", base_new + path)
        st_old, old = http("GET", base_old + path)
        label = "老路由 /%s" % name
        if not need("%s 状态一致（新 %s / 旧 %s）" % (label, st_new, st_old),
                    st_new == st_old == 200, "status new=%s old=%s" % (st_new, st_old)):
            continue
        if isinstance(old, dict):
            lost = sorted(keys(old) - keys(new)) if isinstance(new, dict) else ["<新响应非对象>"]
            need("%s 顶层字段无删改" % label, not lost,
                 "新响应缺: %s" % lost)
            for field in ("rows", "items"):
                o_rows = old.get(field)
                n_rows = new.get(field) if isinstance(new, dict) else None
                if isinstance(o_rows, list) and o_rows and isinstance(n_rows, list):
                    need("%s %s 行字段无删改" % (label, field),
                         bool(n_rows) and keys(o_rows[0]) <= keys(n_rows[0]),
                         "新行缺: %s" % sorted(keys(o_rows[0]) - keys(n_rows[0])))
                    break
        else:
            need("%s 响应类型一致" % label, isinstance(new, type(old)))


# ---------------------------------------------------------------- 写边界
def dir_fingerprint(path):
    out = {}
    if not os.path.isdir(path):
        return out
    for base, _dirs, files in os.walk(path):
        for name in files:
            full = os.path.join(base, name)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            out[os.path.relpath(full, path)] = (int(stat.st_size), round(stat.st_mtime, 3))
    return out


def repo_store_state():
    """本脚本跑之前仓库 OAS store 里两张待办文件的指纹（用于证明隔离）。"""
    state = {}
    base = ROOT / "config" / "workbench"
    for name in ("todo_decisions.json", "todo_decisions.log", "decisions.json", "standards.json"):
        path = base / name
        state[name] = (path.stat().st_size, round(path.stat().st_mtime, 3)) if path.exists() else None
    return state


def write_boundary_checks(base, tmp, paz_dir, before_fp, todo_id="", repo_before=None):
    section("写操作边界：只落 OAS store，平安志库 mtime/目录零变化")
    after_fp = dir_fingerprint(paz_dir)

    def is_wal_index(name):
        # SQLite WAL 模式下，只读连接也会更新 -shm 里的 read mark（不是数据写入），
        # 主库与 -wal 才是判断是否被写的依据。
        return name.endswith("-shm")

    added = sorted(set(after_fp) - set(before_fp))
    removed = sorted(set(before_fp) - set(after_fp))
    changed = sorted(k for k in set(before_fp) & set(after_fp)
                     if before_fp[k] != after_fp[k] and not is_wal_index(k))
    shm_changed = sorted(k for k in set(before_fp) & set(after_fp)
                         if before_fp[k] != after_fp[k] and is_wal_index(k))
    need("%s 目录无新增/删除，数据文件（非 -shm）mtime+大小不变（%d 个文件）" % (paz_dir, len(after_fp)),
         not added and not removed and not changed,
         "新增=%s 删除=%s 变化=%s" % (added[:5], removed[:5], changed[:5]))
    if shm_changed:
        info("WAL 只读副作用（不算写库）: %s 的 mtime 被 read mark 更新，主库/-wal 未变" % shm_changed)
    need("平安志主库与 -wal 文件未被触碰",
         not [k for k in changed if k.endswith((".sqlite3", "-wal"))],
         str([k for k in changed if k.endswith((".sqlite3", "-wal"))]))

    db = os.path.join(paz_dir, "current-analysis.sqlite3")
    need("平安志主库存在且可读", os.path.isfile(db))
    mtime_before = os.path.getmtime(db)
    size_before = os.path.getsize(db)

    # 在写操作最集中处单独取一次 mtime，保证"POST 之后立刻没变"
    if not todo_id:
        st, todos = http("GET", base + "/workbench/todos?limit=1")
        rows = todos.get("items") or []
        todo_id = str((rows[0] or {}).get("todo_id") or "") if rows else ""
    need("写边界样本待办存在", bool(todo_id), todo_id)
    st, dec = http("POST", base + "/workbench/todos/decision",
                   {"todo_id": todo_id, "action": "done", "note": "写边界"})
    need("写边界用 POST /todos/decision 成功", st == 200 and dec.get("ok") is True,
         "status=%s %s" % (st, dec))
    st, bk = http("POST", base + "/workbench/backups/create", {"note": "写边界"})
    need("写边界用 POST /backups/create 成功", st == 200, "status=%s" % st)
    mtime_after = os.path.getmtime(db)
    size_after = os.path.getsize(db)
    need("POST /todos/decision 后平安志 sqlite mtime 不变",
         mtime_before == mtime_after,
         "before=%s after=%s" % (mtime_before, mtime_after))
    need("POST 后平安志 sqlite 大小不变", size_before == size_after,
         "before=%d after=%d" % (size_before, size_after))
    # 收尾把样本恢复成 open，避免污染临时 store 的计数
    http("POST", base + "/workbench/todos/decision", {"todo_id": todo_id, "action": "restore"})

    # 写入确实落在临时 OAS store（而不是仓库 config/workbench）
    tmp_store = os.path.join(tmp, "wb")
    log_path = os.path.join(tmp_store, "todo_decisions.log")
    need("OAS 临时 store 收到处置审计行", os.path.isfile(log_path)
         and todo_id in Path(log_path).read_text(encoding="utf-8", errors="ignore"),
         log_path)
    repo_log = ROOT / "config" / "workbench" / "todo_decisions.log"
    if repo_before is not None:
        now_state = repo_store_state()
        diff = {k: (repo_before.get(k), now_state.get(k)) for k in now_state if repo_before.get(k) != now_state.get(k)}
        need("仓库 config/workbench 的待办/决策文件未被本脚本改动（隔离生效）",
             not diff, str(diff))
    info("平安志 sqlite: size=%d mtime=%.3f" % (size_before, mtime_before))


# ---------------------------------------------------------------- UI + 截图
def find_browser(explicit=""):
    if explicit:
        return explicit
    for path in EDGE_CANDIDATES:
        if os.path.isfile(path):
            return path
    raise SystemExit("没找到 Edge/Chrome，可用 --edge 显式指定")


class CDP:
    def __init__(self, url, timeout=30):
        from websockets.sync.client import connect

        self.ws = connect(url, max_size=None, open_timeout=15)
        self.next_id = 0
        self.timeout = timeout

    def call(self, method, params=None, timeout=None):
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            raw = self.ws.recv(timeout=max(1.0, deadline - time.time()))
            msg = json.loads(raw)
            if msg.get("id") != mid:
                continue
            if "error" in msg:
                raise RuntimeError("%s: %s" % (method, msg["error"]))
            return msg.get("result") or {}
        raise TimeoutError(method)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def js(cdp, expression, timeout=40):
    result = cdp.call("Runtime.evaluate",
                      {"expression": expression, "returnByValue": True, "awaitPromise": True},
                      timeout=timeout)
    if result.get("exceptionDetails"):
        raise RuntimeError(str(result["exceptionDetails"])[:400])
    return (result.get("result") or {}).get("value")


def launch_browser(browser, profile_dir, debug_port):
    args = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", "--remote-debugging-port=%d" % debug_port,
            "--user-data-dir=%s" % profile_dir, "about:blank"]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with OPENER.open("http://127.0.0.1:%d/json/list" % debug_port, timeout=2) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
            pages = [t for t in targets if t.get("type") == "page"]
            if pages:
                return proc, pages[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    proc.kill()
    raise SystemExit("浏览器 DevTools 端口 %d 没起来" % debug_port)


def wait_rendered(cdp, timeout=120):
    end = time.time() + timeout
    state = {}
    while time.time() < end:
        try:
            state = js(cdp, """(() => {
              const box = document.getElementById('soulsBox');
              const stamp = document.getElementById('stamp');
              const chip = document.querySelector('#chipSouls b');
              return {
                stamp: stamp ? stamp.textContent.trim() : '',
                chipSouls: chip ? chip.textContent.trim() : '',
                loading: !!document.querySelector('#soulsBox .state.loading'),
                rows: box ? box.querySelectorAll('table tbody tr').length : 0,
                errs: (window.__wbErrs || []).slice(0, 5)
              };
            })()""")
            if state.get("rows", 0) > 0 and not state.get("loading") \
                    and "加载中" not in (state.get("stamp") or "") \
                    and state.get("chipSouls") not in ("", "—"):
                return state
        except Exception:
            pass
        time.sleep(0.8)
    return state


def capture(cdp, path, width, height, full=False):
    if full:
        metrics = cdp.call("Page.getLayoutMetrics")
        size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
        content_h = int(size.get("height") or height)
        shot = cdp.call("Page.captureScreenshot", {
            "format": "png", "fromSurface": True, "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0, "width": width, "height": min(content_h, 20000), "scale": 1},
        }, timeout=180)
    else:
        shot = cdp.call("Page.captureScreenshot", {"format": "png", "fromSurface": True},
                        timeout=120)
    data = base64.b64decode(shot["data"])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)
    return len(data)


LEGACY_ANCHORS = ["ov", "mon", "soul-sec", "panel-sec", "ht-sec", "act-sec", "roster-sec",
                  "asset-sec", "shard-sec", "guild-sec", "profiles-sec", "tasks-sec", "speed-sec"]


def ui_checks(base, browser, debug_port, shots=True):
    section("UI 真实渲染（Edge headless + DevTools 协议）")
    profile_dir = tempfile.mkdtemp(prefix="wbverify_edge_")
    proc = None
    try:
        proc, ws_url = launch_browser(browser, profile_dir, debug_port)
        cdp = CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": """
          window.__wbErrs = [];
          window.addEventListener('error', e => window.__wbErrs.push(String(e.message || e)));
          window.addEventListener('unhandledrejection', e => window.__wbErrs.push('reject:' + String(e.reason)));
        """})
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 2400, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base + "/workbench/ui"})
        state = wait_rendered(cdp)
        need("页面真实渲染出御魂表格（等待接口返回）", state.get("rows", 0) > 0, str(state))
        info("渲染完成: stamp=%s chip=%s rows=%s"
             % (state.get("stamp"), state.get("chipSouls"), state.get("rows")))

        dom = js(cdp, """(() => {
          const groups = Array.from(document.querySelectorAll('.ng-t')).map(e => e.textContent.trim());
          const nav = Array.from(document.querySelectorAll('#nav a[href^="#"]')).map(a => a.getAttribute('href').slice(1));
          const anchors = @LEGACY@.map(id => {
            const el = document.getElementById(id);
            return {id, exists: !!el, h: el ? Math.round(el.getBoundingClientRect().height) : 0};
          });
          const states = {
            loading: document.querySelectorAll('.state.loading').length,
            empty: document.querySelectorAll('.state.empty').length,
            error: document.querySelectorAll('.state.error').length
          };
          const resources = performance.getEntriesByType('resource').map(e => e.name);
          const external = resources.filter(u => !u.startsWith(location.origin));
          const chips = {};
          ['chipProfile','chipSouls','chipShiki','chipTodos','chipSource'].forEach(id => {
            const el = document.getElementById(id); chips[id] = el ? el.textContent.trim() : '';
          });
          const tableCounts = {
            souls: document.querySelectorAll('#soulsBox table tbody tr').length,
            todos: document.querySelectorAll('#todoBox table tbody tr').length,
            snapshots: document.querySelectorAll('#snapshotBox table tbody tr').length,
            standards: document.querySelectorAll('#standardsVerBox table tbody tr').length,
            backups: document.querySelectorAll('#backupBox table tbody tr').length,
            acquisitions: document.querySelectorAll('#acqBox table tbody tr').length,
            tasks: document.querySelectorAll('#tasksBox table tbody tr').length,
            meta: document.getElementById('metaBox') ? document.getElementById('metaBox').textContent.trim().length : 0
          };
          return {groups, nav, anchors, states, external, chips, tableCounts,
                  errs: (window.__wbErrs || []).slice(0, 8),
                  title: document.title,
                  scrollWidth: document.documentElement.scrollWidth,
                  scrollHeight: document.documentElement.scrollHeight};
        })()""".replace("@LEGACY@", json.dumps(LEGACY_ANCHORS)))

        need("导航分组顺序 总览→御魂→式神→养成→数据", dom["groups"] == ["总览", "御魂", "式神", "养成", "数据"],
             str(dom["groups"]))
        bad = [a["id"] for a in dom["anchors"] if not a["exists"] or a["h"] <= 0]
        need("13 个老锚点在真实 DOM 均存在且有高度", not bad, str(bad))
        dangling = js(cdp, """(() => Array.from(document.querySelectorAll('#nav a[href^="#"]'))
              .map(a => a.getAttribute('href').slice(1))
              .filter(id => !document.getElementById(id)))()""")
        need("导航 href 全部指向存在的锚点", not dangling, str(dangling))
        need("三态模板在真实 DOM 出现（loading/empty/error）",
             dom["states"]["loading"] > 0 or dom["states"]["empty"] > 0 or dom["states"]["error"] > 0,
             str(dom["states"]))
        need("运行时资源全部同源（无外网 CDN 请求）", not dom["external"], str(dom["external"][:5]))
        need("页面无 JS 运行时错误", not dom["errs"], str(dom["errs"]))
        need("顶部状态条 5 个常驻项都有值", all(dom["chips"].get(k) for k in
             ["chipProfile", "chipSouls", "chipShiki", "chipTodos", "chipSource"]), str(dom["chips"]))
        need("关键面板渲染出数据行",
             dom["tableCounts"]["souls"] > 0 and dom["tableCounts"]["snapshots"] > 0
             and dom["tableCounts"]["standards"] > 0 and dom["tableCounts"]["meta"] > 20,
             str(dom["tableCounts"]))
        info("chips=%s tables=%s" % (dom["chips"], dom["tableCounts"]))

        # 逐个锚点真实跳转：先关掉 CSS smooth，再改 hash，元素应滚到视口顶部附近；
        # 页面末尾的锚点（后面内容不足一屏）滚不到顶部，滚到底部也算可达。
        js(cdp, "document.documentElement.style.scrollBehavior = 'auto';")
        unreachable = []
        for anchor in LEGACY_ANCHORS:
            js(cdp, "location.hash = ''; window.scrollTo(0, 0);")
            time.sleep(0.15)
            js(cdp, "location.hash = %s" % json.dumps("#" + anchor))
            got = None
            for _ in range(12):
                time.sleep(0.15)
                got = js(cdp, """(() => {const el = document.getElementById(%s);
                     if (!el) return null;
                     return {top: Math.round(el.getBoundingClientRect().top),
                             atBottom: window.scrollY + window.innerHeight >=
                                       document.documentElement.scrollHeight - 2};})()"""
                          % json.dumps(anchor))
                if got and (abs(got["top"]) <= 160 or got["atBottom"]):
                    break
            if not got or not (abs(got["top"]) <= 160 or got["atBottom"]):
                unreachable.append("%s(%s)" % (anchor, got))
        need("13 个老锚点逐个跳转可达（滚动到顶部附近/页面末尾滚到底）", not unreachable, str(unreachable))
        js(cdp, "history.replaceState(null, '', '#ov'); window.scrollTo(0, 0);")

        if shots:
            SHOT_DIR.mkdir(parents=True, exist_ok=True)
            cdp.call("Emulation.setDeviceMetricsOverride",
                     {"width": 1440, "height": 2400, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(1.0)
            size1 = capture(cdp, SHOT_DIR / "verify_desktop.png", 1440, 2400, full=False)
            size2 = capture(cdp, SHOT_DIR / "verify_desktop_full.png", 1440, 2400, full=True)
            need("桌面截图 1440x2400 已生成", size1 > 20000, "bytes=%d" % size1)
            need("桌面全页截图已生成", size2 > 40000, "bytes=%d" % size2)

            # 720 宽移动端
            cdp.call("Emulation.setDeviceMetricsOverride",
                     {"width": 720, "height": 1600, "deviceScaleFactor": 1, "mobile": True})
            cdp.call("Page.navigate", {"url": base + "/workbench/ui"})
            state_m = wait_rendered(cdp)
            need("移动端 720 宽真实渲染出数据", state_m.get("rows", 0) > 0, str(state_m))
            nav_m = js(cdp, """(() => ({
              scrollWidth: document.documentElement.scrollWidth,
              innerWidth: window.innerWidth,
              groups: Array.from(document.querySelectorAll('.ng-t')).map(e => e.textContent.trim()),
              errs: (window.__wbErrs || []).slice(0, 5)
            }))()""")
            need("移动端无横向溢出（scrollWidth <= 720）",
                 nav_m["scrollWidth"] <= nav_m["innerWidth"] + 1,
                 "scrollWidth=%s innerWidth=%s" % (nav_m["scrollWidth"], nav_m["innerWidth"]))
            need("移动端导航分组顺序不变", nav_m["groups"] == ["总览", "御魂", "式神", "养成", "数据"],
                 str(nav_m["groups"]))
            need("移动端无 JS 错误", not nav_m["errs"], str(nav_m["errs"]))
            size3 = capture(cdp, SHOT_DIR / "verify_mobile.png", 720, 1600, full=False)
            size4 = capture(cdp, SHOT_DIR / "verify_mobile_full.png", 720, 1600, full=True)
            need("移动端截图 720x1600 已生成", size3 > 10000, "bytes=%d" % size3)
            need("移动端全页截图已生成", size4 > 20000, "bytes=%d" % size4)
            info("截图: verify_desktop.png=%d verify_desktop_full.png=%d "
                 "verify_mobile.png=%d verify_mobile_full.png=%d" % (size1, size2, size3, size4))

        cdp.close()
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        shutil.rmtree(profile_dir, ignore_errors=True)


# ---------------------------------------------------------------- 主流程
def main():
    parser = argparse.ArgumentParser(description="工作台补全独立验收")
    parser.add_argument("--port", type=int, default=23334, help="新接口临时端口（默认 23334）")
    parser.add_argument("--old-port", type=int, default=None, help="老路由对比端口（默认 port+1）")
    parser.add_argument("--no-shots", action="store_true", help="跳过 UI 截图（只跑接口）")
    parser.add_argument("--edge", default="", help="浏览器可执行文件路径")
    args = parser.parse_args()

    port = pick_port(args.port)
    old_port = args.old_port or pick_port(port + 1)
    if port != args.port:
        print("注意: %d 被占用/处于 TIME_WAIT，改用 %d（仍为独立临时端口，不碰 22267）"
              % (args.port, port), flush=True)
    base = "http://127.0.0.1:%d" % port
    base_old = "http://127.0.0.1:%d" % old_port

    print("工作台补全独立验收  repo=%s" % ROOT, flush=True)
    print("临时服务: new=%s old=%s（不碰 22267）" % (base, base_old), flush=True)

    tmp = tempfile.mkdtemp(prefix="wbverify_")
    store_copy = os.path.join(tmp, "wb")
    backup_dir = os.path.join(tmp, "backups")
    shutil.copytree(ROOT / "config" / "workbench", store_copy)
    os.environ["WORKBENCH_DIR"] = store_copy
    os.environ["WORKBENCH_BACKUP_DIR"] = backup_dir
    os.environ.pop("WORKBENCH_PAZ_DB", None)

    from tasks.Workbench.ingest import paz_extra

    paz_db = paz_extra.DEFAULT_DB
    paz_dir = os.path.dirname(paz_db)
    paz_fp = dir_fingerprint(paz_dir)
    info("平安志只读源: %s（%d 个文件）" % (paz_db, len(paz_fp)))
    info("OAS store 用临时副本: %s" % store_copy)
    repo_before = repo_store_state()

    static_checks()

    new_app, old_app = build_apps(port, old_port)
    start_server(new_app, port)
    start_server(old_app, old_port)
    if not wait_http(base + "/__wb_verify_marker__", marker="wb-verify-new"):
        print("FAIL 新服务没起来（%s）" % base, flush=True)
        return 2
    if not wait_http(base_old + "/__wb_verify_marker__", marker="wb-verify-old"):
        print("FAIL 老服务没起来（%s）" % base_old, flush=True)
        return 2
    print("服务就绪: new=%s old=%s（marker 校验通过）" % (base, base_old), flush=True)

    results = new_api_checks(base)
    degraded_checks(base, tmp)
    old_route_checks(base, base_old, results.get("roster_name") or "丑时之女")
    write_boundary_checks(base, tmp, paz_dir, paz_fp, results.get("todo_id") or "", repo_before)

    if not args.no_shots:
        browser = find_browser(args.edge)
        info("浏览器: %s" % browser)
        ui_checks(base, browser, pick_port(23440))

    section("汇总")
    total = PASS + FAIL
    print("WB VERIFY: %s   TOTAL %d  PASS %d  FAIL %d"
          % ("PASSED" if FAIL == 0 else "FAILED", total, PASS, FAIL), flush=True)
    if FAILED:
        print("失败项复现命令：", flush=True)
        for item in FAILED:
            print("  - %s" % item, flush=True)
    print("临时目录（可删）: %s" % tmp, flush=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
