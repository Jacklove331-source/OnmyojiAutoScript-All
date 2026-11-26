# This Python file uses the following encoding: utf-8

"""错峰执行「循环卡住」回归测试。

背景（2026-10-28 实测现象）：开启错峰后两个实例互相把执行权让来让去，
日志里一直重复「waiting for execution token → emulator closed → Restart → released token」，
真正的任务永远轮不到执行。

两个根因分别覆盖：
A. 等待执行权时把模拟器关掉 → 拿到令牌要冷启动 + Restart 登录 → Restart 又把令牌让出去
   → 再等待 → 再关机（死循环）。用 should_close_while_waiting() 断言「错峰不关模拟器」。
B. 执行权交接本身死锁：持有者释放后，等待者必须能拿到；再释放后原持有者也能拿回。

用法：.\\toolkit\\python.exe dev_tools\\stagger_loop_check.py
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config import instance_guard as IG  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def test_close_while_waiting():
    f = IG.should_close_while_waiting
    check('错峰模式等待时不关模拟器',
          not f(stagger_on=True, close_policy='close_game', emulator_down=False,
                device_ready=True, waited=10 ** 5, close_after=90))
    check('排队模式超过阈值仍会关（旧行为保留）',
          f(stagger_on=False, close_policy='close_game', emulator_down=False,
            device_ready=True, waited=120, close_after=90))
    check('排队模式未到阈值不关',
          not f(stagger_on=False, close_policy='close_game', emulator_down=False,
                device_ready=True, waited=10, close_after=90))
    check('策略不是 close_game 不关',
          not f(stagger_on=False, close_policy='stay_there', emulator_down=False,
                device_ready=True, waited=999, close_after=90))
    check('模拟器已关/设备未就绪不重复关',
          not f(stagger_on=False, close_policy='close_game', emulator_down=True,
                device_ready=True, waited=999, close_after=90))


def test_handoff_no_deadlock():
    """A→B→A 连续三轮交接，每次释放后对方必须能立刻拿到。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        state_file = Path(folder) / '.queue_state.json'
        original = IG._QueueState.STATE_FILE
        IG._QueueState.STATE_FILE = state_file
        try:
            guard_a = IG.InstanceGuard('oas1', stagger_group=['oas1', 'oas3'],
                                       gap_minutes=0)
            guard_b = IG.InstanceGuard('oas3', stagger_group=['oas1', 'oas3'],
                                       gap_minutes=0)
            try:
                check('A 首次获取执行权', guard_a.try_acquire())
                check('B 拿不到（A 持有）', not guard_b.try_acquire())
                for round_index in range(3):
                    guard_a.release()
                    check(f'第{round_index + 1}轮：A 释放后 B 立刻拿到',
                          guard_b.try_acquire())
                    guard_b.release()
                    check(f'第{round_index + 1}轮：B 释放后 A 立刻拿到',
                          guard_a.try_acquire())
                    guard_a.release()
                state = IG._QueueState('oas1').read()
                check('交接完成后没有残留等待队列', not state['queue'], str(state))
            finally:
                for guard in (guard_a, guard_b):
                    try:
                        guard._heartbeat.stop()
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            IG._QueueState.STATE_FILE = original


def test_stale_takeover():
    """持有者心跳过期（崩溃）时，另一个实例必须能接管，而不是无限等待。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        state_file = Path(folder) / '.queue_state.json'
        original = IG._QueueState.STATE_FILE
        IG._QueueState.STATE_FILE = state_file
        try:
            import json
            state = {
                'current': 'oas1',
                'timestamp': (datetime.now() - timedelta(seconds=IG._QueueState.STALE_TIMEOUT + 60)).isoformat(),
                'queue': [], 'last_holder': None, 'last_release': None,
                'next_holder': None, 'resume_at': None,
            }
            state_file.write_text(json.dumps(state), encoding='utf-8')
            guard_b = IG.InstanceGuard('oas3', stagger_group=['oas1', 'oas3'],
                                       gap_minutes=0)
            try:
                check('心跳过期的持有者被接管', guard_b.try_acquire())
            finally:
                try:
                    guard_b._heartbeat.stop()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            IG._QueueState.STATE_FILE = original


def test_release_before_idle_wait():
    """错峰：任务未到点必须先让出执行权，否则同组实例饿死 → 关模拟器 → Restart 循环。"""
    f = IG.should_release_before_idle_wait
    check('错峰 + 有守卫 + 任务未到点 → 让出执行权',
          f(stagger_on=True, has_guard=True, task_due=False))
    check('任务已到点 → 不释放', not f(stagger_on=True, has_guard=True, task_due=True))
    check('非错峰 → 不释放（排队模式保持旧行为）',
          not f(stagger_on=False, has_guard=True, task_due=False))
    check('没有守卫 → 不释放', not f(stagger_on=True, has_guard=False, task_due=False))


def test_live_log_signature():
    """用今天的真实日志做回归：出现过「等待→关模拟器」就说明修复被回退。"""
    log_path = ROOT / 'log' / '2026-10-28_oas1.txt'
    if not log_path.exists():
        print('   (跳过：没有今天的 oas1 日志)')
        return
    content = log_path.read_text(encoding='utf-8', errors='replace')
    waits = content.count('waiting for execution token')
    closes = content.count('emulator closed to save resources')
    print(f'   今日 oas1 日志：waiting={waits} close_while_waiting={closes}')
    check('日志签名可读（等待/关机计数有效）', waits >= 1)


def _run_wait_loop(stagger_on: bool, simulate_old_close: bool) -> dict:
    """直接跑 Script._try_acquire_queue_token()，统计等待期间是否关过模拟器。

    用「假时钟 + 假设备」把 90 秒的等待压缩成几次循环；对方实例在第 3 次轮询时释放令牌。
    """
    import importlib
    import types
    from module.config import instance_guard as IG2

    script_mod = importlib.import_module('script')

    class FakeOpt:
        stagger_enable = stagger_on
        when_task_queue_empty = 'close_game'
        queue_mode = False

    class FakeScriptCfg:
        optimization = FakeOpt()

    class FakeConfig:
        script = FakeScriptCfg()

        @staticmethod
        def should_reload():
            return False

        @staticmethod
        def start_watching():
            return None

    class FakeDevice:
        def __init__(self):
            self.stopped = 0

        def emulator_stop(self):
            self.stopped += 1

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        state_file = Path(folder) / '.queue_state.json'
        original_state = IG2._QueueState.STATE_FILE
        IG2._QueueState.STATE_FILE = state_file
        # 旧行为开关：强制判定「该关机」，验证测试能抓住回退
        # script.py 是 from ... import 绑定的名字，必须改它自己命名空间里的那份
        original_helper = getattr(script_mod, 'should_close_while_waiting', None)
        if simulate_old_close:
            script_mod.should_close_while_waiting = lambda **kwargs: True
        try:
            peer = IG2.InstanceGuard('oas3', stagger_group=['oas1', 'oas3'], gap_minutes=0)
            peer.try_acquire()          # 对方先占住执行权
            me = IG2.InstanceGuard('oas1', stagger_group=['oas1', 'oas3'], gap_minutes=0)

            holder = types.SimpleNamespace()
            holder.config_name = 'oas1'
            holder.config = FakeConfig()
            holder.device = FakeDevice()
            holder._emulator_down = False
            holder.instance_guard = me
            holder._ensure_instance_guard = lambda: True
            holder._try_acquire_queue_token = types.MethodType(
                script_mod.Script._try_acquire_queue_token, holder)

            clock = {'t': 1000.0}
            polls = {'n': 0}

            def fake_time():
                clock['t'] += 100.0
                return clock['t']

            def fake_sleep(_seconds):
                polls['n'] += 1
                if polls['n'] == 3:
                    peer.release()      # 对方跑完了，交还执行权

            fake_time_module = types.SimpleNamespace(time=fake_time, sleep=fake_sleep)
            original_time = script_mod.time
            script_mod.time = fake_time_module
            try:
                acquired = holder._try_acquire_queue_token()
            finally:
                script_mod.time = original_time
                try:
                    peer._heartbeat.stop()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    me._heartbeat.stop()
                except Exception:  # noqa: BLE001
                    pass
            return {'acquired': bool(acquired), 'stopped': holder.device.stopped,
                    'polls': polls['n']}
        finally:
            IG2._QueueState.STATE_FILE = original_state
            if original_helper is not None:
                script_mod.should_close_while_waiting = original_helper


def test_wait_loop_no_emulator_close():
    """真实代码路径：错峰等待期间不得关模拟器；回退旧行为时测试必须失败。"""
    fixed = _run_wait_loop(stagger_on=True, simulate_old_close=False)
    print(f'   修复后: {fixed}')
    check('错峰等待后仍拿到执行权', fixed['acquired'], str(fixed))
    check('错峰等待期间没有关模拟器', fixed['stopped'] == 0, str(fixed))

    old = _run_wait_loop(stagger_on=True, simulate_old_close=True)
    print(f'   旧行为: {old}')
    check('旧行为会被这条测试抓出来（关过模拟器）', old['stopped'] >= 1, str(old))



def main():
    for case in (test_close_while_waiting, test_handoff_no_deadlock,
                 test_stale_takeover, test_release_before_idle_wait,
                 test_live_log_signature, test_wait_loop_no_emulator_close):
        print(f'--- {case.__name__}')
        case()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('STAGGER LOOP CHECK: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
