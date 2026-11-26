# This file uses the following encoding: utf-8
"""Workbench WebUI 接口。

只读接口，全部走本地文件与纯计算，不触碰设备进程：
  GET  /workbench/overview      首页卡片（档案/式神/资产/任务/监控/寮）
  GET  /workbench/souls         御魂列表 + 评分
  GET  /workbench/roster        式神录
  GET  /workbench/shards?q=     碎片查询
  GET  /workbench/panel?name=   面板计算（可带 slot 过滤）
  GET  /workbench/headtail      头/尾/脚排名
  GET  /workbench/tasks         任务记录
  GET  /workbench/monitor       监控时间线
  GET  /workbench/instances     多开实例（快速枚举）
  POST /workbench/decision      记录一条保留/清理决策
"""
import json
import math
import os
import subprocess
import time

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Body, Query
from fastapi.responses import HTMLResponse, JSONResponse

from tasks.Workbench import features as F
from tasks.Workbench import tools as T
from tasks.Workbench.engine import Catalog, Standards, normalize_soul
from tasks.Workbench.store import Store

app = APIRouter(prefix="/workbench")

# 仓库根目录：module/server/workbench_router.py -> 上溯两层
REPO_ROOT = Path(__file__).resolve().parents[2]

_CATALOG = None


def _default_serial() -> str:
    """取当前配置里的设备 serial（第一个即 oas 用的那台）。"""
    try:
        from module.config.config import Config  # 延迟导入，避免无配置时报错
        cfg = Config("oas3")
        serials = cfg.model.script.device.serial
        return serials[0] if isinstance(serials, (list, tuple)) and serials else str(serials)
    except Exception:
        return ""


def _root_store() -> Store:
    """显式给出档案目录。

    不能用相对路径：导入 module.server 子树会把进程 cwd 改到仓库根
    （tool.py 里 PROJECT_ROOT 取 Path.cwd()），相对路径会在测试/多入口下飘。
    WORKBENCH_DIR 允许测试指向临时目录。
    """
    base = os.environ.get("WORKBENCH_DIR") or str(REPO_ROOT / "config" / "workbench")
    return Store(base)


def _store() -> Store:
    return _root_store().for_profile()


def catalog() -> Catalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = Catalog()
    return _CATALOG


def _ok(payload):
    return JSONResponse(content=json.loads(json.dumps(payload, ensure_ascii=False, default=str)))


def _err(exc):
    return JSONResponse(status_code=500,
                        content={"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})


def _bad(message, status: int = 400):
    return JSONResponse(status_code=status, content={"ok": False, "error": str(message)})


UI_FILE = Path(__file__).resolve().parent / "web" / "workbench" / "index.html"


@app.get("/ui", response_class=HTMLResponse)
def ui():
    """工作台页面（单文件，无构建步骤）。"""
    try:
        return HTMLResponse(UI_FILE.read_text(encoding="utf-8"))
    except OSError as exc:
        return HTMLResponse("<h3>工作台页面缺失: %s</h3>" % exc, status_code=500)


ASSET_DIR = Path(REPO_ROOT) / "config" / "workbench"
ASSET_REQUEST = ASSET_DIR / "asset_request.json"
ASSET_QUEUE = ASSET_DIR / "asset_queue.json"


def _asset_store():
    from tasks.Workbench import asset_store as _S

    return _S


def _asset_state(config: str) -> dict:
    try:
        return _asset_store().load(ASSET_DIR, config)
    except Exception:
        return {}


def _config_serial(config: str) -> str:
    try:
        cfg = json.loads((Path(REPO_ROOT) / "config" / f"{config}.json").read_text(encoding="utf-8"))
        serials = cfg["script"]["device"]["serial"]
        return serials if isinstance(serials, str) else (serials[0] if serials else "")
    except Exception:
        return ""


def _known_configs() -> list:
    return sorted(p.stem for p in (Path(REPO_ROOT) / "config").glob("oas*.json"))


@app.get("/assets/configs")
def assets_configs():
    """列出有资产快照的配置 + 各自实例，供客户端切换时对齐。"""
    out = []
    for cfg in _known_configs():
        st = _asset_state(cfg)
        out.append({"config": cfg, "serial": _config_serial(cfg),
                    "has_snapshot": bool(st.get("items")),
                    "status": st.get("status"), "profile": st.get("profile"),
                    "captured_at": st.get("captured_at")})
    return _ok({"ok": True, "configs": out})


@app.get("/assets/live")
def assets_live(config: str = ""):
    config = (config or "").strip()
    if not config:
        return _bad("需要 config")
    state = _asset_state(config)
    if not state.get("items"):
        return _bad(f"{config} 还没有资产快照", 404)
    queue = {}
    try:
        if ASSET_QUEUE.exists():
            queue = json.loads(ASSET_QUEUE.read_text(encoding="utf-8"))
    except Exception:
        queue = {}
    if queue.get("config") not in (None, config):
        queue = {}
    return _ok({"ok": True, "state": state, "queue": queue,
                "serial_for_config": _config_serial(config),
                "supported": _asset_store().supported_summary()})


_ASSET_WATCH_AT = 0.0


def _ensure_asset_watch() -> bool:
    """刷新兜底：资产守护没在跑就先拉起（重启电脑后第一次点「刷新」也能工作）。

    只在每次请求时做一次很轻的进程检查；拉起脚本本身是幂等的（dev_tools/start_asset_watch.ps1，
    同时负责页采集守护 / 实时帧守护 / 平安志）。60 秒内最多尝试一次，避免刷屏。
    """
    global _ASSET_WATCH_AT
    now = time.time()
    if now - _ASSET_WATCH_AT < 60:
        return False
    _ASSET_WATCH_AT = now
    script = Path(REPO_ROOT) / "dev_tools" / "start_asset_watch.ps1"
    if not script.exists():
        return False
    try:
        check = ("(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                 "Where-Object { $_.CommandLine -like '*asset_collect_daemon.py*' } | "
                 "Measure-Object).Count")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", check],
                             capture_output=True, text=True, timeout=15)
        if (out.stdout or "0").strip() not in ("", "0"):
            return False
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-File", str(script), "-Quiet"],
                         cwd=str(REPO_ROOT), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False


@app.post("/assets/collect")
def assets_collect(payload: dict = Body(default={})):
    payload = payload or {}
    config = str(payload.get("config") or "").strip()
    if not config:
        return _bad("需要 config（例如 oas3）")
    if not (Path(REPO_ROOT) / "config" / f"{config}.json").exists():
        return _bad(f"未知配置: {config}", 404)
    serial = _config_serial(config)
    _ensure_asset_watch()
    try:
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        ASSET_REQUEST.write_text(json.dumps({
            "config": config, "serial": serial,
            "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            # 手动点「刷新」= 要求真读一次游戏（不只重读快照）；后台按 mode 强制让平安志重采
            "mode": str(payload.get("mode") or "manual"),
            "by": payload.get("by") or "oasx"}, ensure_ascii=False), encoding="utf-8")
        return _ok({"ok": True, "queued": True, "config": config, "serial": serial})
    except Exception as exc:
        return _err(exc)


@app.get("/overview")
def overview(config: str = ""):
    try:
        data = F.overview(_store(), catalog=catalog())
        config = (config or "").strip()
        if config:
            state = _asset_state(config)
            cur = _asset_store().currencies(state) if state.get("items") else {}
            assets = data.setdefault("assets", {})
            if cur:
                assets["currencies"] = cur
                assets["currencies_source"] = "oas-ui"
                assets["currencies_at"] = state.get("captured_at")
                assets["currencies_delta"] = None
            assets["asset_config"] = config
            assets["asset_serial"] = state.get("serial") or _config_serial(config)
            assets["asset_profile"] = state.get("profile")
            assets["asset_status"] = state.get("status")
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.get("/profiles")
def profiles():
    from tasks.Workbench.ingest import paz_db

    return _ok({**_root_store().profiles(), "paz": paz_db.profiles()})


@app.post("/profiles/select")
def profile_select(payload: dict = Body(...)):
    try:
        _root_store().select_profile(str(payload.get("id", "")))
        return _ok({"ok": True})
    except KeyError as exc:
        return _bad(exc.args[0], 404)


def _save_archive(data, source):
    profile = data.get("profile") or {}
    pid = str(profile.get("id") or uuid4()).strip()
    name = str(profile.get("name") or "导入档案").strip()
    if not pid or not name or len(pid) > 160 or len(name) > 120:
        raise ValueError("档案编号或名称无效")
    profile = {"id": pid, "name": name, "server": str(profile.get("server") or ""),
               "note": str(profile.get("note") or ""), "captured_at": profile.get("captured_at")}
    _root_store().save_archive(profile, data["souls"], data["owned"], source,
                               extras=data.get("extras"))
    return _ok({"ok": True, "profile": profile, "souls": len(data["souls"]), "owned": len(data["owned"])})


@app.post("/profiles/import")
def profile_import(payload=Body(...)):
    from tasks.Workbench.ingest import parse_archive

    try:
        data = parse_archive(payload)
        return _save_archive(data, data["source"])
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return _bad(f"档案格式有误（{exc}）")


@app.get("/profiles/export")
def profile_export():
    store = _store()
    return JSONResponse({"version": 1, "profile": store.current_profile(), **store.inventory()},
                        headers={"Content-Disposition": 'attachment; filename="workbench-profile.json"'})


@app.post("/profiles/sync")
def profile_sync(payload: dict = Body(default={})):
    from tasks.Workbench.ingest import paz_db

    data = paz_db.load(profile_id=payload.get("id") or None, include_extras=True)
    if not data["ok"]:
        return _bad(data["reason"], 404)
    return _save_archive(data, "paz-db")


@app.get("/standards")
def standards():
    return _ok({"standards": [{"id": s["id"], "title": s.get("title") or s["id"]}
                              for s in Standards().standards]})


@app.get("/souls")
def souls(standard: str = "", offset: int = 0, limit: int = 200, q: str = "",
          set_name: str = Query("", alias="set"), slot: int = 0):
    """御魂分析列表。

    纯增量：offset/limit（默认 200，上限 1000）＋ q/set/slot 过滤，响应含
    total/offset/limit/returned；每行新增 soul_id（可直接喂 /workbench/souls/{id}）、
    set_name、slot、quality、level。旧字段一个不改。
    """
    try:
        # 直接调用（selftest / 内部复用）时 FastAPI 的 Query() 默认值不是字符串，
        # 这里统一归一，免得把 Query 对象当过滤条件。
        q = q if isinstance(q, str) else ""
        set_name = set_name if isinstance(set_name, str) else ""
        standard = standard if isinstance(standard, str) else ""
        if standard and standard not in Standards().ids():
            return _bad("未知评分标准")
        if offset < 0:
            return _bad("offset 不能为负数")
        if not 1 <= limit <= 1000:
            return _bad("limit 必须在 1..1000 之间")
        if slot and not 1 <= slot <= 6:
            return _bad("slot 必须是 1..6")
        st = _store()
        return _ok(F.soul_report(st.souls(), standard_id=standard or None, catalog=catalog(),
                                 offset=offset, limit=limit, q=q, set_name=set_name,
                                 slot=slot or None))
    except (ValueError, TypeError, OverflowError) as exc:
        return _bad(exc)
    except Exception as exc:
        return _err(exc)


@app.get("/roster")
def roster():
    try:
        st = _store()
        return _ok(F.roster(st.owned(), catalog()))
    except Exception as exc:
        return _err(exc)


@app.get("/shards")
def shards(q: str = ""):
    try:
        st = _store()
        inventory = st.inventory()
        return _ok(F.shard_query(q, inventory["owned"], catalog(),
                                 counts=inventory.get("extras", {}).get("shards")))
    except Exception as exc:
        return _err(exc)


@app.get("/shards/matrix")
def shards_matrix(name: str = ""):
    try:
        root = _root_store()
        profiles, counts = {}, {}
        stored = root.profiles()["profiles"] or [{"id": "", "name": "当前档案"}]
        for p in stored:
            label = p["name"]
            if any(other["name"] == label and other is not p for other in stored):
                label = f'{label}（{p.get("server") or p["id"]}）'
            if label in profiles:
                label += f' #{p["id"]}'
            inventory = root.for_profile(p["id"]).inventory()
            profiles[label] = inventory["owned"]
            counts[label] = inventory.get("extras", {}).get("shards") or {}
        return _ok(F.shard_matrix(name, profiles, catalog(), counts_by_profile=counts))
    except Exception as exc:
        return _err(exc)


@app.get("/panel")
def panel(name: str = ""):
    try:
        st = _store()
        row = catalog().panel_of(name) if name else None
        if not name:
            row = next((r for item in st.owned()
                        if (r := catalog().panel_of(str(item.get("id") or item.get("name"))))), None)
        if row is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": "未找到该式神"})
        return _ok({"shikigami": row.get("name"), "id": row.get("id"),
                    "result": F.panel_calc(row)})
    except Exception as exc:
        return _err(exc)


@app.post("/panel/optimize")
def panel_optimize(payload: dict = Body(...)):
    """按当前档案配装。显式指定 paz-db 可直接读取平安志角色。"""
    try:
        if payload.get("source") == "paz-db":
            from tasks.Workbench.ingest import paz_db
            data = paz_db.load(profile_id=payload.get("profile_id") or None)
            if not data["ok"]:
                return _bad(data["reason"])
            souls = data["souls"]
        else:
            souls = _store().souls()
        if not souls:
            return _bad("当前档案没有御魂数据")
        base = payload.get("base") or {}
        if payload.get("name"):
            base = catalog().panel_of(str(payload["name"]).strip())
            if base is None:
                return _bad("未找到该式神", 404)
        top_k, beam = int(payload.get("top_k", 10)), int(payload.get("beam", 300))
        if not 1 <= top_k <= 50 or not 1 <= beam <= 1000:
            return _bad("top_k 或 beam 超出范围")
        recipe = payload.get("recipe") or []
        if (not isinstance(recipe, list) or
                any(not isinstance(r.get("set"), str) or type(r.get("count")) is not int or
                    not 1 <= r["count"] <= 6 for r in recipe) or
                sum(r["count"] for r in recipe) > 6 or len({r["set"] for r in recipe}) != len(recipe)):
            return _bad("套装配方应指定不同套装，所需件数之和不超过 6")
        res = F.panel_optimize(souls, base, payload.get("metric") or "攻击 × 暴伤",
                               slots_allowed=payload.get("slots_allowed"),
                               constraints=payload.get("constraints"), recipe=recipe,
                               top_k=top_k, beam=beam)
        return _ok(res)
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        return _bad(exc)
    except Exception as exc:
        return _err(exc)


@app.post("/speed-preview")
def speed_preview(payload: dict = Body(...)):
    try:
        name = str(payload.get("name") or "").strip()
        speed = payload.get("speed")
        if speed is None:
            row = catalog().panel_of(name)
            if row is None:
                return _bad("请选择式神或填写面板速度")
        else:
            row = {"name": name or "本卡", "speed": _speed(speed)}
        team = payload.get("team") or []
        if not isinstance(team, list) or len(team) > 5:
            return _bad("队友列表最多 5 人")
        return _ok(F.speed_preview(row, [], team=[
            {"name": str(m.get("name") or f"队友 {i}"), "speed": _speed(m.get("speed"))}
            for i, m in enumerate(team, 1)
        ]))
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        return _bad(exc)


def _speed(value):
    speed = float(value)
    if not math.isfinite(speed) or speed < 0:
        raise ValueError("速度必须是非负数")
    return speed


@app.get("/headtail")
def headtail(top: int = 5):
    try:
        st = _store()
        return _ok(F.head_tail(st.souls(), top_n=top))
    except Exception as exc:
        return _err(exc)


@app.get("/guild")
def guild():
    try:
        from tasks.Workbench.guild_monitor import guild_report
        store = _store()
        return _ok({**guild_report(store), "snapshot": store.inventory().get("extras", {}).get("guild")})
    except Exception as exc:
        return _err(exc)


@app.post("/guild/member")
def guild_member(payload: dict = Body(...)):
    try:
        from tasks.Workbench.guild_monitor import guild_add_member, guild_remove_member
        st = _store()
        name = str(payload.get("name", "")).strip()
        if not name:
            return JSONResponse(status_code=400, content={"ok": False, "error": "name 必填"})
        if payload.get("remove"):
            return _ok(guild_remove_member(st, name))
        return _ok(guild_add_member(st, name, payload.get("role", "成员"), payload.get("note", "")))
    except Exception as exc:
        return _err(exc)


@app.post("/guild/event")
def guild_event(payload: dict = Body(...)):
    try:
        from tasks.Workbench.guild_monitor import guild_log_event
        res = guild_log_event(_store(), str(payload.get("kind", "")),
                              payload.get("value", ""), payload.get("note", ""))
        return _ok(res) if res.get("ok") else JSONResponse(status_code=400, content=res)
    except Exception as exc:
        return _err(exc)


@app.get("/activities")
def activities(tag: str = "", limit: int = 0):
    try:
        from tasks.Workbench.ingest import activities as A
        data = A.load()
        return _ok({"report": A.activity_report(data, tag=tag, limit=limit),
                    "announcements": A.announcement_report(data)})
    except Exception as exc:
        return _err(exc)


@app.get("/tasks")
def tasks():
    try:
        return _ok(F.task_summary(_store().tasks(limit=0)))
    except Exception as exc:
        return _err(exc)


@app.post("/tasks/draft")
def draft_task(payload: dict = Body(...)):
    kind = payload.get("kind")
    if kind not in ("cleanup", "strengthen"):
        return _bad("请选择强化或清理")
    store = _store()
    inventory = store.souls()
    try:
        report = F.soul_report(inventory, standard_id=payload.get("standard") or None, catalog=catalog())
    except KeyError:
        return _bad("未知评分标准")
    eligible = ("discard",) if kind == "cleanup" else ("keep", "observe")
    items = []
    for result in report["results"]:
        idx = result["idx"]
        raw = inventory[idx]
        if result["decision"] in eligible and not (kind == "cleanup" and raw.get("locked")):
            soul = normalize_soul(raw)
            items.append({"idx": idx, "set": soul["set_id"], "slot": soul["slot"],
                          "main": soul["main_attribute"], "level": soul["level"],
                          "score": result["score"]})
        if len(items) >= 50:
            break
    if not items:
        return _bad("当前档案没有符合条件的候选")
    return _ok({"ok": True, "task": store.add_task(kind, items)})


@app.get("/monitor")
def monitor():
    try:
        return _ok(F.monitor_summary(_store().monitor()))
    except Exception as exc:
        return _err(exc)


@app.get("/admission")
def admission(limit: int = 0):
    """胚子准入（严格模式）：每件御魂命中了哪些规则。"""
    try:
        rep = F.admission_report(_store().souls(), standard_id="builtin.owner.default-manual",
                                 catalog=catalog())
        if limit:
            rep["rows"] = rep["rows"][:limit]
        return _ok(rep)
    except Exception as exc:
        return _err(exc)


@app.get("/domains")
def domains():
    """六个数据域的状态汇总（inventory/commonness/relevance/groups/todos/lineup）。"""
    try:
        from tasks.Workbench.ingest import paz_extra
        return _ok(paz_extra.summary())
    except Exception as exc:
        return _err(exc)


@app.get("/domains/{name}")
def domain_detail(name: str, limit: int = 20):
    try:
        from tasks.Workbench.ingest import paz_extra
        fn = {
            "inventory": lambda: paz_extra.inventory(limit=limit),
            "commonness": paz_extra.commonness,
            "relevance": paz_extra.relevance,
            "groups": paz_extra.yuhun_groups,
            "todos": lambda: paz_extra.todos(limit=limit),
            "lineup": lambda: paz_extra.lineup_catalog(limit=limit),
        }.get(name)
        if fn is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": "unknown domain"})
        return _ok(fn())
    except Exception as exc:
        return _err(exc)


@app.get("/location/status")
def location_status(serial: str = ""):
    """看设备上有没有可用的模拟定位 App（没有就返回原因，不伪造成功）。"""
    try:
        from tasks.Workbench import tools as T
        s = serial or _default_serial()
        if not s:
            return _bad("请选择设备")
        return _ok(T.detect_mock_app(s))
    except Exception as exc:
        return _err(exc)


@app.post("/location")
def location_set(payload: dict = Body(...)):
    """{serial?, lat, lng}；没有 mock App 时 ok=False 并说明原因。"""
    try:
        from tasks.Workbench import tools as T
        s = payload.get("serial") or _default_serial()
        lat = float(payload.get("lat"))
        lng = float(payload.get("lng"))
        if not s or not -90 <= lat <= 90 or not -180 <= lng <= 180:
            return _bad("设备或坐标无效")
        det = T.detect_mock_app(s)
        if not det.get("ok"):
            return _bad(det.get("reason") or "设备上没有受支持的模拟定位 App")
        return _ok(T.set_location(s, lat, lng, action=det["action"]))
    except (ValueError, TypeError, OverflowError):
        return _bad("设备或坐标无效")
    except Exception as exc:
        return _err(exc)


@app.get("/monitor/trend")
def monitor_trend(days: int = 30):
    try:
        if days < 1:
            return _bad("天数必须大于零")
        from tasks.Workbench import monitor as M
        return _ok(M.trend(_store(), days=days))
    except Exception as exc:
        return _err(exc)


@app.post("/monitor/sample")
def monitor_sample(payload: dict = Body(default={})):
    try:
        from tasks.Workbench import monitor as M
        st = _store()
        src = payload.get("source", "local")
        if payload.get("standard") and payload["standard"] not in Standards().ids():
            return _bad("未知评分标准")
        if src == "paz-db":
            if not st.profile_id:
                return _bad("请先同步平安志档案")
            res = M.sample_from_db(st, payload.get("standard"))
        elif src == "local":
            res = M.sample(st, standard_id=payload.get("standard"))
        else:
            return _bad("未知采样来源")
        if res.get("ok") is False:
            return _bad(res["reason"])
        return _ok({"ok": True, **res})
    except Exception as exc:
        return _err(exc)


@app.get("/instances")
def instances():
    try:
        return _ok(T.list_instances())
    except Exception as exc:
        return _err(exc)


@app.post("/instances/action")
def instance_action(payload: dict = Body(...)):
    index = str(payload.get("index", ""))
    action = payload.get("action")
    if action not in ("launch", "shutdown") or not index.isdecimal():
        return _bad("实例编号或操作无效")
    if index not in {r["index"] for r in T.list_instances().get("instances", [])}:
        return _bad("找不到指定的 MuMu 实例", 404)
    result = T.control_instance(index, action)
    return _ok(result) if result["ok"] else _bad(result.get("errmsg") or "MuMu 操作失败")


@app.post("/decision")
def decision(payload: dict = Body(...)):
    try:
        key = str(payload.get("soul_key") if payload.get("soul_key") is not None else "").strip()
        value = str(payload.get("decision", ""))
        if not key:
            return _bad("soul_key 必填")
        if value not in ("keep", "observe", "plan_strengthen", "plan_recycle", "ignore"):
            return JSONResponse(status_code=400,
                                content={"ok": False, "error": "非法 decision: %s" % value})
        _store().set_decision(key, value, payload.get("note", ""))
        return _ok({"ok": True, "soul_key": key, "decision": value})
    except Exception as exc:
        return _err(exc)


# ============================================================================
# 平安志移植补全（8 个功能域）：
#   单颗御魂详情 / 待办处置与历史 / 标准版本 / 式神详情 / 快照 / 备份 /
#   采集记录 / 面板·速度历史 / 元信息
# 读：平安志 sqlite（mode=ro）；写：只落 OAS 自己的 config/workbench 与
# log/workbench/backups，绝不写 %APPDATA%\com.yys.analysis 下任何文件。
# ============================================================================
def _degraded(reason, **extra):
    """平安志不可用：HTTP 200 + ok:false + reason + 空结构（前端按空态渲染）。"""
    payload = {"ok": False, "reason": str(reason or "平安志数据不可用")}
    payload.update(extra)
    payload.setdefault("items", [])
    return JSONResponse(content=json.loads(json.dumps(payload, ensure_ascii=False, default=str)))


def _backup_dir() -> str:
    return os.environ.get("WORKBENCH_BACKUP_DIR") or str(
        REPO_ROOT / "log" / "workbench" / "backups")


def _local_soul(soul_id):
    """平安志不可用时退回 OAS 自己的档案（导入时保留了 internal_id）。"""
    from tasks.Workbench.engine import ATTR_CN, normalize_soul

    key = str(soul_id)
    for raw in _store().souls():
        if str(raw.get("internal_id") or raw.get("soul_key") or "") != key:
            continue
        soul = normalize_soul(raw)
        attrs = []
        for attr, value in (soul.get("substats") or {}).items():
            attrs.append({"attribute_type": attr, "value": value, "is_fixed": False,
                          "rolls": int((soul.get("rolls") or {}).get(attr, 0))})
        for attr, value in (soul.get("fixed_stats") or {}).items():
            attrs.append({"attribute_type": attr, "value": value, "is_fixed": True, "rolls": 0})
        main = soul.get("main_attribute")
        return {"ok": True, "source": "oas-store",
                "soul": {"internal_id": key, "soul_key": key, "set_name": soul["set_id"],
                         "slot": soul["slot"], "quality": soul["quality"],
                         "level": soul["level"], "main_attribute": ATTR_CN.get(main, main),
                         "main_value": soul.get("main_value"), "equipped": "",
                         "locked": bool(raw.get("locked"))},
                "main": {"attribute_type": main, "name": ATTR_CN.get(main, main),
                         "value": soul.get("main_value")},
                "attributes": attrs, "raw": soul, "todo": {}}
    return None


def _local_roster(name):
    """平安志不可用时用本模块目录 + 当前档案的式神列表兜底。"""
    from tasks.Workbench.engine import Catalog
    from tasks.Workbench.ingest import norm_skills

    cat = Catalog()
    ref = cat.shikigami_of(str(name).strip())
    if ref is None:
        return {"ok": False, "not_found": True, "reason": "式神目录里没有: %s" % str(name).strip()}
    owned = [o for o in _store().owned()
             if str(o.get("id") or o.get("name")) in (str(ref.get("shikigami_id")),
                                                      str(ref.get("name")))]
    owned.sort(key=lambda o: (int(o.get("level") or 0), int(o.get("star") or 0)), reverse=True)
    best = owned[0] if owned else {}
    merged_skills = {}
    for inst in owned:
        raw_sk = inst.get("skills")
        if raw_sk is None:
            raw_sk = inst.get("skills_json") if inst.get("skills_json") is not None else inst.get("skill_levels")
        for s in norm_skills(raw_sk):
            sid = s["id"]
            prev = merged_skills.get(sid)
            if prev is None:
                merged_skills[sid] = dict(s)
            else:
                if s["level"] > prev["level"]:
                    prev["level"] = s["level"]
                for field in ("name", "type", "energy_cost"):
                    if not prev.get(field) and s.get(field) is not None:
                        prev[field] = s[field]
    return {"ok": True, "source": "oas-store",
            "shikigami": {"id": ref.get("shikigami_id"), "name": ref.get("name"),
                          "rarity": ref.get("rarity"), "level": best.get("level"),
                          "star": best.get("star"), "awakened": bool(best.get("awakened")),
                          "skin": best.get("skin"), "icon": ref.get("icon_asset_id"),
                          "panel": {}, "skills": list(merged_skills.values()), "souls": [],
                          "owned": bool(owned), "instances": len(owned)}}


@app.get("/souls/{soul_id}")
def soul_detail(soul_id: str):
    """单颗御魂：属性明细 + 评分贡献 + 雷达。"""
    from tasks.Workbench.ingest import paz_extra

    try:
        key = str(soul_id or "").strip()
        if not key:
            return _bad("soul_id 必填")
        facts = paz_extra.soul_detail(key)
        if not facts.get("ok") and facts.get("not_found"):
            return JSONResponse(status_code=404,
                                content={"ok": False, "error": facts.get("reason") or "找不到该御魂"})
        if not facts.get("ok"):
            facts = _local_soul(key) or facts
        if not facts.get("ok"):
            return _degraded(facts.get("reason"), soul={}, attrs=[], score={}, radar=[])
        return _ok({"ok": True, "source": facts.get("source") or "",
                    **F.soul_detail(facts, catalog=catalog())})
    except Exception as exc:
        return _err(exc)


@app.get("/todos")
def todos(status: str = "open", limit: int = 50):
    """待办清单：analysis_todo + OAS 自己的处置状态。"""
    from tasks.Workbench.ingest import paz_extra

    if status not in ("open", "done", "ignored", "all"):
        return _bad("status 只能是 open / done / ignored / all")
    if not 1 <= limit <= 500:
        return _bad("limit 必须在 1..500 之间")
    try:
        store = _store()
        data = paz_extra.todo_items(store.todo_decisions(), status=status, limit=limit)
        if not data.get("ok"):
            return _degraded(data.get("reason"), counts=data.get("counts") or {},
                             source="oas-store")
        return _ok({"ok": True, "source": data.get("source") or "analysis_todo",
                    "items": data["items"], "counts": data["counts"],
                    "total": data.get("total"), "filtered": data.get("filtered")})
    except Exception as exc:
        return _err(exc)


@app.post("/todos/decision")
def todo_decision(payload: dict = Body(...)):
    """处置一条待办：done / ignore / restore（只写 OAS，不改平安志 analysis_todo）。"""
    from tasks.Workbench.ingest import paz_extra

    try:
        todo_id = str(payload.get("todo_id") or "").strip()
        action = str(payload.get("action") or "").strip()
        if not todo_id:
            return _bad("todo_id 必填")
        if action not in ("done", "ignore", "restore"):
            return _bad("action 只能是 done / ignore / restore")
        note = str(payload.get("note") or "")[:200]
        reason = ""
        known = paz_extra.todo_lookup(todo_id)
        if known.get("ok") is False:
            if known.get("not_found"):
                return JSONResponse(status_code=404,
                                    content={"ok": False, "error": known.get("reason") or
                                                                     "找不到该待办"})
            reason = known.get("reason") or "平安志不可用，处置只记录在 OAS 本地"
        row = _store().set_todo_decision(todo_id, action, note)
        out = {"ok": True, "decision": row}
        if reason:
            out["reason"] = reason
        return _ok(out)
    except Exception as exc:
        return _err(exc)


@app.get("/todos/history")
def todos_history(limit: int = 50):
    if not 1 <= limit <= 500:
        return _bad("limit 必须在 1..500 之间")
    try:
        return _ok({"ok": True, "source": "oas-store", "items": _store().todo_history(limit=limit)})
    except Exception as exc:
        return _err(exc)


@app.get("/standards/versions")
def standards_versions():
    """评分标准版本清单（平安志 rule_version，缺失时用本模块标准包兜底）。"""
    from tasks.Workbench.ingest import paz_extra

    try:
        pref = _root_store().standards_pref()
        data = paz_extra.rule_versions()
        if data.get("ok"):
            items = data["items"]
            active = pref.get("active") or data.get("active") or ""
            for item in items:
                item["active"] = item["id"] == active
            return _ok({"ok": True, "source": "rule_version", "items": items, "active": active,
                        "preference": pref.get("active") or "",
                        "pinganzhi_active": data.get("active") or ""})
        items = F.standards_catalog()
        active = pref.get("active") or (items[0]["id"] if items else "")
        for item in items:
            item["active"] = item["id"] == active
        return _ok({"ok": True, "source": "local-standard", "items": items, "active": active,
                    "preference": pref.get("active") or "",
                    "reason": data.get("reason") or "平安志不可用，使用本模块标准包"})
    except Exception as exc:
        return _err(exc)


@app.post("/standards/activate")
def standards_activate(payload: dict = Body(...)):
    """激活一套评分标准：只改 OAS 偏好，不动平安志 active_score_standard。"""
    from tasks.Workbench.ingest import paz_extra

    try:
        version_id = str(payload.get("version_id") or "").strip()
        if not version_id:
            return _bad("version_id 必填")
        valid = set(Standards().ids())
        data = paz_extra.rule_versions()
        if data.get("ok"):
            valid |= {item["id"] for item in data["items"]}
        else:
            valid |= {item["id"] for item in F.standards_catalog()}
        if version_id not in valid:
            return JSONResponse(status_code=404,
                                content={"ok": False, "error": "未知评分标准版本: %s" % version_id})
        row = _root_store().set_standard(version_id)
        return _ok({"ok": True, "active": row["active"], "updated": row["updated"],
                    "note": "只改 OAS 自己的偏好，平安志的 active_score_standard 不受影响"})
    except Exception as exc:
        return _err(exc)


@app.get("/roster/{name}")
def roster_detail(name: str):
    """单个式神详情：技能等级 + 现有御魂（按 equipped_state 里的实例号匹配）。"""
    from tasks.Workbench.ingest import paz_extra

    try:
        key = str(name or "").strip()
        if not key:
            return _bad("式神名或编号必填")
        data = paz_extra.shikigami_detail(key)
        if not data.get("ok") and data.get("not_found"):
            return JSONResponse(status_code=404,
                                content={"ok": False, "error": data.get("reason") or "找不到该式神"})
        if not data.get("ok"):
            local = _local_roster(key)
            if local is not None:
                data = local
        if not data.get("ok") and data.get("not_found"):
            return JSONResponse(status_code=404,
                                content={"ok": False, "error": data.get("reason") or "找不到该式神"})
        if not data.get("ok"):
            return _degraded(data.get("reason"), name=key)
        return _ok({"ok": True, "source": data.get("source") or "",
                    **F.roster_detail(data)})
    except Exception as exc:
        return _err(exc)


@app.get("/snapshots")
def snapshots():
    """数据快照列表（snapshot + snapshot_summary + snapshot_integrity）。"""
    from tasks.Workbench.ingest import paz_extra

    try:
        data = paz_extra.snapshots()
        if not data.get("ok"):
            return _degraded(data.get("reason"), note=data.get("note") or "")
        return _ok({"ok": True, "source": data.get("source"), "items": data["items"],
                    "note": data.get("note") or ""})
    except Exception as exc:
        return _err(exc)


@app.get("/backups")
def backups():
    """OAS 自己的备份清单；平安志 backup_record 只读展示，不碰其文件。"""
    from tasks.Workbench.ingest import paz_extra

    try:
        items = _root_store().backups(_backup_dir())
        paz = paz_extra.paz_backups()
        return _ok({"ok": True, "source": "oas-store", "items": items,
                    "dir": _backup_dir(),
                    "note": "OAS 自己的备份（档案 + 决策 + 偏好），不复制平安志 471MB 库",
                    "pinganzhi": {"ok": bool(paz.get("ok")), "items": paz.get("items") or [],
                                  "reason": paz.get("reason") or ""}})
    except Exception as exc:
        return _err(exc)


@app.post("/backups/create")
def backups_create(payload: dict = Body(default={})):
    """创建一次备份：写 log/workbench/backups/，绝不写平安志目录。"""
    try:
        note = str(payload.get("note") or "")[:200]
        row = _root_store().create_backup(note, dest_dir=_backup_dir())
        return _ok({"ok": True, "backup": row})
    except Exception as exc:
        return _err(exc)


@app.get("/acquisitions")
def acquisitions(limit: int = 50):
    """数据采集记录（acquisition_event）。"""
    from tasks.Workbench.ingest import paz_extra

    if not 1 <= limit <= 500:
        return _bad("limit 必须在 1..500 之间")
    try:
        data = paz_extra.acquisitions(limit=limit)
        if not data.get("ok"):
            return _degraded(data.get("reason"), source="oas-store")
        return _ok({"ok": True, "source": data.get("source"), "items": data["items"],
                    "total": data.get("total")})
    except Exception as exc:
        return _err(exc)


@app.get("/meta")
def meta():
    """数据源总览：平安志是否可用、三类计数、最近同步时间。"""
    from tasks.Workbench.ingest import paz_extra

    try:
        data = paz_extra.meta()
        if not data.get("ok"):
            return _degraded(data.get("reason"), data_source="oas-store",
                             pinganzhi=data.get("pinganzhi") or {},
                             counts=data.get("counts") or {}, last_sync=None)
        payload = {"ok": True, **data}
        try:
            inventory = _store().inventory()
            payload["counts"] = dict(payload.get("counts") or {})
            payload["counts"]["oas_souls"] = len(inventory.get("souls") or [])
            payload["counts"]["oas_owned"] = len(inventory.get("owned") or [])
            payload["oas"] = {"source": inventory.get("source"),
                              "captured_at": inventory.get("captured_at")}
        except Exception:
            pass
        return _ok(payload)
    except Exception as exc:
        return _err(exc)


def _history(kind: str, limit: int):
    from tasks.Workbench.ingest import paz_extra

    if not 1 <= limit <= 500:
        return _bad("limit 必须在 1..500 之间")
    data = paz_extra.history(kind, limit=limit)
    payload = {"ok": True, "source": data.get("source") or "",
               "items": data.get("items") or [], "total": data.get("total", 0),
               "note": data.get("note") or ""}
    if not data.get("ok"):
        payload.update({"ok": False, "reason": data.get("reason") or "平安志历史不可用"})
    return JSONResponse(content=json.loads(json.dumps(payload, ensure_ascii=False, default=str)))


@app.get("/panel/history")
def panel_history(limit: int = 50):
    """面板历史（平安志 pve_panel_cache；本机为空时 items=[] + note）。"""
    try:
        return _history("panel", limit)
    except Exception as exc:
        return _err(exc)


@app.get("/speed/history")
def speed_history(limit: int = 50):
    """速度历史（平安志 speed_preview_cache；本机为空时 items=[] + note）。"""
    try:
        return _history("speed", limit)
    except Exception as exc:
        return _err(exc)

# ── 平安志功能面（直读本机数据/资料库，不依赖平安志程序）───────────
from tasks.Workbench.ingest import paz_catalog as PC  # noqa: E402


@app.get("/paz/summary")
def _paz_summary():
    return PC.summary()


@app.get("/paz/shikigami")
def _paz_shikigami(q: str = "", limit: int = 50, offset: int = 0):
    return PC.shikigami(q, limit, offset)


@app.get("/paz/bounty")
def _paz_bounty():
    return PC.bounty_seal()


@app.get("/paz/docs/{name}")
def _paz_doc(name: str, key: str = "", limit: int = 50, offset: int = 0):
    return PC.doc(name, key, limit, offset)


@app.get("/paz/cosmetics")
def _paz_cosmetics(q: str = "", limit: int = 60, offset: int = 0):
    return PC.cosmetics_page(q, limit, offset)


@app.get("/paz/groups")
def _paz_groups():
    return PC.yuhun_groups()


@app.get("/paz/damage")
def _paz_damage():
    return PC.damage_presets()


@app.get("/paz/tasks")
def _paz_tasks(limit: int = 200):
    return PC.task_status(limit)

@app.post("/paz/collect")
def _paz_collect():
    """OAS 内的“刷新资料”动作：重读本机快照并报告新鲜度（不启动外部程序）。"""
    return PC.collect()

_ICON_DIR = Path(__file__).resolve().parents[2] / "tasks" / "Workbench" / "assets" / "currency"


@app.get("/paz/icon/{name}")
def _paz_icon(name: str):
    """本地缓存的游戏货币图标（PNG）。缺失返回 404 由前端回退。"""
    from fastapi.responses import FileResponse

    safe = "".join(ch for ch in name if ch.isalnum() or ch == "_")
    fp = _ICON_DIR / f"{safe}.png"
    if not fp.exists():
        return JSONResponse({"ok": False, "reason": "icon not cached"}, status_code=404)
    return FileResponse(str(fp), media_type="image/png")

# ── 模拟玩法（复刻平安志 simulate_* 的行为，不含其代码/资产）──────────────────
# 数据与常量来源：tasks/Workbench/data/gameplay_options.json
#   （从平安志 IPC get_simulation_gameplay_options 原样落盘：ruleVersion / poolVersion /
#    goldSets 57 / demonSets 12 / pool 215 / exchangeEvidence / summonEvidence）
@app.get("/simulate/options")
def _simulate_options():
    from tasks.Workbench import simulate as SIM
    return SIM.options()


@app.post("/simulate/{kind}")
def _simulate_run(kind: str, payload: dict = Body(default=None)):
    """kind ∈ enhance / virtual_enhance / exchange / summoning。

    body 示例：
      {"trials": 200, "seed": 1}                                   # enhance
      {"souls": [{...}], "threshold": 70, "seed": 1}               # virtual_enhance
      {"set_name": "片叶之苇", "slot": 5, "count": 10, "seed": 1}   # exchange
      {"count": 10, "seed": 1, "full_collection": false}           # summoning
    """
    from tasks.Workbench import simulate as SIM
    return SIM.run(kind, payload or {})

# ── 角色分析：两个历史版本对比（复刻平安志 get_character_archive_comparison）────
@app.get("/compare")
def _compare_snapshots(older: str = Query(default=""), newer: str = Query(default="")):
    """对比两条快照：`/workbench/compare?older=<id>&newer=<id>`。

    id 相同 / 不存在 / 空：返回 ok=false 与明确错误（与平安志的拒绝口径一致）。
    """
    from tasks.Workbench import compare as CMP
    return CMP.compare_snapshots(older, newer)


# ── 自动阵容规划预览（结合本机号底与御魂池，一律默认写入第 10 组「适配」）──────
@app.get("/lineup/plan")
def _lineup_plan(
    task: str = Query(default="Orochi"),
    dungeon: str = Query(default=""),
    mode: str = Query(default="local"),
    preset_group: int = Query(default=10),
    preset_team: int = Query(default=1),
):
    """预览自动阵容规划结果：`/workbench/lineup/plan?task=Orochi&dungeon=八岐大蛇·拾层`。"""
    try:
        from module.lineup.planner import plan_lineup

        res = plan_lineup(
            task_name=task,
            dungeon=dungeon,
            mode=mode,
            preset_group=preset_group,
            preset_team=preset_team,
        )
        if not res.get("ok"):
            return _bad(res.get("reason") or "无法生成阵容方案")
        spec = res["spec"]
        return _ok({
            "ok": True,
            "source": res.get("source"),
            "reason": res.get("reason"),
            "dungeon_key": res.get("dungeon_key"),
            "dungeon_tuple": list(res.get("dungeon_tuple") or ()),
            "spec": spec.to_dict(),
        })
    except Exception as exc:
        return _err(exc)
