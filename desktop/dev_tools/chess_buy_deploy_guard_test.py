# This Python file uses the following encoding: utf-8

"""买卡/上阵两处新逻辑的纯逻辑单测（不连真机、不碰截图）。

覆盖：
1. `_skip_transition_duplicate`：过渡位只买一张，正式阵容卡不受影响；
   读不到阵容信息时不能误拦购买。
2. `_explain_no_deploy_candidate`：把「棋盘已满是正常」与
   「手牌里根本没有阵容卡（前期空位）」分开——这是决定下一步该改购买
   还是改上阵的关键证据。

用法：`.\\toolkit\\python.exe dev_tools\\chess_buy_deploy_guard_test.py`
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Chess.runtime.economy import ChessEconomyMixin  # noqa: E402
from tasks.Chess.runtime.hand_operations import ChessHandOperationsMixin  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


class FakeBuy(ChessEconomyMixin):
    def __init__(self, transitions=(), raise_error=False):
        self._transitions = set(transitions)
        self._raise = raise_error

    def is_transition_unit(self, name):
        if self._raise:
            raise RuntimeError('no strategy')
        return name in self._transitions


class FakeDeploy(ChessHandOperationsMixin):
    def __init__(self, cards, roster, classified, raise_error=False):
        self._cards = cards
        self.shikigami_deploy_positions = {name: index + 1
                                           for index, name in enumerate(roster)}
        self._classified = classified
        self._raise = raise_error

    def _hand_card_detections(self):
        if self._raise:
            raise RuntimeError('screen occluded')
        return list(self._cards)

    def classify_hand_card(self, roi):
        return self._classified.get(roi, {})


def test_transition_duplicate():
    buyer = FakeBuy(transitions={'feng_huang_huo'})
    check('过渡位 0 张 → 允许买',
          not buyer._skip_transition_duplicate('feng_huang_huo', 0))
    check('过渡位已有 1 张 → 不再买',
          buyer._skip_transition_duplicate('feng_huang_huo', 1))
    check('过渡位已有 3 张 → 不再买',
          buyer._skip_transition_duplicate('feng_huang_huo', 3))
    check('正式阵容卡有 3 张仍可买（升星需要）',
          not buyer._skip_transition_duplicate('yuki_gozen', 3))
    check('空名字不拦', not buyer._skip_transition_duplicate('', 5))

    broken = FakeBuy(raise_error=True)
    check('读阵容异常时不误拦购买',
          not broken._skip_transition_duplicate('feng_huang_huo', 3))

    class NoStrategy(ChessEconomyMixin):
        pass

    plain = NoStrategy()
    plain.__dict__['is_transition_unit'] = None
    check('没有 is_transition_unit 时不拦',
          not plain._skip_transition_duplicate('x', 3))


def test_gap_reason():
    roster = ['yuki_gozen', 'yuki_onna']
    empty = FakeDeploy(cards=[], roster=roster, classified={})
    check('手牌为空 → hand_empty',
          empty._explain_no_deploy_candidate(set()) == 'hand_empty')

    non_roster = FakeDeploy(
        cards=[{'roi': 1}, {'roi': 2}],
        roster=roster,
        classified={1: {'name': 'heian_kyo'}, 2: {'name': 'kabuki'}},
    )
    check('手牌只有非阵容卡 → hand_has_no_lineup_card',
          non_roster._explain_no_deploy_candidate(set())
          == 'hand_has_no_lineup_card')

    already = FakeDeploy(
        cards=[{'roi': 1}, {'roi': 2}],
        roster=roster,
        classified={1: {'name': 'yuki_gozen'}, 2: {'name': 'yuki_onna'}},
    )
    check('阵容卡都已上阵 → lineup_cards_already_deployed',
          already._explain_no_deploy_candidate({'yuki_gozen', 'yuki_onna'})
          == 'lineup_cards_already_deployed')

    partial = FakeDeploy(
        cards=[{'roi': 1}, {'roi': 2}],
        roster=roster,
        classified={1: {'name': 'yuki_gozen'}, 2: {'name': 'yuki_onna'}},
    )
    check('部分阵容卡未上阵 → 不能归为「重复」',
          partial._explain_no_deploy_candidate({'yuki_gozen'})
          == 'non_roster_or_unclassified')

    broken = FakeDeploy(cards=[{'roi': 1}], roster=roster, classified={},
                        raise_error=True)
    check('手牌扫描异常 → hand_scan_failed（异常分支真断言）',
          broken._explain_no_deploy_candidate(set()) == 'hand_scan_failed')

    # 同一刷新里同名卡占两格时，第二张必须被限购挡住（买前计入已购）
    buyer = FakeBuy(transitions={'feng_huang_huo'})
    already_bought = 1
    check('同刷新第二张同名过渡位被限购',
          buyer._skip_transition_duplicate('feng_huang_huo', already_bought))


def test_imports_present():
    """Counter 必须真的被 import（曾因漏 import 导致商店购买直接 NameError）。"""
    import tasks.Chess.runtime.economy as economy_module

    check('economy.py 已导入 Counter',
          hasattr(economy_module, 'Counter'), 'missing Counter import')


def main():
    for case in (test_transition_duplicate, test_gap_reason,
                 test_imports_present):
        print(f'--- {case.__name__}')
        case()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('CHESS BUY/DEPLOY GUARD: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
