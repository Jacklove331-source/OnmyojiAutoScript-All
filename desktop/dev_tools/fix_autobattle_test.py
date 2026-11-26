"""自动战斗（AutoBattle）离线回归：进战斗后必须确认「自动」，不能再干等 5 分钟。

真机零接触：只用 log/error 下的真实战斗截图 + 假设备驱动。
证据快照：log/error/1794977024777（2026-11-18 AreaBoss 12:38:18 进战斗后一直是「手动」，
12:43:16 被看门狗判 Wait too long → GameStuckError）。

覆盖：
1. 已自动 → 一次都不点；
2. 手动 → 点一次「自动」按钮并重新截图复判确认；
3. 按钮被遮住 / 识别不到 → 只 WARNING，不盲点；
4. 点了没反应 → 有上限（2 轮 × 2 次），超限只 WARNING，不抛异常；
5. 手动与自动同时出现 → 不判断、不点击；
6. battle_wait() / battle_wait_v2() 两条等待路径都在战斗循环前确认自动（真实调用链 + 源码锚点）；
7. 影响面：所有继承 GeneralBattle 的任务都会生效（扫描出的任务清单写进报告）。

运行：.\\toolkit\\python.exe dev_tools\\fix_autobattle_test.py
报告：log/fix-autobattle-check.json
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import os
import re
import sys
import time

ROOT = Path(os.environ.get("OAS_AUTOBATTLE_ROOT", str(Path(__file__).resolve().parents[1])))
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

import cv2
import numpy as np

from tasks.Component.GeneralBattle import battle_wait as battle_wait_module
from tasks.Component.GeneralBattle.battle_wait import (
    BattleWait, O_BATTLE_MODE, BATTLE_MODE_ROI, BATTLE_MODE_AUTO, BATTLE_MODE_MANUAL,
)

AUTO_SNAPSHOT = ROOT / "log/error/1795179473096/2026-11-20_20-57-52-999750.png"
MANUAL_SNAPSHOT = ROOT / "log/error/1794977024777/2026-11-18_12-43-43-098429.png"
# 按钮本体所在的大致矩形（1280x720）。OCR 只给出文字框，这里用于断言点击点落在按钮上。
BUTTON_RECT = (20, 615, 105, 100)

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


AUTO_FRAME = read_frame(AUTO_SNAPSHOT)
MANUAL_FRAME = read_frame(MANUAL_SNAPSHOT)


def covered_frame():
    """模拟进战斗瞬间按钮被横幅/特效遮住：整块 ROI 变纯色，OCR 认不出任何状态。"""
    frame = MANUAL_FRAME.copy()
    x, y, w, h = BATTLE_MODE_ROI
    frame[y:y + h, x:x + w] = 24
    return frame


def both_state_frame():
    """手动帧里再贴一份自动帧的按钮区域：ROI 内同时出现两种状态，应当放弃判断。"""
    frame = MANUAL_FRAME.copy()
    x, y, w, h = BATTLE_MODE_ROI
    half = w // 2
    auto_part = AUTO_FRAME[y:y + h, x:x + half]
    frame[y:y + h, x + half:x + w] = auto_part
    return frame


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


class FakeBattle(BattleWait):
    """只提供 OCR + 点击所需接口的假战斗对象（不初始化 BaseTask，不连设备）。"""

    def __init__(self, frame, click_effect=None):
        self._image = frame
        self.click_effect = click_effect
        self.clicks = []
        self.device = SimpleNamespace(image=frame, click=self._click)

    def screenshot(self):
        self.device.image = self._image

    def _click(self, x, y, control_check=True, control_name='Click'):
        self.clicks.append((x, y, control_name))
        if self.click_effect is not None:
            self._image = self.click_effect(self._image)
        return None


def toggle_effect(frame):
    """点一下「自动」按钮后游戏的实际表现：按钮文字从 手动 变成 自动。"""
    return AUTO_FRAME.copy()


def inside_button(point):
    bx, by, bw, bh = BUTTON_RECT
    return bx <= point[0] <= bx + bw and by <= point[1] <= by + bh


# --------------------------------------------------------------------------- #
# 1. 真实快照：已自动 → 不动、不误点
# --------------------------------------------------------------------------- #
def case_already_auto():
    task = FakeBattle(AUTO_FRAME)
    recorder = Recorder()
    started = time.monotonic()
    with patch.object(battle_wait_module, "logger", recorder):
        state, text, point = task.battle_mode_state()
        result = task.ensure_auto_battle()
    elapsed = time.monotonic() - started
    assert state == 'auto', (state, text, point)
    assert BATTLE_MODE_AUTO in text, text
    assert result is True, result
    assert task.clicks == [], task.clicks
    assert '[AutoBattle] already auto' in recorder.messages("INFO"), recorder.records
    assert elapsed < 5, elapsed
    return {"state": state, "clicks": 0, "elapsed": round(elapsed, 2)}


check('真实快照：已自动时识别为 auto，一次都不点', case_already_auto)


# --------------------------------------------------------------------------- #
# 2. 真实快照：手动 → 点自动并复判确认
# --------------------------------------------------------------------------- #
def case_manual_toggles():
    task = FakeBattle(MANUAL_FRAME, click_effect=toggle_effect)
    recorder = Recorder()
    with patch.object(battle_wait_module, "logger", recorder):
        state, text, point = task.battle_mode_state()
        assert state == 'manual', (state, text, point)
        assert BATTLE_MODE_MANUAL in text, text
        assert inside_button(point), (point, BUTTON_RECT)
        result = task.ensure_auto_battle()
    assert result is True, result
    assert len(task.clicks) == 1, task.clicks
    x, y, name = task.clicks[0]
    assert inside_button((x, y)), (task.clicks, BUTTON_RECT)
    assert name == 'BATTLE_MODE_TOGGLE', name
    logs = recorder.messages()
    assert '[AutoBattle] manual detected -> toggle on' in logs, logs
    assert '[AutoBattle] confirmed auto after toggle' in logs, logs
    return {"clicks": task.clicks, "logs": [m for m in logs if 'AutoBattle' in m]}


check('真实快照：手动时点一次自动按钮并复判确认', case_manual_toggles)


# --------------------------------------------------------------------------- #
# 3. 未识别 → 只 WARNING，不盲点
# --------------------------------------------------------------------------- #
def case_unrecognized():
    task = FakeBattle(covered_frame())
    recorder = Recorder()
    with patch.object(battle_wait_module, "logger", recorder):
        state, text, _ = task.battle_mode_state()
        result = task.ensure_auto_battle(attempts=2, delay=.1)
    assert state is None, (state, text)
    assert result is False, result
    assert task.clicks == [], task.clicks
    warnings = recorder.messages("WARNING")
    assert any('state unrecognized' in msg for msg in warnings), recorder.records
    assert any('no click' in msg for msg in warnings), recorder.records
    return {"warnings": warnings}


check('按钮被遮住（未识别）：只 WARNING 不盲点', case_unrecognized)


# --------------------------------------------------------------------------- #
# 4. 点了没反应 → 有上限、只 WARNING、不抛异常
# --------------------------------------------------------------------------- #
def case_toggle_ignored():
    task = FakeBattle(MANUAL_FRAME, click_effect=None)
    recorder = Recorder()
    with patch.object(battle_wait_module, "logger", recorder):
        result = task.ensure_auto_battle(attempts=2, delay=.1, verify_attempts=2, verify_delay=.1)
    assert result is False, result
    assert len(task.clicks) == 4, task.clicks
    warnings = recorder.messages("WARNING")
    assert any('still manual after toggle' in msg for msg in warnings), recorder.records
    assert any('failed to confirm auto battle' in msg for msg in warnings), recorder.records
    return {"clicks": len(task.clicks), "warnings": warnings[:3]}


check('点击无效：有次数上限、只 WARNING、不抛异常', case_toggle_ignored)


# --------------------------------------------------------------------------- #
# 5. 两种状态同时出现 → 不判断、不点击
# --------------------------------------------------------------------------- #
def case_both_states():
    task = FakeBattle(both_state_frame())
    recorder = Recorder()
    with patch.object(battle_wait_module, "logger", recorder):
        state, text, point = task.battle_mode_state()
        result = task.ensure_auto_battle(attempts=1, delay=.1)
    assert state is None, (state, text)
    assert BATTLE_MODE_MANUAL in text and BATTLE_MODE_AUTO in text, text
    assert result is False
    assert task.clicks == [], task.clicks
    return {"text": text}


check('手动与自动同时出现：不判断也不点击', case_both_states)


# --------------------------------------------------------------------------- #
# 6. 两条等待路径都在战斗循环之前确认自动战斗
# --------------------------------------------------------------------------- #
def case_battle_wait_wiring():
    from tasks.Component.GeneralBattle.general_battle import GeneralBattle
    task = object.__new__(GeneralBattle)
    calls = []
    task.ensure_auto_battle = lambda *args, **kwargs: (calls.append('ensure'), True)[1]
    task.device = SimpleNamespace(stuck_record_add=lambda name: calls.append(('stuck', name)),
                                  click_record_clear=lambda: calls.append('click_clear'))
    task.screenshot = lambda: None
    state = {'win': 1}

    def appear(rule, **kwargs):
        if rule is task.I_WIN and state['win'] > 0:
            state['win'] -= 1
            return True
        return False

    task.appear = appear
    task.appear_then_click = lambda *args, **kwargs: False
    task.ui_click_until_disappear = lambda *args, **kwargs: None
    task.wait_until_appear = lambda *args, **kwargs: False
    task._hook_special_reward = lambda: False
    win = task.battle_wait(False)
    assert calls[0] == ('stuck', 'BATTLE_STATUS_S'), calls
    assert 'ensure' in calls, calls
    assert calls.index('ensure') > calls.index('click_clear'), calls
    assert win is True, win
    return {"order": [str(c) for c in calls]}


check('battle_wait()：进战斗先确认自动，再进战斗循环', case_battle_wait_wiring)


def case_battle_wait_v2_wiring():
    from tasks.Component.GeneralBattle.general_battle import GeneralBattle
    source = __import__('inspect').getsource(GeneralBattle.battle_wait_v2)
    assert 'ensure_auto_battle_once()' in source, source[:400]
    assert source.index('stuck_record_add') < source.index('ensure_auto_battle_once()'), '调用位置不对'
    return {"snippet": 'ensure_auto_battle_once() in battle_wait_v2'}


check('battle_wait_v2()：同样在战斗循环前确认自动（源码锚点）', case_battle_wait_v2_wiring)


# --------------------------------------------------------------------------- #
# 6.5 strategy 装饰器路径（@battle_wait_strategy → battle_wait_with_strategy）
#     EvoZone 等任务覆写了 battle_wait，走的是这条路；t7 把钩子只挂在
#     GeneralBattle.battle_wait/battle_wait_v2 上，所以 22:06-22:12 真机日志里
#     [AutoBattle] 锚点 0 次（log/verify_evozone.txt）。
# --------------------------------------------------------------------------- #
class FakeRuntime:
    """battle_wait 运行时的最小替身：只记录 hook_enabled 更新，不驱动真实 hook 逻辑。

    battle_wait 的 runtime 装饰器会在调用 hook 前访问 runtime.pub_ctx / pri_ctx /
    _ensure_pub_default / _ensure_pri_default / reset_per_task，这里给等价的最小实现。
    """

    def __init__(self):
        self.enabled_updates = []
        self._enabled = set()
        self.task_owner = None
        self.pri_ctx = {}
        self.pub_ctx = SimpleNamespace(
            cross={}, per_task=SimpleNamespace(), options={},
            per_battle=SimpleNamespace(success=battle_wait_module.BattleResult.SUCCESS,
                                       hook_enabled=self._enabled))

    def _ensure_pub_default(self):
        return self.pub_ctx

    def _ensure_pri_default(self, name):
        if name not in self.pri_ctx:
            self.pri_ctx[name] = SimpleNamespace(
                cross={}, per_task=SimpleNamespace(), per_battle=SimpleNamespace(),
                options=SimpleNamespace())
        return self.pri_ctx[name]

    def reset_per_task(self):
        self.pub_ctx.per_task = SimpleNamespace()

    def hook_enabled_update(self, enable=(), disable=()):
        self.enabled_updates.append((tuple(enable), tuple(disable)))
        self._enabled.update(enable)
        return None

    def hook_enabled(self):
        return self._enabled

    def hook2event(self, func_name):
        return func_name[len('_bw_'):].rsplit('_', 1)[0]


class FakePlan:
    """最小 battle_wait_plan：setup 一个桩，循环里一个立刻 DONE 的 completion 桩。"""

    function_setup_name = '_bw_setup_stub'

    def sequence_function_names(self):
        return ['_bw_completion_stub']


class StrategyBattle(BattleWait):
    """走 strategy 路径的假任务（不初始化 BaseTask，只提供本用例用到的接口）。"""

    def __init__(self, frame, current_count=1, click_effect=None):
        self._image = frame
        self.current_count = current_count
        self.click_effect = click_effect
        self.clicks = []
        self.completed = False
        self.device = SimpleNamespace(image=frame, click=self._click)

    def screenshot(self):
        self.device.image = self._image

    def _click(self, x, y, control_check=True, control_name='Click'):
        self.clicks.append((x, y, control_name))
        if self.click_effect is not None:
            self._image = self.click_effect(self._image)
        return None

    def _bw_setup_stub(self, **kwargs):
        return battle_wait_module.HookSignal.DONE

    def _bw_completion_stub(self, **kwargs):
        self.completed = True
        return battle_wait_module.HookSignal.DONE


def run_strategy(task):
    """用替身 runtime 跑真实的 battle_wait_with_strategy（含 ensure_auto_battle_once 调用）。"""
    fake_runtime = FakeRuntime()
    with patch.object(battle_wait_module, 'runtime', fake_runtime):
        result = task.battle_wait_with_strategy(battle_wait_plan=FakePlan())
    assert task.completed, '策略路径的 completion hook 没有跑完'
    return result, fake_runtime


def case_strategy_already_auto():
    task = StrategyBattle(AUTO_FRAME, current_count=1)
    recorder = Recorder()
    with patch.object(battle_wait_module, 'logger', recorder):
        result, _ = run_strategy(task)
    assert result is True, result
    assert task.clicks == [], task.clicks
    info = recorder.messages('INFO')
    assert '[AutoBattle] already auto' in info, recorder.records
    return {"logs": [m for m in info if 'AutoBattle' in m]}


check('策略路径（EvoZone 走的 @battle_wait_strategy）：进战斗即确认自动', case_strategy_already_auto)


def case_strategy_manual_toggles():
    task = StrategyBattle(MANUAL_FRAME, current_count=2, click_effect=toggle_effect)
    recorder = Recorder()
    with patch.object(battle_wait_module, 'logger', recorder):
        result, _ = run_strategy(task)
    assert result is True, result
    assert len(task.clicks) == 1, task.clicks
    x, y, name = task.clicks[0]
    assert inside_button((x, y)), (task.clicks, BUTTON_RECT)
    assert name == 'BATTLE_MODE_TOGGLE', name
    logs = recorder.messages()
    assert '[AutoBattle] manual detected -> toggle on' in logs, logs
    assert '[AutoBattle] confirmed auto after toggle' in logs, logs
    return {"clicks": task.clicks, "logs": [m for m in logs if 'AutoBattle' in m]}


check('策略路径：手动帧点一次并复判确认', case_strategy_manual_toggles)


def case_strategy_once_per_battle():
    """同一场战斗（current_count 不变）：策略路径 + 普通路径叠加也只扫一次 OCR。"""
    task = StrategyBattle(AUTO_FRAME, current_count=7)
    calls = []
    real_state = BattleWait.battle_mode_state

    def counting_state(this):
        calls.append(this.current_count)
        return real_state(this)

    task.battle_mode_state = lambda: counting_state(task)
    recorder = Recorder()
    with patch.object(battle_wait_module, 'logger', recorder):
        run_strategy(task)
        assert len(calls) == 1, calls
        # 同一场战斗里另一条路径再进来：必须被去重
        again = task.ensure_auto_battle_once()
        assert again is True, again
        assert len(calls) == 1, calls
        # 下一场战斗：current_count 变了就要重新确认
        task.current_count = 8
        task.ensure_auto_battle_once()
        assert len(calls) == 2, calls
    info = recorder.messages('INFO')
    assert '[AutoBattle] already checked in this battle, skip' in info, recorder.records
    return {"state_calls": calls, "logs": [m for m in info if 'AutoBattle' in m]}


check('同一场战斗只确认一次（策略+普通路径叠加不重复 OCR）', case_strategy_once_per_battle)


def case_strategy_wiring_source():
    import inspect
    source = inspect.getsource(BattleWait.battle_wait_with_strategy)
    assert 'ensure_auto_battle_once()' in source, source
    body = source.split('ensure_auto_battle_once()')[1]
    assert 'while True' in body, 'ensure_auto_battle_once 必须写在战斗循环之前'
    evozone = (ROOT / 'tasks/EvoZone/script_task.py').read_text(encoding='utf-8', errors='replace')
    assert '@battle_wait_strategy' in evozone, 'EvoZone 没有走策略路径？'
    assert 'battle_wait_with_strategy' in evozone, evozone[:200]
    return {"evozone": 'battle_wait -> battle_wait_with_strategy (@battle_wait_strategy)'}


check('源码锚点：battle_wait_with_strategy 在循环前调用 ensure_auto_battle_once，EvoZone 确实走这条路',
      case_strategy_wiring_source)


# --------------------------------------------------------------------------- #
# 7. 影响面：所有继承 GeneralBattle 的任务
# --------------------------------------------------------------------------- #
def case_affected_tasks():
    pattern = re.compile(r'^\s*class\s+(\w+)\(([^)]*)\)', re.MULTILINE)
    affected = []
    for path in sorted(ROOT.glob('tasks/**/*.py')):
        text = path.read_text(encoding='utf-8', errors='replace')
        for match in pattern.finditer(text):
            bases = [base.strip() for base in match.group(2).split(',')]
            if any(base == 'GeneralBattle' for base in bases):
                affected.append(f'{path.relative_to(ROOT).as_posix()}:{match.group(1)}')
    assert len(affected) >= 20, affected
    joined = ' '.join(affected)
    for expected in ('tasks/AreaBoss/', 'tasks/Orochi/', 'tasks/EvoZone/', 'tasks/RealmRaid/',
                     'tasks/Hunt/', 'tasks/Duel/', 'tasks/DemonEncounter/'):
        assert expected in joined, (expected, affected)
    return {"count": len(affected), "tasks": affected}


check('影响面：所有继承 GeneralBattle 的任务都会生效', case_affected_tasks)


# --------------------------------------------------------------------------- #
# 8. 日志锚点齐全（以后回归时可直接 grep）
# --------------------------------------------------------------------------- #
def case_log_anchors():
    anchors = ['[AutoBattle] already auto', '[AutoBattle] manual detected -> toggle on',
               '[AutoBattle] confirmed auto after toggle', '[AutoBattle] state unrecognized']
    source = Path(battle_wait_module.__file__).read_text(encoding='utf-8')
    missing = [anchor for anchor in anchors if anchor not in source]
    assert not missing, missing
    return {"anchors": anchors}


check('日志锚点：[AutoBattle] already auto / manual detected -> toggle on 等齐全', case_log_anchors)


report = {
    "passed": sum(row["passed"] for row in rows),
    "failed": sum(not row["passed"] for row in rows),
    "snapshots": {"auto": str(AUTO_SNAPSHOT), "manual": str(MANUAL_SNAPSHOT)},
    "cases": rows,
}
target = Path(os.environ.get("OAS_AUTOBATTLE_REPORT", str(ROOT / "log/fix-autobattle-check.json")))
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
for row in rows:
    if not row["passed"]:
        print(json.dumps(row, ensure_ascii=False))
print(json.dumps({"passed": report["passed"], "failed": report["failed"],
                  "report": str(target)}, ensure_ascii=False))
sys.exit(bool(report["failed"]))
