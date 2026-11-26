"""模拟玩法（复刻平安志 0.2.2-beta 的四类模拟，不含其代码与资产）。

数据与常量来源
--------------
`data/gameplay_options.json` = 从平安志 IPC `get_simulation_gameplay_options` 原样落盘，
里面除了 ruleVersion / poolVersion / goldSets(57) / demonSets(12) / pool(215) 之外，
还带着 `exchangeEvidence` / `summonEvidence` 两段说明（官方公告数字 + 明确标注的娱乐假设）。
本模块的概率模型逐条对应那两段说明，未确认的地方在代码里标 `# 假设`，不要当成官方数据。

四类模拟
--------
* enhance()           —— 随机生成 6★ 胚子 → 强化到 +15 → 用本仓库规则引擎打分，输出分数分布
* virtual_enhance()   —— 给定胚子（已有副属性/腿数），只模拟强化落点
* exchange()          —— 按「套装 + 号位」生成若干枚，并给出材料成本
* summoning()         —— 召唤抽卡：R/SR/SSR/SP 基础概率 + 60 抽内 SSR/SP 保底

所有随机都走 `random.Random(seed)`：同一个 seed 结果完全可复现，便于回归与对账。
"""
from __future__ import annotations

import json
import os
import random
from typing import Any, Dict, Iterable, List, Optional

from tasks.Workbench import engine as _engine

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OPTIONS_PATH = os.path.join(DATA_DIR, "gameplay_options.json")

# ---- 属性与概率常量 -------------------------------------------------------
# 交换类别权重：官方公示「特殊主属性合计 10%，其他 90%；副属性 攻击/防御/功能 36%/36%/28%」
_ATTR_CATEGORY = {
    "attack": ["attack_flat", "attack_rate", "crit_rate", "crit_damage"],
    "defense": ["defense_flat", "defense_rate", "hp_flat", "hp_rate"],
    "utility": ["speed", "effect_hit", "effect_resist"],
}
_CATEGORY_WEIGHTS = {"attack": 0.36, "defense": 0.36, "utility": 0.28}  # 官方公示
_INITIAL_COUNT_WEIGHTS = {2: 1 / 3, 3: 1 / 3, 4: 1 / 3}  # 假设（用户确认的娱乐假设）

# 6★ 单次副属性成长范围（# 假设：社区常用范围，若要精确到官方请替换本表）
_GROWTH_RANGE = {
    "attack_flat": (21.0, 25.0),
    "hp_flat": (165.0, 195.0),
    "defense_flat": (12.0, 15.0),
    "attack_rate": (2.7, 3.2),
    "hp_rate": (2.7, 3.2),
    "defense_rate": (2.7, 3.2),
    "crit_rate": (2.7, 3.2),
    "crit_damage": (3.4, 4.0),
    "speed": (2.4, 2.9),
    "effect_hit": (2.7, 3.2),
    "effect_resist": (2.7, 3.2),
}
_GROWTH_LOW_HIGH = (0.8, 1.0)  # 官方说明「成长值在既有参考范围 80%~100% 均匀抽样」

# 号位主属性候选（6★；1/3/5 固定）
_SLOT_MAIN = {
    1: ["attack_flat"],
    2: ["speed", "attack_rate", "hp_rate", "defense_rate", "effect_hit", "effect_resist"],
    3: ["defense_flat"],
    4: ["attack_rate", "hp_rate", "defense_rate", "effect_hit", "effect_resist"],
    5: ["hp_flat"],
    6: ["crit_rate", "crit_damage", "attack_rate", "hp_rate", "defense_rate"],
}

# 召唤基础概率（官方公告：R 78.75% / SR 20% / SSR 1% / SP 0.25%）
_SUMMON_RATES = [("R", 0.7875), ("SR", 0.20), ("SSR", 0.01), ("SP", 0.0025)]
_SSR_PITY = 60  # 60 抽内必出 SSR/SP（# 假设：按公告的 SSR/SP 保底口径简化）

# 兑换成本（# 假设：普通/首领 50 材料/枚，星痕 50 星痕之魂/枚）
COST_PER_SOUL = {"gold": 50, "demon": 50, "starmark": 50}


def options() -> Dict[str, Any]:
    """返回平安志自己给出的模拟选项（版本、套装清单、卡池、证据说明）。"""
    with open(OPTIONS_PATH, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return {
        "ruleVersion": raw.get("ruleVersion"),
        "poolVersion": raw.get("poolVersion"),
        "goldSets": list(raw.get("goldSets") or []),
        "demonSets": list(raw.get("demonSets") or []),
        "pool": list(raw.get("pool") or []),
        "exchangeEvidence": list(raw.get("exchangeEvidence") or []),
        "summonEvidence": list(raw.get("summonEvidence") or []),
    }


# ---- 内部：随机胚子 -------------------------------------------------------

def _pick_category(rng: random.Random) -> str:
    r = rng.random()
    acc = 0.0
    for name, w in _CATEGORY_WEIGHTS.items():
        acc += w
        if r <= acc:
            return name
    return "utility"


def _sample_substats(rng: random.Random, count: int) -> List[str]:
    """按类别抽副属性；类别抽空时在剩余类别间归一化（官方说明的口径）。"""
    pools = {k: list(v) for k, v in _ATTR_CATEGORY.items()}
    out: List[str] = []
    while len(out) < count and any(pools.values()):
        live = {k: v for k, v in pools.items() if v}
        total = sum(_CATEGORY_WEIGHTS[k] for k in live)
        r = rng.random() * total
        acc = 0.0
        chosen = next(iter(live))
        for k, v in live.items():
            acc += _CATEGORY_WEIGHTS[k]
            if r <= acc:
                chosen = k
                break
        attr = rng.choice(pools[chosen])
        pools[chosen].remove(attr)
        out.append(attr)  # 副属性内部去重；主副同名允许
    return out


def _initial_value(rng: random.Random, attr: str) -> float:
    lo, hi = _GROWTH_RANGE[attr]
    low, high = _GROWTH_LOW_HIGH
    return round(rng.uniform(lo * low, hi * high), 3)


def _roll_target(rng: random.Random, subs: List[str], rolls_used: Dict[str, int]) -> str:
    """累计权重强化：每条初始权重 1，命中后 +1（官方公告里对娱乐模型的口径）。"""
    weights = [1 + rolls_used.get(a, 0) for a in subs]
    total = sum(weights)
    r = rng.random() * total
    acc = 0.0
    for attr, w in zip(subs, weights):
        acc += w
        if r <= acc:
            return attr
    return subs[-1]


def _make_soul(rng: random.Random, set_name: str, slot: int,
               initial_count: Optional[int] = None,
               main_attr: Optional[str] = None) -> Dict[str, Any]:
    count = initial_count or rng.choices(
        list(_INITIAL_COUNT_WEIGHTS), weights=list(_INITIAL_COUNT_WEIGHTS.values()))[0]
    subs = _sample_substats(rng, count)
    values = {a: _initial_value(rng, a) for a in subs}
    return {
        "set_id": set_name,
        "slot": slot,
        "main_attribute": main_attr or rng.choice(_SLOT_MAIN[slot]),
        "substats": values,
        "rolls": {a: 0 for a in subs},
        "initial_substat_count": len(subs),
        "level": 0,
        "quality": 6,
        "scenario": "PVE",
    }


def _enhance(rng: random.Random, soul: Dict[str, Any], rolls: int = 5) -> Dict[str, Any]:
    """把胚子强化到 +15：5 次落点，命中后写到 rolls 与 substats 数值上。"""
    subs = list(soul["substats"])
    used = dict(soul.get("rolls") or {})
    for _ in range(rolls):
        attr = _roll_target(rng, subs, used)
        used[attr] = used.get(attr, 0) + 1
        lo, hi = _GROWTH_RANGE[attr]
        low, high = _GROWTH_LOW_HIGH
        soul["substats"][attr] = round(soul["substats"][attr] + rng.uniform(lo * low, hi * high), 3)
    soul["rolls"] = used
    soul["level"] = 15
    return soul


def _score(souls: List[Dict[str, Any]], standard_id: Optional[str] = None) -> Dict[str, Any]:
    """用本仓库的规则引擎打分（口径与平安志一致：Σ 权重 × (1+手数)）。"""
    kwargs: Dict[str, Any] = {}
    if standard_id:
        kwargs["standard_id"] = standard_id
    return _engine.analyze_many(souls, **kwargs)


def _summarize(scored: Dict[str, Any], top: int = 5) -> Dict[str, Any]:
    results = scored.get("results") or []
    scores = sorted((float(r.get("score", 0)) for r in results), reverse=True)
    n = len(scores) or 1
    buckets = {"rainbow(>=75)": 0, "gold(>=70)": 0, "jade(>=60)": 0, "keep(>=50)": 0, "observe(<50)": 0}
    for s in scores:
        if s >= 75:
            buckets["rainbow(>=75)"] += 1
        if s >= 70:
            buckets["gold(>=70)"] += 1
        if s >= 60:
            buckets["jade(>=60)"] += 1
        if s >= 50:
            buckets["keep(>=50)"] += 1
        else:
            buckets["observe(<50)"] += 1
    return {
        "count": len(scores),
        "mean": round(sum(scores) / n, 2),
        "max": scores[0] if scores else 0,
        "buckets": buckets,
        "by_decision": scored.get("by_decision"),
        "standard": scored.get("standard"),
        "top": (scored.get("results") or [])[:top],
    }


# ---- 四类模拟 -------------------------------------------------------------

def _done(data: Dict[str, Any]) -> Dict[str, Any]:
    """统一出口：有 error 就是 ok=False，否则 ok=True（路由与自检都用这个字段）。"""
    data["ok"] = "error" not in data
    return data


def enhance(trials: int = 200, seed: Optional[int] = None, standard_id: Optional[str] = None,
            boss: bool = False, slots: Optional[Iterable[int]] = None) -> Dict[str, Any]:
    """随机胚子 → 强化 → 打分；返回分数分布与最好的几条。"""
    rng = random.Random(seed)
    opts = options()
    set_pool = opts["demonSets"] if boss else opts["goldSets"]
    slot_pool = list(slots or range(1, 7))
    souls = []
    for _ in range(max(1, min(int(trials), 5000))):
        soul = _make_soul(rng, rng.choice(set_pool), rng.choice(slot_pool))
        souls.append(_enhance(rng, soul))
    scored = _score(souls, standard_id)
    out = _summarize(scored)
    out.update({
        "kind": "enhance", "seed": seed, "trials": len(souls),
        "ruleVersion": opts["ruleVersion"], "poolVersion": opts["poolVersion"],
        "note": "初始腿数与成长区间属娱乐假设，见 exchangeEvidence「初始御魂」条",
    })
    return _done(out)


def virtual_enhance(souls: List[Dict[str, Any]], seed: Optional[int] = None,
                    threshold: float = 70.0, standard_id: Optional[str] = None) -> Dict[str, Any]:
    """给定胚子只模拟强化落点：返回每条的最终分数与是否达到阈值。"""
    rng = random.Random(seed)
    prepared = []
    for raw in souls or []:
        soul = _engine.normalize_soul(raw)
        prepared.append({
            "set_id": soul["set_id"], "slot": soul["slot"] or 1,
            "main_attribute": soul["main_attribute"], "main_value": soul["main_value"],
            "substats": dict(soul["substats"]), "rolls": dict(soul["rolls"]),
            "initial_substat_count": soul["initial_substat_count"],
            "level": 0, "quality": soul["quality"], "scenario": soul["scenario"],
            "fixed_attrs": soul.get("fixed_attrs") or [],
            "fixed_stats": soul.get("fixed_stats") or {},
        })
    if not prepared:
        return {"kind": "virtual_enhance", "error": "souls 为空", "count": 0}
    for soul in prepared:
        _enhance(rng, soul)
    scored = _score(prepared, standard_id)
    results = []
    hit = 0
    for row in scored.get("results") or []:
        score = float(row.get("score", 0))
        ok = score >= threshold
        hit += 1 if ok else 0
        results.append({"score": score, "rule": row.get("rule"), "decision": row.get("decision"),
                        "reached": ok})
    out = {
        "kind": "virtual_enhance", "seed": seed, "threshold": threshold,
        "count": len(results), "reached": hit,
        "reached_ratio": round(hit / len(results), 4) if results else 0.0,
        "results": results[:50], "summary": _summarize(scored),
    }
    return _done(out)


def exchange(set_name: str, slot: int = 5, count: int = 10, seed: Optional[int] = None,
             boss: bool = False, standard_id: Optional[str] = None) -> Dict[str, Any]:
    """按套装 + 号位兑换若干枚：给出成本、生成结果与分数分布。"""
    opts = options()
    allowed = opts["demonSets"] if boss else opts["goldSets"]
    if set_name not in allowed:
        return _done({"kind": "exchange", "error": f"该玩法不支持兑换所选套装: {set_name}",
                      "allowed": allowed[:10], "allowedCount": len(allowed)})
    if not 1 <= int(slot) <= 6:
        return _done({"kind": "exchange", "error": "号位必须是 1..6"})
    n = max(1, min(int(count), 500))
    rng = random.Random(seed)
    souls = [_enhance(rng, _make_soul(rng, set_name, int(slot))) for _ in range(n)]
    scored = _score(souls, standard_id)
    unit = COST_PER_SOUL["demon" if boss else "gold"]
    return _done({
        "kind": "exchange", "seed": seed, "set": set_name, "slot": int(slot), "count": n,
        "cost": {"unit": unit, "total": unit * n, "currency": "御魂材料/星痕之魂（假设值）"},
        "summary": _summarize(scored),
    })


def summoning(count: int = 10, seed: Optional[int] = None, full_collection: bool = False,
              mode: str = "normal") -> Dict[str, Any]:
    """召唤模拟：基础概率 + 60 抽内 SSR/SP 保底；产出按稀有度的式神名。"""
    if mode not in ("normal", "现世", "神秘符咒", "single", "ten"):
        # 平安志的 mode 语义未完全公开，这里接受常见别名，其余按 normal 处理并回显
        mode = "normal"
    n = max(1, min(int(count), 5000))
    rng = random.Random(seed)
    opts = options()
    pool_by_rarity: Dict[str, List[str]] = {}
    for row in opts["pool"]:
        pool_by_rarity.setdefault(str(row.get("rarity")), []).append(str(row.get("name")))
    names = [r for r, _ in _SUMMON_RATES]
    weights = [w for _, w in _SUMMON_RATES]
    results: List[Dict[str, Any]] = []
    pity = 0
    for i in range(n):
        pity += 1
        rarity = rng.choices(names, weights=weights)[0]
        if pity >= _SSR_PITY and rarity not in ("SSR", "SP"):
            rarity = "SSR"  # 保底（简化：按 SSR 记账，公告口径是 SSR/SP）
            pity = 0
        if rarity in ("SSR", "SP"):
            pity = 0
        bucket = pool_by_rarity.get(rarity) or []
        results.append({"index": i + 1, "rarity": rarity,
                        "name": rng.choice(bucket) if bucket else None})
    stats: Dict[str, int] = {}
    for row in results:
        stats[row["rarity"]] = stats.get(row["rarity"], 0) + 1
    return _done({
        "kind": "summoning", "seed": seed, "mode": mode, "count": n,
        "fullCollection": bool(full_collection),
        "rates": {r: w for r, w in _SUMMON_RATES}, "pityWithin": _SSR_PITY,
        "stats": stats, "results": results[:100],
        "note": "基础概率为官方公告值；60 抽保底与卡池抽样为简化模型，见 summonEvidence",
    })


HANDLERS = {
    "enhance": enhance,
    "virtual_enhance": virtual_enhance,
    "exchange": exchange,
    "summoning": summoning,
}


def run(kind: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """路由入口：`payload` 直接来自 POST body。"""
    handler = HANDLERS.get(kind)
    if handler is None:
        return {"ok": False, "error": f"不支持的模拟类型: {kind}", "kinds": sorted(HANDLERS)}
    payload = dict(payload or {})
    payload.pop("kind", None)
    try:
        data = handler(**{k: v for k, v in payload.items()
                          if k in ("trials", "seed", "standard_id", "boss", "slots",
                                   "souls", "threshold", "set_name", "slot", "count",
                                   "full_collection", "mode")})
    except TypeError as exc:
        return {"ok": False, "error": f"参数不匹配: {exc}"}
    data["ok"] = "error" not in data
    return data
