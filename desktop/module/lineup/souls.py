# This Python file uses the following encoding: utf-8

"""御魂库存与配装计算。

设计要点：
- 库存来自 ``config/souls.json``（由扫描/手工导入填充），本模块只做筛选与计算；
- 配装不能只看套装名：按「套装 + 二号位速度 + 四/六号位主属性 + 副属性阈值」联合挑选，
  并算出可用于校验的面板增量（速度 / 暴击 / 暴伤 / 攻击加成）；
- 找不到理想组合时返回最优可行替代 + 缺失项，绝不虚构不存在的御魂。
"""

import json
import os
from dataclasses import dataclass, field

DEFAULT_INVENTORY = 'config/souls.json'

# 各号位可能出现的主属性（游戏规则）
SLOT_MAIN_STATS = {
    2: ('速度', '攻击加成', '生命加成', '防御加成', '效果命中', '效果抵抗'),
    4: ('攻击加成', '生命加成', '防御加成', '效果命中', '效果抵抗'),
    6: ('暴击', '暴击伤害', '攻击加成', '生命加成', '防御加成'),
}

# 属性对通用评分的权重（速度优先，其次暴击/暴伤/攻击）
STAT_WEIGHT = {
    '速度': 2.0,
    '暴击': 1.6,
    '暴击伤害': 1.4,
    '攻击加成': 0.8,
    '效果命中': 0.6,
    '效果抵抗': 0.4,
    '生命加成': 0.3,
    '防御加成': 0.3,
}


@dataclass(frozen=True)
class Soul:
    uid: str
    set_name: str
    slot: int
    level: int = 15
    main_stat: str = ''
    main_value: float = 0.0
    sub: dict = field(default_factory=dict)

    def score(self) -> float:
        total = self.main_value * STAT_WEIGHT.get(self.main_stat, 0.5)
        for stat, value in (self.sub or {}).items():
            total += float(value) * STAT_WEIGHT.get(stat, 0.3)
        return round(total + self.level * 0.2, 3)


ATTR_EN_TO_CN = {
    'attack_flat': '攻击',
    'attack_rate': '攻击加成',
    'defense_flat': '防御',
    'defense_rate': '防御加成',
    'hp_flat': '生命',
    'hp_rate': '生命加成',
    'speed': '速度',
    'crit_rate': '暴击',
    'crit_damage': '暴击伤害',
    'effect_hit': '效果命中',
    'effect_resist': '效果抵抗',
}


def _norm_attr(name: str) -> str:
    key = str(name or '').strip()
    return ATTR_EN_TO_CN.get(key, key)


def load_inventory(path: str | None = DEFAULT_INVENTORY) -> list:
    """读取御魂库存；若 config/souls.json 不存在，自动回退读取 OAS 工作台御魂库存。"""
    raw = None
    target = path or DEFAULT_INVENTORY
    if target and os.path.exists(target):
        try:
            with open(target, encoding='utf-8') as file:
                data = json.load(file)
            raw = data.get('souls') if isinstance(data, dict) else data
        except (OSError, ValueError):
            raw = None

    if raw is None:
        # 自动回退到 OAS 工作台 Store / config/workbench/souls.json
        try:
            from tasks.Workbench.store import Store
            raw = Store().souls()
        except Exception:  # noqa: BLE001
            wb_souls = 'config/workbench/souls.json'
            if os.path.exists(wb_souls):
                try:
                    with open(wb_souls, encoding='utf-8') as file:
                        raw = json.load(file)
                except (OSError, ValueError):
                    raw = []
            else:
                raw = []

    result = []
    for index, item in enumerate(raw or []):
        if not isinstance(item, dict):
            continue
        raw_sub = item.get('sub') if item.get('sub') is not None else item.get('substats')
        sub_norm = {_norm_attr(k): float(v) for k, v in (raw_sub or {}).items() if v is not None}
        main_stat = _norm_attr(item.get('main_stat') or item.get('main') or item.get('main_attribute') or '')
        result.append(Soul(
            uid=str(item.get('uid') or item.get('internal_id') or f'soul-{index}'),
            set_name=str(item.get('set') or item.get('set_id') or item.get('set_name') or ''),
            slot=int(item.get('slot') or 0),
            level=int(item.get('level') or 0),
            main_stat=main_stat,
            main_value=float(item.get('main_value') or 0),
            sub=sub_norm,
        ))
    return result


@dataclass
class Plan:
    """一套御魂方案的计算结果。"""
    set_name: str
    picks: dict = field(default_factory=dict)   # slot -> Soul
    bonus: dict = field(default_factory=dict)   # 属性 -> 合计
    missing: list = field(default_factory=list)  # 未满足的槽位/条件
    score: float = 0.0

    def speed(self) -> float:
        return self.bonus.get('速度', 0.0)

    def describe(self) -> str:
        parts = [f'{slot}号:{soul.set_name}/{soul.main_stat}{soul.main_value:g}'
                 for slot, soul in sorted(self.picks.items())]
        return f'{self.set_name}: ' + ', '.join(parts) + f' | 速度+{self.speed():g}'


def best_plan(set_name: str, poses: dict, inventory: list,
              min_speed: float = 0.0) -> Plan:
    """为单个式神挑御魂。

    :param set_name: 目标套装名（四件套的本体）
    :param poses: {号位: 必需主属性}，例如 {2: '速度', 4: '攻击加成', 6: '暴击'}
    :param inventory: 库存
    :param min_speed: 该式神需要的最低御魂速度加成
    """
    plan = Plan(set_name=set_name)
    pool = [soul for soul in inventory if soul.set_name == set_name]
    if not pool:
        plan.missing.append(f'套装[{set_name}]库存为0')
        return plan

    for slot, main_stat in poses.items():
        candidates = [
            soul for soul in pool
            if soul.slot == slot and (not main_stat or soul.main_stat == main_stat)
        ]
        if not candidates:
            # 退而求其次：同号位任意主属性
            candidates = [soul for soul in pool if soul.slot == slot]
            if candidates:
                plan.missing.append(f'{slot}号位缺[{main_stat}]，用替代主属性')
            else:
                plan.missing.append(f'{slot}号位无可用御魂')
                continue
        best = max(candidates, key=Soul.score)
        plan.picks[slot] = best
        plan.bonus[best.main_stat] = plan.bonus.get(best.main_stat, 0.0) + best.main_value
        for stat, value in (best.sub or {}).items():
            plan.bonus[stat] = plan.bonus.get(stat, 0.0) + float(value)

    plan.score = round(sum(soul.score() for soul in plan.picks.values()), 3)
    if plan.speed() < min_speed:
        plan.missing.append(f'速度不足: {plan.speed():g} < {min_speed:g}')
    return plan


def check_speed_order(plans: dict, order: list) -> list:
    """校验配速顺序：order 里前面的式神速度必须不低于后面的。

    :param plans: {式神名: Plan}
    :param order: 期望行动顺序（先手在前）
    """
    problems = []
    previous = None
    for name in order:
        plan = plans.get(name)
        if plan is None:
            problems.append(f'{name}: 没有御魂方案')
            continue
        speed = plan.speed()
        if previous is not None and speed > previous[1]:
            problems.append(f'{name}({speed:g}) 快过先手位 {previous[0]}({previous[1]:g})')
        previous = (name, speed)
    return problems
