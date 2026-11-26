# This file uses the following encoding: utf-8
"""面板计算/提升：在现有御魂库存里搜一套最接近目标的配装，并给出提升方向。

对应平安志「面板计算/提升」页：
    · 目标对象（式神基础面板）
    · 目标指标（面板最低值，或组合公式如 `攻击 × 暴伤 ≥ 30000`）
    · 属性限制（各属性的最低/最高区间）
    · 套装配方（套装方向 + 件数；2/4/6 号位主属性多选）

算法：每个号位先按主属性约束过滤，再按「对目标指标的边际贡献」排序取前 K，
在 K^6 的候选空间里做束搜索（beam），约束实时剪枝。目标指标用表达式求值，
只支持 + - × ÷ 与括号、属性名（避免 eval 注入）。

提升方向：对最优组合逐槽位替换为「未入选的最优候选」，按指标提升幅度排序输出。
"""
from __future__ import annotations

import ast
import math
import operator
import re
from collections import Counter
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

ATTR_KEYS = ("attack", "hp", "defense", "speed", "crit_rate", "crit_damage",
             "effect_hit", "effect_resist")
RATE = {"crit_rate", "crit_damage", "effect_hit", "effect_resist"}
ALIAS = {
    "攻击": "attack", "生命": "hp", "防御": "defense", "速度": "speed",
    "暴击": "crit_rate", "暴伤": "crit_damage", "暴击伤害": "crit_damage",
    "效果命中": "effect_hit", "效果抵抗": "effect_resist",
}


# ---------------------------------------------------------------- 属性合成
FLAT_KEY = {"attack": "attack_flat", "hp": "hp_flat", "defense": "defense_flat"}
RATE_KEY = {"attack": "attack_rate", "hp": "hp_rate", "defense": "defense_rate"}
POINT_KEYS = ("speed", "crit_rate", "crit_damage", "effect_hit", "effect_resist")


def _accumulate(souls: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """把若干御魂折算成 {flat, rate, point} 三组增量，三者口径不同不能混加。"""
    acc = {"flat": {k: 0.0 for k in FLAT_KEY}, "rate": {k: 0.0 for k in RATE_KEY},
           "point": {k: 0.0 for k in POINT_KEYS}}
    for soul in souls:
        pairs = []
        if soul.get("main"):
            pairs.append((soul["main"], float(soul.get("main_value") or 0)))
        pairs += [(k, float(v)) for k, v in (soul.get("substats") or {}).items()]
        pairs += (soul.get("fixed_stats") or {}).items()
        for attr, val in pairs:
            for key, fk in FLAT_KEY.items():
                if attr == fk:
                    acc["flat"][key] += val
            for key, rk in RATE_KEY.items():
                if attr == rk:
                    acc["rate"][key] += val
            if attr in POINT_KEYS:
                acc["point"][attr] += val
    return acc


def _combine(base: Dict[str, float], acc: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    final = {}
    for key in ("attack", "hp", "defense"):
        final[key] = round(base.get(key, 0) * (1 + acc["rate"][key] / 100.0)
                           + acc["flat"][key], 2)
    final["speed"] = round(base.get("speed", 0) + acc["point"]["speed"], 2)
    for key in ("crit_rate", "crit_damage", "effect_hit", "effect_resist"):
        final[key] = round(base.get(key, 0) + acc["point"][key] / 100.0, 4)
    return final


def combine(base: Dict[str, float], souls: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """基础面板 + 御魂 → 最终面板。"""
    return _combine(base, _accumulate(souls))


# ---------------------------------------------------------------- 表达式
_BIN_OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
            ast.Mult: operator.mul, ast.Div: operator.truediv}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_ATTR_NAMES = re.compile("|".join(sorted(ALIAS, key=len, reverse=True)))


@lru_cache(maxsize=64)
def validate_metric(expr: str) -> ast.expr:
    if not isinstance(expr, str) or not 0 < len(expr) <= 160:
        raise ValueError("目标指标应为不超过 160 字的表达式")
    text = _ATTR_NAMES.sub(lambda m: ALIAS[m[0]], expr).strip()
    text = text.replace("×", "*").replace("÷", "/")
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError as exc:
        raise ValueError("目标指标的表达式格式无效") from exc
    for part in ast.walk(node):
        if isinstance(part, ast.Name) and part.id in ATTR_KEYS:
            continue
        if isinstance(part, ast.Constant) and type(part.value) in (int, float) and math.isfinite(part.value):
            continue
        if isinstance(part, (ast.BinOp, ast.UnaryOp, ast.Load)) or type(part) in _BIN_OPS or type(part) in _UNARY_OPS:
            continue
        raise ValueError("目标指标只支持面板属性和四则运算")
    return node


def _eval(node: ast.expr, panel: Dict[str, float]) -> float:
    if isinstance(node, ast.Name):
        return panel.get(node.id, 0.0)
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        return _BIN_OPS[type(node.op)](_eval(node.left, panel), _eval(node.right, panel))
    return _UNARY_OPS[type(node.op)](_eval(node.operand, panel))


def evaluate_metric(expr: str, panel: Dict[str, float]) -> float:
    """求值目标指标，只允许属性名、数值和四则运算。"""
    try:
        value = _eval(validate_metric(expr), panel)
    except ZeroDivisionError as exc:
        raise ValueError("目标指标的除数为零") from exc
    if not math.isfinite(value):
        raise ValueError("目标指标的计算结果超出范围")
    return value


# ---------------------------------------------------------------- 优化
def _slot_of(soul: Dict[str, Any]) -> int:
    return int(soul.get("slot") or 0)


def _marginal(soul: Dict[str, Any], attr: str, base: Optional[Dict[str, float]] = None,
              expr: Optional[str] = None) -> float:
    """装上这件御魂后目标指标能涨多少（有 expr 时按真实增量算）。"""
    if expr and base:
        try:
            before = evaluate_metric(expr, combine(base, []))
            after = evaluate_metric(expr, combine(base, [soul]))
            return after - before
        except ValueError:
            pass
    return _accumulate([soul])["point"].get(attr, 0.0)


def optimize(souls: List[Dict[str, Any]], base: Dict[str, float], metric_expr: str,
             slots_allowed: Optional[Dict[int, List[str]]] = None,
             recipe: Optional[List[Dict[str, Any]]] = None,
             top_k: int = 12, beam: int = 200,
             constraints: Optional[Dict[str, Tuple[Optional[float], Optional[float]]]] = None
             ) -> Dict[str, Any]:
    """束搜索一套 6 件配装。

    slots_allowed: {号位: [允许的主属性键]}，缺省不限制
    recipe: [{"set": "片叶之苇", "count": 4}, {"set": "鬼灵歌伎", "count": 2}]
    constraints: {"attack": (min, max)}，对最终面板做区间限制
    """
    validate_metric(metric_expr)
    by_slot: Dict[int, List[Dict[str, Any]]] = {i: [] for i in range(1, 7)}
    focus = _focus_attr(metric_expr)
    for s in souls:
        slot = _slot_of(s)
        if slot not in by_slot:
            continue
        allow = (slots_allowed or {}).get(slot)
        if allow and s.get("main") not in allow:
            continue
        by_slot[slot].append(s)
    sets_needed = sorted({part["set"] for part in recipe or []})
    candidates = {}
    for slot, pool in by_slot.items():
        pool.sort(key=lambda s: -_marginal(s, focus, base, metric_expr))
        chosen = pool[:top_k]
        for set_name in sets_needed:
            chosen.extend([s for s in pool if s["set"] == set_name][:top_k])
        candidates[slot] = list({id(s): s for s in chosen}.values())

    empty = [slot for slot, pool in candidates.items() if not pool]
    if empty:
        return {"ok": False, "reason": "号位 %s 无可用御魂" % empty}

    states = [([], _accumulate([]))]
    for slot, pool in candidates.items():
        increments = [(soul, _accumulate([soul])) for soul in pool]
        nxt = []
        for picked, acc in states:
            for soul, extra in increments:
                new_picked = picked + [soul]
                if recipe and not _recipe_possible(new_picked, recipe):
                    continue
                new_acc = {group: {k: v + extra[group][k] for k, v in values.items()}
                           for group, values in acc.items()}
                nxt.append((new_picked, new_acc))
        nxt.sort(key=lambda st: -_partial_score(st[1], base, metric_expr))
        states = nxt[:beam]

    best = None
    for picked, acc in states:
        panel = _combine(base, acc)
        if constraints and not _constraints_ok(panel, constraints):
            continue
        try:
            val = evaluate_metric(metric_expr, panel)
        except ValueError:
            continue
        if best is None or val > best[0]:
            best = (val, picked, panel)
    if best is None:
        return {"ok": False, "reason": "没有满足约束的配装"}

    val, picked, panel = best
    return {"ok": True, "metric": metric_expr, "metric_value": round(val, 2),
            "panel": panel,
            "picks": [{"slot": _slot_of(s), "set": s.get("set"), "main": s.get("main"), "idx": s.get("idx"),
                       "level": s.get("level")} for s in sorted(picked, key=_slot_of)],
            "upgrades": _upgrades(picked, by_slot, base, metric_expr, val, recipe, constraints)}


def _focus_attr(expr: str) -> str:
    for cn, key in sorted(ALIAS.items(), key=lambda kv: -len(kv[0])):
        if cn in (expr or ""):
            return key
    return "attack"


def _recipe_possible(picked: List[Dict[str, Any]], recipe: List[Dict[str, Any]]) -> bool:
    counts = Counter(s.get("set") for s in picked)
    missing = sum(max(0, part["count"] - counts[part["set"]]) for part in recipe)
    return missing <= 6 - len(picked)


def _constraints_ok(panel: Dict[str, float],
                    constraints: Dict[str, Tuple[Optional[float], Optional[float]]]) -> bool:
    for key, (lo, hi) in constraints.items():
        v = panel.get(key, 0.0)
        if lo is not None and v < lo:
            return False
        if hi is not None and v > hi:
            return False
    return True


def _partial_score(acc: Dict[str, Dict[str, float]], base: Dict[str, float], expr: str) -> float:
    try:
        return evaluate_metric(expr, _combine(base, acc))
    except ValueError:
        return float("-inf")


def _upgrades(picked, by_slot, base, expr, best_val, recipe, constraints) -> List[Dict[str, Any]]:
    """逐槽位换成未入选的最优候选，报告能提升多少。"""
    out = []
    chosen = {_slot_of(s): s for s in picked}
    for slot, s in chosen.items():
        for alt in by_slot.get(slot, [])[:20]:
            if alt is s:
                continue
            trial = [x for x in picked if x is not s] + [alt]
            if recipe and not _recipe_possible(trial, recipe):
                continue
            panel = combine(base, trial)
            if constraints and not _constraints_ok(panel, constraints):
                continue
            try:
                val = evaluate_metric(expr, panel)
            except ValueError:
                continue
            if val > best_val + 1e-9:
                out.append({"slot": slot, "from": s.get("set"), "to": alt.get("set"),
                            "gain": round(val - best_val, 2), "metric_value": round(val, 2)})
                break
    out.sort(key=lambda r: -r["gain"])
    return out[:6]
