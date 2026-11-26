"""A 类（预设列表定位 / 预设选择链路）离线回归。

不启动 OAS、不连设备、不 adb：全部用 log/error 下 2026-11-20 的 4 张真实失败截图
（1795179676393 / 1795179130467 / 1795179852457 / 1795178774126）与本仓库的
假帧 + 假设备驱动，覆盖：

1. 真实快照里 882fd7c2 位移算法两种误判（回弹帧判成“未向顶部移动”、
   结构变化帧判成“滚动位置不明确”）——同一帧上新算法必须安全通过；
2. 已到顶部时的回弹/噪声帧按“无进展”处理，只有明确反向位移才失败；
3. 模板匹配不明确时有多带投票 + 新鲜帧重试 + 保守回退，并把分数写进日志；
4. 预设面板未打开 / 一键预设御魂按钮缺失 / 目标项不存在 时降级跳过并 WARNING，
   set_next_run(success=False) + TaskEnd（不再抛出让进程 exit 的异常）。

运行：.\\toolkit\\python.exe dev_tools\\fix_presets_test.py
报告：log/fix-presets-check.json
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import os
import re
import sys
import time

ROOT = Path(os.environ.get("OAS_PRESET_ROOT", str(Path(__file__).resolve().parents[1])))
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

import cv2
import numpy as np

from module.exception import ScriptError, TaskEnd
from tasks.Component import preset_list as lists
from tasks.Component import preset_team as teams
from tasks.Component.preset_list import (
    IndexedPresetList, PresetUnavailable, SOUL_GROUPS, SOUL_TEAMS, BATTLE_GROUPS, BATTLE_TEAMS,
)

SNAPSHOTS = {
    "orochi": ROOT / "log/error/1795179676393/2026-11-20_21-01-16-338614.png",
    "hunt": ROOT / "log/error/1795179130467/2026-11-20_20-52-10-414613.png",
    "evozone": ROOT / "log/error/1795179852457/2026-11-20_21-04-12-379636.png",
    "realm": ROOT / "log/error/1795178774126/2026-11-20_20-46-12-770042.png",
}

rows = []


def check(name, fn):
    try:
        rows.append({"case": name, "passed": True, "details": fn()})
    except Exception as error:
        rows.append({"case": name, "passed": False,
                     "error": f'{type(error).__name__}: {error}'})


def read_frame(path):
    if not Path(path).is_file():
        raise AssertionError(f'缺少真实快照：{path}')
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise AssertionError(f'无法解码快照：{path}')
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def crop(image, layout):
    x, y, width, height = map(int, layout.roi)
    return cv2.cvtColor(image[y:y + height, x:x + width], cv2.COLOR_RGB2GRAY)


# --------------------------------------------------------------------------- #
# 真实快照上的“无位移但有变化”帧：882fd7c2 算法在这类帧上误判
# --------------------------------------------------------------------------- #
def bounce_frame(frame, pixels=3, ratio=.12):
    """列表钳制在顶部时的橡皮筋回弹：顶部条带内容整体抖动。"""
    out = frame.copy()
    band = int(len(frame) * ratio)
    out[:band] = np.roll(out[:band], pixels, axis=0)
    return out


def fade_frame(frame, gain=26, ratio=.07):
    """选中高亮/光效造成的局部亮度变化，没有垂直位移。"""
    out = frame.astype(np.int16)
    out[: int(len(frame) * ratio)] += gain
    return np.clip(out, 0, 255).astype(np.uint8)


def retexture_frame(frame, top=.10, bottom=.55, amp=70, seed=5):
    """行内容重绘（换组后队伍列表刷新、头像动画），没有整体垂直位移。"""
    rng = np.random.default_rng(seed)
    out = frame.astype(np.int16)
    a, b = int(len(frame) * top), int(len(frame) * bottom)
    out[a:b] = np.clip(out[a:b] + rng.integers(-amp, amp + 1, out[a:b].shape), 0, 255)
    return out.astype(np.uint8)


def shift_frame(frame, pixels):
    """把列表内容整体滚动 pixels 像素（正数=向后面的项滚动，内容上移）。"""
    if pixels >= 0:
        return np.concatenate((frame[pixels:], np.full_like(frame[:pixels], int(np.median(frame)))), axis=0)
    return np.concatenate((np.full_like(frame[:-pixels], int(np.median(frame))), frame[:pixels]), axis=0)


def rolled_frame(frame, pixels):
    """同上但首尾循环，避免补边造成大片纯色（用于构造连续两帧都富纹理的反向序列）。"""
    return np.roll(frame, -pixels, axis=0)


# --------------------------------------------------------------------------- #
# 882fd7c2 的旧算法（逐字保留），用来证明“修复前会失败”
# --------------------------------------------------------------------------- #
def legacy_same(before, after):
    difference = cv2.absdiff(before, after)
    return float(difference.mean()) <= .8 and float(np.percentile(difference, 99)) <= 8.


def legacy_displacement(before, after, direction):
    if legacy_same(before, after):
        return 0
    if direction < 0:
        return -legacy_displacement(after, before, 1)
    source = cv2.Canny(before, 35, 100)
    current = cv2.Canny(after, 35, 100)
    height = len(source)
    margin = height // 8
    band_height = height // 3
    template = current[margin:margin + band_height]
    if np.count_nonzero(template) < template.size * .005:
        raise ScriptError('预设列表缺少可追踪内容，无法确认滚动位置')
    scores = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)[:, 0]
    maximum = min(len(scores), margin + int(height * .60) + 1)
    candidates = scores[margin:maximum].copy()
    best = int(np.argmax(candidates))
    score = float(candidates[best])
    candidates[max(0, best - 5):best + 6] = -1
    other = float(candidates.max()) if len(candidates) else -1
    if score < .72 or score - other < .025:
        raise ScriptError(f'预设列表滚动位置不明确（匹配{score:.2f}），停止选择')
    return best


def legacy_top_verdict(before, after):
    """旧 _top() 的单次判定：返回 'top' 或抛错（复现线上 CRITICAL）。"""
    if legacy_same(before, after):
        return "no-progress"
    movement = legacy_displacement(before, after, -1)
    if movement >= 0:
        raise ScriptError('预设列表未向顶部移动，停止选择')
    return "progress"


# --------------------------------------------------------------------------- #
# 日志录制
# --------------------------------------------------------------------------- #
class Recorder:
    def __init__(self):
        self.records = []

    def _log(self, level, message):
        self.records.append((level, str(message)))

    def info(self, message, *args, **kwargs):
        self._log("INFO", message)

    def warning(self, message, *args, **kwargs):
        self._log("WARNING", message)

    def error(self, message, *args, **kwargs):
        self._log("ERROR", message)

    def debug(self, message, *args, **kwargs):
        self._log("DEBUG", message)

    def critical(self, message, *args, **kwargs):
        self._log("CRITICAL", message)

    def messages(self, level=None):
        return [msg for lv, msg in self.records if level is None or lv == level]


# --------------------------------------------------------------------------- #
# 假设备：按 offset 渲染列表，可在“列表已到顶部”时注入回弹/噪声帧
# --------------------------------------------------------------------------- #
class FakeList:
    def __init__(self, layout, count=16, start=0, factor=1., animate_top=True):
        self.layout = layout
        self.count = count
        self.factor = factor
        self.animate = animate_top
        self.animate_pending = False
        self.frames = 0
        self.actions = []
        self.config = SimpleNamespace(script=SimpleNamespace(
            device=SimpleNamespace(control_method='test')))
        x, y, width, height = map(int, layout.roi)
        length = max(height, int(layout.first_y - y + (count - 1) * layout.stride + layout.stride * .55))
        self.content = np.full((length, width), 225, dtype=np.uint8)
        rng = np.random.default_rng(731)
        for index in range(1, count + 1):
            center = int(round(layout.first_y - y + (index - 1) * layout.stride))
            top = max(0, center - 18)
            bottom = min(length, center + 18)
            pattern = rng.integers(0, 256, (bottom - top, width - 12), dtype=np.uint8)
            self.content[top:bottom, 6:-6] = pattern
        self.bottom = max(0, length - height)
        self.offset = min(start, self.bottom)
        self.device = SimpleNamespace(image=None, swipe=self.swipe,
                                      stuck_record_clear=lambda: None,
                                      click_record_clear=lambda: None,
                                      click=self.click)
        self.screenshot()

    def screenshot(self):
        x, y, width, height = map(int, self.layout.roi)
        frame = np.full((720, 1280, 3), 180, dtype=np.uint8)
        part = self.content[self.offset:self.offset + height]
        frame[y:y + height, x:x + width] = part[..., None]
        if self.animate_pending and self.animate:
            # 回弹/光效：只在 roi 内变化，且没有整体垂直位移
            roi = frame[y:y + height, x:x + width]
            roi[: int(height * .12)] = np.roll(roi[: int(height * .12)], 3, axis=0)
            roi[int(height * .12): int(height * .55)] = retexture_frame(
                roi[int(height * .12): int(height * .55)], top=0, bottom=1, seed=17)
            self.animate_pending = False
        self.device.image = frame
        self.frames += 1

    def swipe(self, start, end, **kwargs):
        self.actions.append(("scroll", start, end))
        delta = int(round((start[1] - end[1]) * self.factor))
        new_offset = max(0, min(self.bottom, self.offset + delta))
        if new_offset == self.offset:
            self.animate_pending = True  # 钳制在边界时的回弹
        self.offset = new_offset

    def click(self, x, y, **kwargs):
        self.actions.append(("click", x, y))

    def row_present(self, layout, y):
        center = y + self.offset
        return any(abs(center - (layout.first_y + (i - 1) * layout.stride)) <= 4
                   for i in range(1, self.count + 1))

    def navigator(self):
        return IndexedPresetList(self, lambda: True, self.row_present, pause=lambda _: None)


# --------------------------------------------------------------------------- #
# 假任务：用真实截图驱动 preset_team 的判定
# --------------------------------------------------------------------------- #
class FrameTask:
    def __init__(self, frame, task_name="Orochi"):
        from tasks.Component.GeneralBattle.general_battle import GeneralBattle
        self.frame = frame
        self.task_name = task_name
        self.calls = []
        self.deferred = []
        self.forced = {}
        self._real = object.__new__(GeneralBattle)
        self._real.device = SimpleNamespace(image=frame)
        self.device = SimpleNamespace(image=frame, click=self.device_click, swipe=self.swipe,
                                      stuck_record_clear=lambda: None,
                                      click_record_clear=lambda: None)
        self.I_PRESET_ENSURE = self._real.I_PRESET_ENSURE
        self.I_PRESENT_LESS_THAN_5 = self._real.I_PRESENT_LESS_THAN_5
        self.I_PRESET = self._real.I_PRESET
        self.I_PRESET_WIT_NUMBER = self._real.I_PRESET_WIT_NUMBER
        self.O_PRESET_FULL = self._real.O_PRESET_FULL
        self.O_PRESET = self._real.O_PRESET
        self.I_UI_CONFIRM = self._real.I_UI_CONFIRM
        self.I_SOU_TEAM_PRESENT = getattr(self._real, 'I_SOU_TEAM_PRESENT', None)

    def screenshot(self):
        pass

    def get_task_name(self):
        return self.task_name

    def set_next_run(self, task=None, **kwargs):
        self.deferred.append((task, kwargs))

    def appear(self, rule, **kwargs):
        if rule.name in self.forced:
            return self.forced[rule.name]
        return bool(self._real.appear(rule))

    def appear_then_click(self, rule, **kwargs):
        self.calls.append(("appear_then_click", rule.name))
        if rule.name in self.forced and not self.forced[rule.name]:
            return False
        return bool(self.click(rule))

    def click(self, rule=None, **kwargs):
        name = getattr(rule, "name", str(rule))
        self.calls.append(("click", name))
        if name == self.I_PRESET_ENSURE.name:
            self.forced[self.I_PRESET_ENSURE.name] = False
            self.forced[self.I_PRESENT_LESS_THAN_5.name] = False
        return True

    def device_click(self, x, y, **kwargs):
        self.calls.append(("device_click", (x, y)))
        return True

    def ocr_appear(self, rule, **kwargs):
        return bool(self.forced.get(rule.name, False))

    def swipe(self, start, end, **kwargs):
        self.actions = getattr(self, "actions", [])
        self.actions.append(("scroll", start, end))


FRAMES = {}


def frame(name):
    if name not in FRAMES:
        FRAMES[name] = read_frame(SNAPSHOTS[name])
    return FRAMES[name]


# --------------------------------------------------------------------------- #
# 1. 四张真实快照：旧算法失败、新算法通过
# --------------------------------------------------------------------------- #
def legacy_top_raises(before, after, text):
    try:
        legacy_top_verdict(before, after)
    except ScriptError as error:
        assert text in str(error), error
        return str(error)
    raise AssertionError(f'旧算法没有复现失败（期望包含 {text}）')


def real_bounce_case(name, layout):
    """orochi / hunt 快照里的“已到顶部 + 回弹”帧。"""
    image = frame(name)
    before = crop(image, layout)
    after = bounce_frame(before)
    legacy_error = legacy_top_raises(before, after, '预设列表未向顶部移动')
    measurement = lists._measure(before, after, -1)
    assert measurement.offset == 0, measurement
    task = FakeList(layout, count=12, start=0)
    navigator = task.navigator()
    navigator.top_limit = 4
    navigator._drag = lambda layout, direction, factor=.30: None
    frames = {"n": 0}

    def fake_frame(layout):
        frames["n"] += 1
        return before if frames["n"] <= 1 else after

    navigator._frame = fake_frame
    navigator._top(layout)
    assert navigator.measurements, "位移测量没有记录日志"
    return {"legacy": legacy_error, "new_offset": measurement.offset,
            "measurements": len(navigator.measurements)}


for snapshot, layout_name, layout in (
        ("orochi", "SOUL_GROUPS", SOUL_GROUPS),
        ("orochi", "SOUL_TEAMS", SOUL_TEAMS),
        ("hunt", "BATTLE_TEAMS", BATTLE_TEAMS)):
    check(f'真实快照 {snapshot}/{layout_name}：回弹帧不再判成“未向顶部移动”',
          lambda snapshot=snapshot, layout=layout: real_bounce_case(snapshot, layout))


def real_retexture_case(name, layout):
    """hunt 快照里的“行内容刷新/头像动画”帧（线上匹配 0.50 的同类帧）。"""
    image = frame(name)
    before = crop(image, layout)
    after = retexture_frame(before)
    legacy_error = legacy_top_raises(before, after, '滚动位置不明确')
    measurement = lists._measure(before, after, -1)
    assert measurement.verified, measurement
    assert abs(measurement.offset) <= lists.OFFSET_TOLERANCE, measurement
    return {"legacy": legacy_error, "new": str(measurement)}


for snapshot, layout_name, layout in (
        ("hunt", "BATTLE_TEAMS", BATTLE_TEAMS),
        ("evozone", "BATTLE_TEAMS", BATTLE_TEAMS),
        ("orochi", "SOUL_GROUPS", SOUL_GROUPS)):
    check(f'真实快照 {snapshot}/{layout_name}：结构变化帧不再一次就抛“滚动位置不明确”',
          lambda snapshot=snapshot, layout=layout: real_retexture_case(snapshot, layout))


def real_scroll_coverage(name, layout):
    """真实画面上的真滚动必须量得准（不能为了“安全”把位移量成 0）。"""
    image = frame(name)
    before = crop(image, layout)
    details = []
    for pixels in (6, 20, 40, 120):
        down = lists._measure(before, shift_frame(before, pixels), 1)
        up = lists._measure(before, shift_frame(before, -pixels), 1)
        assert down.verified and abs(down.offset - pixels) <= 1, (pixels, down)
        assert up.verified and abs(up.offset + pixels) <= 1, (pixels, up)
        assert lists._displacement(before, shift_frame(before, pixels), 1) == down.offset
        details.append(int(down.offset))
    return {"layout": layout.name, "offsets": details}


for snapshot, layout in (("orochi", SOUL_GROUPS), ("orochi", SOUL_TEAMS),
                         ("hunt", BATTLE_GROUPS), ("hunt", BATTLE_TEAMS),
                         ("evozone", BATTLE_TEAMS)):
    check(f'真实快照 {snapshot}/{layout.name}：6/20/40/120px 真滚动都量得准',
          lambda snapshot=snapshot, layout=layout: real_scroll_coverage(snapshot, layout))


# --------------------------------------------------------------------------- #
# 2. _top()：回弹按无进展处理，只有明确反向位移才失败
# --------------------------------------------------------------------------- #
def top_from_bottom_with_bounce():
    layout = BATTLE_TEAMS
    task = FakeList(layout, count=30, start=10 ** 6, animate_top=True)
    navigator = task.navigator()
    navigator._top(layout)
    assert task.offset == 0, task.offset
    measured = [entry for entry in navigator.measurements]
    assert measured, "没有记录位移测量"
    scores = [text for entry in measured for text in entry[2]]
    assert any(re.search(r'\d\.\d\d', text) for text in scores), scores
    return {"drags": len(navigator.measurements), "offset": task.offset}


check('已到顶部时的回弹帧按无进展处理，长列表仍能一路滚到顶部', top_from_bottom_with_bounce)


def scripted_top(frames, layout):
    """按脚本喂帧的 _top():用真实画面构造“明确反向”/“轻微抖动”的单步场景。"""
    task = FakeList(layout, count=8, start=0, animate_top=False)
    navigator = task.navigator()
    navigator._drag = lambda layout, direction, factor=.30: None
    sequence = list(frames)

    def fake_frame(layout):
        return sequence.pop(0) if sequence else frames[-1]

    navigator._frame = fake_frame
    return navigator


def top_reverse_is_failure():
    """列表往“后面的项”滚（正位移），连续两次就是明确反向：必须判失败。"""
    layout = BATTLE_TEAMS
    base = crop(frame("evozone"), layout)
    navigator = scripted_top([base, rolled_frame(base, 120), rolled_frame(base, 240)], layout)
    try:
        navigator._top(layout)
    except PresetUnavailable as error:
        assert '未向顶部移动' in str(error), error
        return {"error": str(error)}
    raise AssertionError('明确反向位移没有被判失败')


check('只有明确反向位移才判“未向顶部移动”失败', top_reverse_is_failure)


def top_ignores_small_bounce_reverse():
    """反向位移小于容差（3px 抖动/回弹）时不能判失败，只能算无进展。"""
    layout = BATTLE_TEAMS
    base = crop(frame("evozone"), layout)
    navigator = scripted_top([base, shift_frame(base, 3), shift_frame(base, 6)], layout)
    navigator._top(layout)
    return {"measurements": len(navigator.measurements)}


check('小于容差的反向抖动不算失败', top_ignores_small_bounce_reverse)


def ambiguous_frame_keeps_dragging():
    """完全无法量位移时按无进展处理，两次后返回（不抛错、不误点）。"""
    before = np.full((400, 465), 200, dtype=np.uint8)
    after = np.full((400, 465), 200, dtype=np.uint8)
    after[50:300] = np.random.default_rng(3).integers(0, 256, (250, 465), dtype=np.uint8)
    measurement = lists._measure(before, after, -1)
    assert not measurement.verified, measurement
    assert lists._displacement(before, after, -1) == 0
    return {"note": measurement.note}


check('量不出位移时保守返回 0（不抛错）', ambiguous_frame_keeps_dragging)


# --------------------------------------------------------------------------- #
# 3. 真实快照上的面板判定
# --------------------------------------------------------------------------- #
def panel_detection():
    open_task = FrameTask(frame("evozone"))
    closed_task = FrameTask(frame("realm"))
    assert teams._panel(open_task) is not None
    assert teams._panel(closed_task) is None
    return {"evozone": "panel", "realm": "no-panel"}


check('战斗预设面板只认真正打开的帧（evozone=开，realm=未开）', panel_detection)


def soul_button_missing_degrades():
    """1795179852457：面板已开、队伍已选中，但没有一键预设御魂按钮。"""
    task = FrameTask(frame("evozone"))
    recorder = Recorder()
    with patch.object(teams, "logger", recorder):
        try:
            result = teams._find_soul_button(task.frame, BATTLE_TEAMS, BATTLE_TEAMS.first_y)
        except ScriptError as error:
            raise AssertionError(f'缺按钮仍然抛错：{error}')
    assert result is None, result
    assert any('一键预设御魂按钮' in msg for msg in recorder.messages("WARNING")), recorder.records
    return {"warnings": recorder.messages("WARNING")[:1]}


check('真实快照：一键预设御魂按钮缺失时返回 None 并 WARNING（不再抛错）', soul_button_missing_degrades)


def soul_apply_failure_degrades():
    task = FrameTask(frame("evozone"))
    recorder = Recorder()
    panel = teams.Panel(BATTLE_GROUPS, BATTLE_TEAMS, task.I_PRESET_ENSURE)
    with patch.object(teams, "logger", recorder), \
            patch.object(teams, "_find_soul_button", lambda *args: (600, 300)), \
            patch.object(teams, "_soul_messages", lambda task: "御魂不足"):
        applied = teams._apply_saved_souls(task, BATTLE_TEAMS, 300, lambda: True)
    assert applied is False, applied
    assert any('御魂不足' in msg for msg in recorder.messages("WARNING")), recorder.records
    assert task.deferred and task.deferred[0][1].get("success") is False, task.deferred
    return {"deferred": task.deferred}


check('一键预设失败（御魂不足）时 WARNING + set_next_run(success=False)', soul_apply_failure_degrades)


# --------------------------------------------------------------------------- #
# 4. select_battle_team：三条降级路径 + TaskEnd
# --------------------------------------------------------------------------- #
class FakeNavigator:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def select(self, index, layout):
        self.calls.append((layout.name, index))
        if self.error:
            raise self.error
        return layout.click_x, layout.first_y

    def locate(self, index, layout):
        self.calls.append((layout.name, index))
        if self.error:
            raise self.error
        return layout.click_x, layout.first_y


def panel_not_open_degrades():
    """1795178774126：预设入口能识别但面板一直打不开。"""
    task = FrameTask(frame("realm"), task_name="RealmRaid")
    task.forced[task.O_PRESET_FULL.name] = True
    recorder = Recorder()
    started = time.monotonic()
    with patch.object(teams, "logger", recorder), \
            patch.object(teams, "PANEL_OPEN_TIMEOUT", 1.5), \
            patch.object(teams, "PANEL_VERIFY_TIMEOUT", .1):
        result = teams.select_battle_team(task, True, 2, 3)
    elapsed = time.monotonic() - started
    assert result is False, result
    assert elapsed < 6, elapsed
    clicks = [call for call in task.calls if call[0] == "click"]
    assert len(clicks) <= teams.PANEL_OPEN_CLICKS, clicks
    assert task.deferred and task.deferred[0][1].get("success") is False, task.deferred
    assert any('战斗预设面板未打开' in msg for msg in recorder.messages("WARNING")), recorder.records
    return {"clicks": len(clicks), "elapsed": round(elapsed, 2)}


check('面板未打开：点击有上限、返回 False、WARNING 并推迟下次运行', panel_not_open_degrades)


def target_missing_degrades():
    task = FrameTask(frame("evozone"), task_name="EvoZone")
    recorder = Recorder()
    navigator = FakeNavigator(error=PresetUnavailable('战斗预设队伍第9项不存在或无法识别'))
    with patch.object(teams, "logger", recorder), \
            patch.object(teams, "IndexedPresetList", lambda *args, **kwargs: navigator):
        result = teams.select_battle_team(task, True, 1, 9)
    assert result is False, result
    assert task.deferred and task.deferred[0][1].get("success") is False, task.deferred
    assert any('无法定位' in msg for msg in recorder.messages("WARNING")), recorder.records
    return {"warnings": recorder.messages("WARNING")[:1]}


check('目标项不存在：降级返回 False + WARNING，不抛 ScriptError', target_missing_degrades)


def soul_button_missing_still_confirms():
    """1795179852457 的完整链路：跳过御魂装配，但预设队伍照常确认出战。"""
    task = FrameTask(frame("evozone"), task_name="EvoZone")
    recorder = Recorder()
    navigator = FakeNavigator()
    with patch.object(teams, "logger", recorder), \
            patch.object(teams, "IndexedPresetList", lambda *args, **kwargs: navigator):
        result = teams.select_battle_team(task, True, 1, 1)
    assert result is True, result
    assert ("click", task.I_PRESET_ENSURE.name) in task.calls, task.calls
    assert any('未找到一键预设御魂按钮' in msg for msg in recorder.messages("WARNING")), recorder.records
    assert any('继续用该队伍出战' in msg for msg in recorder.messages("WARNING")), recorder.records
    assert [call for call in navigator.calls] == [('战斗预设分组', 1), ('战斗预设队伍', 1)]
    return {"calls": task.calls, "navigator": navigator.calls}


check('真实快照：缺一键预设御魂按钮时跳过御魂装配并确认出战', soul_button_missing_still_confirms)


def less_than_five_ends_task():
    task = FrameTask(frame("evozone"), task_name="EvoZone")
    task.forced[task.I_PRESENT_LESS_THAN_5.name] = True
    recorder = Recorder()
    navigator = FakeNavigator()
    with patch.object(teams, "logger", recorder), \
            patch.object(teams, "IndexedPresetList", lambda *args, **kwargs: navigator):
        try:
            teams.select_battle_team(task, True, 1, 2)
        except TaskEnd:
            assert task.deferred and task.deferred[0][1].get("success") is False, task.deferred
            assert any('不足5位式神' in msg for msg in recorder.messages("WARNING")), recorder.records
            return {"deferred": task.deferred}
        except ScriptError as error:
            raise AssertionError(f'仍然抛 ScriptError：{error}')
    raise AssertionError('不足5位式神没有结束任务')


check('式神不足5位：TaskEnd + set_next_run(success=False)，不是 ScriptError', less_than_five_ends_task)


def disabled_preset_is_noop():
    task = FrameTask(frame("realm"))
    with patch.object(teams, "logger", Recorder()):
        assert teams.select_battle_team(task, False, 1, 1) is True
    assert task.calls == [] and task.deferred == [], (task.calls, task.deferred)
    return {"calls": task.calls}


check('未开启预设时不产生任何输入', disabled_preset_is_noop)


def battle_caller_survives_skip():
    """GeneralBattle.switch_preset_team 在降级时只记 WARNING，不抛异常。"""
    from tasks.Component.GeneralBattle.general_battle import GeneralBattle
    task = object.__new__(GeneralBattle)
    task.config = SimpleNamespace(task_delay=lambda *a, **kw: None)
    task.start_time = None
    recorder = Recorder()
    with patch.object(teams, "logger", recorder), \
            patch.object(teams, "select_battle_team", lambda *args, **kwargs: False), \
            patch("tasks.Component.GeneralBattle.general_battle.logger", recorder):
        assert task.switch_preset_team(True, 3, 4) is False
    assert any('未生效' in msg for msg in recorder.messages("WARNING")), recorder.records
    return {"warnings": recorder.messages("WARNING")[:1]}


check('战斗组件：预设降级只记 WARNING，战斗继续', battle_caller_survives_skip)


def record_preset_degrades():
    """式神录链路：定位失败降级跳过，不设置 _record_team_souls。"""
    task = FrameTask(frame("orochi"))
    task.click_preset = lambda: None
    recorder = Recorder()
    navigator = FakeNavigator(error=PresetUnavailable('御魂队伍第2项不存在或无法识别'))
    with patch.object(teams, "logger", recorder), \
            patch.object(teams, "IndexedPresetList", lambda *args, **kwargs: navigator):
        applied = teams.prepare_record_team_souls(task, 1, 2)
    assert applied is False, applied
    assert task._record_team_souls is None
    assert task.deferred and task.deferred[0][1].get("success") is False, task.deferred
    assert any('式神录预设无法定位' in msg for msg in recorder.messages("WARNING")), recorder.records
    return {"warnings": recorder.messages("WARNING")[:1]}


check('式神录预设定位失败：跳过御魂装配 + WARNING + 推迟下次运行', record_preset_degrades)


def record_preset_success_marks_team():
    task = FrameTask(frame("orochi"))
    task.click_preset = lambda: None
    navigator = FakeNavigator()
    with patch.object(teams, "logger", Recorder()), \
            patch.object(teams, "IndexedPresetList", lambda *args, **kwargs: navigator), \
            patch.object(teams, "_apply_saved_souls", lambda *args, **kwargs: True):
        applied = teams.prepare_record_team_souls(task, 3, 4)
    assert applied is True, applied
    assert task._record_team_souls == (3, 4), task._record_team_souls
    return {"record": task._record_team_souls}


check('式神录预设装配成功时记录 (group, team) 供战斗复用', record_preset_success_marks_team)


# --------------------------------------------------------------------------- #
# 5. 位图接口兼容（switch_soul 等既有调用方）
# --------------------------------------------------------------------------- #
def compatibility():
    from tasks.Component.preset_list import preset_index, foreground_rule
    assert preset_index('3') == 3
    for bad in (0, -1, True, 1.5, 'x'):
        try:
            preset_index(bad)
        except (ValueError, TypeError):
            continue
        raise AssertionError(f'非法序号被接受：{bad!r}')
    before = crop(frame("evozone"), BATTLE_TEAMS)
    assert isinstance(lists._displacement(before, before, 1), int)
    assert lists._displacement(before, before, 1) == 0
    assert callable(foreground_rule)
    return {"ok": True}


check('既有接口（_displacement / preset_index / foreground_rule）保持兼容', compatibility)


report = {
    "passed": sum(row["passed"] for row in rows),
    "failed": sum(not row["passed"] for row in rows),
    "snapshots": {name: str(path) for name, path in SNAPSHOTS.items()},
    "cases": rows,
}
target = Path(os.environ.get("OAS_PRESET_FIX_REPORT", str(ROOT / "log/fix-presets-check.json")))
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
for row in rows:
    if not row["passed"]:
        print(json.dumps(row, ensure_ascii=False))
print(json.dumps({"passed": report["passed"], "failed": report["failed"],
                  "report": str(target)}, ensure_ascii=False))
sys.exit(bool(report["failed"]))
