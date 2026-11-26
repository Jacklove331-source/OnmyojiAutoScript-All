# This file uses the following encoding: utf-8
"""评分校准：把「属性值 → 0~100 分」的归一化参数拟合到实测样本。

背景：规则包（yys-score-standard-package）只给权重与阈值
（coreAttributeWeight / generalAttributeWeight / keepScore / observeScore），
真正的归一化函数在前端加密包里。本模块用**黑箱样本**（御魂 → 平安志给出的分数）
反推三个参数，不改引擎代码。

样本文件格式（JSON 数组）：
    [{"soul": {…同 engine.normalize_soul 的输入…}, "score": 72.5}, …]

用法：
    python -m tasks.Workbench.calibrate samples.json
    python -m tasks.Workbench.calibrate samples.json --grid 0.5,1,2,5,10
输出：最优参数 + 误差（MAE）+ 可直接粘进 engine.DEFAULT_CALIBRATION 的 json。
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from typing import Any, Dict, List, Tuple

from tasks.Workbench.engine import DEFAULT_CALIBRATION, RuleEngine, Standards, Catalog


def load_samples(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("样本文件必须是数组")
    out = []
    for row in data:
        if "soul" not in row or "score" not in row:
            continue
        out.append({"soul": row["soul"], "score": float(row["score"])})
    return out


def evaluate(samples: List[Dict[str, Any]], calibration: Dict[str, float],
             standard_id: str = None) -> Dict[str, float]:
    eng = RuleEngine(catalog=Catalog(), standards=Standards(),
                     standard_id=standard_id, calibration=calibration)
    errs = []
    for s in samples:
        got = eng.score(s["soul"])["score"]
        errs.append(abs(got - s["score"]))
    n = len(errs) or 1
    return {"mae": round(sum(errs) / n, 3),
            "max_err": round(max(errs), 3) if errs else 0.0,
            "n": len(errs)}


def grid_search(samples: List[Dict[str, Any]], units: List[float],
                divisors: List[float], ceilings: List[float],
                standard_id: str = None) -> Tuple[Dict[str, float], Dict[str, float]]:
    best = None
    for unit, div, ceil in itertools.product(units, divisors, ceilings):
        cal = dict(DEFAULT_CALIBRATION, unit=unit, flat_divisor=div, ceiling=ceil)
        m = evaluate(samples, cal, standard_id)
        if best is None or m["mae"] < best[1]["mae"]:
            best = (cal, m)
    return best


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("samples")
    ap.add_argument("--grid", default="0.5,1,2,5,10,20",
                    help="unit 候选，逗号分隔")
    ap.add_argument("--divisors", default="50,100,200,500")
    ap.add_argument("--ceilings", default="100,150,200")
    ap.add_argument("--standard", default=None)
    args = ap.parse_args(argv)

    samples = load_samples(args.samples)
    if not samples:
        print("样本为空或字段缺失（需要 soul + score）")
        return 1

    cal, metrics = grid_search(
        samples,
        [float(x) for x in args.grid.split(",")],
        [float(x) for x in args.divisors.split(",")],
        [float(x) for x in args.ceilings.split(",")],
        args.standard)
    print("best calibration:", json.dumps(cal, ensure_ascii=False))
    print("metrics:", json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
