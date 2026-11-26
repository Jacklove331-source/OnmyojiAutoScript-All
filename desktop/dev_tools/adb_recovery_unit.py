# This Python file uses the following encoding: utf-8
"""ADB 掉线恢复逻辑的行为级回归测试（纯假对象，不接触真机）。

覆盖：
1. `adb_device_status` 的四种返回（device / missing / offline / None）
2. 状态分类：adb_missing / emulator_stopped / adb_offline / adb_server_down
3. 重连阶梯：首连成功、只在 MuMu 新端口上成功（换 serial）、全部失败、实例没起
4. 恢复后判定：游戏可继续 / 游戏真没跑 / 重连不上
5. 异常类型：ADB 掉线抛 DeviceDisconnectedError（GameNotRunningError 子类），
   游戏真没跑仍抛 GameNotRunningError('Game died')

用法：`.\\toolkit\\python.exe dev_tools\\adb_recovery_unit.py`
"""

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.device import mumu_state  # noqa: E402
from module.device.reconnect import (ConnectionRecovery,  # noqa: E402
                                     STATE_ADB_MISSING,
                                     STATE_ADB_OFFLINE,
                                     STATE_ADB_SERVER_DOWN,
                                     STATE_EMULATOR_STOPPED,
                                     STATE_OK)
from module.exception import (DeviceDisconnectedError,  # noqa: E402
                              GameNotRunningError,
                              RequestHumanTakeover)

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    mark = 'PASS' if condition else 'FAIL'
    print(f'[{mark}] {name}' + (f'  ({detail})' if detail and not condition else ''))


class FakeAdbDevice:
    def __init__(self, serial, state):
        self.serial = serial
        self._state = state

    def get_state(self):
        return self._state


class FakeAdbClient:
    def __init__(self, devices=None, raise_error=False):
        self.devices = devices or []
        self.raise_error = raise_error
        self.calls = 0

    def device_list(self):
        self.calls += 1
        if self.raise_error:
            raise RuntimeError('cannot connect to adb server')
        return list(self.devices)


class FakeDevice(ConnectionRecovery):
    """最小可用的 Device 替身，只实现恢复逻辑依赖的部分。"""

    RECOVERY_BACKOFF = (0.0, 0.0, 0.0)

    def __init__(self, serial='127.0.0.1:16416'):
        self.serial = serial
        self.is_desktop = False
        self.config = SimpleNamespace(
            script=SimpleNamespace(
                device=SimpleNamespace(
                    serial=serial,
                    emulatorinfo_path='E:/MuMuPlayer/nx_main/MuMuNxMain.exe',
                )
            ),
            save=self._save_config,
        )
        self.adb_client = FakeAdbClient([FakeAdbDevice(serial, 'device')])
        self.connect_results = {}
        self.connect_attempts = []
        self.adb_restart_calls = 0
        self.app_calls = 0
        self.app_result = True
        self.app_raises = None
        self.saved = 0
        self.CONNECTION_EVENT_LOG = Path(tempfile.gettempdir()) / 'oas_conn_test.jsonl'

    # --- 配置写入
    def _save_config(self):
        self.saved += 1

    # --- 假 adb_connect：按 endpoint 返回结果 / 抛异常
    def adb_connect(self, serial):
        self.connect_attempts.append(serial)
        action = self.connect_results.get(serial, 'fail')
        if action == 'ok':
            # 连接成功后设备出现在列表里
            self.adb_client.devices = [FakeAdbDevice(serial, 'device')]
            return True
        if isinstance(action, BaseException):
            raise action
        return False

    def adb_restart(self):
        self.adb_restart_calls += 1
        self.adb_client.raise_error = False

    # --- 假游戏状态
    def app_is_running(self):
        self.app_calls += 1
        if self.app_raises is not None:
            raise self.app_raises
        return self.app_result


def patch_mumu(instance):
    original = mumu_state.instance_for_serial
    mumu_state.instance_for_serial = lambda *args, **kwargs: instance
    return original


def restore_mumu(original):
    mumu_state.instance_for_serial = original


MUMU_RUNNING = {
    'index': '1',
    'is_android_started': True,
    'is_process_started': True,
    'player_state': 'start_finished',
    'adb_host_ip': '127.0.0.1',
    'adb_port': 16416,
}
MUMU_STOPPED = dict(MUMU_RUNNING, is_android_started=False,
                    is_process_started=False, player_state='not_started')
MUMU_NEW_PORT = dict(MUMU_RUNNING, adb_port=16448)


def test_adb_device_status():
    device = FakeDevice()
    check('adb_device_status(device)', device.adb_device_status() == 'device')
    check('adb_device_status(missing)',
          device.adb_device_status('127.0.0.1:55555') == 'missing')
    device.adb_client = FakeAdbClient([FakeAdbDevice(device.serial, 'offline')])
    check('adb_device_status(offline)', device.adb_device_status() == 'offline')
    device.adb_client = FakeAdbClient(raise_error=True)
    check('adb_device_status(server down)', device.adb_device_status() is None)


def test_classify():
    original = patch_mumu(MUMU_RUNNING)
    try:
        device = FakeDevice()
        check('classify adb ok -> ok',
              device.classify_connection()['state'] == STATE_OK)

        device.adb_client = FakeAdbClient([])
        check('classify adb missing (mumu 仍在跑) -> adb_missing',
              device.classify_connection()['state'] == STATE_ADB_MISSING)
    finally:
        restore_mumu(original)

    original = patch_mumu(MUMU_STOPPED)
    try:
        device = FakeDevice()
        device.adb_client = FakeAdbClient([])
        check('classify 实例没起 -> emulator_stopped',
              device.classify_connection()['state'] == STATE_EMULATOR_STOPPED)
    finally:
        restore_mumu(original)

    original = patch_mumu(MUMU_RUNNING)
    try:
        device = FakeDevice()
        device.adb_client = FakeAdbClient([FakeAdbDevice(device.serial, 'offline')])
        check('classify offline -> adb_offline',
              device.classify_connection()['state'] == STATE_ADB_OFFLINE)

        device.adb_client = FakeAdbClient(raise_error=True)
        check('classify adb server down -> adb_server_down',
              device.classify_connection()['state'] == STATE_ADB_SERVER_DOWN)
    finally:
        restore_mumu(original)


def test_recover_same_serial():
    original = patch_mumu(MUMU_RUNNING)
    try:
        device = FakeDevice()
        device.adb_client = FakeAdbClient([])          # 掉线：列表为空
        device.connect_results = {device.serial: 'ok'}
        record = device.recover_connection(stage='unit')
        check('recover: 首连成功', record['recovered'] is True)
        check('recover: 不换 serial', record['rebound'] is False and
              device.serial == '127.0.0.1:16416')
        check('recover: 只尝试一次地址',
              device.connect_attempts == ['127.0.0.1:16416'],
              str(device.connect_attempts))
    finally:
        restore_mumu(original)


def test_recover_new_port():
    original = patch_mumu(MUMU_NEW_PORT)
    try:
        device = FakeDevice()
        device.adb_client = FakeAdbClient([])
        device.__dict__['adb'] = 'cached-adb'     # 模拟已缓存的会话
        device.__dict__['nemu_ipc'] = 'cached-ipc'
        device.nemu_ipc_release = lambda: device.__dict__.pop('nemu_ipc', None)
        device.connect_results = {'127.0.0.1:16448': 'ok'}
        record = device.recover_connection(stage='unit')
        check('recover: 配置端口失败后走 MuMu 新端口', record['recovered'] is True)
        check('recover: 切换 serial 到新端口',
              device.serial == '127.0.0.1:16448' and record['rebound'] is True)
        check('recover: serial 写回配置',
              device.config.script.device.serial == '127.0.0.1:16448'
              and device.saved == 1)
        check('recover: 旧会话缓存已作废',
              'adb' not in device.__dict__ and 'nemu_ipc' not in device.__dict__)
    finally:
        restore_mumu(original)


def test_recover_failure():
    original = patch_mumu(MUMU_RUNNING)
    try:
        device = FakeDevice()
        device.adb_client = FakeAdbClient([])
        device.connect_results = {}
        record = device.recover_connection(stage='unit')
        check('recover: 全部失败不抛异常', record['recovered'] is False)
        check('recover: 失败也记录状态',
              record['state'] in (STATE_ADB_MISSING, STATE_ADB_SERVER_DOWN),
              str(record['state']))
        check('recover: 按次数重试',
              len(device.connect_attempts) >= 2, str(device.connect_attempts))
    finally:
        restore_mumu(original)


def test_recovery_flag():
    """恢复成功置位 _recovered_this_run，供任务层记为 recovered_continue。"""
    original = patch_mumu(MUMU_RUNNING)
    try:
        device = FakeDevice()
        check('flag: 初始未置位',
              getattr(device, '_recovered_this_run', False) is False)

        device.adb_client = FakeAdbClient([])
        device.connect_results = {device.serial: 'ok'}
        record = device.recover_connection(stage='unit')
        check('flag: 恢复成功 -> _recovered_this_run=True',
              record['recovered'] is True
              and getattr(device, '_recovered_this_run', False) is True)

        failed = FakeDevice()
        failed.adb_client = FakeAdbClient([])
        failed.connect_results = {}
        record = failed.recover_connection(stage='unit')
        check('flag: 恢复失败 -> 不置位',
              record['recovered'] is False
              and getattr(failed, '_recovered_this_run', False) is False)
    finally:
        restore_mumu(original)


def test_recover_emulator_stopped():
    original = patch_mumu(MUMU_STOPPED)
    try:
        device = FakeDevice()
        device.adb_client = FakeAdbClient([])
        record = device.recover_connection(stage='unit')
        check('recover: 实例未启动直接放弃', record['recovered'] is False and
              device.connect_attempts == [])
        check('recover: 实例未启动分类正确',
              record['state'] == STATE_EMULATOR_STOPPED)
    finally:
        restore_mumu(original)


def test_adb_server_down_restart():
    original = patch_mumu(MUMU_RUNNING)
    try:
        device = FakeDevice()
        device.adb_client = FakeAdbClient(raise_error=True)
        device.connect_results = {device.serial: 'ok'}
        record = device.recover_connection(stage='unit')
        check('recover: adb 服务挂了先重启 adb', device.adb_restart_calls >= 1)
        check('recover: 重启后连通', record['recovered'] is True)
    finally:
        restore_mumu(original)


def test_app_running_after_recovery():
    original = patch_mumu(MUMU_RUNNING)
    try:
        # 游戏正常运行：不触发重连
        device = FakeDevice()
        check('app: 正常运行返回 True', device.app_is_running_after_recovery() is True)
        check('app: 正常时不做重连', device.connect_attempts == [])

        # ADB 健康但游戏没跑：不重连，直接 False
        device = FakeDevice()
        device.app_result = False
        check('app: adb 健康+游戏没跑 -> False',
              device.app_is_running_after_recovery() is False)
        check('app: adb 健康时不重连', device.connect_attempts == [])

        # ADB 掉线 + app_is_running 抛 RequestHumanTakeover：重连后游戏还在 -> True
        device = FakeDevice()
        device.adb_client = FakeAdbClient([])
        device.app_raises = RequestHumanTakeover('Retry app_is_running() failed')
        device.connect_results = {device.serial: 'ok'}

        def recover_app():
            device.app_raises = None
            return True

        # 第一次 app_is_running 抛错，恢复后第二次返回 True
        calls = {'n': 0}
        def app_is_running():
            calls['n'] += 1
            if calls['n'] == 1:
                raise RequestHumanTakeover('Retry app_is_running() failed')
            return True
        device.app_is_running = app_is_running
        check('app: ADB 掉线恢复后任务可继续',
              device.app_is_running_after_recovery() is True)
        check('app: 恢复留下记录', bool(getattr(device, '_last_recovery', None)))

        # ADB 掉线且重连失败：False
        device = FakeDevice()
        device.adb_client = FakeAdbClient([])
        device.app_is_running = lambda: (_ for _ in ()).throw(
            RequestHumanTakeover('Retry app_is_running() failed'))
        device.connect_results = {}
        check('app: 重连失败 -> False',
              device.app_is_running_after_recovery() is False)
        check('app: 重连失败记录状态',
              device._last_recovery.get('recovered') is False)
    finally:
        restore_mumu(original)


def test_error_type():
    original = patch_mumu(MUMU_RUNNING)
    try:
        device = FakeDevice()
        error = device.device_not_running_error()
        check('error: ADB 正常 -> GameNotRunningError(\'Game died\')',
              isinstance(error, GameNotRunningError)
              and not isinstance(error, DeviceDisconnectedError)
              and str(error) == 'Game died')

        device = FakeDevice()
        device.adb_client = FakeAdbClient([])
        device.recover_connection(stage='unit')
        error = device.device_not_running_error()
        check('error: ADB 掉线 -> DeviceDisconnectedError',
              isinstance(error, DeviceDisconnectedError))
        check('error: DeviceDisconnectedError 继承 GameNotRunningError',
              isinstance(error, GameNotRunningError))
        check('error: 异常信息含分类与 serial',
              'adb state=' in str(error) and '127.0.0.1:16416' in str(error),
              str(error))
    finally:
        restore_mumu(original)


def main():
    for case in (
        test_adb_device_status,
        test_classify,
        test_recover_same_serial,
        test_recover_new_port,
        test_recover_failure,
        test_recovery_flag,
        test_recover_emulator_stopped,
        test_adb_server_down_restart,
        test_app_running_after_recovery,
        test_error_type,
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
    print('ADB RECOVERY UNIT: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
