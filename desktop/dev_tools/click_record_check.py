# This Python file uses the following encoding: utf-8

"""点击记录键格式的回归测试（纯假对象）。

背景（2026-10-27 12:34 实测事故）：第 2 局被
`GameTooManyClickError: Too many click for a button: shikigami_1` 终止。
根因是设备层点击记录里的键是 **control_name 字符串**（`Control.click()` 里
`handle_control_check(control_name)` → `str(control_name)`），而调用方
`click_record_remove(click_rule)` 传的是规则对象，`str(obj)` 是
`<module.atom.click.RuleClick object at 0x...>`，永远删不到任何记录。
于是同一商店格反复购买时点击数一路累加，到 10 次触发全局保护，整局失败。

覆盖：
1. `Device.click_record_add/remove` 的真实语义（按 str 匹配）
2. 传对象删除 = no-op（解释事故根因，防止再写回这种代码）
3. 按 `.name` 删除生效
4. economy 的 `_slot_click_record_key` / `_clear_slot_click_record` 用的是名字
5. 清掉记录后 10 次保护的计数不会跨尝试累加

用法：`.\\toolkit\\python.exe dev_tools\\click_record_check.py`
"""

import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.atom.click import RuleClick  # noqa: E402
from module.device.device import Device  # noqa: E402
from tasks.Chess.runtime.economy import ChessEconomyMixin  # noqa: E402
from tasks.Chess.runtime.settings import ChessRuntimeSettings  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


class FakeDevice:
    """只带点击记录相关实现，方法直接复用真实 Device 的实现。"""

    def __init__(self):
        self.click_record = deque(maxlen=15)

    click_record_add = Device.click_record_add
    click_record_remove = Device.click_record_remove
    click_record_clear = Device.click_record_clear

    def click(self, control_name):
        # 与 Control.click 一致：写记录用 control_name 字符串
        self.click_record_add(control_name)


class FakeEconomy(ChessEconomyMixin, ChessRuntimeSettings):
    """与真实任务一致地同时混入 settings（常量在 ChessRuntimeSettings 上）。"""

    def __init__(self):
        self.device = FakeDevice()


def make_rule(name='shikigami_1'):
    return RuleClick((0, 0, 10, 10), (0, 0, 10, 10), name=name)


def test_record_key_format():
    device = FakeDevice()
    rule = make_rule()
    device.click(rule.name)
    check('记录里存的是规则名字符串', list(device.click_record) == ['shikigami_1'],
          str(list(device.click_record)))
    check('str(对象) 不等于名字（事故根因）',
          str(rule) != rule.name and 'RuleClick object' in str(rule), str(rule))


def test_remove_by_object_is_noop():
    device = FakeDevice()
    rule = make_rule()
    for _ in range(3):
        device.click(rule.name)
    removed = device.click_record_remove(rule)
    check('传对象删除是 no-op', removed == 0, str(removed))
    check('对象删除后记录仍在', len(device.click_record) == 3,
          str(len(device.click_record)))


def test_remove_by_name():
    device = FakeDevice()
    rule = make_rule()
    for _ in range(3):
        device.click(rule.name)
    removed = device.click_record_remove(rule.name)
    check('按名字删除生效', removed == 3, str(removed))
    check('按名字删除后记录清空', len(device.click_record) == 0,
          str(len(device.click_record)))


def test_economy_helper():
    economy = FakeEconomy()
    rule = make_rule('shikigami_3')
    for _ in range(4):
        economy.device.click(rule.name)
    key = economy._slot_click_record_key(rule)
    check('economy 记录的键是名字', key == 'shikigami_3', key)
    removed = economy._clear_slot_click_record(rule)
    check('economy 清理生效', removed == 4, str(removed))
    check('清理后无残留', len(economy.device.click_record) == 0,
          str(len(economy.device.click_record)))


def test_guard_threshold():
    """同一格连续 3 次尝试各点 3 下：清理后不会累计到触发的 10 下。"""
    device = FakeDevice()
    economy = FakeEconomy()
    economy.device = device
    rule = make_rule()
    tripped = False
    for attempt in range(3):
        economy._clear_slot_click_record(rule)
        for _ in range(3):
            device.click(rule.name)
        counts = {}
        for key in device.click_record:
            counts[key] = counts.get(key, 0) + 1
        if counts.get(rule.name, 0) >= 10:
            tripped = True
    check('三次尝试后仍未达到 10 次阈值', not tripped,
          str(list(device.click_record)))

    # 反例：不清理，同样的点击会触发保护
    device2 = FakeDevice()
    for _ in range(10):
        device2.click(rule.name)
    counts = {}
    for key in device2.click_record:
        counts[key] = counts.get(key, 0) + 1
    check('不清理时 10 次会达到阈值', counts.get(rule.name, 0) >= 10,
          str(counts))


def test_max_attempts_setting():
    economy = FakeEconomy()
    limit = getattr(economy, 'SHOP_BUY_MAX_ATTEMPTS', None)
    check('SHOP_BUY_MAX_ATTEMPTS 已定义且为小整数',
          isinstance(limit, int) and 2 <= limit <= 10, str(limit))


def main():
    for case in (
        test_record_key_format,
        test_remove_by_object_is_noop,
        test_remove_by_name,
        test_economy_helper,
        test_guard_threshold,
        test_max_attempts_setting,
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
    print('CLICK RECORD: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
