
import time
import asyncio
from concurrent.futures import TimeoutError as FuturesTimeoutError

try:  # 模拟器重启期间会抛 NemuIpcError(不是 TimeoutError 子类)
    from module.device.method.nemu_ipc import NemuIpcError
except Exception:  # noqa: BLE001 - 没有该依赖时降级为空元组
    NemuIpcError = None
from collections import deque
from datetime import datetime

# Patch pkg_resources before importing adbutils and uiautomator2
from module.device.pkg_resources import get_distribution
# Just avoid being removed by import optimization
_ = get_distribution

from module.device.env import IS_WINDOWS
from module.base.timer import Timer
from module.config.utils import get_server_next_update
from module.device.app_control import AppControl
from module.device.control import Control
from module.device.desktop_device import DesktopDevice
from module.device.platform2 import Platform
from module.device.reconnect import ConnectionRecovery
from module.device.screenshot import Screenshot
from module.exception import (DeviceDisconnectedError,
                              GameNotRunningError,
                              GameStuckError,
                              GameTooManyClickError,
                              RequestHumanTakeover,
                              EmulatorNotRunningError)
from module.logger import logger


class Device(DesktopDevice, Platform, Screenshot, Control, AppControl, ConnectionRecovery):
    _screen_size_checked = False
    detect_record = set()
    click_record = deque(maxlen=15)
    stuck_timer = Timer(60, count=60).start()
    stuck_timer_long = Timer(300, count=300).start()
    # `LONG_WAIT` 是给任务用的「显式长等待」占位符：任务在开始一段**有时限**的等待
    # （分享面板、面板导航等）前 stuck_record_add('LONG_WAIT')，结束前 clear，
    # 这样 60s 的短定时器不会把一段正常的等待当成卡死（只有 300s 长定时器才会问）。
    # 不注册它的任务行为完全不变。
    stuck_long_wait_list = ['BATTLE_STATUS_S', 'PAUSE', 'LOGIN_CHECK',
                            'PREPARE_BEFORE_BATTLE', 'LONG_WAIT']
    # 看门狗续期（D 类：战斗/组队等待被误杀）。到点前先截帧确认真实状态，
    # 画面仍在推进就续期，最多 STUCK_RENEW_LIMIT 次（约 +15 分钟）。
    STUCK_RENEW_LIMIT = 3
    STUCK_PROBE_INTERVAL = 1.0        # 续期判定两次采样间隔（秒）
    STUCK_PROBE_MOTION = 0.002        # 短窗口（~1s）变化率阈值：0.2% 像素
    STUCK_PROBE_REF_MOTION = 0.005    # 与「打点时基准帧」相比的变化率阈值
    STUCK_ALIVE_STATES = ('battle', 'team_wait', 'alive')

    def __init__(self, *args, **kwargs):
        # 看门狗续期状态必须是实例级：类属性会在多个 Device 实例之间串。
        self.stuck_renew_count = 0
        self._stuck_ref_image = None
        self._stuck_ref_time = 0.0
        self.stuck_state_probers = []
        for trial in range(4):
            try:
                super().__init__(*args, **kwargs)
                break
            except EmulatorNotRunningError:
                # 桌面客户端没有模拟器可拉：确保客户端在，再重跑一次基类完成窗口绑定
                if self.is_desktop:
                    if trial >= 1 or not self._desktop_ensure_launched():
                        logger.critical('Desktop client not found and auto-launch failed, '
                                        'please start the game and bind PID first')
                        raise RequestHumanTakeover from None
                    continue
                if trial >= 3:
                    logger.critical('Failed to start emulator after 3 trial')
                    raise RequestHumanTakeover
                # Try to start emulator
                if self.emulator_instance is not None:
                    self.emulator_start()
                else:
                    logger.critical(
                        f'No emulator with serial "{self.config.Emulator_Serial}" found, '
                        f'please set a correct serial'
                    )
                    raise RequestHumanTakeover

        # 桌面客户端模式：无模拟器生命周期
        if self.is_desktop:
            self._init_desktop()
            return

        # Auto-fill emulator info
        if IS_WINDOWS and self.config.script.device.emulatorinfo_type == 'auto':
            _ = self.emulator_instance

        self.screenshot_interval_set()

        # Auto-select the fastest screenshot method
        if self.config.script.device.screenshot_method == 'auto':
            self.run_simple_screenshot_benchmark()

    def run_simple_screenshot_benchmark(self):
        """
        Perform a screenshot method benchmark, test 3 times on each method.
        The fastest one will be set into config.
        """
        logger.info('run_simple_screenshot_benchmark')
        # Check resolution first
        # self.resolution_check_uiautomator2()
        # Perform benchmark
        from module.daemon.benchmark import Benchmark
        bench = Benchmark(config=self.config, device=self)
        method = bench.run_simple_screenshot_benchmark()
        # Set
        self.config.script.device.screenshot_method = method
        self.config.save()

    def handle_night_commission(self, daily_trigger='21:00', threshold=30):
        """
        Args:
            daily_trigger (int): Time for commission refresh.
            threshold (int): Seconds around refresh time.

        Returns:
            bool: If handled.
        """
        update = get_server_next_update(daily_trigger=daily_trigger)
        now = datetime.now()
        diff = (update.timestamp() - now.timestamp()) % 86400
        if threshold < diff < 86400 - threshold:
            return False

        # if GET_MISSION.match(self.image, offset=True):
        #     logger.info('Night commission appear.')
        #     self.click(GET_MISSION)
        #     return True

        return False

    def screenshot(self):
        """
        Returns:
            np.ndarray:
        """
        self.stuck_record_check()

        # 桌面模式：窗口缺失说明客户端当前不可用（启动期未出窗口，或客户端崩了）。
        # 直接截图会抛 (1400, '无效的窗口句柄') 搞崩整个进程，必须先拦下；拉客户端
        # 是 Restart 的职责。最小化不算窗口缺失——BitBlt 截图前会先还原窗口。
        if self.is_desktop and not self.desktop_window_exists():
            raise GameNotRunningError('Desktop client window not found')

        started = time.monotonic()
        try:
            super().screenshot()
        except tuple(e for e in (RequestHumanTakeover, TimeoutError,
                                 FuturesTimeoutError, asyncio.TimeoutError,
                                 NemuIpcError) if e is not None):
            # Python 3.10 里 asyncio.TimeoutError 就是 concurrent.futures.TimeoutError，
            # **不是**内建 TimeoutError —— 只写 `except TimeoutError` 会漏掉它，
            # 实测 2026-10-28：nemu_ipc 截图超时抛 asyncio TimeoutError，异常穿透整个任务。

            # nemu_ipc 的渲染 IPC 会偶发卡死（实测：adb screencap 正常，但
            # nemu_capture_display 每次 0.15s 超时，重试 3 次后抛
            # RequestHumanTakeover 或裸 TimeoutError，整个任务被判「需要人工介入」直接退出）。
            # 但 2026-10-28 实测发现：**客户端不在前台时截图必然超时**
            # （前台是桌面 launcher，nemu 没有可抓的渲染面）。这种情况不是 IPC 坏，
            # 而是游戏没了，必须交给任务的恢复逻辑去拉起客户端，不能降级 ADB 硬撑。
            if not self._app_foreground_ok():
                raise GameNotRunningError(
                    'Screenshot failed and game is not in foreground'
                )
            # 每次截图失败都尝试释放一次 IPC（下次调用会重新初始化），
            # 但受 60s 冷却限制，避免在加载/切页阶段反复重建连接。
            self._release_screenshot_ipc('screenshot failed')
            if not self._fallback_to_adb_screenshot(reason='primary method failed'):
                raise
        self._screenshot_fail_count = 0
        self._maybe_reset_screenshot_ipc(time.monotonic() - started)

        if self.handle_night_commission():
            super().screenshot()

        return self.image

    SCREENSHOT_FAIL_LIMIT = 2          # 连续失败 2 次就释放 IPC 重连
    SCREENSHOT_RELEASE_COOLDOWN = 60.0  # 释放后 60s 内不再重复释放
    SCREENSHOT_SLOW_SECONDS = 5.0       # 单帧 >5s 视为异常慢（正常 30~120ms）

    def _app_foreground_ok(self) -> bool:
        """客户端是否还在前台；查不到时按「在」处理，避免误判把好帧丢掉。"""
        checker = getattr(self, 'app_is_running', None)
        if not callable(checker):
            return True
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001 - 探测失败不当成游戏没了
            return True

    def _release_screenshot_ipc(self, reason: str) -> None:
        if getattr(self, 'is_desktop', False):
            return
        # 配置里是枚举（ScreenshotMethod.NEMU_IPC），直接 str() 会得到
        # 'ScreenshotMethod.NEMU_IPC' → 必须取 value 再小写匹配。
        method = getattr(self.config.script.device, 'screenshot_method', '')
        method = str(getattr(method, 'value', method) or '').lower()
        if 'nemu' not in method:
            return
        now = time.monotonic()
        last = float(getattr(self, '_screenshot_release_at', 0.0) or 0.0)
        if now - last < self.SCREENSHOT_RELEASE_COOLDOWN:
            return
        release = getattr(self, 'nemu_ipc_release', None)
        if not callable(release):
            return
        try:
            release()
            self._screenshot_release_at = now
            logger.warning(
                'Screenshot IPC released for re-init '
                f'({reason}); will retry the primary method'
            )
        except Exception as error:  # noqa: BLE001 - 自愈失败不能打断任务
            logger.debug(f'nemu_ipc release failed: {error!r}')

    def _maybe_reset_screenshot_ipc(self, elapsed: float) -> None:
        """单帧异常慢（>5s，例如加载界面）同样释放一次 IPC，但有冷却期。"""
        if elapsed < self.SCREENSHOT_SLOW_SECONDS:
            return
        self._release_screenshot_ipc(f'slow frame {elapsed:.1f}s')

    def _fallback_to_adb_screenshot(self, reason='') -> bool:
        """主截图方式失败时降级到 ADB 截图；成功则后续帧都用 ADB。

        Returns:
            bool: True 表示已拿到新帧，可以继续任务。
        """
        if self.is_desktop:
            return False
        try:
            image = self.screenshot_adb()
        except Exception as error:  # noqa: BLE001 - 降级失败就按原异常向上抛
            logger.error(f'ADB screenshot fallback failed: {error!r}')
            return False
        if image is None or getattr(image, 'size', 0) == 0:
            logger.error('ADB screenshot fallback returned an empty frame')
            return False
        self.image = image
        self._bump_frame_id()
        self._dump_live_frame()
        self._screenshot_force_adb = True
        logger.warning(
            'Screenshot method fell back to ADB '
            f'({reason}); nemu_ipc will not be retried this session'
        )
        return True

    def release_during_wait(self):
        # Scrcpy server is still sending video stream,
        # stop it during wait
        # self.config.script.device.screenshot_method = 'scrcpy'
        if self.config.script.device.screenshot_method == 'scrcpy':
            self._scrcpy_server_stop()
        if self.config.Emulator_ScreenshotMethod == 'nemu_ipc':
            self.nemu_ipc_release()

    def stuck_record_add(self, button):
        """
        当你要设置这个时候检测为长时间的时候，你需要在这里添加
        如果取消后，需要在`stuck_record_clear`中清除
        :param button:
        :return:
        """
        self.detect_record.add(str(button))
        logger.info(f'Add stuck record: {button}')
        # 记下「打点时」的画面，看门狗到点时用它判断这 5 分钟里画面到底有没有动过。
        self._stuck_snapshot_reference()

    def stuck_record_remove(self, button):
        """只移除一个占位符（不走 stuck_record_clear，避免顺带清掉别的记录/计时器）。"""
        try:
            self.detect_record.discard(str(button))
        except AttributeError:
            self.detect_record = set()

    def stuck_record_clear(self):
        self.detect_record = set()
        self.stuck_timer.reset()
        self.stuck_timer_long.reset()
        self.stuck_renew_count = 0
        self._stuck_ref_image = None

    # ---------------------------------------------------------------- 看门狗续期
    # 背景（2026-11-20）：BATTLE_STATUS_S 在「组队集结/进入战斗」阶段就被记录，
    # 而首领战本身可能打 5 分钟以上（快照 1795178295813：1/8 回合、伞柄 33495/236397，
    # 明显还在打），旧逻辑到 300s 直接抛 GameStuckError 把整局任务打死 3 次。
    # 现在到点先截帧确认真实状态：画面仍在推进 → 续期（有上限）；确认停滞 → 原语义抛错。

    def register_stuck_state_prober(self, prober, name: str = ''):
        """注册一个「我现在到底在哪个页面」探针，让看门狗判断更准。

        prober(device) 返回 None（不知道）或 (state, detail)；state 命中
        STUCK_ALIVE_STATES 视为仍在战斗/组队等待（可续期），'stuck' 视为确认卡住。
        注册的探针优先于通用帧差探针，适合「静止画面但有明确战斗 UI」的场景。
        """
        if prober is None:
            return
        self.stuck_state_probers.append(prober)
        logger.debug(f'Stuck state prober registered: {name or prober!r}')

    def _stuck_snapshot_reference(self):
        image = getattr(self, 'image', None)
        self._stuck_ref_time = time.monotonic()
        if image is None:
            self._stuck_ref_image = None
            return
        try:
            self._stuck_ref_image = image.copy()
        except Exception:  # noqa: BLE001 - 不是 ndarray 就存引用
            self._stuck_ref_image = image

    def _stuck_capture_frame(self):
        """给看门狗探针单独取一帧。

        走 `super().screenshot()`（与 `Device.screenshot` 内部同一条父类调用），
        因此不会再进一次 `stuck_record_check`，没有递归。
        """
        try:
            super().screenshot()
        except Exception as error:  # noqa: BLE001 - 取不到帧就交给原语义处理
            logger.warning(f'[Stuck] probe screenshot failed: {error!r}')
            return None
        return getattr(self, 'image', None)

    @staticmethod
    def _frame_motion(prev, curr):
        """两帧的「变化像素占比」，0.0~1.0；无法比较返回 None。"""
        if prev is None or curr is None:
            return None
        try:
            import numpy as np
            a = np.asarray(prev)
            b = np.asarray(curr)
            if a.shape != b.shape:
                return 1.0
            if a.ndim == 3:
                a = a.max(axis=2)
                b = b.max(axis=2)
            diff = np.abs(a.astype('int16') - b.astype('int16'))
            total = int(diff.size) or 1
            return float((diff > 24).sum()) / total
        except Exception:  # noqa: BLE001 - 探测失败按「不知道」处理
            return None

    def stuck_state_probe(self) -> tuple:
        """确认真实状态：返回 (state, detail)。

        state:
        - 'battle' / 'team_wait' / 'alive'：画面仍在推进（战斗/组队等待中）→ 可续期
        - 'stuck'：自打点时起画面完全没变 → 真卡住，不续期
        - 'unknown'：探测失败（截图失败/无帧）→ 不续期，保持原看门狗语义
        """
        for prober in list(self.stuck_state_probers):
            try:
                result = prober(self)
            except Exception as error:  # noqa: BLE001 - 任务探针坏了不能拖垮看门狗
                logger.debug(f'[Stuck] state prober failed: {error!r}')
                continue
            if not result:
                continue
            if isinstance(result, str):
                return result, 'registered prober'
            state = str(result[0])
            detail = str(result[1]) if len(result) > 1 else 'registered prober'
            return state, detail

        first = self._stuck_capture_frame()
        if first is None:
            return 'unknown', 'probe screenshot failed'
        time.sleep(self.STUCK_PROBE_INTERVAL)
        second = self._stuck_capture_frame()
        if second is None:
            return 'unknown', 'probe screenshot failed'

        motion = self._frame_motion(first, second)
        ref_motion = self._frame_motion(self._stuck_ref_image, second)
        detail = (f'帧间变化 {0 if motion is None else motion:.4%}'
                  f' / 与打点帧变化 {0 if ref_motion is None else ref_motion:.4%}')
        if motion is None and ref_motion is None:
            return 'unknown', detail
        if (motion is not None and motion >= self.STUCK_PROBE_MOTION) or \
                (ref_motion is not None and ref_motion >= self.STUCK_PROBE_REF_MOTION):
            return 'battle', detail
        return 'stuck', detail

    def stuck_try_renew(self) -> bool:
        """到点时尝试续期一次；返回 True 表示已续期（调用方不要再抛错）。"""
        # 防御：万一有子类/替身绕过了 Device.__init__，这里补上续期状态
        if getattr(self, 'stuck_renew_count', None) is None:
            self.stuck_renew_count = 0
        if getattr(self, 'stuck_state_probers', None) is None:
            self.stuck_state_probers = []
        if self.stuck_renew_count >= self.STUCK_RENEW_LIMIT:
            logger.warning(
                f'[Stuck] 等待 {self.detect_record} 已续期 {self.stuck_renew_count} 次，'
                f'达到上限 {self.STUCK_RENEW_LIMIT}，不再续期')
            return False
        state, detail = self.stuck_state_probe()
        if state not in self.STUCK_ALIVE_STATES:
            logger.info(f'[Stuck] 截帧确认已停滞（{state}: {detail}），不续期')
            return False
        self.stuck_renew_count += 1
        self.stuck_timer.reset()
        self.stuck_timer_long.reset()
        self._stuck_snapshot_reference()
        logger.info(
            f'[Stuck] {sorted(self.detect_record) or "空闲"} 仍在推进'
            f'（{state}: {detail}），第 {self.stuck_renew_count}/{self.STUCK_RENEW_LIMIT} 次续期')
        return True

    def stuck_record_check(self):
        """
        Raises:
            GameStuckError:
            GameNotRunningError:
            DeviceDisconnectedError:
        """
        reached = self.stuck_timer.reached()
        reached_long = self.stuck_timer_long.reached()

        if not reached:
            return False
        if not reached_long:
            for button in self.stuck_long_wait_list:
                if button in self.detect_record:
                    return False

        # 到点前先截帧确认真实状态：仍在战斗/组队等待页 → 续期（有上限 3 次）并记 INFO。
        # 只有确认真卡住（画面从打点时起毫无变化）或续期用尽，才走下面的抛错路径。
        if self.detect_record and all(
                button in self.stuck_long_wait_list for button in self.detect_record):
            if self.stuck_try_renew():
                return False

        logger.warning('Wait too long')
        logger.warning(f'Waiting for {self.detect_record}')
        self.stuck_record_clear()

        # 实测多数 "Game died" 其实是 ADB 掉线（MuMu 实例仍在运行）。
        # 这里先尝试有限次重连：连通且游戏还在前台就继续任务；
        # 连通但游戏确实没跑，才抛 GameNotRunningError；重连不上抛
        # DeviceDisconnectedError（GameNotRunningError 子类，调度器同样转 Restart）。
        if self.app_is_running_after_recovery():
            # 游戏仍在前台：先让 AI 自愈层最后尝试一次（仅在 enabled+auto 时生效；
            # 关闭 AI 时 handler 直接返回 False，行为与原来完全一致）。
            handler = getattr(self, '_ai_stuck_handler', None)
            if callable(handler):
                try:
                    if handler():
                        logger.info('[AI] Recovered before GameStuckError, continue task')
                        self.stuck_record_clear()
                        return False
                except Exception as error:  # noqa: BLE001
                    logger.warning(f'[AI] stuck handler failed: {type(error).__name__}: {error}')
            # 游戏仍在前台：保持原有 GameStuckError 语义。恢复标志只在
            # reconnect.recover_connection() 真正连通成功时置位，这里不能再置：
            # app_is_running() 本来为真（无任何重连）时同样会走本分支。
            raise GameStuckError(f'Wait too long')
        raise self.device_not_running_error()

    def handle_control_check(self, button):
        self.stuck_record_clear()
        self.click_record_add(button)
        self.click_record_check()

    def click_record_add(self, button):
        self.click_record.append(str(button))

    def click_record_clear(self):
        self.click_record.clear()

    def click_record_remove(self, button):
        """
        Remove a button from `click_record`

        Args:
            button (Button):

        Returns:
            int: Number of button removed
        """
        removed = 0
        for _ in range(self.click_record.maxlen):
            try:
                self.click_record.remove(str(button))
                removed += 1
            except ValueError:
                # Value not in queue
                break

        return removed

    def click_record_remove_rule(self, rule):
        """按规则名删除点击记录。

        点击记录里存的是 `control_name`（即 `RuleClick/RuleImage.name` 字符串），
        直接传规则对象 `str()` 出来是 `<... object at 0x..>`，一条也删不掉
        （实测：同一商店格反复购买累计到 10 次 → `GameTooManyClickError` 整局失败）。
        需要「允许再次点击同一目标」时统一走这个入口。
        """
        if rule is None:
            return 0
        name = getattr(rule, 'name', None)
        key = str(name) if name else str(rule)
        return self.click_record_remove(key)

    def click_record_check(self):
        """
        Raises:
            GameTooManyClickError:
        """
        count = {}
        for key in self.click_record:
            count[key] = count.get(key, 0) + 1
        count = sorted(count.items(), key=lambda item: item[1], reverse=True)
        if count[0][1] >= 10:
            logger.warning(f'Too many click for a button: {count[0][0]}')
            logger.warning(f'History click: {[str(prev) for prev in self.click_record]}')
            self.click_record_clear()
            raise GameTooManyClickError(f'Too many click for a button: {count[0][0]}')
        if len(count) >= 2 and count[0][1] >= 6 and count[1][1] >= 6:
            logger.warning(f'Too many click between 2 buttons: {count[0][0]}, {count[1][0]}')
            logger.warning(f'History click: {[str(prev) for prev in self.click_record]}')
            self.click_record_clear()
            raise GameTooManyClickError(f'Too many click between 2 buttons: {count[0][0]}, {count[1][0]}')

    def disable_stuck_detection(self):
        """
        Disable stuck detection and its handler. Usually uses in semi auto and debugging.
        """
        logger.info('Disable stuck detection')

        def empty_function(*arg, **kwargs):
            return False

        self.click_record_check = empty_function
        self.stuck_record_check = empty_function

    def app_start(self):
        # 桌面模式：窗口缺失时自动启动客户端并绑定 PID（空闲关闭后由 Restart 重新拉起）
        if self.is_desktop:
            if not self._desktop_ensure_launched():
                logger.error('Desktop client not running and auto-launch failed, '
                             'please start the game manually or check game_path')
                raise GameNotRunningError('Desktop client auto-launch failed')
            # 这里必然早于 app_handle_login，且是拉起客户端的唯一入口：客户区没校准到
            # 1280x720 的话登录 OCR 与点击坐标全错。运行期重拉时 Device 对象是复用的，
            # _init_desktop 不会再跑，所以校准不能只挂在初始化路径上
            self.desktop_window_set_size()
            return
        if not self.config.script.error.handle_error:
            logger.critical('No app stop/start, because HandleError disabled')
            logger.critical('Please enable Alas.Error.HandleError or manually login to AzurLane')
            raise RequestHumanTakeover
        super().app_start()
        self.stuck_record_clear()
        self.click_record_clear()

    def app_stop(self):
        # 桌面模式：关闭客户端。desktop_stop_client 内部已做「窗口消失 且 进程退出」
        # 验证并多轮重杀，关不掉时它已记 ERROR，这里只把结论透出
        if self.is_desktop:
            if not self.desktop_stop_client():
                logger.warning('Desktop client not released, residual process may remain')
            return
        if not self.config.script.error.handle_error:
            logger.critical('No app stop/start, because HandleError disabled')
            logger.critical('Please enable Alas.Error.HandleError or manually login to AzurLane')
            raise RequestHumanTakeover
        super().app_stop()
        self.stuck_record_clear()
        self.click_record_clear()


if __name__ == "__main__":
    device = Device(config="oas1")
    # cv2.imshow("imgSrceen", device.screenshot())  # 显示
    # cv2.waitKey(0)
    # cv2.destroyAllWindows()
