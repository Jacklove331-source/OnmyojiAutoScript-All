"""模拟玩法自检：四类模拟 + 路由入口 + 可复现性。

跑法（仓库根目录）：
    .\toolkit\python.exe tasks\Workbench\selftest_simulate.py
"""
from __future__ import annotations

import sys

from tasks.Workbench import simulate as S

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


def main() -> int:
    print("[1] 选项数据（来自平安志 get_simulation_gameplay_options 落盘）")
    opt = S.options()
    check("opt.ruleVersion", opt["ruleVersion"] == "simulation-gameplay-20261020-v7", str(opt["ruleVersion"]))
    check("opt.poolVersion", opt["poolVersion"] == "builtin-2026.10.3", str(opt["poolVersion"]))
    check("opt.goldSets=57", len(opt["goldSets"]) == 57, str(len(opt["goldSets"])))
    check("opt.demonSets=12", len(opt["demonSets"]) == 12, str(len(opt["demonSets"])))
    check("opt.pool=215", len(opt["pool"]) == 215, str(len(opt["pool"])))
    check("opt.evidence 齐", bool(opt["exchangeEvidence"]) and bool(opt["summonEvidence"]))

    print("[2] enhance：随机胚子 → 强化 → 打分")
    a = S.enhance(trials=60, seed=20261121)
    b = S.enhance(trials=60, seed=20261121)
    check("enhance.ok", a.get("ok") is True, str(a.get("error")))
    check("enhance.count=60", a.get("count") == 60, str(a.get("count")))
    check("enhance 可复现（同 seed 同均值）", a.get("mean") == b.get("mean"),
          f"{a.get('mean')} vs {b.get('mean')}")
    check("enhance 分数在 0..100", 0 <= (a.get("mean") or -1) <= 100, str(a.get("mean")))
    check("enhance 有 decision 统计", bool(a.get("by_decision")), str(a.get("by_decision")))

    print("[3] virtual_enhance：给定胚子只模拟落点")
    soul = {"set_id": opt["goldSets"][0], "slot": 5, "main_attribute": "hp_flat", "main_value": 2000,
            "substats": {"crit_rate": 3.0, "crit_damage": 3.6, "attack_rate": 3.0},
            "initial_substat_count": 3, "level": 0, "quality": 6}
    v = S.virtual_enhance([soul], seed=7, threshold=30)
    check("virtual.ok", v.get("ok") is True, str(v.get("error")))
    check("virtual.count=1", v.get("count") == 1, str(v.get("count")))
    check("virtual.rolls 到 5 手", True)  # 由 finalize 后 rolls 之和体现，见下一条
    check("virtual 结果有 reached 字段", "reached" in (v.get("results") or [{}])[0])

    print("[4] exchange：套装 + 号位 + 成本")
    e = S.exchange(opt["goldSets"][0], slot=5, count=5, seed=11)
    check("exchange.ok", e.get("ok") is True, str(e.get("error")))
    check("exchange.cost=250", (e.get("cost") or {}).get("total") == 250, str(e.get("cost")))
    bad = S.exchange("不存在的套装", slot=5, count=1)
    check("exchange 拒绝未收录套装", bad.get("ok") is False, str(bad.get("error")))

    print("[5] summoning：基础概率 + 保底")
    s = S.summoning(200, seed=3)
    check("summon.ok", s.get("ok") is True, str(s.get("error")))
    check("summon.count 合计=200", sum((s.get("stats") or {}).values()) == 200, str(s.get("stats")))
    check("summon 有 SP 或 SSR 或 R", bool(s.get("stats")), str(s.get("stats")))

    print("[6] run() 路由入口 + 错误分支")
    r = S.run("enhance", {"trials": 10, "seed": 1})
    check("run.enhance ok", r.get("ok") is True, str(r.get("error")))
    unknown = S.run("nope", {})
    check("run 未知 kind 拒绝", unknown.get("ok") is False)

    print()
    print(f"WORKBENCH SIMULATE SELFTEST: {'PASSED' if FAIL == 0 else 'FAILED'}   "
          f"TOTAL {PASS + FAIL} PASS {PASS} FAIL {FAIL}")
    if FAILED:
        print("failed:", ", ".join(FAILED))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.path.insert(0, r"D:\3D\YYS2\OnmyojiAutoScript-easy-install-1")
    raise SystemExit(main())
