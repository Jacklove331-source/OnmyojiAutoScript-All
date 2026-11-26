# This file uses the following encoding: utf-8
"""面板计算/提升 回归。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tasks.Workbench.analyze import panel_opt as O

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


BASE = {"attack": 4000, "hp": 11000, "defense": 480, "speed": 110,
        "crit_rate": 0.12, "crit_damage": 1.5, "effect_hit": 0.0, "effect_resist": 0.0}


def mk(slot, set_name, main, main_val, subs=None):
    return {"slot": slot, "set": set_name, "main": main, "main_value": main_val,
            "substats": subs or {}, "level": 15, "internal_id": "%s-%s" % (set_name, slot)}


def main() -> int:
    # 表达式求值
    panel = dict(BASE)
    panel["crit_damage"] = 2.0
    check("expr.mul", abs(O.evaluate_metric("攻击 * 暴伤", panel) - 8000.0) < 0.01,
          str(O.evaluate_metric("攻击 * 暴伤", panel)))
    check("expr.cn_alias", abs(O.evaluate_metric("生命 × 暴伤", panel) - 22000.0) < 0.01)
    try:
        O.evaluate_metric("__import__('os').system('x')", panel)
        check("expr.rejects_code", False, "应当抛异常")
    except ValueError:
        check("expr.rejects_code", True)

    # 属性合成：百分比作用于基础值
    soul = mk(1, "破势", "attack_flat", 486, {"attack_rate": 10.0})
    p = O.combine(BASE, [soul])
    # 486 平值 + 10% 攻击加成(400) → 4886
    check("combine.flat", abs(p["attack"] - 4886.0) < 0.01, str(p["attack"]))
    check("combine.rate", abs(O.combine(BASE, [mk(1, "破势", "attack_rate", 55.0)])["attack"] - 6200.0) < 0.5,
          str(O.combine(BASE, [mk(1, "破势", "attack_rate", 55.0)])["attack"]))

    # 求解器：6 个号位各给两件，目标 攻击×暴伤 应选中攻/暴伤更高的那套
    souls = []
    for slot in range(1, 7):
        souls.append(mk(slot, "好", "attack_rate" if slot != 2 else "speed", 55.0,
                        {"crit_damage": 20.0}))
        souls.append(mk(slot, "差", "attack_flat", 100.0, {}))
    res = O.optimize(souls, BASE, "攻击 * 暴伤", top_k=4, beam=50)
    check("opt.ok", res["ok"] is True, str(res))
    check("opt.picks_6", len(res.get("picks", [])) == 6, str(len(res.get("picks", []))))
    check("opt.uses_best_set", all(p["set"] == "好" for p in res["picks"]), str(res["picks"]))
    check("opt.metric_positive", res["metric_value"] > 8000, str(res["metric_value"]))

    # 主属性约束：2 号位只允许速度
    res2 = O.optimize(souls, BASE, "攻击 * 暴伤", slots_allowed={2: ["speed"]}, top_k=4, beam=50)
    check("opt.slot_constraint", res2["ok"] and res2["picks"][1]["main"] == "speed",
          str(res2.get("picks")))

    # 属性区间约束无法满足时返回 ok=False 而不是崩
    res3 = O.optimize(souls, BASE, "攻击 * 暴伤",
                      constraints={"attack": (999999.0, None)})
    check("opt.infeasible", res3["ok"] is False, str(res3))

    # 提升方向：只有当存在更强候选时才有内容
    res4 = O.optimize(souls, BASE, "攻击 * 暴伤", top_k=4, beam=50)
    check("opt.upgrades_is_list", isinstance(res4.get("upgrades"), list), str(type(res4.get("upgrades"))))

    print("\nWORKBENCH PANEL SELFTEST: %s   TOTAL %d PASS %d FAIL %d"
          % ("PASSED" if FAIL == 0 else "FAILED", PASS + FAIL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
