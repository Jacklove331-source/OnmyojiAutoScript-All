# This Python file uses the following encoding: utf-8
"""`ui_click_until_disappear` / `RuleClick` 行为级回归测试（不接触真机）。

背景：2026-10-26 更新器回退把 `ui_click_until_disappear` 冲成旧签名
`(click, interval)`，为兼容又加了 `**kwargs`，结果 `RuleClick` 被当成图片
送进 `appear()` → `'RuleClick' object has no attribute 'match'`，棋局在第 13
回合崩掉。静态检查（compileall/方法存在性扫描）看不出这类问题，必须有行为测试。

覆盖：
1. `RuleClick` + `visible` + `click_target`：点击区与判定区分开，且不做图像匹配
2. 点击目标与判定目标是不同对象时，点的是 `click_target`
3. `guard` / `timeout` / `max_clicks` / `confirm_frames` 真实生效
4. 默认路径（`visible=None`）仍走 `appear_then_click`，与旧调用一致
5. 未知关键字参数必须抛 TypeError（禁止 `**kwargs` 吞参数）
6. `RuleClick` 缺 `visible` 时立刻报 TypeError（不再静默走到 `match()`）

用法：`.\\toolkit\\python.exe dev_tools\\ui_click_regression.py`
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.atom.click import RuleClick  # noqa: E402
from module.atom.image import RuleImage  # noqa: E402
from tasks.base_task import BaseTask  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


class FakeImage:
    """最小 RuleImage 替身：只有 name / match()，用于默认路径测试。"""

    def __init__(self, name, present=True):
        self.name = name
        self.present = present
        self.match_calls = 0

    def match(self, image, threshold=None, **kwargs):
        self.match_calls += 1
        return self.present

    def coord(self):
        return 100, 200


class FakeTask:
    """只实现 ui_click_until_disappear 依赖的四个方法，全部记录调用。"""

    def __init__(self):
        self.screenshots = 0
        self.clicks = []          # click() 的目标
        self.appear_targets = []  # appear()/appear_then_click() 的目标
        self.appear_frames = []   # 逐帧可见性（appear 路径）
        self.assertEqual = None

    def screenshot(self):
        self.screenshots += 1

    def _next_frame(self):
        if not self.appear_frames:
            return False
        if len(self.appear_frames) > 1:
            return self.appear_frames.pop(0)
        return self.appear_frames[0]

    def appear(self, target, interval=None, threshold=None):
        self.appear_targets.append(target)
        return self._next_frame()

    def appear_then_click(self, target, interval=None, **kwargs):
        # 真实实现内部会再调一次 self.appear()，这里保持一致
        if self.appear(target):
            self.clicks.append(target)
            return True
        return False

    def click(self, target, interval=None):
        self.clicks.append(target)
        return True


def run(fake, click, **kwargs):
    """以 FakeTask 作为 self 调用被测方法。"""
    return BaseTask.ui_click_until_disappear(fake, click, **kwargs)


class ScriptedVisibility:
    """按脚本返回可见性，并记录调用次数。"""

    def __init__(self, frames):
        self.frames = list(frames)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self.frames:
            return self.frames_last if hasattr(self, 'frames_last') else False
        value = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
        self.frames_last = value
        return value


def test_rule_click_with_visible():
    buy_click = RuleClick((520, 560, 80, 60), (520, 560, 80, 60), name='chess_buy')
    fake = FakeTask()
    visible = ScriptedVisibility([True, True, False, False])
    result = run(fake, buy_click,
                 click_target=buy_click, visible=visible,
                 interval=0, timeout=2.0, confirm_frames=2, max_clicks=3)
    check('RuleClick: 买到消失返回 True', result is True)
    check('RuleClick: 点击目标是点击规则本身',
          fake.clicks and all(c is buy_click for c in fake.clicks),
          f'clicks={len(fake.clicks)}')
    check('RuleClick: 点击 2 次后消失', len(fake.clicks) == 2, str(len(fake.clicks)))
    check('RuleClick: 全程未做图像匹配', fake.appear_targets == [],
          str(fake.appear_targets))


def test_click_target_differs():
    detect = FakeImage('detect')
    click_point = RuleClick((300, 400, 20, 20), (300, 400, 20, 20), name='target')
    fake = FakeTask()
    visible = ScriptedVisibility([True, False])
    result = run(fake, detect, click_target=click_point, visible=visible,
                 interval=0, timeout=2.0, confirm_frames=1)
    check('分离判定/点击: 返回 True', result is True)
    check('分离判定/点击: 点的是 click_target',
          fake.clicks == [click_point] or all(c is click_point for c in fake.clicks),
          str(fake.clicks))


def test_guard_stops():
    click_rule = RuleClick((10, 10, 20, 20), (10, 10, 20, 20), name='g')
    fake = FakeTask()
    state = {'n': 0}

    def guard():
        state['n'] += 1
        return state['n'] < 3

    result = run(fake, click_rule, click_target=click_rule,
                 visible=lambda: True, guard=guard,
                 interval=0, timeout=5.0)
    check('guard: 不满足时返回 False', result is False)
    check('guard: 第 3 帧就停止点击', len(fake.clicks) == 2, str(len(fake.clicks)))


def test_max_clicks():
    click_rule = RuleClick((10, 10, 20, 20), (10, 10, 20, 20), name='m')
    fake = FakeTask()
    result = run(fake, click_rule, click_target=click_rule,
                 visible=lambda: True, interval=0, timeout=5.0, max_clicks=2)
    check('max_clicks: 仍然可见时返回 False', result is False)
    check('max_clicks: 只点 2 次', len(fake.clicks) == 2, str(len(fake.clicks)))


def test_timeout():
    click_rule = RuleClick((10, 10, 20, 20), (10, 10, 20, 20), name='t')
    fake = FakeTask()
    started = time.monotonic()
    result = run(fake, click_rule, click_target=click_rule,
                 visible=lambda: True, interval=0, timeout=0.3)
    elapsed = time.monotonic() - started
    check('timeout: 超时返回 False', result is False)
    check('timeout: 在 0.3s 附近结束', elapsed < 2.0, f'{elapsed:.3f}s')
    check('timeout: 期间有点击', len(fake.clicks) >= 1, str(len(fake.clicks)))


def test_timeout_zero_budget():
    click_rule = RuleClick((10, 10, 20, 20), (10, 10, 20, 20), name='t0')
    fake = FakeTask()
    result = run(fake, click_rule, click_target=click_rule,
                 visible=lambda: True, interval=0, timeout=0.0)
    check('timeout=0: 立即返回 False 不抛异常', result is False)
    check('timeout=0: 不点击', fake.clicks == [], str(fake.clicks))


def test_confirm_frames():
    click_rule = RuleClick((10, 10, 20, 20), (10, 10, 20, 20), name='c')
    fake = FakeTask()
    # 单帧消失不算消失：True, False, True, False, False
    visible = ScriptedVisibility([True, False, True, False, False])
    result = run(fake, click_rule, click_target=click_rule, visible=visible,
                 interval=0, timeout=3.0, confirm_frames=2)
    check('confirm_frames=2: 需要连续两帧消失才返回', result is True)
    check('confirm_frames=2: 单帧 False 后仍继续点击', len(fake.clicks) == 2,
          str(len(fake.clicks)))
    check('confirm_frames=2: visible 至少被问 5 次', visible.calls >= 5,
          str(visible.calls))

    # confirm_frames=1：第一帧 False 就返回
    fake = FakeTask()
    visible = ScriptedVisibility([True, False])
    result = run(fake, click_rule, click_target=click_rule, visible=visible,
                 interval=0, timeout=3.0, confirm_frames=1)
    check('confirm_frames=1: 单帧消失即返回', result is True)
    check('confirm_frames=1: 只点击 1 次', len(fake.clicks) == 1,
          str(len(fake.clicks)))


def test_default_path():
    """旧调用形态：只传 RuleImage，走 appear/appear_then_click。"""
    image = FakeImage('win')
    fake = FakeTask()
    fake.appear_frames = [True, True, True, False]
    result = run(fake, image, interval=0)
    check('默认路径: 图像消失后返回 True', result is True)
    check('默认路径: 点击对象是图像本身',
          fake.clicks and all(c is image for c in fake.clicks))
    check('默认路径: 走 appear_then_click 而不是 click_target',
          fake.appear_targets.count(image) >= 2, str(len(fake.appear_targets)))
    check('默认路径: 每轮都截图', fake.screenshots >= 2, str(fake.screenshots))


def test_unknown_kwarg():
    click_rule = RuleClick((10, 10, 20, 20), (10, 10, 20, 20), name='k')
    fake = FakeTask()
    try:
        run(fake, click_rule, click_target=click_rule, interval=0,
            visible=lambda: True, bogus_option=1)
    except TypeError as error:
        check('未知参数: 抛 TypeError', True, str(error))
        check('未知参数: 报错信息含参数名', 'bogus_option' in str(error), str(error))
    else:
        check('未知参数: 抛 TypeError', False, '未抛异常（**kwargs 吞参数）')


def test_rule_click_without_visible():
    click_rule = RuleClick((10, 10, 20, 20), (10, 10, 20, 20), name='novis')
    fake = FakeTask()
    try:
        run(fake, click_rule, interval=0)
    except TypeError as error:
        check('RuleClick 缺 visible: 抛 TypeError', True, str(error))
        check('RuleClick 缺 visible: 提示要传 visible',
              'visible' in str(error), str(error))
        check('RuleClick 缺 visible: 未做任何图像匹配',
              fake.appear_targets == [], str(fake.appear_targets))
    else:
        check('RuleClick 缺 visible: 抛 TypeError', False, '未抛异常')


def test_confirm_frames_validation():
    click_rule = RuleClick((10, 10, 20, 20), (10, 10, 20, 20), name='v')
    fake = FakeTask()
    try:
        run(fake, click_rule, click_target=click_rule, interval=0,
            visible=lambda: True, confirm_frames=0)
    except ValueError:
        check('非法 confirm_frames: 抛 ValueError', True)
    else:
        check('非法 confirm_frames: 抛 ValueError', False)
    try:
        run(fake, click_rule, click_target=click_rule, interval=0,
            visible=lambda: True, max_clicks=0)
    except ValueError:
        check('非法 max_clicks: 抛 ValueError', True)
    else:
        check('非法 max_clicks: 抛 ValueError', False)


def main():
    for case in (
        test_rule_click_with_visible,
        test_click_target_differs,
        test_guard_stops,
        test_max_clicks,
        test_timeout,
        test_timeout_zero_budget,
        test_confirm_frames,
        test_default_path,
        test_unknown_kwarg,
        test_rule_click_without_visible,
        test_confirm_frames_validation,
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
    print('UI CLICK REGRESSION: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
