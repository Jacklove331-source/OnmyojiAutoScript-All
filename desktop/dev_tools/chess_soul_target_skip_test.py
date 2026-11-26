# -*- coding: utf-8 -*-
"""Chess soul equip target-skip unit test (plain script).

覆盖 equip_souls_from_hand 的目标校验：
- 站位识别不到 -> target_position_unknown，整格作废；
- 同名御魂/槽位已满 -> 只作废 (御魂, 站位) 组合，其他御魂仍可用；
- hand_count_unchanged -> 下一轮换目标，不对同一组合重复拖拽。
"""

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.Chess.runtime.hand_operations import ChessHandOperationsMixin  # noqa: E402


class FakeOp(ChessHandOperationsMixin):
    def __init__(self, names, attrs, targets):
        self._names = names
        self._attrs = attrs
        self._targets = targets

    def _shikigami_name_at_set(self, set_index):
        return self._names.get(set_index)

    def _shikigami_attributes(self, name):
        return self._attrs.get(name, (None, None, None))

    def _set_shikigami_attributes(self, name, values):
        self._attrs[name] = values

    def _soul_targets(self, soul_name, category, verified_names):
        return list(self._targets.get(soul_name, ()))


class FakeEquip(ChessHandOperationsMixin):
    SOUL_EQUIP_SAFETY_LIMIT = 3
    # 与 tasks/Chess/runtime/settings.py 保持一致（生产代码会读这两个常量）
    SOUL_MIN_PHASE_REMAINING = 6.0
    SOUL_DRAG_RETRY_OFFSET = -12

    @staticmethod
    def _soul_category_display(category):
        if category == 'attack':
            return '输出'
        if category == 'functional':
            return '功能'
        return str(category or '未知')

    def __init__(self):
        self._souls = [{
            'name': 'x',
            'category': 'attack',
            'position': (10, 20),
            'text': 'X',
        }]
        self._attrs = {'A': (None, None, None), 'B': (None, None, None)}
        self._player_deployed_positions = {2, 4}
        self.drag_targets = []

    def _is_preparation_mode(self):
        return True

    def screenshot(self):
        pass

    def discover_souls_from_hand(self):
        pass

    def use_golden_copies_from_hand(self, verified_names):
        pass

    def equip_emblems_from_hand(self, verified_names):
        return []

    def equip_hakuzosu_protect_after_deploy(self, verified_names):
        return False

    def _soul_hand_cards(self):
        return list(self._souls)

    def _is_lineup_preferred_soul(self, soul_name):
        return False

    def _soul_targets(self, soul_name, category, verified_names):
        return [(2, (0, 0)), (4, (0, 0))]

    def _shikigami_name_at_set(self, set_index):
        return {2: 'A', 4: 'B'}.get(set_index)

    def _shikigami_attributes(self, name):
        return self._attrs[name]

    def _set_shikigami_attributes(self, name, values):
        self._attrs[name] = values

    def _equip_soul_card(self, source, set_index, operation_name,
                        target_offset=0):
        self.drag_targets.append(set_index)
        self.drag_offsets = getattr(self, 'drag_offsets', [])
        self.drag_offsets.append(int(target_offset or 0))
        return True

    def _read_remaining_time(self):
        return getattr(self, 'remaining_time', 99)

    def _wait_hand_card_removed(self, count_before, count_cards):
        return False


class ModeRetryEquip(FakeEquip):
    """入口正常；循环首判误读，重拍一次后恢复。"""

    def __init__(self):
        super().__init__()
        self.mode_calls = 0
        self.shots = 0
        self.consumed = False

    def _is_preparation_mode(self):
        # 调用序列：入口(1114)第1次True、循环首判(1152)第2次False、
        # screenshot 后(1155)第3次True，才能走到 mode_changed 重拍分支。
        self.mode_calls += 1
        return self.mode_calls != 2

    def screenshot(self):
        self.shots += 1

    def _soul_hand_cards(self):
        return [] if self.consumed else list(self._souls)

    def _wait_hand_card_removed(self, count_before, count_cards):
        self.consumed = True
        return True


class ModeStoppedEquip(FakeEquip):
    """入口正常；循环首判与重拍后均非准备阶段。"""

    def __init__(self):
        super().__init__()
        self.shots = 0
        self.mode_calls = 0

    def _is_preparation_mode(self):
        # 入口第1次True；循环首判与重拍后（第2、3次）均False。
        self.mode_calls += 1
        return self.mode_calls == 1

    def screenshot(self):
        self.shots += 1


TOTAL = 0
PASS = 0
FAIL = 0


def check(label, actual, expected):
    global TOTAL, PASS, FAIL
    TOTAL += 1
    if actual == expected:
        PASS += 1
        print(f'[PASS] {label}')
    else:
        FAIL += 1
        print(f'[FAIL] {label}: expected={expected!r} actual={actual!r}')


def main():
    op = FakeOp(
        names={2: 'A', 4: 'B', 6: 'C', 8: 'D'},
        attrs={
            'A': (None, None, None),
            'B': (None, 'x', 'y'),
            'C': (None, 'x', None),
            'D': (None, 'x', 'y'),
        },
        targets={},
    )
    check('empty slots -> ready', op._soul_target_block_reason(2, 'x'), None)
    check(
        'unknown position -> target_position_unknown',
        op._soul_target_block_reason(3, 'x'),
        'target_position_unknown',
    )
    check(
        'duplicate soul -> no_eligible_target',
        op._soul_target_block_reason(6, 'x'),
        'no_eligible_target',
    )
    check(
        'both slots full -> no_eligible_target',
        op._soul_target_block_reason(8, 'z'),
        'no_eligible_target',
    )

    cand = [
        {
            'name': 'x',
            'category': 'attack',
            'position': (10, 20),
            'text': 'X',
        }
    ]
    op = FakeOp(
        names={2: 'A', 4: 'B'},
        attrs={'A': (None, 'x', 'y'), 'B': (None, None, None)},
        targets={'x': [(2, (0, 0)), (4, (0, 0))]},
    )
    failed, blocked, attempts, reasons = set(), set(), {}, Counter()
    selected, target = op._select_soul_target(
        cand, {'A', 'B'}, failed, attempts, reasons, blocked
    )
    check('skip blocked target and pick next', target[0] if target else None, 4)
    check('blocked combination recorded', ('x', 2) in blocked, True)
    check('other soul target not globally failed', 2 in failed, False)
    check('blocked reason counted', reasons['no_eligible_target'], 1)

    # 核心回归：A 已带 x 只是 (x, 2) 不可用，(y, 2) 仍应被选中，
    # 不能因为一个式神的手牌把整个站位拉黑而误报 no_eligible_target。
    op = FakeOp(
        names={2: 'A'},
        attrs={'A': (None, 'x', None), 'B': (None, None, None)},
        targets={'x': [(2, (0, 0))], 'y': [(2, (0, 0))]},
    )
    cand_xy = [
        {'name': 'x', 'category': 'attack',
         'position': (10, 20), 'text': 'X'},
        {'name': 'y', 'category': 'attack',
         'position': (40, 20), 'text': 'Y'},
    ]
    failed, blocked, attempts, reasons = set(), set(), {}, Counter()
    selected, target = op._select_soul_target(
        cand_xy, {'A', 'B'}, failed, attempts, reasons, blocked
    )
    check(
        'position reusable by another soul',
        (selected['name'], target[0]) if target else None,
        ('y', 2),
    )

    op = FakeOp(
        names={2: 'A'},
        attrs={'A': (None, None, None)},
        targets={'x': [(2, (0, 0))]},
    )
    attempts = {('x', 0, 2): 2}
    selected, target = op._select_soul_target(
        cand, {'A'}, set(), attempts, Counter()
    )
    check('exhausted pair -> no selection', (selected, target), (None, None))

    op = FakeOp(names={}, attrs={}, targets={})
    selected, target = op._select_soul_target(
        cand, set(), set(), {}, Counter()
    )
    check('no target -> None', (selected, target), (None, None))

    op = FakeOp(
        names={2: 'A'},
        attrs={'A': (None, None, None)},
        targets={'x': [(2, (0, 0))]},
    )
    failed, attempts, reasons = set(), {}, Counter()
    selected, target = op._select_soul_target(
        cand, {'A'}, failed, attempts, reasons
    )
    check('select valid target', (selected['name'], target[0]), ('x', 2))
    check('attempt recorded', attempts[('x', 0, 2)], 1)

    # hand_count_unchanged：同一目标只拖一次，随后换目标。
    fake = FakeEquip()
    result = fake.equip_souls_from_hand({'A', 'B'})
    check('hand unchanged -> no soul equipped', result, [])
    check(
        'hand unchanged -> retry same target once, then switch',
        fake.drag_targets,
        [2, 2, 4, 4],
    )

    # 备阶段剩余时间不足：不应再发起新的御魂拖拽（避免 mode_changed_after_drag）
    short = FakeEquip()
    short.remaining_time = 3
    short._wait_hand_card_removed = lambda *args, **kwargs: True
    result = short.equip_souls_from_hand({'A', 'B'})
    check('short phase -> no drag', short.drag_targets, [])
    check('short phase -> nothing equipped', result, [])

    # 偏移参数必须被真正用到：同一目标两次拖拽偏移依次 0、-12
    check('hand unchanged -> offsets [0, -12] per target',
          getattr(fake, 'drag_offsets', []), [0, -12, 0, -12])

    # 首拖失败、偏移重试成功 → 判成功且只对该站位拖 2 次
    class _RetryConfirm(FakeEquip):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.consumed = False

        def _wait_hand_card_removed(self, *args, **kwargs):
            self.calls += 1
            if self.calls >= 2:
                self.consumed = True
                return True
            return False

        def _soul_hand_cards(self):
            return [] if self.consumed else list(self._souls)

    retry_ok = _RetryConfirm()
    result = retry_ok.equip_souls_from_hand({'A', 'B'})
    check('retry confirm -> equipped', result, ['x'])
    check('retry confirm -> offsets [0, -12]', retry_ok.drag_offsets, [0, -12])

    # 首拖后剩余时间不足 → 不再重试（不允许绕开 6s 保护）
    class _ShortAfterFirst(FakeEquip):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def _read_remaining_time(self):
            self.reads += 1
            return 99 if self.reads == 1 else 3

    short_retry = _ShortAfterFirst()
    short_retry.equip_souls_from_hand({'A', 'B'})
    check('retry skipped when time short -> single drag',
          short_retry.drag_offsets, [0])

    # 手牌数量变了但没确认（mismatch）→ 不重试（避免拖错卡）
    class _Mismatch(FakeEquip):
        def __init__(self):
            super().__init__()
            self.dropped = False

        def _equip_soul_card(self, source, set_index, operation_name,
                             target_offset=0):
            self.drag_offsets = getattr(self, 'drag_offsets', []) + [int(target_offset or 0)]
            return True

        def _wait_hand_card_removed(self, *args, **kwargs):
            self.dropped = True
            return False

        def _soul_hand_cards(self):
            return [] if self.dropped else list(self._souls)

    mismatch = _Mismatch()
    mismatch.equip_souls_from_hand({'A', 'B'})
    check('mismatch -> no retry offset', mismatch.drag_offsets, [0])

    # mode_changed：阶段 OCR 首帧误读时重拍一次，恢复后继续装配。
    retry = ModeRetryEquip()
    result = retry.equip_souls_from_hand({'A', 'B'})
    check('mode retry -> equip proceeds', result, ['x'])
    check('mode retry -> screenshots once', retry.shots, 1)
    check('mode retry -> single drag', retry.drag_targets, [2])

    # mode_changed：重拍后仍非准备阶段则只重试一次并放弃。
    stopped = ModeStoppedEquip()
    result = stopped.equip_souls_from_hand({'A', 'B'})
    check('mode changed -> no equip', result, [])
    check('mode changed -> retry only once', stopped.shots, 1)
    check('mode changed -> no drag', stopped.drag_targets, [])

    print(f'TOTAL {TOTAL}  PASS {PASS}  FAIL {FAIL}')
    if FAIL:
        print('SOUL TARGET SKIP: FAILED')
        return 1
    print('SOUL TARGET SKIP: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
