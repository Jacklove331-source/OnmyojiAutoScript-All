"""手机端 `module.device`（OAS 设备层的 Android 重实现）。

设计：
- 截图/点击/滑动 → 走 `oas_mobile.bridge`（Shizuku 虚拟屏：screencap -d / input -d）。
- App 生命周期 → 走 Shizuku shell 的 `am start` / `am force-stop` / `pidof`。
- 桌面端独有的东西（minitouch、scrcpy、窗口消息点击、登录弹窗、录像等）一律 no-op，
  但**保留同名方法**，让 OAS 的任务代码不用改。

坐标约定：OAS 的素材按 1280x720 制作，虚拟屏也建 1280x720，所以坐标直接透传。
"""

from __future__ import annotations

import os
import threading
import time

import numpy as np
import cv2
from collections import Counter, deque
from module.base.timer import Timer
from oas_mobile.runtime import checkpoint

from oas_mobile import bridge

from module.atom.ocr import RuleOcr
from module.logger import logger

# 与 OAS 桌面端保持同名常量
ORIENTATION_LANDSCAPE = "landscape"
ORIENTATION_PORTRAIT = "portrait"

# 系统弹窗（退出游戏确认）检测与关闭
O_EXIT_DIALOG = RuleOcr(
    roi=(280, 220, 720, 260), area=(280, 220, 720, 260),
    mode='Single', method='Default', keyword='', name='mobile_exit_dialog')
O_EXIT_DIALOG_CANCEL = RuleOcr(
    roi=(280, 350, 720, 180), area=(280, 350, 720, 180),
    mode='Full', method='Default', keyword='取消', name='mobile_exit_cancel')
C_DIALOG_CANCEL = (843, 416)
# 资源包下载框：坐标必须写 1280x720 归一空间（_native_point 按 scale=720/native_h 反算）。
# 实测原生 1600x900 帧上「取消」≈(600,633)、「下载」≈(760,633) → 归一后取消 =(480,506)，
# 右侧归一 ≈(608,506) 是「下载」，绝不能点。勾选框（30天内不再提醒）原生≈(390,457) → (312,366)。
C_PACK_CANCEL = (480, 506)
C_PACK_MUTE = (312, 366)
POPUP_CHECK_INTERVAL = 45.0  # 每隔这么多秒按新帧抽检一次系统/游戏弹窗
# 说明：弹窗 OCR 是全 ROI（700×240）识别，手机上单次 5–36 s（实测真机战斗中曾出现 36.6 s），
# 频率太高会把正常战斗等待拖到 `mobile page made no progress` 超时；
# 仅在画面真正长时间无点击进展时低频抽检，既能关掉卡住页面的资源包弹窗，又不干扰正常战斗。

TARGET_PACKAGE = bridge.target_package()


class Device:
    """OAS 设备层的手机实现。"""

    def __init__(self, config=None, *args, **kwargs) -> None:  # noqa: ARG002
        self.config = config
        self._image = None
        self._image_frame_id = 0
        self._lock = threading.Lock()
        self._display_id = -1
        configured = getattr(getattr(getattr(config, 'script', None), 'device', None), 'package_name', 'auto')
        configured = str(getattr(configured, 'value', configured))
        if configured not in ('auto', '', 'None'):
            bridge.set_target_package(configured)
        self._target_package = bridge.target_package()
        self._last_save = {}
        self.is_desktop = False
        self.click_record = deque(maxlen=15)
        self._detect_record = set()
        self._stuck_since = time.monotonic()
        self._native_frame_id = -1
        self._click_frame_seen = -1
        self._screenshot_interval = 0.1
        self._last_capture = 0.0
        self._last_popup_check = 0.0
        self.stuck_timer_long = Timer(300, count=300).start()
        self.screenshot_deque = deque(maxlen=4)

    def _target(self) -> str:
        self._target_package = bridge.target_package() or self._target_package
        return self._target_package

    # ---------------------------------------------------------------- 基础状态
    @property
    def image(self):
        return self._image

    @image.setter
    def image(self, value):
        with self._lock:
            if value is not self._image:
                self._image = value
                self._image_frame_id += 1

    @property
    def image_frame_id(self) -> int:
        return self._image_frame_id

    def get_orientation(self) -> str:
        return ORIENTATION_LANDSCAPE

    def check_screen_size_sample(self) -> bool:
        return True

    # ---------------------------------------------------------------- 截图
    _hidden_disabled = False

    def _ensure_display(self) -> int:
        """确保虚拟屏可用（overlay 屏会被系统销毁，重建后把游戏搬回来）。"""
        stream_display = bridge.vdstream_display()
        if bridge.vdstream_running() and stream_display >= 0:
            try:
                info = bridge.vdstream_info()
            except Exception:  # noqa: BLE001
                info = {}
            if info.get('target_on_display', True) is not False:
                self._display_id = stream_display
                return stream_display
            # 直播流所在屏上没有目标游戏：**先试着把任务搬过来**，别一上来就停流。
            # 实测（+42，2026-11-22 05:11）客户端被留在旧 display 上时，停流再重建会因为
            # 「游戏不在主屏」而 ensure_fail，整轮直接卡在登录。搬一次通常就够了。
            moved = False
            try:
                moved = bool(bridge.display_move_task(self._target_package, stream_display))
                if moved:
                    time.sleep(1.5)
                    info = bridge.vdstream_info()
                    moved = info.get('target_on_display', True) is not False
            except Exception as error:  # noqa: BLE001
                logger.warning(f'[mobile] move task onto stream display failed: {error!r}')
            if moved:
                logger.info(f'[mobile] task moved onto stream display {stream_display}')
                self._display_id = stream_display
                return stream_display
            # 搬不动才停掉流重走建屏/搬游戏
            logger.warning('[mobile] stream display has no target game; restart stream')
            try:
                bridge.vdstream_stop()
            except Exception as error:  # noqa: BLE001
                logger.warning(f'[mobile] stop stale stream failed: {error!r}')
        if self._display_id >= 0 and bridge.display_alive(self._display_id):
            return self._display_id
        # 优先用隐藏屏（MediaProjection，主屏完全看不到）；抓帧失败过就不再尝试
        try:
            if (not getattr(self, '_hidden_disabled', False)
                    and bridge.hidden_preferred() and bridge.hidden_ready()):
                hidden = bridge.hidden_display()
                bridge.display_ensure_app(self._target_package)
                if hidden >= 0:
                    self._display_id = hidden
                    logger.info(f"[mobile] hidden display ready: {hidden}")
                    return hidden
        except Exception as error:  # noqa: BLE001
            logger.warning(f"[mobile] hidden display failed: {error!r}")
        display = bridge.display_ensure_app(self._target_package)
        if display >= 0:
            self._display_id = display
            logger.info(f"[mobile] virtual display ready: {display}")
        else:
            self._display_id = -1
            logger.error("[mobile] ensure virtual display failed")
        return self._display_id

    def _dismiss_system_dialog(self) -> bool:
        """检测并关闭系统/游戏弹窗（退出确认、资源包下载、每日一签等）。返回是否关掉了一个弹窗。

        只在截图后按帧号间隔抽检，避免每次截图都跑 OCR。
        实测（2026-11-22）：
        - 「确定退出阴阳师?」→ 点取消 (800,438)；
        - 「『式神音频』扩展包下载 2139MB … 是否下载?」→ 点取消 (600,630)，不点「下载」；
        - 「每日一签 · 今日最佳奖励」→ 点签/屏幕中心推进，最多两次。
        """
        if self._image is None:
            return False
        try:
            text = str(O_EXIT_DIALOG.ocr(self._image) or '')
        except Exception:  # noqa: BLE001
            return False
        display = self._ensure_display()
        if display < 0:
            return False

        def _tap_norm(point) -> bool:
            nx, ny = self._native_point(*point)
            return bool(bridge.display_tap(display, nx, ny))

        if '退出游戏' in text or '退出阴阳师' in text:
            cancel_point = C_DIALOG_CANCEL
            try:
                box = O_EXIT_DIALOG_CANCEL.ocr(self._image)
                if isinstance(box, (tuple, list)) and len(box) == 4 and box[2] > 0 and box[3] > 0:
                    cancel_point = (int(box[0] + box[2] // 2), int(box[1] + box[3] // 2))
            except Exception:  # noqa: BLE001
                pass
            logger.warning(f'[mobile] system dialog detected: {text!r}, tap cancel at {cancel_point}')
            if not _tap_norm(cancel_point):
                logger.warning('[mobile] tap cancel failed')
            time.sleep(0.8)
            return True
        if '是否下载' in text or '扩展包' in text:
            # 资源包下载框：先勾「30天内不再提醒」再点「取消」，绝不点「下载」（会拉几百 MB~2 GB 流量）。
            logger.warning('[mobile] resource-pack dialog detected, tick mute then tap cancel')
            _tap_norm(C_PACK_MUTE)
            time.sleep(0.3)
            if not _tap_norm(C_PACK_CANCEL):
                logger.warning('[mobile] tap pack cancel failed')
            time.sleep(0.8)
            return True
        if '每日一签' in text or '今日最佳奖励' in text:
            logger.warning('[mobile] daily-sign dialog detected, tap to advance')
            if not _tap_norm((640, 320)):
                logger.warning('[mobile] tap daily-sign failed')
            time.sleep(1.2)
            return True
        return False

    def screenshot(self, timeout: float = 8.0):
        before = time.monotonic()
        checkpoint()
        if time.monotonic() - before > .3:
            self.stuck_record_clear()
        self.stuck_record_check()
        deadline = time.monotonic() + timeout
        freeze_recoveries = 0
        while True:
            checkpoint()
            if not bridge.vdstream_running():
                raise RuntimeError('background capture stopped; refusing main-screen fallback')
            if (time.monotonic() - self._last_capture >= self._screenshot_interval
                    and callable(getattr(bridge, 'vdstream_capture', None))):
                bridge.vdstream_capture(85)
            info = bridge.vdstream_info()
            native_id = int(info.get('frame_id', -1))
            ready = info.get('target_on_display', True) is not False
            age = int(info.get('age_ms', -1))
            if (ready and 0 <= age <= 5000 and native_id != self._native_frame_id
                    and time.monotonic() - self._last_capture >= self._screenshot_interval):
                data = bridge.vdstream_jpeg()
                if data:
                    frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        width, height = int(info.get('width', 0)), int(info.get('height', 0))
                        if width and height and frame.shape[:2] != (height, width):
                            raise RuntimeError('background frame dimensions do not match its display')
                        if frame.shape[:2] != (720, 1280):
                            # 不能直接 resize 到 1280x720：20:9 手机（iQOO 12 2400x1080）会把画面横向压扁，
                            # 模板分数从 0.8+ 掉到 0.5 左右（实测 run 174e332d：登录超时）。
                            # 统一「按高度归一 + 横向居中裁 16:9」。
                            frame = self._normalize_frame(frame, width, height)
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        with self._lock:
                            self._image = frame
                            self._native_frame_id = native_id
                            self._image_frame_id += 1
                        self._last_capture = time.monotonic()
                        # 只在「画面已经长时间无点击进展」时才做弹窗 OCR：
                        # 战斗等待期间（BATTLE_STATUS_S / PAUSE）正常 30~60s 无点击，放宽到 60s 再抽检，
                        # 避免真机单次 OCR 6~36s 把正常战斗拖死。
                        idle_for = time.monotonic() - self._stuck_since
                        in_battle_wait = any(
                            k in str(self._detect_record) for k in ('BATTLE_STATUS_S', 'PAUSE')
                        )
                        idle_threshold = 60.0 if in_battle_wait else 30.0
                        check_interval = 60.0 if in_battle_wait else POPUP_CHECK_INTERVAL
                        if (idle_for >= idle_threshold
                                and time.monotonic() - self._last_popup_check >= check_interval):
                            self._last_popup_check = time.monotonic()
                            # 弹窗会顶掉所有页面：式神音频扩展包下载框曾让 SoulsTidy 卡 11 分钟
                            # （evidence/probe-shop2/soulstidy-stuck.jpg）。命中就关掉。
                            try:
                                self._dismiss_system_dialog()
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(f'[mobile] dismiss popup failed: {exc}')
                        return frame
            sleep_started = time.monotonic()
            if sleep_started >= deadline:
                break
            time.sleep(.05)
            step_gap = time.monotonic() - sleep_started
            # 进程被 Android/OriginOS 后台冻结后恢复时，单次 0.05s sleep 会跳变数秒甚至数分钟；
            # 此时重置截屏超时与卡死计时并补抓一次新帧，避免任务白白抛出 no fresh frame 失败。
            if step_gap >= 2.0 and freeze_recoveries < 2:
                freeze_recoveries += 1
                logger.warning(f'[mobile] 检测到后台冻结恢复 (暂停 {step_gap:.1f}s)，重置截屏超时并重新抓帧')
                self.stuck_record_clear()
                deadline = time.monotonic() + max(timeout, 5.0)
        raise RuntimeError('no fresh frame from the managed background display')

    def save_screenshot(self, genre="items", interval=None, to_base_folder=False) -> str:  # noqa: ARG002
        """把当前帧落盘到应用私有目录（OAS 的失败样本保存会用到）。"""
        if self._image is None:
            return ""
        now = time.time()
        if interval and genre in self._last_save and now - self._last_save[genre] < interval:
            return ""
        self._last_save[genre] = now
        folder = os.path.join(os.path.expanduser("~"), "OASMobile", genre)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{int(now)}_{self._image_frame_id}.png")
        import cv2
        cv2.imwrite(path, cv2.cvtColor(self._image, cv2.COLOR_RGB2BGR))
        return path

    def screenshot_interval_set(self, interval=None) -> None:
        self._screenshot_interval = max(0.0, float(interval)) if isinstance(interval, (int, float)) else .1

    def screenshot_nemu_ipc(self):
        return self.screenshot()

    def screenshot_window_background(self):
        return self.screenshot()

    def _frame_metrics(self, width, height):
        """返回 (scale, offset, scaled_w)：scale = 720/height，把原生帧等比缩到高 720；
        offset 为横向偏移：宽屏裁剪时为正（左裁量），窄屏补黑时为负（负左补量）。"""
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            raise RuntimeError('background display dimensions unavailable')
        scale = 720.0 / height
        scaled_w = int(round(width * scale))
        offset = (scaled_w - 1280) // 2 if scaled_w >= 1280 else -((1280 - scaled_w) // 2)
        return scale, offset, scaled_w

    def _normalize_frame(self, frame, width, height):
        """按高度等比缩放后横向居中裁到 1280x720（保持 16:9 素材的比例，20:9 手机不再压扁）。"""
        import cv2  # noqa: PLC0415

        scale, offset, scaled_w = self._frame_metrics(width, height)
        resized = cv2.resize(frame, (scaled_w, 720), interpolation=cv2.INTER_AREA)
        if scaled_w >= 1280:
            return resized[:, offset:offset + 1280]
        # 比 16:9 更窄的屏（很少见）：两侧补黑，保证 1280 宽
        import numpy as np  # noqa: PLC0415

        padded = np.zeros((720, 1280, 3), dtype=resized.dtype)
        left = -offset
        padded[:, left:left + scaled_w] = resized
        return padded

    def _native_point(self, x, y):
        info = bridge.vdstream_info()
        if info.get('target_on_display', True) is False:
            raise RuntimeError('target game is not on the managed display')
        width, height = int(info.get('width', 1280)), int(info.get('height', 720))
        scale, offset, _ = self._frame_metrics(width, height)
        nx = (float(x) + offset) / scale
        ny = float(y) / scale
        return (min(width - 1, max(0, round(nx))), min(height - 1, max(0, round(ny))))

    # ---------------------------------------------------------------- 控制
    def click(self, x: int, y: int, control_check: bool = True, control_name: str = "Click") -> None:
        checkpoint()
        display = self._ensure_display()
        if display < 0:
            raise RuntimeError('background display unavailable; refusing input on the main display')
        if control_check:
            self.handle_control_check(control_name)
        else:
            self.stuck_record_clear()
        native_x, native_y = self._native_point(x, y)
        if not bridge.display_tap(display, native_x, native_y):
            raise RuntimeError(f'tap failed on display {display} at ({x}, {y})')

    def click_minitouch(self, x: int, y: int) -> None:
        self.click(x, y, control_check=False)

    def click_window_message(self, x: int, y: int, fast: bool = False) -> None:
        self.click(x, y, control_check=not fast)

    def long_click(self, x: int, y: int, duration=(0.5, 2), control_name: str = "LongClick") -> None:
        checkpoint()
        seconds = duration[0] if isinstance(duration, (tuple, list)) else float(duration)
        display = self._ensure_display()
        if display < 0:
            raise RuntimeError('background display unavailable')
        self.handle_control_check(control_name)
        native_x, native_y = self._native_point(x, y)
        if not bridge.display_swipe(display, native_x, native_y, native_x, native_y, max(300, int(seconds * 1000))):
            raise RuntimeError('long click failed')

    def swipe(self, p1, p2, duration=(0.1, 0.2), control_name: str = "SWIPE", distance_check: bool = True) -> None:
        checkpoint()
        seconds = duration[0] if isinstance(duration, (tuple, list)) else float(duration)
        display = self._ensure_display()
        if display < 0:
            raise RuntimeError('background display unavailable')
        self.handle_control_check(control_name)
        start = self._native_point(*p1)
        end = self._native_point(*p2)
        if not bridge.display_swipe(display, *start, *end, max(120, int(seconds * 1000))):
            raise RuntimeError('swipe failed')

    def swipe_vector(self, vector, box=None, random_range=None, padding=15,
                     duration=(0.1, 0.2), **kwargs) -> None:  # noqa: ARG002
        """把 (x, y) 向量换算成两点滑动。"""
        box = box or (0, 0, 1280, 720)
        cx = box[0] + box[2] // 2
        cy = box[1] + box[3] // 2
        self.swipe((cx, cy), (cx + int(vector[0]), cy + int(vector[1])), duration=duration)

    def swipe_adb(self, p1, p2, duration=(0.1, 0.2), **kwargs) -> None:  # noqa: ARG002
        self.swipe(p1, p2, duration=duration)

    def drag(self, p1, p2, **kwargs) -> None:  # noqa: ARG002
        self.swipe(p1, p2)

    def multi_click(self, button, n, interval=(0.1, 0.2)) -> None:
        for _ in range(int(n)):
            checkpoint()
            point = button.coord() if hasattr(button, 'coord') else button
            self.click(int(point[0]), int(point[1]))
            time.sleep(interval[0] if isinstance(interval, (tuple, list)) else float(interval))

    # ---------------------------------------------------------------- App 生命周期
    def adb_shell(self, command, **kwargs):  # noqa: ARG002
        """走 Shizuku 的 shell 通道（支持 str 或 list）。"""
        from oas_mobile.shizuku_exec import exec_shell
        return exec_shell(command)

    def app_is_running(self) -> bool:
        from oas_mobile.shizuku_exec import exec_shell
        out = exec_shell(f"pidof {self._target()} || true").strip()
        return bool(out)

    def keyevent(self, keycode: int) -> None:
        """只往后台虚拟屏注入按键（严禁无 displayId 注入到用户主屏）。"""
        checkpoint()
        display = self._ensure_display()
        if display < 0:
            raise RuntimeError('background display unavailable; refusing keyevent on the main display')
        if not bridge.display_keyevent(display, int(keycode)):
            raise RuntimeError(f'keyevent {keycode} failed on display {display}')

    def press_back(self) -> None:
        self.keyevent(4)

    def app_start(self) -> int:
        checkpoint()
        target = self._target()
        # 优先直接建后台虚拟屏并在虚拟屏内静默启动（Android 14+ VDM 不打断主屏、不跨 DPI 重建）；
        # 若该机型/老模拟器拒绝直接从虚拟屏冷启动，再回退到主屏拉起并搬入虚拟屏。
        if not bridge.vdstream_running() and not bridge.vdstream_start(1280, 720):
            raise RuntimeError('background display start failed: ' + bridge.vdstream_status())
        checkpoint()
        self._display_id = bridge.display_ensure_app(target)
        if self._display_id < 0:
            if not bridge.ensure_game_started(target):
                logger.warning('[mobile] target game is not running; continuing anyway')
            checkpoint()
            self._display_id = bridge.display_ensure_app(target)
        if self._display_id < 0:
            raise RuntimeError('cannot launch target app on the background display')
        logger.info(f"[mobile] app_start -> display {self._display_id}")
        return self._display_id

    def app_stop(self) -> None:
        from oas_mobile.shizuku_exec import exec_shell
        exec_shell(f"am force-stop {self._target()}")
        logger.info("[mobile] app stopped")

    def app_lost_login(self) -> bool:
        """手机端不做登录态判定（由任务自己的页面识别兜底）。"""
        return False

    def find_desktop_login_popup(self):  # noqa: D401
        return None

    def desktop_confirm_login_popup(self) -> None:
        return None

    def desktop_mark_logged_in(self) -> None:
        return None

    def desktop_stop_client(self) -> None:
        self.app_stop()

    # ---------------------------------------------------------------- 反检测 / 录像 no-op
    def click_record_add(self, button) -> None:
        self.click_record.append(str(button))

    def click_record_clear(self) -> None:
        self.click_record.clear()

    def click_record_remove(self, button) -> int:
        key = str(button)
        count = self.click_record.count(key)
        for _ in range(count):
            self.click_record.remove(key)
        return count

    def click_record_remove_rule(self, rule) -> int:
        return self.click_record_remove(getattr(rule, 'name', None) or str(rule))

    def click_record_check(self) -> bool:
        from module.exception import GameTooManyClickError
        # 手机端一帧 5–8s：只要帧号在推进，就说明画面在变，不该按"连点卡死"处理。
        frame_id = getattr(self, '_native_frame_id', -1)
        if frame_id != getattr(self, '_click_frame_seen', -1):
            if self.click_record:
                logger.debug(f"[mobile] frames advanced {self._click_frame_seen}->{frame_id}, clear click record")
            self.click_record_clear()
            self._click_frame_seen = frame_id
        common = Counter(self.click_record).most_common(2)
        if common and (common[0][1] >= 10 or (len(common) > 1 and common[1][1] >= 6)):
            diag = f"display={getattr(self, '_display_id', -1)} frame={frame_id}"
            self.click_record_clear()
            raise GameTooManyClickError('repeated mobile input: ' + str(common) + ' ' + diag)
        return False

    @property
    def detect_record(self) -> set:
        return self._detect_record

    @detect_record.setter
    def detect_record(self, value) -> None:
        # 桌面端 device 的 detect_record 是普通属性，上游代码会直接赋值
        # （实测 2026-11-22：导航新增探索页 OCR 兜底后走到该分支，报
        #  `AttributeError: can't set attribute 'detect_record'`）。这里补上 setter。
        self._detect_record = set(value) if value is not None else set()

    def stuck_record_add(self, button) -> None:
        self._detect_record.add(str(button))

    def stuck_record_clear(self) -> None:
        self._detect_record.clear()
        self._stuck_since = time.monotonic()

    def stuck_record_check(self) -> bool:
        from module.exception import GameStuckError
        long_wait = any(key in str(self._detect_record) for key in ('BATTLE_STATUS_S', 'PAUSE', 'LOGIN_CHECK', 'PREPARE_BEFORE_BATTLE'))
        # 60 s 对手机端太紧：任务在同一页上连续 OCR/截图（委派扫 5 个委托名、御魂排序扫标签、
        # 商店翻页找商品）时**没有点击** → 不会触发 handle_control_check 的清零，几十秒就误判卡死
        # （实测 2026-11-22：Delegation `delegate_one` 扫完 5 个名字后被判 GameStuckError 130s；
        #  SoulsTidy 排序阶段同理）。真正卡死会伴随连点保护/登录超时，这条放宽到 150 s 仍能兜住。
        if time.monotonic() - self._stuck_since > (float(self.stuck_timer_long.limit) if long_wait else 150):
            self.stuck_record_clear()
            handler = getattr(self, '_ai_stuck_handler', None)
            if callable(handler) and handler():
                self.stuck_record_clear()
                return False
            raise GameStuckError('mobile page made no progress')
        return False

    def uninstall_minicap(self) -> None:
        return None

    def draw_adb(self, image=None, **kwargs):  # noqa: ARG002
        return image

    # ---------------------------------------------------------------- 兼容入口
    def handle_control_check(self, button) -> None:
        checkpoint()
        self.stuck_record_clear()
        self.click_record_add(button)
        self.click_record_check()

    @property
    def click_methods(self):
        return {"mobile_vd": self.click}

    @property
    def long_click_methods(self):
        return {"mobile_vd": self.long_click}
