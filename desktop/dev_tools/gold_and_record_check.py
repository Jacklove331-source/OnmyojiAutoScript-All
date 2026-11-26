# This Python file uses the following encoding: utf-8

"""金币读数防抖 + 点击记录按规则名删除 的回归测试（纯假对象）。

两个都是**实测事故驱动**的修复：
1. 金币误读：开/关商店瞬间同一 ROI 会读到 8->98、36->86、43->89 这类跳升，
   直接干扰「买不买得起 / 吃不吃利息 / 该不该升级」的判断。
2. 点击记录按对象删除是 no-op：记录里存的是 `control_name` 字符串，
   于是同一按钮累计 10 次触发 `GameTooManyClickError` 整局失败。

用法：`.\\toolkit\\python.exe dev_tools\\gold_and_record_check.py`
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.atom.click import RuleClick  # noqa: E402
from module.atom.image import RuleImage  # noqa: E402
from module.device.device import Device  # noqa: E402
from tasks.Chess.runtime.economy import ChessEconomyMixin  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


class FakeEconomy(ChessEconomyMixin):
    def __init__(self):
        self.dumps = []
        self._gold_last_value = None
        self._gold_last_at = 0.0

    def _dump_gold_misread(self, previous, reading):
        self.dumps.append((previous, reading))


def test_gold_accepts_normal():
    economy = FakeEconomy()
    check('首次读数接受', economy._stabilize_gold_reading(8) == 8)
    check('相近读数接受', economy._stabilize_gold_reading(10) == 10)
    check('花钱后更低读数接受', economy._stabilize_gold_reading(2) == 2)


def test_gold_rejects_jump():
    economy = FakeEconomy()
    economy._stabilize_gold_reading(8)
    kept = economy._stabilize_gold_reading(98)
    check('突增读数需二次确认（先拒绝）', kept == 8, str(kept))
    check('拒绝时保存证据样本', economy.dumps == [(8, 98)], str(economy.dumps))


def test_gold_transient_misread_flips_back():
    """真实场景：8 -> 96(误读) -> 2(真实)。第二帧回落后不能再被当成收入。"""
    economy = FakeEconomy()
    economy._stabilize_gold_reading(8)
    economy._stabilize_gold_reading(96)          # 拒绝，记为候选
    value = economy._stabilize_gold_reading(2)   # 回落
    check('瞬时误读下一帧回落即采纳真值', value == 2, str(value))
    check('回落后候选被清除', getattr(economy, '_gold_candidate', None) is None)


def test_gold_confirms_real_income():
    """真实收入：8 -> 96 -> 97，两帧一致 → 承认 97。"""
    economy = FakeEconomy()
    economy._stabilize_gold_reading(8)
    economy._stabilize_gold_reading(96)          # 拒绝
    value = economy._stabilize_gold_reading(97)  # 与候选一致 → 承认
    check('连续两帧一致确认为真实收入', value == 97, str(value))


def test_gold_candidate_expires():
    economy = FakeEconomy()
    economy._stabilize_gold_reading(8)
    economy._stabilize_gold_reading(96)
    economy._gold_candidate_at -= 20.0           # 候选过期
    value = economy._stabilize_gold_reading(96)
    check('过期候选不能直接生效（需新的二次确认）', value == 8, str(value))


class FakeDevice(Device):
    """只借 Device 的点击记录方法，不走 __init__。"""

    from collections import deque as _deque

    def __init__(self):
        self.click_record = self._deque(maxlen=15)


def test_click_record_remove_rule():
    device = FakeDevice()
    for _ in range(3):
        device.click_record_add('shikigami_1')
    click_rule = RuleClick((0, 0, 10, 10), (0, 0, 10, 10), name='shikigami_1')
    removed = device.click_record_remove_rule(click_rule)
    check('按规则名删除生效', removed == 3, str(removed))
    check('删除后记录为空', len(device.click_record) == 0)

    image = RuleImage(
        roi_front=(0, 0, 10, 10),
        roi_back=(0, 0, 100, 100),
        method='Template matching',
        threshold=0.8,
        file='template/TEMPLATE_MISSING_FOR_TEST.png',
    )
    for _ in range(2):
        device.click_record_add(image.name)
    removed = device.click_record_remove_rule(image)
    check('RuleImage 按 name 删除生效', removed == 2, str(removed))
    check('None 安全返回 0', device.click_record_remove_rule(None) == 0)


def main():
    for case in (
        test_gold_accepts_normal,
        test_gold_rejects_jump,
        test_gold_transient_misread_flips_back,
        test_gold_confirms_real_income,
        test_gold_candidate_expires,
        test_click_record_remove_rule,
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
    print('GOLD & RECORD: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
