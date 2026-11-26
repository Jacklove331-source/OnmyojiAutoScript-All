"""Workbench 模块回归：不碰设备，纯数据与算法自检。

用法（OAS 仓库根目录）：
    .\\toolkit\\python.exe tasks\\Workbench\\selftest.py
退出码 0 = 全绿。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tasks.Workbench.engine import (  # noqa: E402
    Catalog, Standards, RuleEngine, analyze_many, normalize_soul,
)

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


def main() -> int:
    cat = Catalog()
    check("catalog.soul_sets >= 70", len(cat.soul_sets) >= 70, str(len(cat.soul_sets)))
    check("catalog.shikigami >= 250", len(cat.shikigami) >= 250, str(len(cat.shikigami)))
    check("catalog.lookup 破势", cat.soul_set("破势") is not None)
    check("catalog.category 破势", bool(cat.category_of("破势")))

    std = Standards()
    ids = std.ids()
    check("standards >= 3", len(ids) >= 3, str(ids))
    eng = RuleEngine(catalog=cat, standards=std)
    check("rules loaded", len(eng.rules) > 0, str(len(eng.rules)))
    check("thresholds present", "goldScore" in std.thresholds(), str(std.thresholds()))

    # 正向样本：六号暴击主属性 + 双暴副属性 + 攻速 → 应命中"六号暴击双暴"这类规则
    good = normalize_soul({
        "set": "破势", "slot": 6, "main": "暴击",
        "substats": {"暴击伤害": 22.0, "攻击加成": 15.0, "速度": 8.0, "生命加成": 6.0},
        "level": 0, "quality": 6, "initial_substat_count": 4,
    })
    r_good = eng.score(good)
    check("good soul matched", r_good["matched"] is True, str(r_good))
    check("good soul rule hit", r_good["rule_id"] is not None, str(r_good.get("rule_id")))
    check("good soul score > 0", r_good["score"] > 0, str(r_good["score"]))

    # 负向样本：三号位固定防御主属性 + 无关副属性 → 不应命中六号位规则
    bad = normalize_soul({
        "set": "破势", "slot": 3, "main": "防御",
        "substats": {"生命加成": 5.0}, "level": 0, "quality": 6,
        "initial_substat_count": 3,
    })
    r_bad = eng.score(bad)
    check("bad soul not keep", r_bad["decision"] != "keep", str(r_bad))

    # 单调性：同一规则下，核心属性越高分越高
    base = {"set": "破势", "slot": 6, "main": "暴击", "level": 0,
            "quality": 6, "initial_substat_count": 4}
    # 评分口径 = Σ weight × (1 + 强化手数)，与数值无关 → 用 rolls 造差异
    # 该样本命中的是「速度胚子」类规则（core=speed），所以用速度强化手数造差异
    s1 = dict(base, substats={"速度": 2.0}, rolls={"速度": 0})
    s2 = dict(base, substats={"速度": 2.0}, rolls={"速度": 3})
    a = eng.score(normalize_soul(s1))
    b = eng.score(normalize_soul(s2))
    check("monotonic core attr", b["score"] > a["score"], "%s vs %s" % (a["score"], b["score"]))
    check("same rule", a["rule_id"] == b["rule_id"], "%s vs %s" % (a["rule_id"], b["rule_id"]))

    # 批量分析
    bulk = analyze_many([s1, s2, bad], catalog=cat, standards=std)
    check("bulk count", bulk["count"] == 3, str(bulk["count"]))
    check("bulk by_decision", sum(bulk["by_decision"].values()) == 3, str(bulk["by_decision"]))

    print("\nWORKBENCH SELFTEST: %s   TOTAL %d PASS %d FAIL %d" % (
        "PASSED" if FAIL == 0 else "FAILED", PASS + FAIL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
