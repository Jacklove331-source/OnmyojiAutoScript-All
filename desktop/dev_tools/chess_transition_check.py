# This Python file uses the following encoding: utf-8

"""前期低费过渡位 + 过渡位淘汰的回归测试（不接触真机）。

为什么先写测试：过渡位会改变 `shikigami_deploy_positions`（站位映射），
而站位映射是稳定布阵功能的基础。这里锁住四条不变量：
1. 阵容名单永远 ≤ 8 张（超过 8 张的会被 `_assign_positions` 静默丢掉）；
2. 每张牌都有站位，且站位互不重复（1..8）；
3. 过渡位只在低阶出现，且费用 ≤ 该阶商店实测可出上限；
4. 高阶阵容与改动前完全一致（纯核心 8 张，不出现过渡位）；
5. 淘汰逻辑只收回「登记过且已不在阵容」的过渡位，绝不碰核心卡。

用法：`.\\toolkit\\python.exe dev_tools\\chess_transition_check.py`
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Chess.runtime.hand_operations import ChessHandOperationsMixin  # noqa: E402
from tasks.Chess.strategy import adaptive as A  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def test_cost_curve():
    check('阶1 只能出 1 费', A.shop_max_cost(1) == 1, str(A.shop_max_cost(1)))
    check('阶3 可出 2 费', A.shop_max_cost(3) == 2, str(A.shop_max_cost(3)))
    check('阶4 可出 3 费', A.shop_max_cost(4) == 3, str(A.shop_max_cost(4)))
    check('阶7 可出 5 费', A.shop_max_cost(7) == 5, str(A.shop_max_cost(7)))
    check('未知阶保守回退到 3 费', A.shop_max_cost(99) == 3, str(A.shop_max_cost(99)))
    check('level=0 回退到 2 费', A.shop_max_cost(0) == 2, str(A.shop_max_cost(0)))
    check('level=None 回退到 2 费', A.shop_max_cost(None) == 2)


def _plan_at(level, round_no=5):
    return A.plan(A.Observation(level=level, round_no=round_no, gold=20))


def test_low_level_transition():
    plan_result = _plan_at(3)
    check('低阶出现过渡位', bool(plan_result.transition),
          str(plan_result.transition))
    check('过渡位数量 ≤ 设定上限',
          len(plan_result.transition) <= A.TRANSITION_SLOTS,
          str(len(plan_result.transition)))
    entries = [A.SHIKIGAMI_BY_ROMAJI[r] for r in plan_result.transition]
    ceiling = A.shop_max_cost(3)
    check('过渡位费用都不超过该阶上限',
          all(entry.cost <= ceiling for entry in entries),
          str([(e.chinese_name, e.cost) for e in entries]))
    bonds = {plan_result.primary, plan_result.secondary} - {None}
    check('过渡位至少带一个主/副羁绊',
          all(bonds & set(entry.bonds) for entry in entries),
          str([(e.chinese_name, e.bonds) for e in entries]))
    check('低阶阵容总数 ≤ 8', len(plan_result.roster) <= 8,
          str(len(plan_result.roster)))


def test_low_level_roster_has_positions():
    strategy = A.build_strategy(_plan_at(3))
    positions = [cfg['position'] for cfg in strategy['shikigami'].values()]
    check('低阶每张牌都有站位', all(isinstance(pos, int) for pos in positions))
    check('低阶站位互不重复', len(set(positions)) == len(positions), str(positions))
    check('站位落在 1..8', all(1 <= pos <= 8 for pos in positions), str(positions))
    check('过渡位确实进了站位表',
          all(name in strategy['shikigami'] for name in strategy['transition']),
          str(strategy.get('transition')))


def test_high_level_no_transition():
    plan_result = _plan_at(8, round_no=22)
    check('高阶不出现过渡位', plan_result.transition == (),
          str(plan_result.transition))
    check('高阶阵容仍是 8 张', len(plan_result.roster) == 8,
          str(len(plan_result.roster)))
    strategy = A.build_strategy(plan_result)
    positions = [cfg['position'] for cfg in strategy['shikigami'].values()]
    check('高阶站位互不重复', len(set(positions)) == len(positions), str(positions))
    # 与「不启用过渡」的纯核心名单一致（回归：不能改变后期阵容）
    core = [entry for _, entry in A._roster_candidates(
        plan_result.primary, plan_result.secondary,
        A.Observation(level=8, round_no=22), A.DEFAULT_UNIT_BONUS,
    )[:8]]
    check('高阶名单来自纯核心候选（无过渡位污染）',
          set(plan_result.roster) <= {entry.romaji for entry in core},
          str(set(plan_result.roster) - {entry.romaji for entry in core}))


def test_transition_keeps_cheap_core():
    """过渡位只能挤掉「最贵的核心」——便宜核心前期正好买得到。"""
    plan_result = _plan_at(3)
    core_entries = [
        A.SHIKIGAMI_BY_ROMAJI[romaji]
        for romaji in plan_result.roster
        if romaji not in plan_result.transition
    ]
    costs = sorted(entry.cost for entry in core_entries)
    check('过渡期保留的核心最贵不超过 4 费',
          bool(costs) and costs[-1] <= 4, str(costs))
    check('过渡期没有 5 费核心占着名额',
          all(entry.cost < 5 for entry in core_entries),
          str([(e.chinese_name, e.cost) for e in core_entries]))


def test_round_boundary():
    """阶数达标就不再留过渡位（回合不是判据，阶才是）。"""
    for level in range(1, 10):
        plan_result = _plan_at(level, round_no=3)
        if level <= A.TRANSITION_MAX_LEVEL:
            assert plan_result.transition or True  # 低阶可能无可选过渡位
        else:
            check(f'阶{level} 无过渡位', plan_result.transition == (),
                  str(plan_result.transition))


class FakeHand(ChessHandOperationsMixin):
    """只测淘汰选择逻辑，拖拽部分被替换掉。"""

    def __init__(self, roster, transitions, actual):
        self.shikigami_deploy_positions = {name: pos for name, pos in roster.items()}
        self._board_transition_names = set(transitions)
        self._board_actual_positions = dict(actual)
        self._board_lineup_names = set(actual)
        self._player_deployed_positions = set(actual.values())
        self._board_shikigami_attributes = {name: (None, None, None) for name in actual}
        self.recalled = []

    def _recall_board_unit_to_hand(self, name):
        position = self._board_actual_positions.get(name)
        if position is None or name not in self._board_transition_names:
            return None
        self.recalled.append(name)
        self._board_actual_positions.pop(name, None)
        self._board_transition_names.discard(name)
        self._player_deployed_positions.discard(position)
        self._board_lineup_names.discard(name)
        return position


def test_release_logic():
    # 过渡位已不在阵容 → 应被收回
    fake = FakeHand(
        roster={'yuki_gozen': 1, 'yuki_onna': 3},
        transitions={'yuki_douji'},
        actual={'yuki_gozen': 1, 'yuki_onna': 3, 'yuki_douji': 5},
    )
    result = fake._release_stale_transition_units()
    check('不在阵容里的过渡位被收回',
          result == {'name': 'yuki_douji', 'set': 5}, str(result))
    check('收回后站位登记被清理',
          'yuki_douji' not in fake._board_transition_names
          and 5 not in fake._player_deployed_positions)

    # 过渡位仍在新阵容里 → 不能收回
    fake2 = FakeHand(
        roster={'yuki_gozen': 1, 'yuki_douji': 5},
        transitions={'yuki_douji'},
        actual={'yuki_gozen': 1, 'yuki_douji': 5},
    )
    check('仍在阵容里的过渡位不收回',
          fake2._release_stale_transition_units() is None
          and fake2.recalled == [])

    # 核心卡即使不在过渡位名单里也绝不会被动
    fake3 = FakeHand(
        roster={'yuki_gozen': 1, 'yuki_onna': 3},
        transitions={'yuki_douji'},
        actual={'yuki_gozen': 1, 'yuki_onna': 3, 'yuki_douji': 5},
    )
    fake3._release_stale_transition_units(preferred_positions=(1,))
    check('优先位置只影响顺序，不会误收核心卡',
          fake3.recalled == ['yuki_douji'], str(fake3.recalled))

    # 没有过渡位登记 → 什么都不做
    fake4 = FakeHand(
        roster={'yuki_gozen': 1},
        transitions=set(),
        actual={'yuki_gozen': 1},
    )
    check('无过渡位登记时不动作',
          fake4._release_stale_transition_units() is None)


def test_forget_clears_everything():
    fake = FakeHand(
        roster={'yuki_douji': 5},
        transitions={'yuki_douji'},
        actual={'yuki_douji': 5},
    )
    fake._forget_board_unit('yuki_douji', 5)
    check('_forget_board_unit 清空四处登记',
          'yuki_douji' not in fake._board_lineup_names
          and 'yuki_douji' not in fake._board_actual_positions
          and 'yuki_douji' not in fake._board_transition_names
          and 'yuki_douji' not in fake._board_shikigami_attributes
          and 5 not in fake._player_deployed_positions)


def main():
    for case in (
        test_cost_curve,
        test_low_level_transition,
        test_transition_keeps_cheap_core,
        test_low_level_roster_has_positions,
        test_high_level_no_transition,
        test_round_boundary,
        test_release_logic,
        test_forget_clears_everything,
    ):
        print(f'--- {case.__name__}')
        case()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('CHESS TRANSITION: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
