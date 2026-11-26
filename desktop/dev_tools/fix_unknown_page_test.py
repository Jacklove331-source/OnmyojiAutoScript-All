#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C/D 类未知页离线回归（不需要设备、不需要 OCR 模型）。

覆盖：
1. 真实失败快照 `log/error/1795179872096`（全屏「悬赏封印」浮层压在页面上）：
   修复前页面 checks 为空、没有关闭动作；修复后能识别浮层并给出关闭动作序列。
2. 关闭动作后必须重新截图复判：复判失败再给 1~2 轮回退尝试，仍失败才交回上层。
3. 庭院(page_main)/町中 不按 BACK、不点右上角。
4. `EXIT_GAME_CONFIRM` 只点「取消」，绝不点「确认」；`['町中']`/`['15']`/`[]` 这类
   误报帧一律判 False（StrictKeywordOcr 去掉逐字兜底）。
5. 未知页关闭路径重复点击在每次尝试前清空点击记录，且次数有上限，
   不再抛 GameTooManyClickError。

游戏画面用 `FakeScreen` 帧序列模拟（点击/返回键可推进到下一帧），
OCR 结果由 `FakeBoxed` 注入到真实的 `StrictKeywordOcr` 判定代码里，
几何判据用的是真实截图，不是合成图。

用法：
    .\\toolkit\\python.exe dev_tools\\fix_unknown_page_test.py
"""

from __future__ import annotations

import sys
import time as time_module
import types
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from module.atom.ocr import RuleOcr  # noqa: E402
from module.exception import GameTooManyClickError  # noqa: E402
from module.ocr.base_ocr import OcrMode  # noqa: E402
from tasks.GameUi.navigator import GameUi  # noqa: E402
from tasks.GameUi.session import NavigatorSession  # noqa: E402

SAMPLE_SCREENSHOT = (
    REPO_ROOT / 'log' / 'error' / '1795179872096' / '2026-11-20_21-04-31-591471.png'
)
# 样本快照里右上角红色关闭叉的实测位置（log/error/1795179872096）
SAMPLE_CLOSE_CROSS = (1213, 104)
CANCEL_ROI = GameUi.EXIT_GAME_CANCEL_ROI  # (760, 425, 100, 40)

_RESULTS: list[tuple[str, bool, str]] = []


# --------------------------------------------------------------------------- #
# 假画面 / 假设备 / 假任务
# --------------------------------------------------------------------------- #
class FakeBoxed:
    """模拟 ppocronnx 的 BoxedResult（只需要 box / ocr_text / score）。"""

    def __init__(self, text: str, box):
        self.ocr_text = text
        self.box = np.array(box, dtype=float)
        self.score = 0.99


@dataclass
class Frame:
    """一帧游戏画面。"""

    name: str
    main: bool = False                  # 庭院主界面是否可识别
    town: bool = False                  # 町中是否可识别
    texts: tuple = ()                   # OCR 在该帧能读到的文本框内容
    image: np.ndarray | None = None     # None → 全黑图
    advance_on_click: bool = False      # 被点击后是否推进到下一帧
    advance_on_back: bool = False       # 按返回键后是否推进到下一帧


class FakeScreen:
    """帧序列，模拟「游戏画面随点击/返回键变化」。"""

    def __init__(self, frames: list[Frame], close_radius: int = 30):
        self.frames = list(frames)
        self.index = 0
        self.close_radius = close_radius

    @property
    def frame(self) -> Frame:
        return self.frames[min(self.index, len(self.frames) - 1)]

    @property
    def texts(self) -> tuple:
        return self.frame.texts

    def advance(self) -> None:
        if self.index < len(self.frames) - 1:
            self.index += 1

    def image(self) -> np.ndarray:
        if self.frame.image is None:
            return np.zeros((720, 1280, 3), dtype=np.uint8)
        return self.frame.image

    def on_click(self, x: int, y: int) -> None:
        if self.frame.advance_on_click:
            self.advance()

    def on_back(self) -> None:
        if self.frame.advance_on_back:
            self.advance()


class FakeDevice:
    """假设备：截图/点击/返回键/点击记录熔断，语义与 module/device/device.py 对齐。"""

    def __init__(self, screen: FakeScreen):
        self.screen = screen
        self.image = None
        self.click_record: deque = deque(maxlen=15)
        self.clicks: list[tuple[int, int, str]] = []
        self.back_keys = 0
        self.config = types.SimpleNamespace(
            script=types.SimpleNamespace(device=types.SimpleNamespace(control_method='minitouch'))
        )

    # --- 截图 ---
    def screenshot(self):
        self.image = self.screen.image()
        return self.image

    # --- 点击 ---
    def click(self, x: int, y: int, control_check: bool = True, control_name: str = 'Click') -> None:
        x, y = int(x), int(y)
        if control_check:
            self.handle_control_check(control_name)
        self.clicks.append((x, y, control_name))
        self.screen.on_click(x, y)

    def handle_control_check(self, button) -> None:
        self.click_record_add(button)
        self.click_record_check()

    def click_record_add(self, button) -> None:
        self.click_record.append(str(button))

    def click_record_clear(self) -> None:
        self.click_record.clear()

    def click_record_check(self) -> None:
        count: dict = {}
        for key in self.click_record:
            count[key] = count.get(key, 0) + 1
        ordered = sorted(count.items(), key=lambda item: item[1], reverse=True)
        if ordered and ordered[0][1] >= 10:
            raise GameTooManyClickError(f'Too many click for a button: {ordered[0][0]}')
        if len(ordered) >= 2 and ordered[0][1] >= 6 and ordered[1][1] >= 6:
            raise GameTooManyClickError(
                f'Too many click between 2 buttons: {ordered[0][0]}, {ordered[1][0]}')

    # --- 其它 ---
    def adb_shell(self, cmd, **_kwargs):
        if list(cmd) != ['input', 'keyevent', '4']:
            raise AssertionError(f'unexpected adb_shell: {cmd}')
        self.back_keys += 1
        self.screen.on_back()
        return ''

    def app_lost_login(self) -> bool:
        return False

    def app_is_running(self) -> bool:
        return True

    def get_orientation(self) -> int:
        return 1

    def uninstall_minicap(self) -> None:
        return None


class FakeTask(GameUi):
    """只替换「相机 + 输入」，其余逻辑全部走真实的 GameUi 代码。"""

    def __init__(self, screen: FakeScreen):
        self.screen = screen
        self.device = FakeDevice(screen)
        self.config = self.device.config
        self.navigator = NavigatorSession(task_category='orochi')
        self.interval_timer = {}

    # --- 相机（页面模板识别）---
    def screenshot(self):
        return self.device.screenshot()

    def maybe_screenshot(self, soft_skip: bool = False):
        if not soft_skip or self.device.image is None:
            return self.screenshot()
        return self.device.image

    def appear(self, target, interval: float | None = None, threshold: float | None = None) -> bool:
        if target is self.I_CHECK_MAIN:
            return self.screen.frame.main
        if target is self.I_CHECK_TOWN:
            return self.screen.frame.town
        if isinstance(target, RuleOcr):
            return bool(self.ocr_appear(target))
        return False

    # 复刻 BaseTask.ocr_appear 的 Full 分支，让 StrictKeywordOcr 真正跑一遍
    def ocr_appear(self, target: RuleOcr, interval: float | None = None, exact: bool = False) -> bool:
        if not isinstance(target, RuleOcr) or not target.keyword:
            return False
        if target.mode != OcrMode.FULL:
            return False
        result = target.ocr(self.device.image, exact=exact)
        return result != (0, 0, 0, 0)

    # --- 输入（未知页关闭模板在这里一律「没点到」）---
    def appear_then_click(self, target, action=None, interval=None, threshold=None) -> bool:
        return False

    def click(self, click=None, interval=None) -> bool:
        return False

    def ai_try_learned_template(self) -> bool:
        return False

    def ai_recover_unknown_page(self) -> bool:
        return False


# --------------------------------------------------------------------------- #
# OCR 注入（走到真实的 StrictKeywordOcr 判定代码）
# --------------------------------------------------------------------------- #
_ALL_OCR_RULES = (GameUi.O_EXIT_GAME_CONFIRM, *GameUi.OVERLAY_TITLE_RULES)


def _ocr_stub(screen: FakeScreen):
    def detect_and_ocr(image, logDisplay: bool = True):
        results = []
        for index, text in enumerate(screen.texts):
            if not text:
                continue
            top = 10 + index * 30
            box = [[30, top], [170, top], [170, top + 28], [30, top + 28]]
            results.append(FakeBoxed(text, box))
        return results

    return detect_and_ocr


class patched_ocr:
    """把当前帧的 OCR 文本注入到已注册的 OCR 规则上，退出时还原。"""

    def __init__(self, screen: FakeScreen):
        self.screen = screen
        self._restore = []

    def __enter__(self):
        stub = _ocr_stub(self.screen)
        for rule in _ALL_OCR_RULES:
            had = 'detect_and_ocr' in rule.__dict__
            old = rule.__dict__.get('detect_and_ocr')
            rule.detect_and_ocr = stub
            self._restore.append((rule, had, old))
        return self

    def __exit__(self, *_exc):
        for rule, had, old in self._restore:
            if had:
                rule.detect_and_ocr = old
            else:
                del rule.detect_and_ocr
        return False


# --------------------------------------------------------------------------- #
# 素材
# --------------------------------------------------------------------------- #
def real_screenshot() -> np.ndarray:
    import cv2

    image = cv2.imread(str(SAMPLE_SCREENSHOT))
    if image is None:
        raise AssertionError(f'读不到样本截图：{SAMPLE_SCREENSHOT}')
    return image


def image_with_red_cross() -> np.ndarray:
    """全黑图 + 右上角红色关闭叉（用来证明主界面保护生效：有叉也不许点）。"""

    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    x, y = SAMPLE_CLOSE_CROSS
    image[y - 12:y + 12, x - 12:x + 12] = (40, 40, 255)  # BGR 红
    return image


def image_with_cancel_panel() -> np.ndarray:
    """深色背景 + 「取消」按钮面板（比四周亮），给退出确认框几何判据用。"""

    image = np.full((720, 1280, 3), 30, dtype=np.uint8)
    x, y, width, height = CANCEL_ROI
    image[y:y + height, x:x + width] = 190
    return image


# --------------------------------------------------------------------------- #
# 断言工具
# --------------------------------------------------------------------------- #
def check(name: str, condition: bool, detail: str = '') -> None:
    ok = bool(condition)
    _RESULTS.append((name, ok, detail))
    flag = 'PASS' if ok else 'FAIL'
    print(f'[{flag}] {name}' + (f' :: {detail}' if detail else ''))
    if not ok:
        raise AssertionError(f'{name} :: {detail}')


def near(point, target, tolerance: int = 15) -> bool:
    return abs(point[0] - target[0]) <= tolerance and abs(point[1] - target[1]) <= tolerance


def new_task(screen: FakeScreen) -> FakeTask:
    task = FakeTask(screen)
    task.device.screenshot()
    return task


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
def case_real_snapshot_before_and_after() -> None:
    """真实快照：修复前「页面 checks 为空 / 没识别到浮层」，修复后能识别并给出点击坐标。"""

    sample = real_screenshot()
    screen = FakeScreen([Frame('bounty_overlay', main=False, town=False, texts=(), image=sample)])
    task = new_task(screen)

    with patched_ocr(screen):
        # 修复前的现场：页面模板全部匹配不到（对应日志 `Current page checks: []`）
        page_checks = [name for name, visible in (
            ('page_main', task.appear(task.I_CHECK_MAIN)),
            ('page_town', task.appear(task.I_CHECK_TOWN)),
        ) if visible]
        check('修复前：真实快照页面 checks 为空', page_checks == [], f'checks={page_checks}')
        check('修复前：老逻辑的两个关闭判据都不命中（关键字 / 退出确认）',
              not task.ocr_appear(GameUi.O_OVERLAY_TITLE_BOUNTY)
              and not task.appear(GameUi.O_EXIT_GAME_CONFIRM),
              'OCR 文本为空时两个规则都必须判 False')

        detected = task._detect_fullscreen_overlay()
        check('修复后：真实快照能识别出全屏浮层', detected is not None, f'detected={detected}')
        source, point = detected
        check('修复后：几何判据定位到样本里的红色关闭叉',
              source == 'x' and point is not None and near(point, SAMPLE_CLOSE_CROSS, 15),
              f'source={source} point={point} 期望≈{SAMPLE_CLOSE_CROSS}')


def case_overlay_ocr_close_and_recheck() -> None:
    """浮层存在 → 关闭 → 重新截图复判通过（记录 overlay_ocr）。"""

    frames = [
        Frame('bounty_overlay', main=False, texts=('悬赏封印',), image=real_screenshot(),
              advance_on_click=True),
        Frame('page_main', main=True, texts=()),
    ]
    screen = FakeScreen(frames)
    task = new_task(screen)

    with patched_ocr(screen):
        closed = task.close_unknown_pages(skip_first_screenshot=False, allow_ai=False)

    check('浮层(OCR 通道)：close_unknown_pages 返回 True', closed is True, f'closed={closed}')
    check('浮层(OCR 通道)：只点了一次浮层关闭按钮', len(task.device.clicks) == 1,
          f'clicks={task.device.clicks}')
    x, y, control_name = task.device.clicks[0]
    check('浮层(OCR 通道)：点击目标是浮层关闭按钮且落在样本红叉附近',
          control_name == 'OVERLAY_CLOSE' and near((x, y), SAMPLE_CLOSE_CROSS, 15),
          f'click=({x}, {y}, {control_name})')
    check('浮层(OCR 通道)：关闭后重新截图复判到了主界面', screen.index == 1, f'frame={screen.frame.name}')
    check("浮层(OCR 通道)：记录为 _record_unknown_close_event('overlay_ocr')",
          task.navigator.unknown_close_history[-1] == 'overlay_ocr',
          f'history={task.navigator.unknown_close_history}')
    check('浮层(OCR 通道)：未按返回键、未走 AI 兜底',
          task.device.back_keys == 0 and 'None' not in task.navigator.unknown_close_history,
          f'back_keys={task.device.back_keys} history={task.navigator.unknown_close_history}')


def case_overlay_geometry_close() -> None:
    """没有 OCR 关键词时，几何判据同样能关掉浮层（记录 overlay_x）。"""

    frames = [
        Frame('bounty_overlay', main=False, texts=(), image=real_screenshot(), advance_on_click=True),
        Frame('page_main', main=True, texts=()),
    ]
    screen = FakeScreen(frames)
    task = new_task(screen)

    with patched_ocr(screen):
        closed = task.close_unknown_pages(skip_first_screenshot=False, allow_ai=False)

    check('浮层(几何通道)：close_unknown_pages 返回 True', closed is True, f'closed={closed}')
    check('浮层(几何通道)：只点了一次且落在红叉附近',
          len(task.device.clicks) == 1 and near(task.device.clicks[0][:2], SAMPLE_CLOSE_CROSS, 15),
          f'clicks={task.device.clicks}')
    check("浮层(几何通道)：记录为 _record_unknown_close_event('overlay_x')",
          task.navigator.unknown_close_history[-1] == 'overlay_x',
          f'history={task.navigator.unknown_close_history}')


def case_overlay_attempt_cap_and_no_many_click_error() -> None:
    """浮层关不掉：最多 3 次；连续多轮未知页关闭不触发 GameTooManyClickError。"""

    screen = FakeScreen([Frame('bounty_overlay', main=False, texts=('悬赏封印',),
                               image=real_screenshot())])
    task = new_task(screen)

    with patched_ocr(screen):
        closed = task._close_fullscreen_overlay()
        check('浮层关不掉：_close_fullscreen_overlay 返回 False', closed is False, f'closed={closed}')
        check('浮层关不掉：重试次数有上限（3 次）', len(task.device.clicks) == GameUi.OVERLAY_CLOSE_ATTEMPTS,
              f'clicks={len(task.device.clicks)}')

        for _ in range(12):
            task.close_unknown_pages(skip_first_screenshot=False, allow_ai=False)
        try:
            task.device.click_record_check()
            raised = None
        except GameTooManyClickError as error:  # noqa: BLE001
            raised = error
        check('浮层反复关闭 12 轮不触发 GameTooManyClickError', raised is None, f'raised={raised}')
        check('浮层反复关闭 12 轮没有把点击记录堆满',
              len(task.device.click_record) < 10, f'record={list(task.device.click_record)}')
        # 返回键有总量预算：关不掉时可以按，但不会无限按（否则上层超时永远不触发）
        check('浮层反复关闭 12 轮 BACK 次数不超过预算',
              task.device.back_keys <= GameUi.MAX_UNKNOWN_PAGE_BACK_PRESSES,
              f'back_keys={task.device.back_keys} budget={GameUi.MAX_UNKNOWN_PAGE_BACK_PRESSES}')


def case_main_page_is_protected() -> None:
    """庭院页：即使右上角有红色关闭叉，也不许点右上角、不许按 BACK。"""

    screen = FakeScreen([Frame('page_main', main=True, texts=(), image=image_with_red_cross())])
    task = new_task(screen)

    with patched_ocr(screen):
        detected = task._detect_fullscreen_overlay()
        closed = task.close_unknown_pages(skip_first_screenshot=False, allow_ai=False)

    check('庭院页：全屏浮层判定直接跳过（不点右上角）', detected is None, f'detected={detected}')
    check('庭院页：close_unknown_pages 不点任何坐标', task.device.clicks == [],
          f'clicks={task.device.clicks}')
    check('庭院页：close_unknown_pages 不按 BACK', task.device.back_keys == 0,
          f'back_keys={task.device.back_keys}')
    check('庭院页：判定为可用页面（恢复轮看到主界面后停止）', closed is True, f'closed={closed}')


def case_town_page_is_protected() -> None:
    """町中：同样不许点右上角、不许按 BACK。"""

    screen = FakeScreen([Frame('page_town', main=False, town=True, texts=('町中',),
                               image=image_with_red_cross())])
    task = new_task(screen)

    with patched_ocr(screen):
        detected = task._detect_fullscreen_overlay()
        task.close_unknown_pages(skip_first_screenshot=False, allow_ai=False)

    check('町中：全屏浮层判定跳过', detected is None, f'detected={detected}')
    check('町中：不点任何坐标', task.device.clicks == [], f'clicks={task.device.clicks}')
    check('町中：不按 BACK', task.device.back_keys == 0, f'back_keys={task.device.back_keys}')


def case_exit_confirm_false_positive_frames() -> None:
    """`['町中']`、`['15']`、`['切换']`、`[]` 这些误报帧必须判 False。"""

    for text in ('町中', '15', '切换', ''):
        screen = FakeScreen([Frame(f'false_positive[{text!r}]', main=False, texts=((text,) if text else ()),
                                   image=image_with_cancel_panel())])
        task = new_task(screen)
        with patched_ocr(screen):
            visible = task.appear(GameUi.O_EXIT_GAME_CONFIRM)
            handled = task._handle_exit_game_confirm()
            task.close_unknown_pages(skip_first_screenshot=False, allow_ai=False)
        check(f'退出确认误报帧 {text!r} 判 False', visible is False and handled is False,
              f'visible={visible} handled={handled}')
        check(f'退出确认误报帧 {text!r} 不点取消按钮',
              all(not _in_roi(click[:2], CANCEL_ROI) for click in task.device.clicks),
              f'clicks={task.device.clicks}')


def _in_roi(point, roi) -> bool:
    x, y = point
    rx, ry, rw, rh = roi
    return rx - 25 <= x <= rx + rw + 25 and ry - 25 <= y <= ry + rh + 25


def case_exit_confirm_real_dialog() -> None:
    """真实「退出游戏」确认框：判 True，只点取消，点完复判通过。"""

    frames = [
        Frame('exit_confirm', main=False, texts=('是否退出游戏？',), image=image_with_cancel_panel(),
              advance_on_click=True),
        Frame('page_main', main=True, texts=()),
    ]
    screen = FakeScreen(frames)
    task = new_task(screen)

    with patched_ocr(screen):
        handled = task.close_unknown_pages(skip_first_screenshot=False, allow_ai=False)

    check('退出确认框：close_unknown_pages 返回 True', handled is True, f'handled={handled}')
    check('退出确认框：只点了一次取消', len(task.device.clicks) == 1, f'clicks={task.device.clicks}')
    x, y, control_name = task.device.clicks[0]
    check('退出确认框：点击目标是取消按钮', control_name == 'EXIT_GAME_CANCEL' and _in_roi((x, y), CANCEL_ROI),
          f'click=({x}, {y}, {control_name}) 取消 ROI={CANCEL_ROI}')
    check('退出确认框：没有点到「确认」一侧（x 必须落在取消按钮里）',
          all(_in_roi(click[:2], CANCEL_ROI) for click in task.device.clicks),
          f'clicks={task.device.clicks}')
    check("退出确认框：记录为 _record_unknown_close_event('exit_game_cancel')",
          'exit_game_cancel' in task.navigator.unknown_close_history,
          f'history={task.navigator.unknown_close_history}')


def case_exit_confirm_click_cap() -> None:
    """确认框点不掉：最多点 2 次取消，然后放手交给上层。"""

    screen = FakeScreen([Frame('exit_confirm_stuck', main=False, texts=('是否退出游戏？',),
                               image=image_with_cancel_panel())])
    task = new_task(screen)

    with patched_ocr(screen):
        handled = task._handle_exit_game_confirm()

    check('退出确认框点不掉：返回 False', handled is False, f'handled={handled}')
    check('退出确认框点不掉：点击次数有上限（2 次）',
          len(task.device.clicks) == GameUi.EXIT_GAME_CONFIRM_MAX_CLICKS, f'clicks={task.device.clicks}')
    check('退出确认框点不掉：每一次都只点取消',
          all(_in_roi(click[:2], CANCEL_ROI) for click in task.device.clicks),
          f'clicks={task.device.clicks}')


def case_recovery_rounds() -> None:
    """复判失败再给 1~2 轮回退尝试；仍失败才交回上层。"""

    # 1) 主界面可见 → 不做任何点击直接收敛
    screen = FakeScreen([Frame('page_main', main=True)])
    task = new_task(screen)
    with patched_ocr(screen):
        recovered = task._recover_to_usable_page()
    check('回退轮：主界面可见时不按键直接返回 True',
          recovered is True and task.device.back_keys == 0 and task.device.clicks == [],
          f'recovered={recovered} back={task.device.back_keys}')

    # 2) 未知页 + 返回键有效 → 一轮返回键后复判到主界面
    frames = [
        Frame('unknown', main=False, advance_on_back=True),
        Frame('page_main', main=True),
    ]
    screen = FakeScreen(frames)
    task = new_task(screen)
    with patched_ocr(screen):
        recovered = task._recover_to_usable_page()
    check('回退轮：按一次 BACK 后退回可用页面', recovered is True and task.device.back_keys == 1,
          f'recovered={recovered} back={task.device.back_keys}')
    check("回退轮：记录为 _record_unknown_close_event('recover_back')",
          task.navigator.unknown_close_history[-1] == 'recover_back',
          f'history={task.navigator.unknown_close_history}')

    # 3) 返回键也救不回来 → 上限 2 轮后返回 False（上层才判未知页）
    screen = FakeScreen([Frame('unknown', main=False)])
    task = new_task(screen)
    with patched_ocr(screen):
        recovered = task._recover_to_usable_page()
    check('回退轮：救不回来时轮次有上限（2 轮）并返回 False',
          recovered is False and task.device.back_keys == GameUi.PAGE_RECOVERY_ROUNDS,
          f'recovered={recovered} back={task.device.back_keys}')


def case_back_key_repeat_no_many_click_error() -> None:
    """连续多轮未知页关闭按返回键：不触发 GameTooManyClickError，且按键有总量预算。"""

    screen = FakeScreen([Frame('unknown', main=False)])
    task = new_task(screen)
    raised = None
    results = []
    with patched_ocr(screen):
        try:
            for _ in range(12):
                results.append(task.close_unknown_pages(skip_first_screenshot=False, allow_ai=False))
        except GameTooManyClickError as error:  # noqa: BLE001
            raised = error
    check('返回键兜底连跑 12 轮不触发 GameTooManyClickError', raised is None, f'raised={raised}')
    check('返回键兜底连跑 12 轮按键次数有上限',
          task.device.back_keys == GameUi.MAX_UNKNOWN_PAGE_BACK_PRESSES,
          f'back_keys={task.device.back_keys} budget={GameUi.MAX_UNKNOWN_PAGE_BACK_PRESSES}')
    check('返回键预算用完后不再声称「有进展」（让上层超时能生效）',
          results[-1] is False, f'last_result={results[-1]} results={results}')


def case_fullscreen_overlay_does_not_touch_main_page() -> None:
    """浮层关掉后落到主界面：关闭动作只有一次，不再继续点。"""

    frames = [
        Frame('bounty_overlay', main=False, texts=('悬赏封印',), image=real_screenshot(),
              advance_on_click=True),
        Frame('page_main', main=True, image=image_with_red_cross()),
    ]
    screen = FakeScreen(frames)
    task = new_task(screen)
    with patched_ocr(screen):
        task._close_fullscreen_overlay()
        # 再跑一次未知页关闭：此时已在主界面，必须一次点击都不产生
        before = len(task.device.clicks)
        task.close_unknown_pages(skip_first_screenshot=False, allow_ai=False)
    check('浮层关闭后落到主界面：后续未知页关闭不再点击',
          len(task.device.clicks) == before and task.device.back_keys == 0,
          f'clicks={task.device.clicks} back={task.device.back_keys}')


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
CASES = [
    case_real_snapshot_before_and_after,
    case_overlay_ocr_close_and_recheck,
    case_overlay_geometry_close,
    case_overlay_attempt_cap_and_no_many_click_error,
    case_main_page_is_protected,
    case_town_page_is_protected,
    case_exit_confirm_false_positive_frames,
    case_exit_confirm_real_dialog,
    case_exit_confirm_click_cap,
    case_recovery_rounds,
    case_back_key_repeat_no_many_click_error,
    case_fullscreen_overlay_does_not_touch_main_page,
]


def main() -> int:
    print(f'样本截图：{SAMPLE_SCREENSHOT}')
    print(f'浮层判定：keyword={[r.keyword for r in GameUi.OVERLAY_TITLE_RULES]} '
          f'geometry_roi={GameUi.OVERLAY_CLOSE_CROSS_ROI}')
    original_sleep = time_module.sleep
    # 关闭动作之间的等待在离线用例里没有意义，直接跳过
    time_module.sleep = lambda *_args, **_kwargs: None
    try:
        for case in CASES:
            print(f'\n===== {case.__name__} =====')
            case()
    finally:
        time_module.sleep = original_sleep

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f'\n===== 结果：{passed}/{total} 项断言通过 =====')
    for name, ok, detail in _RESULTS:
        if not ok:
            print(f'FAILED: {name} :: {detail}')
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
