"""Locate arbitrary one-based preset indices in a scrolling game list.

Coordinates describe the visible viewport, never an off-screen target. Actual
content displacement is measured after each drag, including clamped last pages.

2026-11-20 A 类回归修复（882fd7c2 引入按像素位移定位之后爆发）：
- 位移测量改为多带投票 + 新鲜帧重试 + 静止/回弹判定，单次模糊匹配不再抛错；
- 每次测量都把各带的匹配分数与最终选择写进日志，便于事后核对；
- 无法确认滚动位置时抛 PresetUnavailable（ScriptError 子类），由调用方
  降级跳过该预设，而不是让整条任务失败退出进程。
"""
from dataclasses import dataclass
import time

import cv2
import numpy as np

from module.exception import ScriptError
from module.logger import logger


class PresetUnavailable(ScriptError):
    """预设无法在当前画面完成定位：调用方应记 WARNING 并跳过该预设。"""


@dataclass(frozen=True)
class PresetListLayout:
    name: str
    roi: tuple
    first_y: float
    stride: float
    click_x: int


SOUL_GROUPS = PresetListLayout('御魂分组', (1090, 80, 150, 550), 109, 70, 1165)
SOUL_TEAMS = PresetListLayout('御魂队伍', (575, 132, 435, 493), 166, 150, 990)
BATTLE_GROUPS = PresetListLayout('战斗预设分组', (25, 235, 135, 440), 265, 62.5, 60)
BATTLE_TEAMS = PresetListLayout('战斗预设队伍', (195, 230, 465, 400), 290, 120, 425)

# 追踪带的取法：带上边缘占列表高度的比例
BAND_FRACTIONS = (.06, .28, .50, .72)
# 带宽必须小于最窄行距（战斗预设分组 62.5px），否则模板会跨行产生周期性歧义
BAND_HEIGHT = 56
# 灰度标准差低于此值的带没有可追踪内容
FLAT_STD = 3.
# 单带得分足够高可以直接信任
SCORE_TRUST = .82
# 多带一致时的最低平均分
SCORE_CONSENSUS = .58
# 多带一致的位移容差（像素）
OFFSET_TOLERANCE = 4
# 只有自身得分达到此值的带才参与投票
SCORE_VOTE = .70
# 整行差异的 90 分位低于此值视为“列表没动”（粒子/光效只影响少数行）
QUIET_ROW_DIFF = 6.


@dataclass(frozen=True)
class Measurement:
    """一次位移测量的结果。offset 为 None 表示这次没有量出可信位移。"""
    offset: object
    direction: int
    score: float
    bands: tuple = ()
    note: str = ''

    @property
    def verified(self):
        return self.offset is not None

    def __str__(self):
        detail = ','.join(f'{start}+{offset}/{score:.2f}' for start, offset, score in self.bands)
        return (f'方向{self.direction} 位移{self.offset if self.offset is not None else "未确认"} '
                f'最高分{self.score:.2f} 各带[{detail}] {self.note}').strip()


def preset_index(value):
    """No upper bound; zero and negative numbers are not list indices."""
    if isinstance(value, bool):
        raise ValueError('预设序号必须为从1开始的整数')
    number = int(value)
    if number < 1 or str(value).strip() != str(number):
        raise ValueError('预设序号必须为从1开始的整数')
    return number


def foreground_rule(task, rule):
    if not task.appear(rule):
        return False
    target = getattr(rule, 'appear_target', rule)
    x, y, width, height = map(int, rule.roi_front)
    image = task.device.image[y:y + height, x:x + width]
    template = target.image
    return (image.shape == template.shape and bool(image.size)
            and float(image.mean()) >= float(template.mean()) * .78)


def _same(before, after):
    difference = cv2.absdiff(before, after)
    return float(difference.mean()) <= .8 and float(np.percentile(difference, 99)) <= 8.


def _quiet(before, after):
    """列表内容是否静止。

    垂直滚动会让几乎每一行都变化；回弹、粒子、光效只影响少数行，
    所以用“整行平均差的 90 分位”区分“没动”和“动了”。
    """
    if before.shape != after.shape or not before.size:
        return False
    if _same(before, after):
        return True
    rows = cv2.absdiff(before, after).mean(axis=1)
    return float(np.percentile(rows, 90)) <= QUIET_ROW_DIFF


def _signal(image):
    return cv2.Canny(image, 35, 100)


def _band_match(before, after, start):
    """在一条带上量位移：返回 (offset, score)，量不出返回 None。"""
    band = max(24, min(BAND_HEIGHT, len(before) - start))
    template = after[start:start + band]
    if template.size == 0 or float(template.std()) < FLAT_STD:
        return None
    scores = np.nan_to_num(cv2.matchTemplate(before, template, cv2.TM_CCOEFF_NORMED)[:, 0],
                           nan=-1.)
    best = int(np.argmax(scores))
    score = float(scores[best])
    masked = scores.copy()
    masked[max(0, best - 3):best + 4] = -1.
    other = float(masked.max()) if masked.size else -1.
    return best - start, score, other


def _vote(bands):
    """多带投票：只有各自得分够高的带才参与投票。

    低分带（半截内容、间隙）即使两两一致也不能压过单条满分带，
    否则真实的大位移会被噪声投票投掉。
    """
    trusted = [band for band in bands if band[2] >= SCORE_VOTE]
    groups = []
    for start, offset, score in sorted(trusted, key=lambda band: -band[2]):
        for group in groups:
            if abs(group['offset'] - offset) <= OFFSET_TOLERANCE:
                group['members'].append((start, offset, score))
                break
        else:
            groups.append({'offset': offset, 'members': [(start, offset, score)]})
    if not groups:
        return None
    return max(groups, key=lambda group: (len(group['members']), group['members'][0][2]))


def _measure(before, after, direction=1, stride=None):
    """量帧间位移。返回 Measurement，永远不因为“量不准”抛错。

    direction 1 表示列表向后面的项滚动（内容上移为正位移），-1 反之。
    """
    if getattr(before, 'shape', ()) != getattr(after, 'shape', ()) or not before.size:
        raise PresetUnavailable('预设列表截图尺寸不一致，无法测量滚动')
    if _quiet(before, after):
        return Measurement(0, direction, 1., (), '列表静止/回弹')
    if direction < 0:
        flipped = _measure(after, before, 1, stride)
        return Measurement(None if flipped.offset is None else -flipped.offset, -1,
                           flipped.score, flipped.bands, flipped.note)
    height = len(before)
    bands = []
    for fraction in BAND_FRACTIONS:
        start = int(height * fraction)
        if start >= height - 24:
            continue
        found = _band_match(before, after, start)
        if found is not None:
            bands.append((start, found[0], found[1]))
    if not bands:
        return Measurement(None, 1, 0., (), '没有可追踪内容')
    group = _vote(bands)
    if group is None:
        return Measurement(None, 1, max(band[2] for band in bands), tuple(bands), '匹配不明确')
    members = group['members']
    mean_score = sum(member[2] for member in members) / len(members)
    top = members[0]
    if len(members) >= 2 and mean_score >= SCORE_CONSENSUS:
        return Measurement(top[1], 1, top[2], tuple(bands), f'{len(members)}带一致/{mean_score:.2f}')
    if top[2] >= SCORE_TRUST:
        return Measurement(top[1], 1, top[2], tuple(bands), '单带高置信')
    return Measurement(None, 1, top[2], tuple(bands), '匹配不明确')


def _displacement(before, after, direction):
    """Positive pixels toward later items, negative pixels toward the top.

    量不出可信位移时返回 0（按“无进展”处理），绝不因为一次模糊匹配抛错。
    """
    offset = _measure(before, after, direction).offset
    return 0 if offset is None else offset


class IndexedPresetList:
    def __init__(self, task, panel_visible, row_present=None, pause=time.sleep,
                 retries=2, top_limit=60, drag_limit=80, reverse_tolerance=8):
        self.task = task
        self.panel_visible = panel_visible
        self.row_present = row_present or self._row_has_text
        self.pause = pause
        self.retries = retries
        self.top_limit = top_limit
        self.drag_limit = drag_limit
        self.reverse_tolerance = reverse_tolerance
        self.measurements = []

    def _frame(self, layout):
        self.task.screenshot()
        if not self.panel_visible():
            raise PresetUnavailable('预设面板已关闭或被弹窗遮挡，停止选择')
        x, y, width, height = map(int, layout.roi)
        image = self.task.device.image
        if x < 0 or y < 0 or x + width > image.shape[1] or y + height > image.shape[0]:
            raise PresetUnavailable('预设列表区域超出实际截图')
        return cv2.cvtColor(image[y:y + height, x:x + width], cv2.COLOR_RGB2GRAY)

    def _drag(self, layout, direction, factor=.30):
        x, y, width, height = map(int, layout.roi)
        distance = max(24, int(height * factor))
        middle = y + height // 2
        start = (x + width // 2, middle + direction * distance // 2)
        end = (start[0], middle - direction * distance // 2)
        device = self.task.device
        name = f'PRESET_SCROLL_{layout.name}'
        configured = self.task.config.script.device.control_method
        method = str(getattr(configured, 'value', configured))
        drag = getattr(device, 'drag_' + method, None)
        if callable(drag):
            device.handle_control_check(name)
            drag(start, end, point_random=(0, 0, 0, 0))
        else:
            device.swipe(start, end, duration=(.7, .8), control_name=name)
        self.pause(.5)

    def _progress(self):
        # Reset the existing watchdog only after verified visual progress.
        clear = getattr(self.task.device, 'stuck_record_clear', None)
        if callable(clear):
            clear()
        clear_clicks = getattr(self.task.device, 'click_record_clear', None)
        if callable(clear_clicks):
            clear_clicks()

    def _measure_with_retry(self, layout, before, after, direction):
        """量位移；不明确时用新鲜帧重试，并把每次分数写进日志。"""
        attempts = []
        measurement = _measure(before, after, direction)
        attempts.append(measurement)
        for _ in range(max(0, int(self.retries))):
            if measurement.verified:
                break
            self.pause(.35)
            fresh = self._frame(layout)
            after = fresh
            measurement = _measure(before, fresh, direction)
            attempts.append(measurement)
        logger.info(f'[{layout.name}位移] ' + ' -> '.join(str(item) for item in attempts))
        self.measurements.append((layout.name, direction, [str(item) for item in attempts]))
        return after, measurement

    def _top(self, layout):
        """滚到列表顶部。静止/回弹按无进展处理，只有明确反向位移才判失败。"""
        before = self._frame(layout)
        unchanged = 0
        reverse = 0
        drags = 0
        while unchanged < 2 and drags < self.top_limit:
            drags += 1
            self._drag(layout, -1)
            after = self._frame(layout)
            after, movement = self._measure_with_retry(layout, before, after, -1)
            before = after
            if movement.offset is None:
                unchanged += 1
                logger.warning(f'{layout.name}滚动位置未确认，按无进展处理（{movement}）')
            elif movement.offset <= -1:
                unchanged = 0
                reverse = 0
                self._progress()
            elif movement.offset <= self.reverse_tolerance:
                unchanged += 1
                if movement.offset:
                    logger.info(f'{layout.name}回弹{movement.offset}px，按无进展处理')
            else:
                unchanged += 1
                reverse += 1
                logger.warning(f'{layout.name}向反方向移动{movement.offset}px（{reverse}/2）')
                if reverse >= 2:
                    raise PresetUnavailable('预设列表未向顶部移动，停止选择')
        if unchanged < 2:
            raise PresetUnavailable(f'{layout.name}滚动{drags}次仍未确认顶部，停止选择')
        return before

    def locate(self, index, layout):
        index = preset_index(index)
        before = self._top(layout)
        offset = 0
        top = layout.roi[1]
        bottom = top + layout.roi[3]
        wanted = layout.first_y + (index - 1) * layout.stride
        stalled = 0
        drags = 0
        while drags < self.drag_limit:
            visible_y = wanted - offset
            if top + 12 <= visible_y <= bottom - 12:
                point = (layout.click_x, int(round(visible_y)))
                if not self.row_present(layout, point[1]):
                    raise PresetUnavailable(f'{layout.name}第{index}项不存在或无法识别')
                logger.info(f'{layout.name}第{index}项已定位，滚动偏移{offset}px')
                return point
            if visible_y < top + 12:
                raise PresetUnavailable('预设列表滚动超过目标，停止选择')
            drags += 1
            self._drag(layout, 1)
            after = self._frame(layout)
            after, movement = self._measure_with_retry(layout, before, after, 1)
            before = after
            if movement.offset is not None and movement.offset > 1:
                stalled = 0
                offset += movement.offset
                self._progress()
            else:
                stalled += 1
                logger.warning(f'{layout.name}滚动未取得进展（{movement}）')
                if stalled >= 2:
                    raise PresetUnavailable(f'{layout.name}已到底，未找到第{index}项')
        raise PresetUnavailable(f'{layout.name}滚动次数超过上限，未找到第{index}项')

    def select(self, index, layout):
        x, y = self.locate(index, layout)
        self.task.device.click(x, y, control_name=f'PRESET_INDEX_{layout.name}_{index}')
        self.pause(.5)
        self._frame(layout)
        return x, y

    def _row_has_text(self, layout, center_y):
        from module.atom.ocr import RuleOcr
        x, top, width, height = map(int, layout.roi)
        half = int(min(50, layout.stride * .40))
        y = max(top, center_y - half)
        end = min(top + height, center_y + half)
        rule = RuleOcr(roi=(x, y, width, end - y), area=(x, y, width, end - y),
                       mode='Full', method='Default', keyword='', name='preset_index_row')
        results = rule.detect_and_ocr(self.task.device.image, logDisplay=False)
        return any(str(result.ocr_text).strip() for result in results)
