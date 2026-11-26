# This Python file uses the following encoding: utf-8

"""ADB 自动重连实机验证（会主动切断 ADB，仅在棋局/任务空闲时使用）。

模式：
    status     只读：打印 adb 状态、MuMu 实例、前台窗口、游戏是否在跑
    cut        断开当前 serial（adb disconnect）→ 调用 recover_connection()
               并校验「adb 状态恢复为 device / 能重新截图 / 游戏仍在前台」
    cut-server 先 `adb kill-server` 再走同样流程（覆盖 adb 服务挂掉的分支）
    port       用错端口模拟「实例换端口」：把 serial 改成不存在的高端口，
               recover_connection() 应通过 MuMuManager 报告的端口纠正回来

用法：
    .\\toolkit\\python.exe dev_tools\\adb_cycle_check.py status
    .\\toolkit\\python.exe dev_tools\\adb_cycle_check.py cut
    .\\toolkit\\python.exe dev_tools\\adb_cycle_check.py cut-server
    .\\toolkit\\python.exe dev_tools\\adb_cycle_check.py port

注意：`cut*` 会让正在运行的任务掉线（这正是被测场景）；不要在关键任务
执行期间运行，除非目的就是验证任务能否自动恢复。
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402
from module.device.reconnect import summarize_status  # noqa: E402


def build_device(config_name):
    config = Config(config_name)
    return Device(config)


def print_json(title, payload):
    print(f'--- {title}')
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def adb_path():
    from module.device.reconnect import ConnectionRecovery  # noqa: F401
    candidate = ROOT / 'toolkit' / 'Lib' / 'site-packages' / 'adbutils' / 'binaries' / 'adb.exe'
    return str(candidate) if candidate.exists() else 'adb'


def run_adb(*args):
    command = [adb_path(), *args]
    print(f'$ {" ".join(command)}', flush=True)
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    print((completed.stdout or '').strip())
    if completed.stderr:
        print((completed.stderr or '').strip())
    return completed


def verify(device, label):
    """恢复后重新校验：adb / 截图 / 前台包名 / 游戏状态。"""
    checks = {}
    checks['adb_status'] = device.adb_device_status()
    checks['serial'] = device.serial
    try:
        started = time.monotonic()
        device.screenshot()
        checks['screenshot'] = 'ok'
        checks['screenshot_ms'] = round((time.monotonic() - started) * 1000)
        checks['image_shape'] = list(getattr(device.image, 'shape', ()))
    except Exception as error:  # noqa: BLE001
        checks['screenshot'] = f'error: {error!r}'
    try:
        checks['app_running'] = bool(device.app_is_running())
    except Exception as error:  # noqa: BLE001
        checks['app_running'] = f'error: {error!r}'
    try:
        window = device.adb_shell(['dumpsys', 'window'], timeout=10)
        marker = 'mCurrentFocus'
        checks['foreground'] = (
            window.split(marker, 1)[1][:140].strip() if marker in window else '<not found>'
        )
    except Exception as error:  # noqa: BLE001
        checks['foreground'] = f'error: {error!r}'
    print_json(f'{label} verify', checks)
    ok = (
        checks['adb_status'] == 'device'
        and checks['screenshot'] == 'ok'
        and checks['app_running'] is True
    )
    return ok, checks


def force_stuck(device):
    """强制 stuck_record_check 进入判定分支。

    Timer.reached() 需要「超过 limit」且「调用次数 > count」两个条件，
    所以 limit 设为 -1、count 设为 0 才能真正触发（只把 limit 归零不够）。
    """
    device.stuck_timer.limit = -1
    device.stuck_timer.count = 0
    device.stuck_timer.reset()


def restore_stuck(device):
    device.stuck_timer.limit = 60
    device.stuck_timer.count = 60
    device.stuck_record_clear()


def guard_test(device):
    """验证 Device.stuck_record_check() 的接线：掉线时应自动恢复而不是直接抛 Game died。"""
    from module.exception import (DeviceDisconnectedError,
                                  GameNotRunningError,
                                  GameStuckError)

    run_adb('disconnect', device.serial)
    force_stuck(device)
    results = {}

    try:
        device.stuck_record_check()
    except DeviceDisconnectedError as error:
        results['disconnected_case'] = f'DeviceDisconnectedError: {error}'
    except GameNotRunningError as error:
        results['disconnected_case'] = f'GameNotRunningError: {error}'
    except GameStuckError as error:
        results['disconnected_case'] = f'GameStuckError（预期：恢复后游戏仍在前台）: {error}'

    recovery = getattr(device, '_last_recovery', None)
    results['recovery_recovered'] = bool(recovery and recovery.get('recovered'))
    results['adb_after'] = device.adb_device_status()
    try:
        results['app_running'] = bool(device.app_is_running())
    except Exception as error:  # noqa: BLE001
        results['app_running'] = f'error: {error!r}'
    restore_stuck(device)

    # 分支 2：ADB 正常但游戏没跑 -> 仍是 GameNotRunningError('Game died')
    original_package = getattr(device, 'package', None)
    device.package = 'com.example.not.onmyoji'
    force_stuck(device)
    try:
        device.stuck_record_check()
        results['game_dead_case'] = 'no exception（不符合预期）'
    except DeviceDisconnectedError as error:
        results['game_dead_case'] = f'DeviceDisconnectedError（不符合预期）: {error}'
    except GameNotRunningError as error:
        results['game_dead_case'] = f'GameNotRunningError: {error}'
    except GameStuckError as error:
        results['game_dead_case'] = f'GameStuckError（不符合预期）: {error}'
    finally:
        if original_package is not None:
            device.package = original_package
        restore_stuck(device)

    # 分支 3：重连失败 -> DeviceDisconnectedError（用替身模拟，不动真机）
    original_recover = device.recover_connection
    device.recover_connection = lambda **kwargs: {
        'recovered': False,
        'state': 'adb_missing',
        'adb_before': 'missing',
        'adb_after': 'missing',
        'emulator': 'mumu#1 state=start_finished',
    }
    device._last_recovery = None
    force_stuck(device)
    # app_is_running 也要压成 False，否则会先走「游戏在前台」的成功分支
    original_status = device.adb_device_status
    original_app = device.app_is_running
    device.adb_device_status = lambda serial=None: 'missing'
    device.app_is_running = lambda: False
    try:
        device.stuck_record_check()
        results['unrecoverable_case'] = 'no exception（不符合预期）'
    except DeviceDisconnectedError as error:
        results['unrecoverable_case'] = f'DeviceDisconnectedError: {error}'
    except Exception as error:  # noqa: BLE001
        results['unrecoverable_case'] = f'{type(error).__name__}: {error}'
    finally:
        device.recover_connection = original_recover
        device.adb_device_status = original_status
        device.app_is_running = original_app
        restore_stuck(device)
        device._last_recovery = None

    print_json('guard_test', results)
    # 分支 1 的关键：掉线后 stuck_record_check 不抛 GameNotRunningError/
    # DeviceDisconnectedError（说明没有被误判成「游戏死了」），且 adb 已恢复、
    # 游戏仍在前台 —— 任务是继续跑，而不是转 Restart。
    ok = (
        results.get('adb_after') == 'device'
        and results.get('app_running') is True
        and results.get('disconnected_case', '').startswith('GameStuckError')
        and results.get('game_dead_case') == "GameNotRunningError: Game died"
        and results.get('unrecoverable_case', '').startswith('DeviceDisconnectedError')
    )
    if not results.get('recovery_recovered'):
        print('   note: 本次恢复由 legacy adb retry 完成（app_is_running 内部 adb_reconnect），'
              '未走到 ConnectionRecovery 分支')
    print()
    print(f'GUARD: {"PASSED" if ok else "FAILED"}')
    return 0 if ok else 1


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else 'status'
    config_name = sys.argv[2] if len(sys.argv) > 2 else 'oas3'
    device = build_device(config_name)

    if mode == 'status':
        print_json('status', summarize_status(device))
        return 0
    if mode == 'guard':
        print_json('before', summarize_status(device))
        return guard_test(device)

    print_json('before', summarize_status(device))
    if mode == 'cut':
        run_adb('disconnect', device.serial)
    elif mode == 'cut-server':
        run_adb('kill-server')
    elif mode == 'port':
        original = device.serial
        bad_port = 16999
        print(f'[port] 把 serial 临时改成 127.0.0.1:{bad_port}（原 {original}）')
        device.serial = f'127.0.0.1:{bad_port}'
    else:
        print(f'unknown mode: {mode}')
        return 2

    started = time.monotonic()
    record = device.recover_connection(stage=f'cycle_{mode}')
    elapsed = time.monotonic() - started
    print_json('recover_connection', record)
    print(f'[timing] recover took {elapsed:.2f}s')

    ok, checks = verify(device, mode)
    print()
    print(f'CYCLE {mode}: {"PASSED" if ok else "FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
