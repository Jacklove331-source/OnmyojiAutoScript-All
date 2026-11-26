# This file uses the following encoding: utf-8
"""校准回归：合成样本 → 网格搜索 → 参数与分数可复现。

用法：.\\toolkit\\python.exe tasks\\Workbench\\selftest_calibrate.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tasks.Workbench import calibrate as C
from tasks.Workbench.engine import DEFAULT_CALIBRATION, RuleEngine, Catalog, Standards

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


def main() -> int:
    # 用一套已知参数生成"真值"分数，再让网格搜索找回来
    truth = dict(DEFAULT_CALIBRATION, unit=5.0, flat_divisor=100.0, ceiling=100.0)
    eng_truth = RuleEngine(catalog=Catalog(), standards=Standards(), calibration=truth)

    souls = [
        {"set": "破势", "slot": 6, "main": "暴击伤害", "level": 0, "quality": 6,
         "initial_substat_count": 4,
         "substats": {"暴击": 5.0, "攻击加成": 8.0, "速度": 6.0}},
        {"set": "针女", "slot": 2, "main": "速度", "level": 0, "quality": 6,
         "initial_substat_count": 3,
         "substats": {"暴击": 3.0, "暴击伤害": 15.0}},
        {"set": "心眼", "slot": 4, "main": "攻击加成", "level": 0, "quality": 6,
         "initial_substat_count": 4,
         "substats": {"速度": 4.0, "效果命中": 6.0, "生命加成": 3.0}},
    ]
    samples = [{"soul": s, "score": eng_truth.score(s)["score"]} for s in souls]
    check("samples.built", len(samples) == 3, str(len(samples)))
    check("samples.nonzero", any(s["score"] > 0 for s in samples), str(samples))

    tmp = tempfile.mkdtemp(prefix="cal_")
    path = os.path.join(tmp, "samples.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(samples, fh, ensure_ascii=False)
    loaded = C.load_samples(path)
    check("load_samples", len(loaded) == 3, str(len(loaded)))

    m0 = C.evaluate(samples, DEFAULT_CALIBRATION)
    cal, metric = C.grid_search(samples, [5.0], [100.0], [100.0])
    check("grid_recovers_unit", abs(cal["unit"] - 5.0) < 1e-9, str(cal))
    check("grid_zero_error", metric["mae"] == 0.0, str(metric))

    # 完整网格应优于或等于默认参数
    cal2, metric2 = C.grid_search(samples, [1.0, 5.0, 10.0], [100.0], [100.0])
    check("grid_beats_or_equals_default", metric2["mae"] <= m0["mae"],
          "%s vs %s" % (metric2, m0))

    print("\nWORKBENCH CALIBRATE SELFTEST: %s   TOTAL %d PASS %d FAIL %d"
          % ("PASSED" if FAIL == 0 else "FAILED", PASS + FAIL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
