# This Python file uses the following encoding: utf-8

"""阶段时间预算（动态停止阈值）的回归测试（纯假对象）。

背景：经济循环原来写死「剩余 < 15 秒就停」，但上阵/上魂的真实耗时随截图方式
（nemu 约 0.2s、ADB 约 0.9s/帧）和局面（拖拽次数）变化，固定值要么浪费经济
时间，要么把关店/上阵挤掉。现在改成：用实测 EMA 推算「关店+上阵+上魂+确认」
所需秒数，作为经济循环的停止阈值。

覆盖：
1. 没有历史时用保守回退值；
2. 有历史时 = deploy + soul + 固定开销 + margin，且不低于下限；
3. EMA 平滑（不会因一次抖动突变）；
4. 预算随截图变慢而自动变大（模拟 ADB 降级）。

用法：`.\\toolkit\\python.exe dev_tools\\chess_phase_budget_check.py`
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Chess.script_task import ChessLegacyScriptTask  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


class Fake(ChessLegacyScriptTask):
    """只借时间预算的纯逻辑，不走 __init__。"""

    def __init__(self):
        self._phase_costs = {}


def test_fallback():
    fake = Fake()
    check('无历史时用保守回退值',
          fake._phase_time_budget() == fake.ECONOMY_RESERVE_FALLBACK,
          str(fake._phase_time_budget()))


def test_composition():
    fake = Fake()
    fake._update_phase_cost('deploy', 6.0)
    fake._update_phase_cost('soul', 4.0)
    budget = fake._phase_time_budget()
    expected = 6.0 + 4.0 + 2.0 + fake.ECONOMY_RESERVE_MARGIN
    check('预算 = deploy + soul + 固定开销 + margin',
          abs(budget - expected) < 0.01, f'{budget} vs {expected}')


def test_minimum_floor():
    fake = Fake()
    fake._update_phase_cost('deploy', 0.5)
    fake._update_phase_cost('soul', 0.5)
    budget = fake._phase_time_budget()
    check('极快的操作也有下限保护',
          budget >= fake.ECONOMY_RESERVE_MINIMUM, str(budget))


def test_ema_smoothing():
    fake = Fake()
    fake._update_phase_cost('deploy', 10.0)
    fake._update_phase_cost('deploy', 2.0)
    value = fake._phase_costs['deploy']
    check('EMA 不会直接跳到最新值',
          2.0 < value < 10.0, str(value))
    for _ in range(20):
        fake._update_phase_cost('deploy', 2.0)
    check('多次采样后收敛到新水平',
          abs(fake._phase_costs['deploy'] - 2.0) < 0.2,
          str(fake._phase_costs['deploy']))


def test_slower_capture_raises_budget():
    """ADB 降级后每帧更慢 → 同样操作耗时可观测变大 → 预算自动上调。"""
    fast = Fake()
    fast._update_phase_cost('deploy', 5.0)
    fast._update_phase_cost('soul', 3.0)
    slow = Fake()
    slow._update_phase_cost('deploy', 14.0)
    slow._update_phase_cost('soul', 7.0)
    check('慢截图环境下预算更大',
          slow._phase_time_budget() > fast._phase_time_budget(),
          f'{slow._phase_time_budget()} vs {fast._phase_time_budget()}')


def test_budget_above_legacy_threshold_for_normal_deploy():
    """正常一局里，deploy+soul 实测应接近 10~20s，预算要明显大于旧 15s 之外的场景。"""
    fake = Fake()
    fake._update_phase_cost('deploy', 8.0)
    fake._update_phase_cost('soul', 5.0)
    budget = fake._phase_time_budget()
    check('正常耗材下预算落在 12~25s 的合理区间',
          12.0 <= budget <= 25.0, str(budget))


def main():
    for case in (
        test_fallback,
        test_composition,
        test_minimum_floor,
        test_ema_smoothing,
        test_slower_capture_raises_budget,
        test_budget_above_legacy_threshold_for_normal_deploy,
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
    print('CHESS PHASE BUDGET: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
