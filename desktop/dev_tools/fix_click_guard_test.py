# This Python file uses the following encoding: utf-8
"""B 类（GameTooManyClickError 连点熔断）离线回归。

不启动 OAS 实例、不 adb、不碰真机：把 **真实的** `Device.click_record_*` /
`handle_control_check` 方法绑到一个假 device 上（记录语义完全一致），再用假
task 跑被修过的真实方法，断言四件事：

1. 熔断判定本身没被改坏（同一控制名 10 次仍然抛 GameTooManyClickError）；
2. SwitchSoul 的 `S_SS_GROUP_SWIPE_DOWN` 连续滑动 20 次**不**抛熔断
   （合法重复：清记录 + 自身翻页上限）；
3. 无意义重复改成提前退出：Pets 的投喂入口最多点 3 次、EvoZone 的阵容锁最多
   点 3 次、RealmRaid 的御魂格最多点 5 次，都不再累积到 10 次；
4. 熔断发生后走「清记录 + 关一次弹窗 + 重试一次」，仍失败则
   `set_next_run(success=False)` + `TaskEnd`（软失败，不拖死实例）。

用法（仓库根目录）：

    .\\toolkit\\python.exe dev_tools\\fix_click_guard_test.py
"""
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from module.device.device import Device
from module.exception import GameTooManyClickError, TaskEnd
from tasks.Component.SwitchSoul.click_guard import (
    clear_click_record,
    control_name,
    retry_once_then_soft_fail,
)
from tasks.Pets.script_task import ScriptTask as PetsTask
from tasks.EvoZone.script_task import ScriptTask as EvoZoneTask
from tasks.RealmRaid.script_task import ScriptTask as RealmRaidTask
from tasks.Component.GeneralBuff.general_buff import GeneralBuff


class FakeRule:
    """只有 name 的规则替身（RuleSwipe/RuleClick/RuleImage 都有这个属性）。"""

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f'<FakeRule {self.name}>'


class FakeDevice:
    """复用真实点击记录逻辑的假 device。

    `handle_control_check` / `click_record_*` 直接取 `Device` 上的真方法，
    所以「同一名字 10 次 → 抛错」「两个名字各 6 次 → 抛错」的行为与线上一致；
    截图/连接相关的一概不碰。
    """

    click_record_add = Device.click_record_add
    click_record_remove = Device.click_record_remove
    click_record_remove_rule = Device.click_record_remove_rule
    click_record_check = Device.click_record_check
    handle_control_check = Device.handle_control_check

    def __init__(self):
        self.click_record = deque(maxlen=15)
        self.clicks = 0
        self.clear_count = 0

    # handle_control_check() 会先清卡死计时器；假 device 没有计时器
    def stuck_record_clear(self):
        pass

    def click_record_clear(self):
        self.clear_count += 1
        self.click_record.clear()

    def record(self, control):
        """模拟一次 click/swipe：计数 + 走真实熔断判定。"""
        self.clicks += 1
        self.handle_control_check(control_name(control))


# ---------------------------------------------------------------- 反向对照

def test_guard_still_bites():
    """对照组：不清记录时，同一控制名第 10 次仍然熔断（证明用例是真的）。"""
    device = FakeDevice()
    rule = FakeRule('same_button')
    raised_at = None
    for i in range(1, 13):
        try:
            device.record(rule)
        except GameTooManyClickError:
            raised_at = i
            break
    assert raised_at == 10, f'期望第 10 次熔断，实际 {raised_at}'
    return f'不带清记录时第 {raised_at} 次熔断（对照成立）'


def test_remove_by_object_is_noop():
    """坑位回归：click_record_remove(规则对象) 是 no-op，必须传 name。"""
    device = FakeDevice()
    rule = FakeRule('ss_group_swipe_down')
    device.record(rule)
    removed_by_object = device.click_record_remove(rule)
    removed_by_name = clear_click_record(device, rule)
    assert removed_by_object == 0, f'传对象本应删 0 条，实际 {removed_by_object}'
    assert removed_by_name == 1, f'传 name 本应删 1 条，实际 {removed_by_name}'
    assert len(device.click_record) == 0
    return 'click_record_remove(obj) 删 0 条、clear_click_record(rule) 删 1 条'


# ---------------------------------------------------------------- 合法重复

def test_switch_soul_swipe_15_plus():
    """验收点 1：S_SS_GROUP_SWIPE_DOWN 连续滑动 20 次不抛 GameTooManyClickError。"""
    device = FakeDevice()
    swipe = FakeRule('ss_group_swipe_down')
    for _ in range(20):
        clear_click_record(device, swipe)
        device.record(swipe)
    assert device.clicks == 20, f'期望 20 次滑动，实际 {device.clicks}'
    assert len(device.click_record) <= device.click_record.maxlen
    return f'连续滑动 {device.clicks} 次无熔断（记录窗口 {len(device.click_record)} 条）'


def test_switch_soul_swipe_without_clear_would_fail():
    """对照：同一段 20 次滑动如果不清记录，第 10 次就会炸。"""
    device = FakeDevice()
    swipe = FakeRule('ss_group_swipe_down')
    raised_at = None
    for i in range(1, 21):
        try:
            device.record(swipe)
        except GameTooManyClickError:
            raised_at = i
            break
    assert raised_at == 10, f'期望不清记录时第 10 次熔断，实际 {raised_at}'
    return '不清记录的同一路径第 10 次熔断（修复前后差异可复现）'


# ---------------------------------------------------------------- 无意义重复

def test_pets_feed_entry_bounded():
    """Pets：投喂入口最多点 3 次就放弃（面板永远不出现也不空点）。"""
    device = FakeDevice()

    class FakePets:
        _open_pet_panel = PetsTask._open_pet_panel

        def __init__(self):
            self.device = device
            self.I_PET_FEAST = FakeRule('pet_pet_feast')
            self.I_PET_FEED = FakeRule('pet_pet_feed')

        def screenshot(self):
            return None

        def appear(self, rule):
            return rule is self.I_PET_FEAST  # 入口在、面板永远不在

        def appear_then_click(self, rule, interval=1):
            self.device.record(rule)
            return True

    task = FakePets()
    opened = task._open_pet_panel(task.I_PET_FEAST, task.I_PET_FEED, '投喂')
    assert opened is False, '面板未出现时应返回 False'
    assert device.clicks == 3, f'期望最多 3 次点击，实际 {device.clicks}'
    return f'投喂入口点 {device.clicks} 次后放弃，未触发熔断'


def test_evozone_lock_bounded():
    """EvoZone：阵容锁状态切不动时最多点 3 次就返回 False。"""
    device = FakeDevice()

    class FakeEvoZone:
        check_lock = EvoZoneTask.check_lock

        def __init__(self):
            self.device = device
            self.I_EVOZONE_LOCK = FakeRule('o_evozone_lock')
            self.I_EVOZONE_UNLOCK = FakeRule('o_evozone_unlock')

        def screenshot(self):
            return None

        def appear(self, rule):
            return False  # 状态永远不变（模板失配/被浮层挡住）

        def appear_then_click(self, rule, interval=1):
            self.device.record(rule)
            return True

    task = FakeEvoZone()
    result = task.check_lock(lock=False)  # 配置要求「不锁定」→ 点 I_EVOZONE_LOCK
    assert result is False, '切换不生效时应返回 False'
    assert device.clicks == 3, f'期望最多 3 次点击，实际 {device.clicks}'
    return f'阵容锁点 {device.clicks} 次后放弃并返回 False'


def test_realm_raid_fire_bounded():
    """RealmRaid：同一御魂格（partition_7）最多点 5 次就退出进攻循环。"""
    device = FakeDevice()

    class FakeRealmRaid:
        fire = RealmRaidTask.fire

        def __init__(self):
            self.device = device
            self.I_RR_PERSON = FakeRule('rr_person')
            self.I_UI_CONFIRM = FakeRule('ui_confirm')
            self.I_SOUL_RAID = FakeRule('soul_raid')
            self.I_FIRE = FakeRule('fire')
            self.O_TICKET_SHORT = FakeRule('ticket_short')
            self.partition = [FakeRule(f'partition_{i}') for i in range(1, 10)]

        def screenshot(self):
            return None

        def appear(self, rule, threshold=None):
            return rule is self.I_RR_PERSON  # 一直停在选怪界面

        def ocr_appear(self, rule):
            return False  # 不是「挑战券不足」

        def appear_then_click(self, rule, interval=1):
            return False  # 进攻按钮不点

        def click(self, rule, interval=None):
            self.device.record(rule)
            return True

    task = FakeRealmRaid()
    task.fire(7)
    assert device.clicks == 5, f'期望最多 5 次点击，实际 {device.clicks}'
    assert all(name == 'partition_7' for name in device.click_record)
    return f'partition_7 点 {device.clicks} 次后退出进攻循环'


def _make_fake_buff(device, awake_after_shots):
    """构造假的 GeneralBuff：I_CLOUD 一直可见，I_AWAKE 在第 N 帧后才可见。"""

    class FakeBuff:
        open_buff = GeneralBuff.open_buff

        def __init__(self):
            self.device = device
            self.I_CLOUD = FakeRule('cloud')
            self.I_BUFF_1 = FakeRule('gb_buff_1')
            self.S_BUFF_UP = FakeRule('buff_up')
            self.I_AWAKE = FakeRule('awake')
            self.shots = 0
            self.swipes = 0

        def screenshot(self):
            self.shots += 1

        def appear(self, rule):
            if rule is self.I_CLOUD:
                return True  # buff 面板一开始就是开着的
            if rule is self.I_AWAKE:
                return self.shots > awake_after_shots
            return False

        def appear_then_click(self, rule, interval=2):
            return False

        def swipe(self, rule, interval=None):
            self.swipes += 1
            self.device.record(rule)  # 走真实熔断判定

    return FakeBuff()


def test_general_buff_open_buff_normal():
    """GeneralBuff：上限内行为与旧实现一致 —— 看到觉醒图标就返回 True。"""
    device = FakeDevice()
    task = _make_fake_buff(device, awake_after_shots=2)
    result = task.open_buff()
    assert result is True, '上限内应该和旧实现一样返回 True'
    # 第 1 帧确认 buff 面板已开（I_CLOUD），第 2 帧没看到觉醒 → 滑 1 次，
    # 第 3 帧看到觉醒 → 立刻返回 True（不额外滑动、不等到上限）。
    assert task.swipes == 1, f'期望滑动 1 次，实际 {task.swipes}'
    assert len(device.click_record) <= 1, '每次滑动前都应清掉 buff_up 记录'
    return f'上限内滑动 {task.swipes} 次后返回 True（行为与旧实现一致）'


def test_general_buff_open_buff_bounded():
    """GeneralBuff：觉醒图标一直不出现时，滑 8 次后 warning 软退出（返回 False）。"""
    device = FakeDevice()
    task = _make_fake_buff(device, awake_after_shots=10 ** 6)
    result = task.open_buff()
    assert result is False, '到顶应软退出返回 False'
    assert task.swipes == 8, f'期望最多 8 次滑动，实际 {task.swipes}'
    return f'buff_up 滑 {task.swipes} 次后软退出，未触发熔断'


# ---------------------------------------------------------------- 熔断后软失败
class FakeOwner:
    def __init__(self, device):
        self.device = device
        self.calls = 0
        self.closed = 0
        self.next_run = None

    def close_unknown_pages(self, skip_first_screenshot=True, allow_ai=True):
        self.closed += 1

    def set_next_run(self, task=None, success=True, finish=None):
        self.next_run = (task, success)


def test_guard_retry_once_then_ok():
    """熔断一次后：清记录 + 关弹窗 + 重试成功 → 正常返回。"""
    owner = FakeOwner(FakeDevice())

    def action():
        owner.calls += 1
        if owner.calls == 1:
            raise GameTooManyClickError('Too many click for a button: pet_pet_feast')
        return 'ok'

    result = retry_once_then_soft_fail(owner, action, 'Pets')
    assert result == 'ok', f'期望重试后返回 ok，实际 {result!r}'
    assert owner.calls == 2, f'期望调用 2 次，实际 {owner.calls}'
    assert owner.device.clear_count == 1, '重试前应清空一次点击记录'
    assert owner.closed == 1, '重试前应关一次弹窗'
    assert owner.next_run is None, '重试成功时不应写 set_next_run'
    return '熔断 → 清记录(1)+关弹窗(1) → 重试成功，未判死任务'


def test_guard_retry_then_soft_fail():
    """重试仍熔断：warning + set_next_run(success=False) + TaskEnd。"""
    owner = FakeOwner(FakeDevice())

    def action():
        owner.calls += 1
        owner.device.record(FakeRule('pet_pet_feast'))
        raise GameTooManyClickError('Too many click for a button: pet_pet_feast')

    try:
        retry_once_then_soft_fail(owner, action, 'Pets')
    except TaskEnd:
        pass
    else:
        raise AssertionError('重试仍熔断时应该抛 TaskEnd 收尾')
    assert owner.calls == 2, f'期望只重试一次（共 2 次），实际 {owner.calls}'
    assert owner.next_run == ('Pets', False), f'set_next_run 应标记失败，实际 {owner.next_run}'
    assert owner.closed == 1, '重试前应关一次弹窗'
    return '重试仍熔断 → set_next_run(Pets, success=False) + TaskEnd'


TESTS = [
    ('对照组：不清记录仍然熔断', test_guard_still_bites),
    ('坑位：click_record_remove(对象) 是 no-op', test_remove_by_object_is_noop),
    ('验收：S_SS_GROUP_SWIPE_DOWN 连续滑动 20 次', test_switch_soul_swipe_15_plus),
    ('对照：不清记录的同一滑动路径第 10 次炸', test_switch_soul_swipe_without_clear_would_fail),
    ('Pets：pet_pet_feast 有界重试', test_pets_feed_entry_bounded),
    ('EvoZone：o_evozone_lock 有界重试', test_evozone_lock_bounded),
    ('RealmRaid：partition_7 有界重试', test_realm_raid_fire_bounded),
    ('GeneralBuff：上限内行为不变（返回 True）', test_general_buff_open_buff_normal),
    ('GeneralBuff：buff_up 到顶软退出（返回 False）', test_general_buff_open_buff_bounded),
    ('熔断后：关弹窗+重试一次（成功）', test_guard_retry_once_then_ok),
    ('熔断后：重试仍失败 → 软失败', test_guard_retry_then_soft_fail),
]


def main() -> int:
    total = len(TESTS)
    passed = 0
    failed = 0
    for name, func in TESTS:
        try:
            detail = func()
        except Exception as error:  # noqa: BLE001 - 单条失败不中断整套
            failed += 1
            print(f'[FAIL] {name}: {type(error).__name__}: {error}')
        else:
            passed += 1
            print(f'[PASS] {name}: {detail}')
    print(f'TOTAL {total} PASS {passed} FAIL {failed}')
    if failed:
        print('FIX CLICK GUARD TEST: FAILED')
        return 1
    print('FIX CLICK GUARD TEST: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
