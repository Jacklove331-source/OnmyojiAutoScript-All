"""Offline regression for numeric preset selection; never controls a device."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import os
import sys

ROOT = Path(os.environ.get("OAS_PRESET_ROOT", str(Path(__file__).resolve().parents[1])))
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
from module.exception import ScriptError
from tasks.Component import preset_list as module
from tasks.Component.preset_list import (
    IndexedPresetList, SOUL_GROUPS, SOUL_TEAMS, BATTLE_GROUPS, BATTLE_TEAMS, preset_index,
)


class ListTask:
    def __init__(self, layout, count=16, initial=0, factor=.91):
        self.layout = layout
        self.count = count
        self.factor = factor
        self.offset = 0
        self.actions = []
        self.visible = True
        self.config = SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(control_method='test')))
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
        self.offset = min(initial, max(0, length - height))
        self.device = SimpleNamespace(image=None, swipe=self.swipe,
                                      stuck_record_clear=lambda: None, click_record_clear=lambda: None,
                                      click=self.click)
        self.screenshot()

    def screenshot(self):
        x, y, width, height = map(int, self.layout.roi)
        frame = np.full((720, 1280, 3), 180, dtype=np.uint8)
        part = self.content[self.offset:self.offset + height]
        frame[y:y + height, x:x + width] = part[..., None]
        self.device.image = frame

    def swipe(self, start, end, **kwargs):
        assert all(0 <= p[0] < 1280 and 0 <= p[1] < 720 for p in (start, end))
        self.actions.append(('scroll', start, end))
        delta = int(round((start[1] - end[1]) * self.factor))
        self.offset = max(0, min(len(self.content) - self.layout.roi[3], self.offset + delta))

    def click(self, x, y, **kwargs):
        assert 0 <= x < 1280 and 0 <= y < 720
        self.actions.append(('click', x, y))

    def row_present(self, layout, y):
        if not self.count:
            return False
        center = y + self.offset
        return any(abs(center - (layout.first_y + (i - 1) * layout.stride)) <= 4 for i in range(1, self.count + 1))

    def navigator(self):
        return IndexedPresetList(self, lambda: self.visible, self.row_present, pause=lambda _: None)


rows = []
def check(name, fn):
    try:
        detail = fn()
        rows.append({'case': name, 'passed': True, 'details': detail})
    except Exception as exc:
        rows.append({'case': name, 'passed': False, 'error': type(exc).__name__ + ': ' + str(exc)})

def select_case(layout, index, count=16, factor=.91):
    task = ListTask(layout, count=count, initial=99999, factor=factor)
    point = task.navigator().select(index, layout)
    actual = (point[1] + task.offset - layout.first_y) / layout.stride + 1
    assert abs(actual - index) <= .05, (actual, index, task.offset, point)
    assert len([a for a in task.actions if a[0] == 'click']) == 1
    return {'requested': index, 'actual': actual, 'scrolls': sum(a[0] == 'scroll' for a in task.actions)}

for layout in (SOUL_GROUPS, SOUL_TEAMS, BATTLE_GROUPS, BATTLE_TEAMS):
    for index in (1, 4, 5, 8, 9):
        check(f'{layout.name} index {index}, starts on a scrolled page',
              lambda layout=layout, index=index: select_case(layout, index))
    check(f'{layout.name} last item on a clamped final page',
          lambda layout=layout: select_case(layout, 16))
check('group 99 has no artificial upper bound', lambda: select_case(SOUL_GROUPS, 99, count=104))
check('team 42 has no artificial upper bound', lambda: select_case(BATTLE_TEAMS, 42, count=45))
check('short scroll displacement is measured rather than assumed', lambda: select_case(SOUL_TEAMS, 9, factor=.62))
check('long scroll displacement is measured rather than assumed', lambda: select_case(BATTLE_TEAMS, 9, factor=1.28))

def missing(index=20, count=8):
    task = ListTask(SOUL_TEAMS, count=count)
    try:
        task.navigator().select(index, SOUL_TEAMS)
    except ScriptError:
        assert not any(a[0] == 'click' for a in task.actions)
        return
    raise AssertionError('missing preset was clicked')
check('reaching the last page never substitutes a different team', missing)
check('empty slot on the first page never substitutes another team', lambda: missing(3, 1))
check('empty group never starts a team selection', lambda: missing(1, 0))

def hidden():
    task = ListTask(SOUL_GROUPS)
    task.visible = False
    try:
        task.navigator().select(9, SOUL_GROUPS)
    except ScriptError:
        assert task.actions == []
        return
    raise AssertionError('covered panel accepted')
check('covered or closed panel receives no input', hidden)

def stopped():
    task = ListTask(SOUL_TEAMS)
    def screenshot():
        raise InterruptedError('user stop')
    task.screenshot = screenshot
    try:
        task.navigator().select(9, SOUL_TEAMS)
    except InterruptedError:
        assert task.actions == []
        return
    raise AssertionError('stop swallowed')
check('user stop propagates without another input', stopped)

def invalid(value):
    try:
        preset_index(value)
    except (ValueError, TypeError):
        return
    raise AssertionError('invalid list index accepted')
for value in (0, -1, True, 1.5, 'x'):
    check(f'invalid index {value!r} rejected', lambda value=value: invalid(value))

def soul_caller():
    from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
    task = object.__new__(SwitchSoul)
    calls = []
    task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
    task.screenshot = lambda: None
    task.appear_then_click = lambda target, **kw: calls.append(('equip', target.roi_back)) or True
    task.ui_click_until_disappear = lambda *_args, **_kw: calls.append(('confirm',))
    class Navigator:
        def __init__(self, *_args):
            pass
        def select(self, index, layout):
            calls.append((layout.name, index))
        def locate(self, index, layout):
            calls.append((layout.name, index))
            return 990, 430
    with patch.object(module, 'IndexedPresetList', Navigator), patch.object(module, 'foreground_rule', lambda *_: True):
        task.switch_soul_one(9, 8)
    assert calls[0] == ('御魂分组', 9) and calls[1] == ('御魂队伍', 8), calls
    assert calls[-1] == ('confirm',), calls
    assert 0 <= calls[2][1][1] < 720
check('real soul-switch caller supports group 9/team 8 with a visible equip ROI', soul_caller)

def battle_caller(disabled=False):
    from tasks.Component.GeneralBattle.general_battle import GeneralBattle
    from tasks.Component import preset_team as linked
    task = object.__new__(GeneralBattle)
    state = SimpleNamespace(open=True, calls=[])
    task.screenshot = lambda: None
    task.appear = lambda rule, **_: False
    def click(*_args, **_kw):
        state.open = False
        state.calls.append('confirm')
        return True
    task.appear_then_click = click
    class Navigator:
        def __init__(self, *_args): pass
        def select(self, index, layout):
            state.calls.append((layout.name, index))
            return layout.click_x, 450
    panel = linked.Panel(BATTLE_GROUPS, BATTLE_TEAMS, task.I_PRESET_ENSURE)
    with patch.object(linked, 'IndexedPresetList', Navigator), patch.object(linked, '_panel', lambda _: panel if state.open else None), patch.object(linked, 'apply_team_souls', lambda *_: state.calls.append('souls')):
        task.switch_preset_team(not disabled, 9, 9)
    expected = [] if disabled else [('战斗预设分组', 9), ('战斗预设队伍', 9), 'souls', 'confirm']
    assert state.calls == expected, state.calls
check('real battle caller scrolls group9/team9 and applies souls before confirming', battle_caller)
check('disabled battle preset still performs no input', lambda: battle_caller(True))

def abyss_caller():
    from tasks.AbyssShadows.script_task import ScriptTask
    task = object.__new__(ScriptTask)
    state = SimpleNamespace(preparing=True, calls=[])
    battle = SimpleNamespace(preset_enable=True, preset_group=2, preset_team=9)
    task.config = SimpleNamespace(abyss_shadows=SimpleNamespace(general_battle_config=battle,
        abyss_shadows_combat_time=SimpleNamespace(CombatTime_enable=False)))
    task.screenshot = lambda: None
    task.wait_until_appear = lambda *_args, **_kw: state.preparing
    task.switch_preset_team = lambda *args: state.calls.append(args)
    task.click = lambda *_args, **_kw: setattr(state, 'preparing', False)
    task.appear = lambda rule, **_: state.preparing if rule is task.I_EQUIPPING else False
    task.appear_then_click = lambda rule, **_: rule is task.I_WIN
    assert task.run_general_battle_back('BOSS') is True
    assert state.calls == [(True, 2, 9)], state.calls
check('real Abyss preparation applies configured group 2/team 9 once before ready', abyss_caller)

def model():
    from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
    conf = GeneralBattleConfig(preset_group=99999, preset_team=99999)
    assert conf.preset_group == conf.preset_team == 99999
check('configuration model accepts arbitrary positive group and team numbers', model)

def enum_control():
    from enum import Enum
    class Method(str, Enum):
        MINITOUCH = 'minitouch'
    task = ListTask(SOUL_TEAMS)
    task.config.script.device.control_method = Method.MINITOUCH
    calls = []
    task.device.handle_control_check = lambda name: calls.append(name)
    task.device.drag_minitouch = lambda start, end, **kw: task.swipe(start, end)
    task.navigator().select(9, SOUL_TEAMS)
    assert calls and all(name.startswith('PRESET_SCROLL_') for name in calls), calls
check('actual enum control setting dispatches native slow drag correctly', enum_control)

fixture = Path(os.environ.get('OAS_PRESET_FIXTURE', str(ROOT / 'log/lineup_capture/click_20261025_093055.png')))
if fixture.is_file():
    real_frame = cv2.cvtColor(cv2.imdecode(np.fromfile(str(fixture), dtype=np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    def real_motion():
        x, y, w, h = SOUL_GROUPS.roi
        original = cv2.cvtColor(real_frame[y:y+h, x:x+w], cv2.COLOR_RGB2GRAY)
        shifted = np.concatenate((original[40:], np.full_like(original[:40], 225)), axis=0)
        assert module._displacement(original, shifted, 1) == 40
        assert module._displacement(shifted, original, -1) == -40
    check('real captured game group list tracks measured motion in both directions', real_motion)

    def real_dim_panel():
        from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
        task = object.__new__(SwitchSoul)
        task.device = SimpleNamespace(image=real_frame.copy())
        assert module.foreground_rule(task, task.I_SOU_TEAM_PRESENT)
        task.device.image = (real_frame * .4).astype(real_frame.dtype)
        assert not module.foreground_rule(task, task.I_SOU_TEAM_PRESENT)
    check('real game panel is accepted and its dimmed background is rejected', real_dim_panel)

    def empty_real_team():
        from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
        task = object.__new__(SwitchSoul)
        clicks = []
        task.interval_timer = {}
        task.device = SimpleNamespace(image=real_frame.copy(), click=lambda *a, **kw: clicks.append(a))
        task.screenshot = lambda: None
        class Navigator:
            def __init__(self, *_args):
                pass
            def select(self, *_args):
                pass
            def locate(self, *_args):
                return 990, 166
        with patch.object(module, 'IndexedPresetList', Navigator):
            try:
                task.switch_soul_one(4, 1)
            except ScriptError:
                assert not clicks, clicks
                return
        raise AssertionError('empty real team was accepted')
    check('real captured empty team never applies another row or confirms anything', empty_real_team)

report = {'passed': sum(r['passed'] for r in rows), 'failed': sum(not r['passed'] for r in rows), 'cases': rows}
target = Path(os.environ.get('OAS_PRESET_REPORT', str(ROOT / 'log/preset-index-check.json')))
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
for row in rows:
    if not row['passed']:
        print(json.dumps(row, ensure_ascii=False))
print(json.dumps({'passed': report['passed'], 'failed': report['failed'], 'report': str(target)}, ensure_ascii=False))
sys.exit(bool(report['failed']))
