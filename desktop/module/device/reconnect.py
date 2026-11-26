# This Python file uses the following encoding: utf-8
"""ADB 掉线自动重连与设备状态区分。

背景：`device.py` 的 `stuck_record_check()` 在 `app_is_running()` 为假时抛
`GameNotRunningError('Game died')`，而实测多数情况是 **ADB 掉线**（MuMu 实例
仍然 `start_finished`），不是游戏崩溃。旧行为下：

- 一次瞬时 ADB 抖动 = 任务 `recovered_aborted`，靠 Restart 重来；
- 若 ADB 方法重试耗尽还会抛 `RequestHumanTakeover`，直接 `exit(1)` 结束整个调度器；
- 实例重启换了 adb 端口时，配置里的旧 serial 永远连不上。

本模块负责：区分「设备掉线 / 模拟器没起 / 游戏没跑 / 未知」，用配置或
MuMuManager 报告的地址做有限次数重连，恢复后重新校验画面与前台包名，
并把每次事件写进 `log/device_connection.jsonl` 作为证据。
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

from module.base.decorator import del_cached_property, has_cached_property
from module.device import mumu_state
from module.exception import (DeviceDisconnectedError,
                              EmulatorNotRunningError,
                              GameNotRunningError,
                              RequestHumanTakeover)
from module.logger import logger

# 连接状态分类
STATE_OK = 'ok'
STATE_ADB_SERVER_DOWN = 'adb_server_down'
STATE_ADB_MISSING = 'adb_missing'
STATE_ADB_OFFLINE = 'adb_offline'
STATE_ADB_UNAUTHORIZED = 'adb_unauthorized'
STATE_EMULATOR_STOPPED = 'emulator_stopped'
STATE_UNKNOWN = 'unknown'


class ConnectionRecovery:
    """Mixin：给 Device 增加 ADB 健康检查与自动重连。"""

    RECOVERY_ATTEMPTS = 3
    RECOVERY_BACKOFF = (0.0, 1.5, 3.0)
    CONNECTION_EVENT_LOG = Path('./log/device_connection.jsonl')
    # 恢复成功时置位的运行期标记：任务层据此把该局记为 recovered_continue。
    # 只做可机读打点，不参与恢复流程本身的决策。
    _recovered_this_run = False

    # ---------------------------------------------------------------- 查询

    def mumu_hints(self):
        """配置里的模拟器 exe 路径，用来定位同目录的 MuMuManager.exe。"""
        try:
            path = self.config.script.device.emulatorinfo_path
        except Exception:  # noqa: BLE001 - 配置缺字段时降级为自动查找
            path = None
        return (path,) if path else ()

    def mumu_configured_index(self):
        name = getattr(self.config.script.device, 'emulatorinfo_name', '') or ''
        match = re.fullmatch(r'MuMuPlayer(?:Global)?-(?:12|15)\.0-(\d+)', name)
        return int(match.group(1)) if match else None

    def mumu_instance(self, force=False):
        """当前 serial 对应的 MuMu 实例信息；非 MuMu 或查询失败返回 None。"""
        index = self.mumu_configured_index()
        if index is not None:
            return mumu_state.instance_for_index(index, hints=self.mumu_hints())
        serial = getattr(self, 'serial', '') or ''
        if not serial.startswith('127.0.0.1:') and 'mumu' not in str(
            getattr(self.config.script.device, 'emulatorinfo_type', '')
        ).lower():
            return None
        return mumu_state.instance_for_serial(
            serial, force=force, hints=self.mumu_hints()
        )

    def adb_device_status(self, serial=None):
        """返回 serial 的 adb 状态；adb 服务不可用时返回 None。"""
        serial = serial or getattr(self, 'serial', '')
        try:
            devices = self.adb_client.device_list()
        except Exception as error:  # noqa: BLE001 - AdbError / OSError / reset
            logger.warning(f'[DeviceRecovery] adb server query failed: {error!r}')
            return None
        for device in devices or []:
            if getattr(device, 'serial', None) != serial:
                continue
            try:
                return str(device.get_state())
            except Exception:  # noqa: BLE001
                return 'unknown'
        return 'missing'

    def classify_connection(self, check_game=False):
        """给出当前设备状态分类，供日志与异常类型选择使用。"""
        adb = self.adb_device_status()
        instance = self.mumu_instance() if adb != 'device' else None
        if adb is None:
            state = STATE_ADB_SERVER_DOWN
        elif adb == 'device':
            state = STATE_OK
        elif adb == 'missing':
            state = (
                STATE_EMULATOR_STOPPED
                if instance is not None
                and not (instance.get('is_process_started') or instance.get('is_android_started'))
                else STATE_ADB_MISSING
            )
        elif adb == 'offline':
            state = STATE_ADB_OFFLINE
        elif adb == 'unauthorized':
            state = STATE_ADB_UNAUTHORIZED
        else:
            state = STATE_UNKNOWN

        info = {
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'state': state,
            'serial': getattr(self, 'serial', ''),
            'adb': adb,
            'emulator': mumu_state.summary(instance),
            'endpoint_hint': mumu_state.instance_endpoint(instance),
            'game': 'unknown',
        }
        if check_game and state == STATE_OK:
            info['game'] = self._game_state_probe()
        return info

    def _game_state_probe(self):
        """粗查前台是不是游戏画面；失败返回 unknown，不抛异常。"""
        try:
            return 'running' if self.app_is_running() else 'stopped'
        except Exception as error:  # noqa: BLE001
            logger.warning(f'[DeviceRecovery] game state probe failed: {error!r}')
            return 'unknown'

    # ---------------------------------------------------------------- 重连

    @staticmethod
    def _sleep(seconds):
        if seconds:
            time.sleep(seconds)

    def _try_connect_endpoint(self, endpoint, record):
        """连接单个地址，返回 adb 状态（'device' 表示成功）。"""
        entry = {'endpoint': endpoint, 'connect': False, 'status': None}
        try:
            entry['connect'] = bool(self.adb_connect(endpoint))
        except EmulatorNotRunningError as error:
            entry['error'] = f'emulator_not_running: {error}'
        except RequestHumanTakeover as error:
            entry['error'] = f'request_human_takeover: {error}'
        except Exception as error:  # noqa: BLE001 - 任何连接异常都只记录
            entry['error'] = repr(error)
        entry['status'] = self.adb_device_status(endpoint)
        record['attempts'].append(entry)
        logger.info(
            '[DeviceRecovery] connect endpoint={} connect={} status={}{}'.format(
                endpoint, entry['connect'], entry['status'],
                f" error={entry['error']}" if entry.get('error') else '',
            )
        )
        return entry['status']

    def rebind_serial(self, endpoint):
        """把 serial 切到实际可用的地址，并作废所有与旧 serial 绑定的会话。"""
        old = getattr(self, 'serial', '')
        if endpoint == old:
            return False
        logger.warning(
            f'[DeviceRecovery] rebind serial: {old} -> {endpoint} '
            f'(MuMu 报告的 adb 地址与配置不一致)'
        )
        self.serial = endpoint
        for name in ('adb', 'u2', 'hermit_session', 'droidcast_session',
                     'minitouch_builder', 'reverse_server'):
            try:
                del_cached_property(self, name)
            except Exception:  # noqa: BLE001
                pass
        try:
            if has_cached_property(self, 'nemu_ipc'):
                self.nemu_ipc_release()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.config.script.device.serial = endpoint
            self.config.save()
            logger.info('[DeviceRecovery] serial 已写回配置')
        except Exception as error:  # noqa: BLE001
            logger.warning(f'[DeviceRecovery] 写回配置 serial 失败: {error!r}')
        return True

    def recover_connection(self, stage='unknown', attempts=None,
                          allow_adb_restart=True, rebind=True):
        """有限次数重连 ADB，返回记录（`recovered` 字段表示是否恢复）。

        顺序：查状态 → 必要时重启 adb server → 逐个候选地址连接
        （配置 serial + MuMuManager 报告的地址）→ 重新校验。
        """
        attempts = int(attempts or self.RECOVERY_ATTEMPTS)
        record = {
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'stage': stage,
            'serial_before': getattr(self, 'serial', ''),
            'adb_before': self.adb_device_status(),
            'attempts': [],
            'recovered': False,
            'rebound': False,
            'state': None,
        }
        instance = self.mumu_instance(force=True)
        record['emulator'] = mumu_state.summary(instance)
        endpoint_hint = mumu_state.instance_endpoint(instance)
        record['endpoint_hint'] = endpoint_hint
        logger.warning(
            f'[DeviceRecovery] stage={stage} adb_before={record["adb_before"]} '
            f'{record["emulator"]}'
        )

        if record['adb_before'] is None and allow_adb_restart:
            try:
                logger.warning('[DeviceRecovery] adb server 无响应，重启 adb')
                self.adb_restart()
            except Exception as error:  # noqa: BLE001
                logger.warning(f'[DeviceRecovery] adb_restart failed: {error!r}')

        if instance is not None and not (instance.get('is_process_started')
                                         or instance.get('is_android_started')):
            logger.warning(
                '[DeviceRecovery] MuMu 实例未启动，'
                '重连无法解决，交由 Restart 处理'
            )
            record['state'] = STATE_EMULATOR_STOPPED
            self._log_connection_event(record)
            return record

        if instance is not None and self.mumu_configured_index() is not None:
            candidates = [endpoint_hint]
        else:
            candidates = [getattr(self, 'serial', '')]
            if endpoint_hint and endpoint_hint not in candidates:
                candidates.append(endpoint_hint)

        for index in range(attempts):
            for endpoint in candidates:
                if not endpoint:
                    continue
                status = self._try_connect_endpoint(endpoint, record)
                if status == 'device':
                    rebound = False
                    if endpoint != getattr(self, 'serial', '') and rebind:
                        rebound = self.rebind_serial(endpoint)
                    record['recovered'] = True
                    self._recovered_this_run = True
                    record['rebound'] = rebound
                    record['adb_after'] = 'device'
                    record['state'] = STATE_OK
                    break
            if record['recovered']:
                break
            backoff = self.RECOVERY_BACKOFF[
                min(index, len(self.RECOVERY_BACKOFF) - 1)
            ]
            logger.warning(
                f'[DeviceRecovery] attempt {index + 1}/{attempts} failed, '
                f'retry after {backoff}s'
            )
            self._sleep(backoff)

        if not record['recovered']:
            record['adb_after'] = self.adb_device_status()
            record['state'] = self.classify_connection().get('state')
            logger.error(
                f'[DeviceRecovery] ADB 重连失败 state={record["state"]} '
                f'adb_after={record["adb_after"]}'
            )
        else:
            logger.info(
                f'[DeviceRecovery] ADB 已恢复 serial={getattr(self, "serial", "")} '
                f'rebound={record["rebound"]}'
            )
        self._log_connection_event(record)
        return record

    # ---------------------------------------------------------------- 决策

    def app_is_running_after_recovery(self) -> bool:
        """判断游戏是否在跑；ADB 掉线时先尝试恢复再判断。

        Returns:
            bool: True 表示游戏在前台（任务可以继续）。
        """
        try:
            if self.app_is_running():
                return True
        except RequestHumanTakeover:
            logger.warning(
                '[DeviceRecovery] app_is_running() 重试耗尽，按 ADB 掉线处理'
            )
        except Exception as error:  # noqa: BLE001
            logger.warning(
                f'[DeviceRecovery] app_is_running() 异常: {error!r}'
            )

        if self.is_desktop:
            # 桌面模式没有 ADB 可修，保持原有语义
            return False

        # ADB 明确健康时，读到“不是游戏”就是游戏真的没跑，不折腾连接
        if self.adb_device_status() == 'device':
            return False

        record = self.recover_connection(stage='app_is_running')
        self._last_recovery = record
        if not record.get('recovered'):
            return False
        try:
            return bool(self.app_is_running())
        except Exception as error:  # noqa: BLE001
            logger.warning(
                f'[DeviceRecovery] 恢复后复查游戏状态失败: {error!r}'
            )
            return False

    def device_not_running_error(self):
        """构造合适的异常：区分 ADB 掉线与游戏真的死了。"""
        state = None
        recovery = getattr(self, '_last_recovery', None)
        if recovery:
            state = recovery.get('state')
        if state is None:
            state = self.classify_connection().get('state')
        if state == STATE_OK:
            self._last_recovery = None
            return GameNotRunningError('Game died')
        detail = (
            f'adb state={state} serial={getattr(self, "serial", "")}'
        )
        if recovery:
            detail += (
                f' adb_before={recovery.get("adb_before")}'
                f' adb_after={recovery.get("adb_after")}'
                f' emulator={recovery.get("emulator")}'
            )
        self._last_recovery = None
        return DeviceDisconnectedError(f'ADB disconnected after recovery: {detail}')

    # ---------------------------------------------------------------- 记录

    def _log_connection_event(self, record):
        """把连接事件追加到 log/device_connection.jsonl。"""
        try:
            self.CONNECTION_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
            with self.CONNECTION_EVENT_LOG.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception:  # noqa: BLE001 - 记录失败不影响任务
            pass


def summarize_status(device) -> dict:
    """诊断工具用的完整状态快照（含 MuMu 实例、前台窗口）。"""
    snapshot = device.classify_connection(check_game=True)
    try:
        snapshot['foreground'] = device.adb_shell(
            ['dumpsys', 'window'], timeout=10
        ).split('mCurrentFocus')[1][:160].strip()
    except Exception as error:  # noqa: BLE001
        snapshot['foreground'] = f'<unavailable: {error!r}>'
    instance = device.mumu_instance(force=True)
    snapshot['mumu_raw'] = instance
    return snapshot
