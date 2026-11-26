# This Python file uses the following encoding: utf-8

"""百鬼棋局「随机应变」阵容教练。

固定阵容表只能应对一种开局，但棋局每局发牌、对手、幻境都不一样。
本模块负责：给定当前局面（手牌、上阵、商店、金币、回目、对手羁绊），
动态算出应该主攻哪个羁绊、买哪些卡、站什么位置。

设计约束：

- 纯函数核心 ``plan()``，不接触设备，方便离线单测；
- 环境强度表内置一份（六道之门赛季），也支持从网络 JSON 覆盖；
- 决策有惯性（hysteresis）：局面没明显变化时不来回换阵容。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from module.logger import logger
from tasks.Chess.strategy.lineup_strategy import build_lineup_strategy
from tasks.Chess.strategy.season_data import (
    SHOP_MAX_COST_BY_LEVEL,
    shop_max_cost,
)
from tasks.Chess.strategy.shikigami_catalog import (
    SHIKIGAMI_ENTRIES,
    SHIKIGAMI_BY_ROMAJI,
    build_lineup_shikigami,
)

ADAPTIVE_KEY = 'adaptive'
ADAPTIVE_DISPLAY_NAME = '随机应变'

# 站位：1/3/5/7 是前排，2/4/6/8 是后排（与 board_positions 一致）。
FRONT_SLOTS = (1, 3, 5, 7)
BACK_SLOTS = (2, 4, 6, 8)

# 需要顶在前面的近战/坦克。未列出的默认站后排。
FRONT_UNITS = {
    '雪女', '雪御前', '禅冰雪女', '鲸汐千姬', '雪童子', '封阳君', '觉',
    '大岳丸', '酒吞童子', '茨木童子', '荒川之主', '黑童子', '鬼使黑',
    '判官', '山童', '狸猫', '蟹姬', '海坊主', '椒图', '白童子', '鬼使白',
    '古笼火', '化鲸', '久次良', '天火命铃彦姬',
}

# 前排优先级：数值越高越优先占前排位置（同分时按费用）。
FRONT_PRIORITY = {
    '雪女': 100,
    '禅冰雪女': 95,
    '雪御前': 90,
    '鲸汐千姬': 85,
    '雪童子': 80,
    '封阳君': 76,
    '大岳丸': 72,
    '觉': 60,
    '酒吞童子': 70,
    '茨木童子': 70,
    '荒川之主': 68,
    '黑童子': 64,
}

# 六道之门赛季的羁绊强度（0-100）。数值只影响「主攻谁」的相对排序，
# 不是绝对胜率；联网数据可以整表覆盖。
DEFAULT_BOND_TIER = {
    '寒霜': 95,
    '六道': 90,
    '易形': 88,
    '海国': 82,
    '遗念': 74,
    '回响': 72,
    '常世': 68,
    '七角山': 66,
    '冥府': 62,
    '流火': 58,
    '平安京': 56,
    '荒川': 52,
    '大江山': 50,
    '狐妖': 46,
    '鬼火': 40,
    '甲胄': 36,
    '分裂': 36,
    '强体': 32,
    '锻刃': 30,
    '追击': 30,
    '嗜战': 26,
    '护佑': 24,
}

# 单卡强度修正：费用是基础强度，这里补充「费用之外的价值」。
DEFAULT_UNIT_BONUS = {
    '雪御前': 20,
    '禅冰雪女': 18,
    '孔雀明王': 16,
    '月读': 16,
    '铃鹿御前': 15,
    '大岳丸': 15,
    '季': 14,
    '雪女': 12,
    '鲸汐千姬': 12,
    '平将门': 12,
    '阿修罗': 12,
    '酒吞童子': 12,
    '言灵': 12,
    '寻香行': 10,
    '禅心云外镜': 10,
    '茨木童子': 10,
    '妖刀姬': 10,
    '荒骷髅': 8,
    '青行灯': 8,
    '花鸟卷': 8,
    '鬼切': 8,
}

# 御魂偏好：与固定阵容的配置保持一致。
DEFAULT_SOULS = {
    '雪女': ('蚌精',),
    '鲸汐千姬': ('狰', '青女房'),
    '青行灯': ('招财猫',),
    '梦山白藏主': ('招财猫', '狰'),
    '薰': ('魍魉之匣', '钓瓶火'),
    '白狼': ('钓瓶火',),
}

# 换阵容的惯性阈值：新局面比当前主攻高出的分数不足该值时保持不变。
PIVOT_MARGIN = 15.0
# 已经投入（在手/上阵）的卡达到该数量时，几乎不再换阵容。
PIVOT_STICKY_UNITS = 4
# 「六道」不随羁绊人数增强（官方 2026-10-21 公告，见 strategy/season_data.py）：
# 叠六道的收益来自个体强度与各自唯一效果，不来自羁绊等级。若按普通羁绊
# 那样按人数加权，会把寒霜/易形这类真正随人数成长的羁绊挤下去。
NON_SCALING_BONDS = frozenset({'六道'})
NON_SCALING_COUNT_WEIGHT = 0.35

# ---------------------------------------------------------------- 前期过渡
# 商店可出费用上限来自**实测**（5 局日志、约 1900 张商店卡），数据落在
# strategy/season_data.py（配合 dev_tools/chess_shop_cost_curve.py 可复现）。
# 阶 ≤4 时商店绝大多数是 1~2 费，阵容里 3~5 费的核心卡买不到；若只按
# 「最强 8 张」选人，前期就会一个都买不到、棋盘空着（实测前 10 回合
# 只有 0~3 次购买、0 次上阵）。所以前期必须留出低费过渡位。
TRANSITION_MAX_LEVEL = 4
TRANSITION_SLOTS = 2
# 过渡位只收 1~2 费：3 费已经是阵容主力，不能再算「过渡」。
TRANSITION_FALLBACK_MAX_COST = 2

SHOP_MAX_COST_BY_LEVEL = SHOP_MAX_COST_BY_LEVEL


def _transition_candidates(primary: str, secondary: str | None,
                           observation: Observation,
                           bonus: Mapping[str, float]) -> list[tuple[float, object]]:
    """当期**能买到**的低费同羁绊单位（过渡位候选）。

    排序优先级：已在手/已上阵 > 费用低 > 个体强度。已拥有的优先，
    这样过渡位常常是「补上阵」而不是额外花钱。

    过渡位费用上限取「该阶商店上限」与 ``TRANSITION_FALLBACK_MAX_COST``
    的较小值：即使阶数已经能刷出 3 费，3 费卡也不该占过渡位。

    若主/副羁绊在该阶完全没有 1~2 费卡（例如「六道」最低也要 3 费），
    退回任意当期能买到的低费卡兜底，避免前 10 回合棋盘空着。
    """
    ceiling = min(
        shop_max_cost(observation.level),
        TRANSITION_FALLBACK_MAX_COST,
    )

    def _score(entry, same_bond: bool) -> float:
        score = unit_power(entry.chinese_name, bonus)
        if entry.romaji in observation.hand:
            score += 40.0
        if entry.romaji in observation.owned:
            score += 30.0
        score += (ceiling - entry.cost) * 4.0
        if same_bond:
            score += 6.0
        return score

    pool: list[tuple[float, object]] = []
    for entry in SHIKIGAMI_ENTRIES:
        if entry.cost > ceiling:
            continue
        bonds = entry.bonds
        if primary not in bonds and (secondary is None or secondary not in bonds):
            continue
        pool.append((_score(entry, primary in bonds), entry))
    if not pool:
        for entry in SHIKIGAMI_ENTRIES:
            if entry.cost <= ceiling:
                pool.append((_score(entry, False), entry))
    pool.sort(key=lambda item: item[0], reverse=True)
    return pool



@dataclass(frozen=True)
class Observation:
    """一局棋在某个时刻的可用局面信息。"""

    owned: frozenset[str] = frozenset()
    hand: frozenset[str] = frozenset()
    shop: tuple[str, ...] = ()
    gold: int = 0
    level: int = 0
    round_no: int = 0
    health: int | None = None
    opponents: Mapping[str, int] = field(default_factory=dict)
    current_primary: str | None = None

    @property
    def investment(self) -> frozenset[str]:
        return self.owned | self.hand


@dataclass(frozen=True)
class Plan:
    primary: str
    secondary: str | None
    roster: tuple[str, ...]  # romaji
    scores: Mapping[str, float]
    reason: str
    # 前期低费过渡位（romaji）：本局阶数还低时临时占位，后续会退出阵容。
    transition: tuple[str, ...] = ()


def unit_power(chinese_name: str, bonus: Mapping[str, float]) -> float:
    entry = SHIKIGAMI_BY_ROMAJI.get(chinese_name)
    if entry is None:
        for candidate in SHIKIGAMI_ENTRIES:
            if candidate.chinese_name == chinese_name:
                entry = candidate
                break
    if entry is None:
        return 0.0
    return entry.cost * 10.0 + float(bonus.get(entry.chinese_name, 0.0))


def _entries_with_bond(bond: str) -> list:
    return [entry for entry in SHIKIGAMI_ENTRIES if bond in entry.bonds]


def bond_scores(
    observation: Observation,
    tier: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """给每个羁绊打分：环境强度 + 已投入 + 可买 + 图鉴规模 - 对手抢占。"""
    tier = tier or DEFAULT_BOND_TIER
    owned = observation.owned
    hand = observation.hand
    shop = set(observation.shop)
    opponents = observation.opponents or {}

    scores: dict[str, float] = {}
    for bond, base in tier.items():
        members = _entries_with_bond(bond)
        if not members:
            continue
        owned_count = sum(1 for entry in members if entry.romaji in owned)
        hand_count = sum(1 for entry in members if entry.romaji in hand)
        shop_count = sum(1 for entry in members if entry.romaji in shop)
        contest = float(opponents.get(bond, 0))
        # 六道这类「不随人数增强」的羁绊，人数项打折；个体强度由
        # _roster_candidates 的 unit_power 单独计入，不会被抹掉。
        count_weight = (
            NON_SCALING_COUNT_WEIGHT if bond in NON_SCALING_BONDS else 1.0
        )
        scores[bond] = (
            float(base)
            + count_weight * (
                10.0 * owned_count
                + 7.0 * hand_count
                + 5.0 * shop_count
            )
            + min(len(members), 8) * 1.5
            - 6.0 * contest
        )
    return scores


def _roster_candidates(primary: str, secondary: str | None, observation: Observation,
                       bonus: Mapping[str, float]) -> list[tuple[float, object]]:
    candidates: list[tuple[float, object]] = []
    for entry in SHIKIGAMI_ENTRIES:
        bonds = entry.bonds
        if primary not in bonds and (secondary is None or secondary not in bonds):
            continue
        score = unit_power(entry.chinese_name, bonus)
        if primary in bonds:
            score += 12.0
        if secondary is not None and secondary in bonds:
            score += 8.0
        if entry.romaji in observation.hand:
            score += 14.0
        if entry.romaji in observation.owned:
            score += 10.0
        candidates.append((score, entry))
    if len(candidates) < 8:
        # 体系卡不够 8 张时，用全局高强度卡补位。
        chosen = {entry.romaji for _, entry in candidates}
        for entry in SHIKIGAMI_ENTRIES:
            if entry.romaji in chosen:
                continue
            candidates.append((unit_power(entry.chinese_name, bonus) * 0.6, entry))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates


def plan(
    observation: Observation,
    tier: Mapping[str, float] | None = None,
    bonus: Mapping[str, float] | None = None,
    size: int = 8,
) -> Plan:
    """根据局面给出主攻羁绊、副羁绊与上阵名单。"""
    tier = tier or DEFAULT_BOND_TIER
    bonus = bonus or DEFAULT_UNIT_BONUS
    scores = bond_scores(observation, tier)
    if not scores:
        # 极端兜底：图鉴为空时沿用七角山。
        return Plan('七角山', None, (), {}, 'fallback: empty catalog')

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_bond, best_score = ranked[0]
    current = observation.current_primary
    reason = f'top bond {best_bond} ({best_score:.1f})'

    if current and current in scores and current != best_bond:
        current_score = scores[current]
        invested = sum(
            1 for entry in _entries_with_bond(current)
            if entry.romaji in observation.investment
        )
        sticky = invested >= PIVOT_STICKY_UNITS
        margin = best_score - current_score
        if sticky or margin < PIVOT_MARGIN:
            best_bond, best_score = current, current_score
            reason = (
                f'keep {current} ({current_score:.1f}), '
                f'best was {ranked[0][0]} (+{margin:.1f}), invested={invested}'
            )

    candidates = _roster_candidates(best_bond, None, observation, bonus)
    roster = [entry for _, entry in candidates[:size]]
    secondary = _pick_secondary(roster, best_bond)
    if secondary:
        # 第二遍：优先带副羁绊的双羁绊卡，让 8 人同时吃两份加成。
        candidates = _roster_candidates(best_bond, secondary, observation, bonus)
        refined = [entry for _, entry in candidates[:size]]
        if len(refined) >= size:
            roster = refined
            secondary = _pick_secondary(roster, best_bond) or secondary

    # 前期过渡位：阶数还低时把「买不到的高费核心」让出 1~2 个位置给
    # **当期能买到的同羁绊低费单位**，避免前 10 回合买不到任何卡、棋盘空着。
    transition: tuple[str, ...] = ()
    try:
        level = int(observation.level or 0)
    except (TypeError, ValueError):
        level = 0
    if 0 < level <= TRANSITION_MAX_LEVEL:
        pool = _transition_candidates(best_bond, secondary, observation, bonus)
        in_roster = {entry.romaji for entry in roster}
        chosen = []
        for _, entry in pool:
            if len(chosen) >= TRANSITION_SLOTS:
                break
            if entry.romaji in in_roster:
                continue
            chosen.append(entry)
        if chosen:
            # 保留「买得起」的核心：把最贵的核心让位给过渡位。若只砍名单末尾，
            # 被砍掉的往往是 2~3 费的便宜核心（前期正好买得起），而 5 费核心
            # 反而留在名单里永远买不到。
            keep_count = max(1, size - len(chosen))
            kept_romaji = {
                entry.romaji
                for entry in sorted(roster, key=lambda item: item.cost)[
                    :keep_count
                ]
            }
            kept = [entry for entry in roster if entry.romaji in kept_romaji]
            dropped = [
                entry.chinese_name
                for entry in roster if entry.romaji not in kept_romaji
            ]
            roster = kept + chosen
            transition = tuple(entry.romaji for entry in chosen)
            reason += (
                f'; transition='
                f'{[entry.chinese_name for entry in chosen]} '
                f'(level={level}, shop<= {shop_max_cost(level)} cost, '
                f'parked={dropped})'
            )

    return Plan(
        primary=best_bond,
        secondary=secondary,
        roster=tuple(entry.romaji for entry in roster),
        scores=scores,
        reason=reason,
        transition=transition,
    )


def _pick_secondary(roster: Sequence, primary: str) -> str | None:
    """在名单里选出出现次数最多的非主羁绊（满分不足 2 张就不算）。"""
    counts: dict[str, int] = {}
    for entry in roster:
        for bond in entry.bonds:
            if bond != primary:
                counts[bond] = counts.get(bond, 0) + 1
    if not counts:
        return None
    best = max(counts, key=counts.__getitem__)
    return best if counts[best] >= 2 else None


def _assign_positions(roster: Sequence[str]) -> dict[str, tuple[int, int]]:
    """按前后排规则分配站位与上阵权重。"""
    def entry_of(romaji: str):
        return SHIKIGAMI_BY_ROMAJI.get(romaji)

    def name_of(romaji: str) -> str:
        entry = entry_of(romaji)
        return entry.chinese_name if entry else romaji

    def cost_of(romaji: str) -> int:
        entry = entry_of(romaji)
        return entry.cost if entry else 0

    front: list[str] = []
    back: list[str] = []
    for romaji in roster:
        (front if name_of(romaji) in FRONT_UNITS else back).append(romaji)

    front.sort(
        key=lambda r: (FRONT_PRIORITY.get(name_of(r), 50), cost_of(r)),
        reverse=True,
    )
    back.sort(key=cost_of, reverse=True)

    result: dict[str, tuple[int, int]] = {}
    for index, romaji in enumerate(front[:len(FRONT_SLOTS)]):
        result[romaji] = (1, FRONT_SLOTS[index])
    for index, romaji in enumerate(back[:len(BACK_SLOTS)]):
        result[romaji] = (1, BACK_SLOTS[index])
    # 多出来的（前排溢出）放到剩余的后排位置。
    remaining = [r for r in roster if r not in result]
    free_slots = [slot for slot in FRONT_SLOTS + BACK_SLOTS if slot not in {p for _, p in result.values()}]
    for index, romaji in enumerate(remaining):
        if index >= len(free_slots):
            break
        result[romaji] = (2, free_slots[index])
    return result


def build_strategy(plan_result: Plan) -> dict:
    """把 Plan 转成与固定阵容完全一致的结构，供现有代码直接消费。"""
    positions = _assign_positions(plan_result.roster)
    shikigami_positions: dict[str, tuple] = {}
    for romaji, (weight, position) in positions.items():
        entry = SHIKIGAMI_BY_ROMAJI.get(romaji)
        if entry is None:
            continue
        souls = DEFAULT_SOULS.get(entry.chinese_name, ())
        shikigami_positions[entry.chinese_name] = (weight, position, souls)

    config = {
        'key': ADAPTIVE_KEY,
        'display_name': ADAPTIVE_DISPLAY_NAME,
        'bond': plan_result.primary,
        'secondary_bond': plan_result.secondary,
        'hakuzosu_protect_position': None,
        'shikigami_positions': shikigami_positions,
    }
    strategy = build_lineup_strategy(config)
    strategy['adaptive_reason'] = plan_result.reason
    strategy['adaptive_scores'] = dict(plan_result.scores)
    # 过渡位用 romaji 记录：上阵/回收逻辑比较的也是 romaji。
    strategy['transition'] = [
        romaji for romaji in plan_result.transition
        if romaji in positions
    ]
    return strategy


def load_meta(url: str, cache_path: str, cache_hours: float = 12.0) -> dict | None:
    """从网络拉取环境数据（失败时用本地缓存，再失败返回 None）。

    期望 JSON 结构::

        {"bonds": {"寒霜": 95, ...}, "units": {"雪御前": 20, ...}}
    """
    if not url:
        return None
    cached = None
    if cache_path and os.path.exists(cache_path):
        try:
            age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600.0
            with open(cache_path, 'r', encoding='utf-8') as file:
                cached = json.load(file)
            if age_hours <= cache_hours:
                return cached
        except (OSError, ValueError):
            cached = None
    try:
        import requests

        response = requests.get(url, timeout=8)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and (data.get('bonds') or data.get('units')):
            if cache_path:
                try:
                    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                    with open(cache_path, 'w', encoding='utf-8') as file:
                        json.dump(data, file, ensure_ascii=False, indent=2)
                except OSError:
                    pass
            logger.info(f'Chess adaptive: loaded meta from {url}')
            return data
    except Exception as error:  # noqa: BLE001 - 网络失败不应影响棋局
        logger.warning(f'Chess adaptive: load meta failed ({error}), use cache/builtin')
    return cached


def describe(plan_result: Plan) -> str:
    names = []
    for romaji in plan_result.roster:
        entry = SHIKIGAMI_BY_ROMAJI.get(romaji)
        names.append(entry.chinese_name if entry else romaji)
    transition_names = [
        SHIKIGAMI_BY_ROMAJI[romaji].chinese_name
        if romaji in SHIKIGAMI_BY_ROMAJI else romaji
        for romaji in plan_result.transition
    ]
    suffix = ''
    if transition_names:
        suffix = f', transition={"/".join(transition_names)}'
    return (
        f'primary={plan_result.primary}, secondary={plan_result.secondary}, '
        f'roster={"/".join(names)}{suffix} | {plan_result.reason}'
    )
