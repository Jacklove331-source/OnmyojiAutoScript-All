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


def load_inventory(path: str = DEFAULT_INVENTORY) -> list:
    """读取御魂库存；文件不存在返回空列表（调用方必须处理空库存）。"""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding='utf-8') as file:
            data = json.load(file)
    except (OSError, ValueError):
        return []
    raw = data.get('souls') if isinstance(data, dict) else data
    result = []
    for index, item in enumerate(raw or []):
        if not isinstance(item, dict):
            continue
        result.append(Soul(
            uid=str(item.get('uid') or f'soul-{index}'),
            set_name=str(item.get('set') or item.get('set_name') or ''),
            slot=int(item.get('slot') or 0),
            level=int(item.get('level') or 0),
            main_stat=str(item.get('main_stat') or ''),
            main_value=float(item.get('main_value') or 0),
            sub=dict(item.get('sub') or {}),
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
