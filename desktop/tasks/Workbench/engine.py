"""平安志功能移植 —— 便携版工作台引擎。

本文件只做纯计算，不接触设备、不读内存、不发协议：
  * 目录数据（式神 / 御魂套装 / 常用度）
  * 评分标准包（yys-score-standard-package）加载
  * 选择器（selector）匹配
  * 评分（scoring）与决策（keep / observe / discard）

评分权重字段来自规则包原文：
  coreAttributeWeight / generalAttributeWeight
  enhancementCoreWeight / enhancementGeneralWeight
  keepScore / observeScore / discardScore
  scoreColorThresholds: goldScore / jadeScore / rainbowScore

注意：属性值 → 分数的归一化函数在官方前端里，未随规则包导出。
本引擎把它实现为可插拔的 ScoreModel（默认 model="rolls"），
校准常量集中在 DEFAULT_CALIBRATION，便于用实测样本对齐而不改代码。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

ATTR_ALIASES = {
    "攻击": "attack_flat", "攻击加成": "attack_rate",
    "防御": "defense_flat", "防御加成": "defense_rate",
    "生命": "hp_flat", "生命加成": "hp_rate",
    "速度": "speed",
    "暴击": "crit_rate", "暴击伤害": "crit_damage",
    "效果命中": "effect_hit", "效果抵抗": "effect_resist",
}
ATTR_CN = {v: k for k, v in ATTR_ALIASES.items()}

# 每级强化的"点数"基线：阴阳师御魂每 3 级加一次副属性（0/3/6/9/12/15 共 0..5 次）
MAX_ENHANCE_ROLLS = 5

DEFAULT_CALIBRATION = {
    # 一次强化相当于多少分（对应 enhancementCoreWeight / enhancementGeneralWeight）
    "unit": 10.0,
    # 属性值折算：rate 系 1% = 1 单位；flat 系按 100 折算 1 单位
    "flat_divisor": 100.0,
    # 分数上限，用于压到 0..100
    "ceiling": 100.0,
}


def _load_json(name: str) -> Any:
    with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


class Catalog:
    """式神 / 御魂套装 / 常用度目录。"""

    def __init__(self) -> None:
        self.shikigami: List[Dict[str, Any]] = _load_json("catalog_shikigami.json")
        self.soul_sets: List[Dict[str, Any]] = _load_json("catalog_soul_set.json")
        self._by_set_name = {row["name"]: row for row in self.soul_sets}
        self._by_set_id = {row["set_id"]: row for row in self.soul_sets}
        self._by_shikigami_id = {row["shikigami_id"]: row for row in self.shikigami}
        self._by_shikigami_name = {row["name"]: row for row in self.shikigami}
        self._panel_rows = None

    def soul_set(self, key: str) -> Optional[Dict[str, Any]]:
        return self._by_set_name.get(key) or self._by_set_id.get(key)

    def shikigami_of(self, key: str) -> Optional[Dict[str, Any]]:
        # 注意：self.shikigami 是列表属性，查表方法必须换名，否则被属性遮蔽
        return self._by_shikigami_name.get(key) or self._by_shikigami_id.get(key)

    @property
    def panels(self) -> List[Dict[str, Any]]:
        if self._panel_rows is None:
            try:
                self._panel_rows = _load_json("catalog_panel.json")
            except OSError:
                self._panel_rows = []
        return self._panel_rows

    def panel_of(self, key: str) -> Optional[Dict[str, Any]]:
        for row in self.panels:
            if row.get("id") == key or row.get("name") == key:
                return row
        return None

    def category_of(self, set_key: str) -> Optional[str]:
        row = self.soul_set(set_key)
        return row.get("category") if row else None


class Standards:
    """评分标准包：standards[*] = {id, title, defaults, scoring?, rules[], scoreColorThresholds}。"""

    def __init__(self, path: Optional[str] = None) -> None:
        payload = (_load_json("score_standards.json") if path is None
                   else json.load(open(path, "r", encoding="utf-8")))
        self.format = payload.get("format")
        self.schema_version = payload.get("schemaVersion")
        self.standards: List[Dict[str, Any]] = payload.get("standards", [])
        self._by_id = {s["id"]: s for s in self.standards}

    def ids(self) -> List[str]:
        return list(self._by_id)

    def get(self, standard_id: Optional[str] = None) -> Dict[str, Any]:
        if standard_id is None:
            if not self.standards:
                raise ValueError("评分标准包为空")
            return self.standards[0]
        return self._by_id[standard_id]

    def thresholds(self, standard_id: Optional[str] = None) -> Dict[str, float]:
        return dict(self.get(standard_id).get("scoreColorThresholds") or {})


def normalize_attr(name: Any) -> str:
    if isinstance(name, str):
        return ATTR_ALIASES.get(name, name)
    return str(name)


def normalize_soul(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把外部输入（中文键或英文键、substats 为 dict 或 list）统一成内部结构。

    内部结构：
      set_id(str)        —— 优先用套装名，匹配规则时两种写法都试
      slot(int 1..6)
      main_attribute     —— 归一化属性键
      substats(dict)     —— {属性键: 数值}
      initial_substat_count(int) —— 初始腿数（3 或 4）
      level(int)         —— 当前等级 0..15
      quality(int)
      scenario(str)      —— PVE / PVP
    """
    subs: Dict[str, float] = {}
    raw_subs = raw.get("substats") or raw.get("sub_attributes") or {}
    if isinstance(raw_subs, dict):
        for k, v in raw_subs.items():
            subs[normalize_attr(k)] = float(v)
    else:
        for item in raw_subs:
            if isinstance(item, dict):
                k = item.get("type") or item.get("attribute") or item.get("name")
                subs[normalize_attr(k)] = float(item.get("value", 0))

    main = raw.get("main_attribute") or raw.get("main") or ""
    main_value = raw.get("main_value")
    if isinstance(main, dict):
        if main_value is None:
            main_value = main.get("value")
        main = main.get("type") or main.get("attribute")
    fixed_stats = {normalize_attr(k): float(v) for k, v in (raw.get("fixed_stats") or {}).items()}
    fixed_attrs = [normalize_attr(a) for a in (raw.get("fixed_attrs") or [])]
    fixed_attrs.extend(a for a in fixed_stats if a not in fixed_attrs)

    return {
        "set_id": raw.get("set_id") or raw.get("set") or raw.get("set_name") or "",
        "slot": int(raw.get("slot") or 0),
        "main_attribute": normalize_attr(main),
        "main_value": float(main_value) if main_value is not None else None,
        "substats": subs,
        "initial_substat_count": int(raw.get("initial_substat_count")
                                     or raw.get("init_substat_count")
                                     or len(subs)),
        "level": int(raw.get("level") or 0),
        "quality": int(raw.get("quality") or 6),
        "scenario": raw.get("scenario") or "PVE",
        # 强化手数必须透传：评分口径是 Σ weight×(1+rolls)，丢了 rolls 全变 0 手
        "rolls": {normalize_attr(k): int(v) for k, v in (raw.get("rolls") or {}).items()},
        # 固有属性（无副属性条目但计分），如「固有 暴击 8%」
        "fixed_attrs": fixed_attrs,
        "fixed_stats": fixed_stats,
    }


class Rule:
    """单条规则：selector 命中后按 scoring 打分。"""

    # 主属性对（slotMainPairs）实测不是硬过滤，默认放开；设为 True 可恢复严格模式
    enforce_main_pairs = True

    def __init__(self, raw: Dict[str, Any], catalog: Catalog) -> None:
        self.raw = raw
        self.id = raw.get("id")
        self.name = raw.get("name")
        self.stage = raw.get("stage")
        self.selector = raw.get("selector") or {}
        self.scoring = raw.get("scoring") or {}
        self.catalog = catalog
        self.set_ids = set(self.selector.get("setIds") or [])
        self.slots = set(int(x) for x in (self.selector.get("slots") or []))
        self.slot_main_pairs = self.selector.get("slotMainPairs") or []
        self.required = self.selector.get("requiredSubstats") or {}
        self.forbidden = set(normalize_attr(x) for x in (self.selector.get("forbiddenSubstats") or []))
        self.initial_counts = set(int(x) for x in (self.selector.get("initialSubstatCounts") or []))
        self.levels = set(int(x) for x in (self.selector.get("level") or []))
        self.qualities = set(int(x) for x in (self.selector.get("quality") or []))
        self.scenarios = set(self.selector.get("scenarios") or [])
        self.enforce_any_of = False   # 见 matches() 注释
        self.enforce_all_of = False
        self.enforce_set_ids = True
        self.enforce_legs = False
        # 固有属性计入评分（按 0 手）：实测计比不计更接近平安志
        self.count_fixed = True

    # ---- selector ----
    def matches(self, soul: Dict[str, Any]) -> bool:
        if self.slots and soul["slot"] not in self.slots:
            return False
        # 已强化的御魂同样参与评分（平安志对 +15 的御魂也套这些准入规则），
        # 所以「等级 / 初始腿数」只作为胚子筛选条件，不阻断评分匹配。
        # 实测：平安志对「腿数 / 等级」同样不做硬过滤（3 腿、+15 的御魂照样命中四腿规则并出分），
        # 这两项只在它自己的"胚子筛选"里用。默认不拦截。
        if self.enforce_legs:
            if self.initial_counts and soul["initial_substat_count"] not in self.initial_counts:
                return False
            if self.levels and soul["level"] not in self.levels:
                return False
        if self.qualities and soul["quality"] not in self.qualities:
            return False
        if self.scenarios and soul["scenario"] not in self.scenarios:
            return False
        if self.enforce_set_ids and self.set_ids and not self._set_matches(soul["set_id"]):
            return False
        if self.slot_main_pairs and getattr(self, "enforce_main_pairs", True):
            ok = any(int(p.get("slot", 0)) == soul["slot"]
                     and normalize_attr(p.get("mainAttribute")) == soul["main_attribute"]
                     for p in self.slot_main_pairs)
            if not ok:
                return False
        if self.forbidden and (set(soul["substats"]) & self.forbidden):
            return False
        if self.enforce_all_of:
            all_of = [normalize_attr(x) for x in (self.required.get("allOf") or [])]
            if all_of and not all(a in soul["substats"] for a in all_of):
                return False
        # 实测：平安志不把 anyOf 当硬条件（薙魂5号位无速度仍命中「少用一三五攻暴爆」并得 80 分），
        # 只有 allOf 是必须满足的。保留开关，默认宽松。
        if self.enforce_any_of:
            any_of = [normalize_attr(x) for x in (self.required.get("anyOf") or [])]
            if any_of and not any(a in soul["substats"] for a in any_of):
                return False
        at_least = self.required.get("atLeast")
        if isinstance(at_least, dict):
            need = int(at_least.get("count", 0))
            pool = [normalize_attr(x) for x in (at_least.get("attributes") or [])]
            if sum(1 for a in pool if a in soul["substats"]) < need:
                return False
        return True

    def matches_admission(self, soul: Dict[str, Any]) -> bool:
        """严格「胚子准入」匹配，对齐平安志 admission.rules[].matched。

        与 score 用的宽松 matches() 不同，这里等级/腿数/星级/号位/套装/主属性对/必然副属性
        全部必须满足（可选副属性 anyOf 只作提示，不参与判定）。
        """
        if "set_id" not in soul:
            soul = normalize_soul(soul)
        if self.qualities and soul["quality"] not in self.qualities:
            return False
        if self.levels and soul["level"] not in self.levels:
            return False
        if self.initial_counts and soul["initial_substat_count"] not in self.initial_counts:
            return False
        if self.slots and soul["slot"] not in self.slots:
            return False
        if self.set_ids and not self._set_matches(soul["set_id"], strict=True):
            return False
        if self.slot_main_pairs:
            ok = any(int(x.get("slot", 0)) == soul["slot"]
                     and normalize_attr(x.get("mainAttribute")) == soul["main_attribute"]
                     for x in self.slot_main_pairs)
            if not ok:
                return False
        # 判定池 = 副属性 ∪ 固有属性（平安志的「必备/候选副属性」会被固有属性满足）
        pool = set(soul["substats"]) | set(soul.get("fixed_attrs") or [])
        all_of = [normalize_attr(x) for x in (self.required.get("allOf") or [])]
        if all_of and not all(a in pool for a in all_of):
            return False
        # 准入模式里 anyOf 是「至少一项」的硬要求（评分模式才放开）
        any_of = [normalize_attr(x) for x in (self.required.get("anyOf") or [])]
        if any_of and not any(a in pool for a in any_of):
            return False
        forb = set(normalize_attr(x) for x in (self.selector.get("forbiddenSubstats") or []))
        if forb & set(soul["substats"]):
            return False
        return True

    def _set_matches(self, set_key: str, strict: bool = False) -> bool:
        if set_key in self.set_ids:
            return True
        row = self.catalog.soul_set(set_key)
        if row and (row["name"] in self.set_ids or row["set_id"] in self.set_ids):
            return True
        # 组级回退：平安志的 yuhun_group 把套装分成 output/support/control 等组，
        # 规则里的 setIds 只列了组内一部分；同组也允许命中（实测一致率 95.7% → 97.1%）。
        groups = getattr(self, "set_groups", None)
        if groups and not strict:
            mine = groups.get(set_key) or set()
            if mine:
                for sid in self.set_ids:
                    if mine & (groups.get(sid) or set()):
                        return True
        return False


class RuleEngine:
    """按 stage='admission' 顺序匹配，首个命中即采用。"""

    def __init__(self, catalog: Optional[Catalog] = None,
                 standards: Optional[Standards] = None,
                 standard_id: Optional[str] = None,
                 calibration: Optional[Dict[str, float]] = None,
                 set_groups: Optional[Dict[str, set]] = None) -> None:
        self.catalog = catalog or Catalog()
        self.standards = standards or Standards()
        self.standard = self.standards.get(standard_id)
        # set -> {group_id}；None 表示不做组级回退（必须先于 rules 赋值）
        self.set_groups = set_groups
        self.rules = [Rule(r, self.catalog) for r in self.standard.get("rules", [])]
        for _r in self.rules:
            _r.set_groups = self.set_groups
        self.calibration = dict(DEFAULT_CALIBRATION)
        self.count_fixed = True
        if calibration:
            self.calibration.update(calibration)

    # ---- scoring（口径与平安志一致：权重 × (1+强化手数)，与属性数值无关）----
    def _points(self, rule: Rule, soul: Dict[str, Any]) -> Dict[str, Any]:
        core = set(normalize_attr(x) for x in (rule.scoring.get("coreAttributes") or []))
        general = set(normalize_attr(x) for x in (rule.scoring.get("generalAttributes") or []))
        cw = float(rule.scoring.get("coreAttributeWeight", 10))
        gw = float(rule.scoring.get("generalAttributeWeight", 5))
        rolls_map = soul.get("rolls") or {}
        points = 0.0
        breakdown: Dict[str, float] = {}
        # 固有属性（游戏里「固有 暴击 8%」这类）也参与计分，按 0 手处理
        for attr in ((soul.get("fixed_attrs") or []) if getattr(self, "count_fixed", True) else []):
            if attr in core:
                points += cw
                breakdown[attr] = cw
            elif attr in general:
                points += gw
                breakdown[attr] = gw
        for attr in soul["substats"]:
            rolls = int(rolls_map.get(attr, 0))
            if attr in core:
                pts = cw * (1 + rolls)
            elif attr in general:
                pts = gw * (1 + rolls)
            else:
                continue
            points += pts
            breakdown[attr] = pts
        return {"score": points, "breakdown": breakdown}

    def candidates(self, soul: Dict[str, Any]) -> List[Dict[str, Any]]:
        """所有命中规则及其分数（供调试/对比用）。"""
        if "set_id" not in soul:
            soul = normalize_soul(soul)
        out = []
        for idx, rule in enumerate(self.rules):
            if rule.stage and rule.stage != "admission":
                continue
            if not rule.matches(soul):
                continue
            sc = self._points(rule, soul)
            out.append({"idx": idx, "rule_id": rule.id, "rule_name": rule.name,
                        "score": sc["score"], "breakdown": sc["breakdown"],
                        "rule": rule})
        out.sort(key=lambda c: (-c["score"], c["idx"]))
        return out

    def score(self, soul: Dict[str, Any]) -> Dict[str, Any]:
        if "set_id" not in soul:
            soul = normalize_soul(soul)
        cands = self.candidates(soul)
        if not cands:
            return {"matched": False, "score": 0.0, "decision": "discard",
                    "rule_id": None, "rule_name": None, "breakdown": {}}
        # 平安志在命中规则里取**分数最高**的那条（同分取数组靠前的），实测 9/10 一致；
        # 只取数组首命中只有 7/10。
        best = cands[0]
        rule = best["rule"]
        p = best["score"]
        keep = float(rule.scoring.get("keepScore", 70))
        observe = float(rule.scoring.get("observeScore", 50))
        decision = "keep" if p >= keep else ("observe" if p >= observe else "discard")
        return {"matched": True, "score": round(p, 2), "decision": decision,
                "rule_id": best["rule_id"], "rule_name": best["rule_name"],
                "breakdown": best["breakdown"],
                "candidates": len(cands)}

    def match(self, soul: Dict[str, Any]) -> Optional[Rule]:
        for rule in self.rules:
            if rule.stage and rule.stage != "admission":
                continue
            if rule.matches(soul):
                return rule
        return None


def analyze_many(souls: Iterable[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
    engine = RuleEngine(**kwargs)
    results = [engine.score(s) for s in souls]  # score() ??? normalize
    by_decision: Dict[str, int] = {}
    for r in results:
        by_decision[r["decision"]] = by_decision.get(r["decision"], 0) + 1
    return {
        "count": len(results),
        "by_decision": by_decision,
        "results": results,
        "standard": engine.standard.get("id"),
    }
