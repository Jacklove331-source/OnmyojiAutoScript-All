# This file uses the following encoding: utf-8
"""13 项功能的可执行证据生成器：每项跑一次、输出落 log/workbench/evidence/*.json。

用法（仓库根目录）：
    .\\toolkit\\python.exe tasks\\Workbench\\evidence_all.py
输出：
    log/workbench/evidence/<n>_<feature>.json   每项一条真实数据
    log/workbench/evidence/SUMMARY.md           汇总表（项 / 命令 / 输出文件 / 关键数字）
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

OUT_DIR = os.path.join("log", "workbench", "evidence")

FEATURES = [
    ("01_monitor", "监控"),
    ("02_workbench_home", "工作台首页"),
    ("03_data_read", "数据读取"),
    ("04_profile", "角色档案"),
    ("05_task_records", "任务记录"),
    ("06_soul_analysis", "御魂分析"),
    ("07_panel_calc", "面板计算/提升"),
    ("08_simulation", "模拟玩法"),
    ("09_roster", "式神录"),
    ("10_shard_query", "碎片查询"),
    ("11_game_assets", "游戏资产"),
    ("12_guild", "寮管理"),
    ("13_tools", "小工具"),
]


def _dump(name: str, payload) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, name + ".json")
    io.open(p, "w", encoding="utf-8").write(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str))
    return p


def main() -> int:
    from tasks.Workbench import features as F
    from tasks.Workbench import monitor as M
    from tasks.Workbench import tools as T
    from tasks.Workbench.engine import Catalog
    from tasks.Workbench.ingest import paz_db, paz_extra
    from tasks.Workbench.store import Store

    catalog = Catalog()
    store = Store()
    db = paz_db.load()
    souls, owned = (db.get("souls") or []), (db.get("owned") or [])
    rows = []

    # 先把真实库存绑进本地档案，首页/角色卡片才有真实数字（覆盖写，幂等）
    _player = db.get("player") or {}
    _pid = _player.get("short_id") or "from-paz-db"
    store.add_profile(str(_pid), _player.get("name") or "平安志档案",
                      server=str(_player.get("server_id") or ""), note="evidence")
    store.save_souls(souls)
    store.save_owned(owned)

    def emit(key, title, cmd, fn):
        try:
            data = fn()
            path = _dump(key, data)
            brief = ""
            keys = ("total", "count", "soul_count", "ok", "hits", "members",
                    "metric_value", "owned", "points", "days", "groups", "profiles")
            def _scan(d, depth=0):
                out = ""
                if not isinstance(d, dict) or depth > 1:
                    return out
                for k in keys:
                    if k in d and not isinstance(d[k], (dict, list)):
                        out += "%s=%s " % (k, d[k])
                if depth == 0:
                    for v in d.values():
                        if isinstance(v, dict):
                            out += _scan(v, depth + 1)
                return out
            brief = _scan(data)
            if not brief.strip() and isinstance(data, dict):
                brief = "keys=%d(%s)" % (len(data), ",".join(list(data)[:3]))
            rows.append((title, cmd, path, brief.strip() or "-"))
            if brief.strip() == "":
                rows[-1] = (title, cmd, path, brief.strip() or "-")
        except Exception as exc:
            rows.append((title, cmd, "ERROR", "%s: %s" % (type(exc).__name__, exc)))

    emit("01_monitor", "监控", "python tasks/Workbench/evidence_all.py",
         lambda: {"trend": M.trend(store), "latest": M.sample(store, souls, owned, note="evidence")})
    emit("02_workbench_home", "工作台首页", "GET /workbench/overview",
         lambda: F.overview(store, catalog=catalog))
    emit("03_data_read", "数据读取", "ingest.paz_db.load()",
         lambda: {"source": db.get("source"), "souls": len(souls), "owned": len(owned),
                  "fixed_attrs": sum(1 for s in souls if s.get("fixed_attrs"))})
    emit("04_profile", "角色档案", "GET /workbench/overview.profile",
         lambda: {"current": store.current_profile(),
                  "profiles": store.profiles().get("profiles", [])})
    store.add_task("workbench_evidence", ["01_monitor", "06_soul_analysis"], "completed")
    emit("05_task_records", "任务记录", "GET /workbench/tasks",
         lambda: F.task_summary(store.tasks()))
    emit("06_soul_analysis", "御魂分析", "POST /workbench/souls  (F.soul_report)",
         lambda: F.soul_report(souls, standard_id="builtin.owner.default-manual", catalog=catalog))
    emit("07_panel_calc", "面板计算/提升", "POST /workbench/panel/optimize",
         lambda: F.panel_optimize(
             souls,
             {"attack": 4154, "hp": 11734.76, "defense": 493.92, "speed": 112,
              "crit_rate": 0.12, "crit_damage": 1.5, "effect_hit": 0.0, "effect_resist": 0.0},
             "攻击 × 暴伤", top_k=10, beam=300))
    def _sim():
        sp = F.speed_preview(catalog.panel_of("须佐之男") or {"panel": {}}, souls[:6],
                             team=[{"name": "队友A", "speed": 210}])
        ht = F.head_tail(souls)
        return {"self_speed": sp["self"]["speed"], "order_len": len(sp["order"]),
                "head_candidates": ht["head"]["candidates"],
                "top_speed_order": sp["order"][:3]}
    emit("08_simulation", "模拟玩法", "GET /workbench/headtail  (F.speed_preview 同源)", _sim)
    emit("09_roster", "式神录", "GET /workbench/roster",
         lambda: F.roster(owned, catalog))
    emit("10_shard_query", "碎片查询", "GET /workbench/shards/matrix",
         lambda: F.shard_matrix("609", {"current": owned}, catalog))
    emit("11_game_assets", "游戏资产", "GET /workbench/domains/inventory",
         lambda: paz_extra.inventory(limit=10))
    emit("12_guild", "寮管理", "GET /workbench/guild",
         lambda: {"report": __import__("tasks.Workbench.guild_monitor", fromlist=["x"]).guild_report(store),
                  "domains": paz_extra.commonness()})
    emit("13_tools", "小工具", "GET /workbench/instances + /workbench/location/status",
         lambda: {"instances": T.list_instances(),
                  "mock_location": T.detect_mock_app("127.0.0.1:16416")})

    md = ["# 13 项功能证据表（自动生成 %s）" % time.strftime("%Y-%m-%d %H:%M:%S"), "",
          "| # | 功能 | 命令 | 输出文件 | 关键数字 |", "|---|---|---|---|---|"]
    for i, (title, cmd, path, brief) in enumerate(rows, 1):
        md.append("| %d | %s | `%s` | `%s` | %s |" % (i, title, cmd, path, brief))
    md.append("")
    md.append("全部由 `tasks/Workbench/evidence_all.py` 一次跑出；输出文件在 `log/workbench/evidence/`。")
    os.makedirs(OUT_DIR, exist_ok=True)
    io.open(os.path.join(OUT_DIR, "SUMMARY.md"), "w", encoding="utf-8").write("\n".join(md))
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
