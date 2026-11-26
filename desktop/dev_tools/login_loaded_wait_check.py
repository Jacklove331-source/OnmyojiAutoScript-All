# This Python file uses the following encoding: utf-8

"""离线验证 `LoginHandler._wait_login_loaded` 的等待语义（不碰模拟器）。

覆盖三条分支：
  1. 角色选择页出现 → 提前返回 True；
  2. 只出现庭院标志（加成图标 + 式神录） → 提前返回 True；
  3. 两者都不出现 → 等满超时后返回 False，让调用方重试点击。

同时断言超时上限是 45 秒（旧的 5 秒等待正是「一次加载被拆成几十次点击」的根因），
并断言 login.py 里已经没有 `wait_time=5` 的空等调用 —— 旧实现会让本测试变红。

用法：``.\\toolkit\\python.exe dev_tools\\login_loaded_wait_check.py``
"""

import sys
import types

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class FakeTimer:
    """可控计时器：每个 tick 视为过 1 秒，超过 limit 才 reached（与真实 Timer 的
    `elapsed > limit` 语义一致，否则第一帧就会「超时」，测不出提前返回分支）。"""

    instances = []

    def __init__(self, limit):
        self.limit = limit
        self.count = 0
        self.instances.append(self)

    def start(self):
        return self

    def reached(self):
        return self.count > self.limit

    def tick(self):
        self.count += 1


def run_case(name, sequence, expect, expect_shots=None):
    import tasks.Restart.login as login_module

    original_timer = login_module.Timer
    FakeTimer.instances = []
    login_module.Timer = FakeTimer
    try:
        class Fake:
            I_LOGIN_SPECIFIC_SERVE = 'specific_serve'
            I_BUFF_1 = 'buff_1'
            I_MAIN_GOTO_SHIKIGAMI_RECORDS = 'main_goto_shikigami'

            def __init__(self):
                self.shots = 0
                self.sequence = sequence
                self.timer = None

            def screenshot(self):
                self.shots += 1
                if FakeTimer.instances:
                    FakeTimer.instances[0].tick()

            def appear(self, target, interval=None):
                return bool(self.sequence(self.shots, target))

        fake = Fake()
        method = types.MethodType(login_module.LoginHandler._wait_login_loaded, fake)
        result = method()
        ok = result == expect
        if expect_shots is not None:
            ok = ok and fake.shots == expect_shots
        limit = FakeTimer.instances[0].limit if FakeTimer.instances else None
        print(f'[{"PASS" if ok else "FAIL"}] {name}: result={result} shots={fake.shots} limit={limit}')
        return ok, limit
    finally:
        login_module.Timer = original_timer


def main() -> int:
    failures = 0
    limits = []

    # 1) 第 3 帧起出现角色选择页
    ok, limit = run_case('角色页提前返回',
                         lambda shot, target: target == 'specific_serve' and shot >= 3,
                         True, 3)
    failures += 0 if ok else 1
    limits.append(limit)

    # 2) 第 5 帧起同时出现庭院标志
    ok, limit = run_case('庭院标志提前返回',
                         lambda shot, target: shot >= 5 and target in ('buff_1', 'main_goto_shikigami'),
                         True, 5)
    failures += 0 if ok else 1
    limits.append(limit)

    # 3) 什么都不出现 → 等满 45 秒后超时 False（每帧 tick 1 秒，第 46 帧超过上限）
    ok, limit = run_case('超时返回 False',
                         lambda shot, target: False,
                         False, 46)
    failures += 0 if ok else 1
    limits.append(limit)

    # 4) 超时上限必须是 45s（5s 就是被打断的根因）
    ok = all(value == 45 for value in limits)
    print(f'[{"PASS" if ok else "FAIL"}] 超时上限为 45s: {limits}')
    failures += 0 if ok else 1

    # 5) 源码里不应再存在旧的 5 秒空等
    text = (ROOT / 'tasks' / 'Restart' / 'login.py').read_text(encoding='utf-8')
    ok = 'wait_time=5' not in text and '_wait_login_loaded()' in text
    print(f'[{"PASS" if ok else "FAIL"}] login.py 已替换 5 秒空等')
    failures += 0 if ok else 1

    print(f'\nLOGIN LOADED WAIT CHECK: {"PASSED" if failures == 0 else "FAILED"} '
          f'(failures={failures})')
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
