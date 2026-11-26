# This Python file uses the following encoding: utf-8
"""D 类长尾（等待/停滞）离线回归：看门狗续期 / 分享降级 / WQ2 面板探针降级。

不启动 OAS 实例、不 adb、不碰真机。全部用**假设备 + 假时钟 + 假帧**驱动真实的
生产代码路径：

1. 看门狗续期（`module/device/device.py`）
   - 战斗中（画面在动）→ 不抛 GameStuckError，续期 + INFO，计时器归零；
   - 真卡住（画面自打点时起一动不动）→ 仍然抛 GameStuckError；
   - 续期上限 3 次，用尽后一定抛；
   - 任务自注册探针（组队等待页：画面静止但确知在等待）→ 续期；
   - 非长等待占位符（普通 stuck 记录）→ 不续期，仍抛；
   - `LONG_WAIT` 显式长等待占位符 → 60s 短定时器不再误杀。
2. 分享降级（`tasks/WeeklyTrifles/script_task.py`）
   - 单步等待超时 → 返回 False + WARNING，等待期间 LONG_WAIT 成对增删；
   - 分享项抛可降级异常 → 只跳过该项（回主界面），后续分享项继续跑；
   - 设备/任务级致命异常（GameStuckError 等）→ 继续向上抛，不被吞掉。
3. WQ2 面板识别降级（`tasks/WantedQuests/ui_wq_v2.py`）
   - 标题/刷新 OCR 连续读空 → 停用这两条探针并记 WARNING（不反复空转）；
   - OCR 停用期间用「进度横带」探针仍能确认面板；
   - 面板始终判不到 → `wq2_ensure_panel` 在预算内返回 False（跳过本次面板）。

另附 `verify_snapshot_evidence()`：拿 `log/error/<ts>/log.txt` 复核三个战斗等待
快照的「打点 → 看门狗」时长与结论（快照不在了就跳过，不算失败）。

用法（仓库根目录）：

    .\\toolkit\\python.exe dev_tools\\fix_longtail_test.py
"""
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import module.device.device as device_module
from module.device.device import Device
from module.exception import GameStuckError
import tasks.WeeklyTrifles.script_task as wt
import tasks.WantedQuests.ui_wq_v2 as wq

RESULTS = []


def check(name: str, ok: bool, detail: str = '') -> None:
    RESULTS.append((name, bool(ok), detail))
    flag = 'PASS' if ok else 'FAIL'
    print(f'[{flag}] {name}' + (f' — {detail}' if detail else ''))


class FakeLogger:
    """记录日志的假 logger（只关心 message 与级别）。"""

    def __init__(self):
        self.records = []

    def info(self, message, *args, **kwargs):
        self.records.append(('info', str(message)))

    def warning(self, message, *args, **kwargs):
        self.records.append(('warning', str(message)))

    def debug(self, message, *args, **kwargs):
        self.records.append(('debug', str(message)))

    def text(self, level: str = '') -> str:
        return '\n'.join(msg for lvl, msg in self.records if not level or lvl == level)


# --------------------------------------------------------------------------- 假时钟
class FakeTimer:
    """假时钟：`elapsed` 手动推进，`reset()` 归零，语义与 Timer.reached 一致。"""

    def __init__(self, limit, count=0):
        self.limit = limit
        self.count = count
        self.elapsed = 0.0
        self.resets = 0

    def start(self):
        return self

    def reached(self):
        return self.elapsed > self.limit

    def reset(self):
        self.elapsed = 0.0
        self.resets += 1
        return self

    def expire(self, margin: float = 1.0):
        self.elapsed = self.limit + margin
        return self


class InstantTimer:
    """预算立即到期的假 Timer（覆盖「有界等待超时」这一条路径）。"""

    def __init__(self, limit, count=0):
        self.limit = limit
        self.count = count

    def start(self):
        return self

    def reached(self):
        return True

    def reset(self):
        return self


# ------------------------------------------------------------------ 看门狗续期：假设备
def make_frame(seed: int, size=(180, 320)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(size[0], size[1], 3), dtype=np.uint8)


class FakeBattleDevice(Device):
    """只跑看门狗逻辑的假设备：帧序列 + 假时钟，其余一律不碰。"""

    STUCK_PROBE_INTERVAL = 0.0  # 探针不需要真等 1 秒

    def __init__(self, frames, detect=('BATTLE_STATUS_S',), long_expired=True):
        # 故意不调 Device.__init__：那会去连模拟器/窗口
        self._frames = list(frames)
        self._frame_index = 0
        self.image = self._frames[0]
        self.detect_record = set(detect)
        self.stuck_timer = FakeTimer(60).expire()
        self.stuck_timer_long = FakeTimer(300)
        if long_expired:
            self.stuck_timer_long.expire()
        self.stuck_renew_count = 0
        self._stuck_ref_image = self._frames[0].copy()
        self._stuck_ref_time = 0.0
        self.stuck_state_probers = []
        self.app_running = True
        self.probe_calls = 0

    def _stuck_capture_frame(self):
        self.probe_calls += 1
        if self._frame_index < len(self._frames):
            frame = self._frames[self._frame_index]
        else:
            frame = self._frames[-1]
        self._frame_index += 1
        self.image = frame
        return frame

    def app_is_running_after_recovery(self):
        return self.app_running

    def device_not_running_error(self):
        return GameStuckError('fake device not running')

    def screenshot(self):  # 看门狗里不该再走这里（否则就是递归风险）
        raise AssertionError('stuck_record_check 不应重新进入 Device.screenshot')


def raise_if(device, error_types=(GameStuckError,)):
    """跑一次 stuck_record_check，返回 (raised, exception)。"""
    try:
        device.stuck_record_check()
    except error_types as error:  # noqa: BLE001
        return True, error
    except Exception as error:  # noqa: BLE001
        return True, error
    return False, None


def test_watchdog_battle_keeps_alive(log: FakeLogger):
    """战斗中（画面在动）→ 续期，不抛 GameStuckError。"""
    frames = [make_frame(1), make_frame(2), make_frame(3)]
    dev = FakeBattleDevice(frames)
    raised, error = raise_if(dev)
    check('D1 战斗中：看门狗到点不抛错', not raised, repr(error) if raised else '')
    check('D1 战斗中：续期 1 次', dev.stuck_renew_count == 1,
          f'stuck_renew_count={dev.stuck_renew_count}')
    check('D1 战斗中：两个计时器都归零',
          dev.stuck_timer.elapsed == 0 and dev.stuck_timer_long.elapsed == 0,
          f'{dev.stuck_timer.elapsed}/{dev.stuck_timer_long.elapsed}')
    check('D1 战斗中：记了 INFO（含状态与变化率）',
          '仍在推进' in log.text('info') and '次续期' in log.text('info'),
          log.text('info').splitlines()[-1] if log.records else '')


def test_watchdog_frozen_raises(log: FakeLogger):
    """真卡住（画面自打点时起一动没动）→ 仍然抛 GameStuckError。"""
    frame = make_frame(7)
    dev = FakeBattleDevice([frame.copy(), frame.copy()])
    raised, error = raise_if(dev)
    check('D1 真卡住：抛 GameStuckError', raised and isinstance(error, GameStuckError),
          repr(error))
    check('D1 真卡住：没有续期', dev.stuck_renew_count == 0,
          f'stuck_renew_count={dev.stuck_renew_count}')
    check('D1 真卡住：确认停滞的 INFO 有落日志',
          '确认已停滞' in log.text('info'), '')


def test_watchdog_renew_limit(log: FakeLogger):
    """续期上限 3 次：用尽后即使画面在动也必须抛（不能无限续）。"""
    frames = [make_frame(11), make_frame(12)]
    dev = FakeBattleDevice(frames)
    dev.stuck_renew_count = dev.STUCK_RENEW_LIMIT
    raised, error = raise_if(dev)
    check('D1 续期用尽：仍抛 GameStuckError', raised and isinstance(error, GameStuckError),
          repr(error))
    check('D1 续期用尽：不再截帧探测', dev.probe_calls == 0,
          f'probe_calls={dev.probe_calls}')


def test_watchdog_registered_prober(log: FakeLogger):
    """任务注册的探针命中组队等待页 → 画面静止也续期。"""
    frame = make_frame(21)
    dev = FakeBattleDevice([frame.copy(), frame.copy()], detect=('PREPARE_BEFORE_BATTLE',))
    dev.register_stuck_state_prober(
        lambda device: ('team_wait', '组队等待页：等待队友准备'), name='fake-team')
    raised, error = raise_if(dev)
    check('D1 组队等待：注册探针命中则续期不抛', not raised, repr(error) if raised else '')
    check('D1 组队等待：续期 1 次', dev.stuck_renew_count == 1,
          f'stuck_renew_count={dev.stuck_renew_count}')
    check('D1 组队等待：日志里带探针状态',
          'team_wait' in log.text('info'), log.text('info').splitlines()[-1] if log.records else '')


def test_watchdog_normal_stuck_untouched(log: FakeLogger):
    """普通 stuck 记录（非长等待）不受续期影响：画面在动也照旧抛。"""
    frames = [make_frame(31), make_frame(32)]
    dev = FakeBattleDevice(frames, detect=('SOME_BATTLE_BUTTON',))
    raised, error = raise_if(dev)
    check('D1 普通记录：不续期仍抛错', raised and isinstance(error, GameStuckError),
          repr(error))
    check('D1 普通记录：没有续期计数', dev.stuck_renew_count == 0,
          f'stuck_renew_count={dev.stuck_renew_count}')


def test_watchdog_long_wait_token(log: FakeLogger):
    """LONG_WAIT 显式长等待：60s 短定时器到点不再误杀（等长定时器）。"""
    frames = [make_frame(41), make_frame(42)]
    dev = FakeBattleDevice(frames, detect=('LONG_WAIT',), long_expired=False)
    raised, error = raise_if(dev)
    check('D2 长等待占位符：短定时器不误杀', not raised, repr(error) if raised else '')
    check('D2 长等待占位符：此时还没续期', dev.stuck_renew_count == 0,
          f'stuck_renew_count={dev.stuck_renew_count}')
    # 长定时器也到点，但画面在动 → 续期而不是抛
    dev.stuck_timer_long.expire()
    raised, error = raise_if(dev)
    check('D2 长等待占位符：长定时器到点后画面在动则续期', not raised,
          repr(error) if raised else '')
    check('D2 长等待占位符：续期 1 次', dev.stuck_renew_count == 1,
          f'stuck_renew_count={dev.stuck_renew_count}')


def test_watchdog_empty_record_unchanged(log: FakeLogger):
    """空记录（一条 stuck record 都没有）+ 短定时器到点：语义保持不变（仍抛）。"""
    frames = [make_frame(51), make_frame(52)]
    dev = FakeBattleDevice(frames, detect=(), long_expired=False)
    raised, error = raise_if(dev)
    check('D1 空记录：保持原语义（抛）', raised and isinstance(error, GameStuckError),
          repr(error))


# ------------------------------------------------------------- WeeklyTrifles：假 task/device
class FakeShareDevice:
    def __init__(self):
        self.calls = []
        self.image = np.zeros((8, 8, 3), np.uint8)

    def stuck_record_add(self, name):
        self.calls.append(('add', str(name)))

    def stuck_record_remove(self, name):
        self.calls.append(('remove', str(name)))

    def stuck_record_clear(self):
        self.calls.append(('clear', ''))


class FakeShareTask:
    """只用真 ScriptTask 的方法，自己补 screenshot/recover 这些依赖。"""

    SHARE_STEP_TIMEOUT = 45
    SHARE_NAV_TIMEOUT = 60
    SHARE_FATAL_ERRORS = wt.ScriptTask.SHARE_FATAL_ERRORS

    def __init__(self):
        self.device = FakeShareDevice()
        self.screenshots = 0
        self.recovered = 0

    def screenshot(self):
        self.screenshots += 1

    def _share_recover(self):
        self.recovered += 1
        return True


def test_weekly_share_timeout(log: FakeLogger):
    """单步等待超时 → False + WARNING + LONG_WAIT 成对增删。"""
    original_timer = wt.Timer
    wt.Timer = InstantTimer
    try:
        task = FakeShareTask()
        ok = wt.ScriptTask._share_wait(
            task, 'open-collect-qrcode',
            done=lambda: False, actions=(lambda: False,), timeout=None)
    finally:
        wt.Timer = original_timer
    check('D2 分享超时：返回 False（跳过该步）', ok is False, f'returned={ok!r}')
    check('D2 分享超时：记 WARNING',
          '超时' in log.text('warning'), log.text('warning').splitlines()[-1] if log.records else '')
    check('D2 分享超时：LONG_WAIT 成对增删',
          task.device.calls == [('add', 'LONG_WAIT'), ('remove', 'LONG_WAIT')],
          str(task.device.calls))
    check('D2 分享超时：至少截过一帧', task.screenshots >= 1, f'{task.screenshots}')


def test_weekly_share_done_path(log: FakeLogger):
    """done() 立即可见 → 不超时、返回 True（正常路径没被改坏）。"""
    task = FakeShareTask()
    ok = wt.ScriptTask._share_wait(task, 'enter-collect', done=lambda: True)
    check('D2 分享正常：返回 True', ok is True, f'returned={ok!r}')
    check('D2 分享正常：LONG_WAIT 也成对增删',
          task.device.calls == [('add', 'LONG_WAIT'), ('remove', 'LONG_WAIT')],
          str(task.device.calls))


def test_weekly_share_section_degrades(log: FakeLogger):
    """一项分享失败 → 跳过该项 + WARNING + 后续项照常执行。"""
    task = FakeShareTask()
    executed = []

    def boom():
        executed.append('share_collect')
        raise RuntimeError('分享面板卡死（模拟 1794908631901）')

    def ok_section():
        executed.append('share_area_boss')

    ok1 = wt.ScriptTask._run_share_section(task, '图鉴分享', boom)
    ok2 = wt.ScriptTask._run_share_section(task, '地鬼分享', ok_section)
    check('D2 失败跳过：第一项返回 False', ok1 is False, f'{ok1!r}')
    check('D2 失败跳过：后续项仍执行并成功', ok2 is True and executed ==
          ['share_collect', 'share_area_boss'], str(executed))
    check('D2 失败跳过：失败项触发回主界面', task.recovered == 1, f'{task.recovered}')
    check('D2 失败跳过：WARNING 里有具体异常',
          'RuntimeError' in log.text('warning'), log.text('warning').splitlines()[-1]
          if log.records else '')


def test_weekly_share_section_fatal(log: FakeLogger):
    """设备/任务级致命异常必须继续向上抛（降级不能吞掉 GameStuckError）。"""
    task = FakeShareTask()
    fatal = GameStuckError('Wait too long')

    def raise_fatal():
        raise fatal

    raised = False
    try:
        wt.ScriptTask._run_share_section(task, '图鉴分享', raise_fatal)
    except GameStuckError:
        raised = True
    check('D2 致命异常：GameStuckError 继续上抛', raised)
    check('D2 致命异常：不做回主界面降级', task.recovered == 0, f'{task.recovered}')


# ------------------------------------------------------------------- WQ2：假 task/device
class FakeWQDevice:
    def __init__(self):
        self.image = np.zeros((8, 8, 3), np.uint8)
        self.calls = []

    def stuck_record_add(self, name):
        self.calls.append(('add', str(name)))

    def stuck_record_remove(self, name):
        self.calls.append(('remove', str(name)))

    def stuck_record_clear(self):
        self.calls.append(('clear', ''))


class FakeWQ(wq.WantedQuestsUiV2):
    """面板判定用假 task：OCR 结果/进度横带/模板都由测试给。"""

    def __init__(self, progress=(), templates=False, panel_visible=None):
        self.device = FakeWQDevice()
        self.screenshots = 0
        self._progress = list(progress)
        self._templates = templates
        self._panel_visible = panel_visible

    def screenshot(self):
        self.screenshots += 1

    def wq2_detail_visible(self):
        return False

    def wq2_progress_tokens(self):
        return list(self._progress)

    def _wq2_panel_by_templates(self):
        return self._templates

    def wq2_panel_visible(self):
        if self._panel_visible is not None:
            return self._panel_visible
        return wq.WantedQuestsUiV2.wq2_panel_visible(self)

    def appear(self, rule, *args, **kwargs):
        return False

    def goto_page(self, page):
        return True

    def _wq2_click_panel_entry(self):
        return False


def test_wq2_panel_probe_backoff(log: FakeLogger):
    """标题/刷新 OCR 连续读空 → 停用这两条探针（换探针，不反复空转）。"""
    original_title = wq.O_WQ_PANEL_TITLE.ocr
    original_refresh = wq.O_WQ_REFRESH.ocr
    wq.O_WQ_PANEL_TITLE.ocr = lambda image: ''
    wq.O_WQ_REFRESH.ocr = lambda image: ''
    try:
        task = FakeWQ()
        visible = [task.wq2_panel_visible() for _ in range(3)]
        check('D3 OCR 全空：判不到面板', visible == [False, False, False], str(visible))
        check('D3 OCR 全空：连续 3 次后停用 OCR 探针',
              task._wq2_ocr_probe_active() is False, '')
        check('D3 OCR 全空：记了 WARNING',
              '读空' in log.text('warning'), log.text('warning').splitlines()[-1]
              if log.records else '')
        # OCR 停用后不再调用 OCR（用会抛错的假 OCR 证明它真的不跑了）
        def explode(image):
            raise AssertionError('OCR 探针应该已被停用')

        wq.O_WQ_PANEL_TITLE.ocr = explode
        wq.O_WQ_REFRESH.ocr = explode
        task2 = FakeWQ()
        task2._wq2_ocr_state = {'empty': 3, 'resume_at': time.monotonic() + 60}
        check('D3 OCR 停用：判定不再走 OCR 探针',
              task2.wq2_panel_visible() is False, '')
    finally:
        wq.O_WQ_PANEL_TITLE.ocr = original_title
        wq.O_WQ_REFRESH.ocr = original_refresh


def test_wq2_panel_progress_probe(log: FakeLogger):
    """OCR 探针停用期间，进度横带读到 ≥2 个 a/b → 仍能确认面板。"""
    original_title = wq.O_WQ_PANEL_TITLE.ocr
    original_refresh = wq.O_WQ_REFRESH.ocr
    wq.O_WQ_PANEL_TITLE.ocr = lambda image: ''
    wq.O_WQ_REFRESH.ocr = lambda image: ''
    try:
        task = FakeWQ(progress=[(240, 1, 3, '1/3'), (510, 0, 2, '0/2')])
        task._wq2_ocr_state = {'empty': 3, 'resume_at': time.monotonic() + 60}
        check('D3 换探针：进度横带命中即认面板', task.wq2_panel_visible() is True)
        task_only_one = FakeWQ(progress=[(240, 1, 3, '1/3')])
        task_only_one._wq2_ocr_state = {'empty': 3, 'resume_at': time.monotonic() + 60}
        check('D3 换探针：只有 1 个进度不算面板（避免误判）',
              task_only_one.wq2_panel_visible() is False)
    finally:
        wq.O_WQ_PANEL_TITLE.ocr = original_title
        wq.O_WQ_REFRESH.ocr = original_refresh


def test_wq2_ensure_panel_gives_up(log: FakeLogger):
    """面板始终判不到 → 预算内返回 False（跳过本面板），LONG_WAIT 成对增删。"""
    original_sleep = wq.sleep
    wq.sleep = lambda seconds: None
    try:
        task = FakeWQ(panel_visible=False)
        started = time.monotonic()
        ok = wq.WantedQuestsUiV2.wq2_ensure_panel(task, tries=3, budget=0.05)
        elapsed = time.monotonic() - started
    finally:
        wq.sleep = original_sleep
    check('D3 跳过面板：返回 False', ok is False, f'{ok!r}')
    check('D3 跳过面板：在预算内放弃（不空转）', elapsed < 5.0, f'{elapsed:.2f}s')
    check('D3 跳过面板：LONG_WAIT 成对增删',
          task.device.calls == [('add', 'LONG_WAIT'), ('remove', 'LONG_WAIT')],
          str(task.device.calls))
    check('D3 跳过面板：记了 WARNING',
          '放弃本面板' in log.text('warning'), log.text('warning').splitlines()[-1]
          if log.records else '')


def test_wq2_ensure_panel_ok(log: FakeLogger):
    """面板一开始就判得到 → 直接 True（正常路径没有被改坏）。"""
    task = FakeWQ(panel_visible=True)
    ok = wq.WantedQuestsUiV2.wq2_ensure_panel(task, tries=1, budget=5.0)
    check('D3 跳过面板：正常路径返回 True', ok is True, f'{ok!r}')


# --------------------------------------------------------------- 快照证据复核（可跳过）
SNAPSHOT_EVIDENCE = {
    '1795178295813': ('续期（自动战斗中，画面在推进）',
                      '截图 2026-11-20_20-38-12：首领「伞剑」HP 33495/236397（约 14%）、'
                      '1/8 回合、左下「自动」开启、画面有 2950/2634 伤害数字；'
                      '20:32:32 打点 → 20:38:08 看门狗，336.6s。同一日志前一战 '
                      '20:28:43→20:31:52（189s）正常打赢，说明这场只是打得久'),
    '1794977024777': ('续期（但这是「跑不完的战斗」，真因另有其事）',
                      '截图 2026-11-18_12-43-43：首领 HP 51124/236397（约 21%）、2/8 回合，'
                      '左下是「手动」——战斗在等玩家输入；12:38:18 打点 → 12:43:16，297.2s。'
                      '截帧只能判「画面在动」→ 续期；真正该修的是「进战斗没切自动」'
                      '（battle_wait/general_battle，不属本任务范围）'),
    '1795179473096': ('续期（但这是「打不赢的战斗」，真因另有其事）',
                      '截图 2026-11-20_20-57-52：第 60 轮、底部 7 个技能位里 6 个显示「阵亡」、'
                      '「自动」开启；20:52:55 打点 → 20:57:52，296.8s，期间 20:55:52 还有 '
                      'unknown_page 事件。画面在推进（轮次在涨）→ 续期；'
                      '但第 60 轮阵亡 6/7 的局赢不了，续期 3 次（+15 分钟）后仍会抛'),
}


def verify_snapshot_evidence() -> None:
    """用快照 log.txt 复核「打点 → 看门狗」时长（文件不在就跳过，不算失败）。

    同一个 log.txt 里可能有多次战斗：只取**看门狗之前最后一次** `Add stuck record`
    作为打点时间，否则会被前面正常结束的战斗带跑。
    """
    from datetime import datetime

    fmt = '%Y-%m-%d %H:%M:%S.%f'
    for snapshot, (verdict, reason) in SNAPSHOT_EVIDENCE.items():
        log_path = ROOT / 'log' / 'error' / snapshot / 'log.txt'
        if not log_path.exists():
            print(f'[SKIP] 快照 {snapshot} 不存在（{verdict}）: {reason}')
            continue
        text = log_path.read_text(encoding='utf-8', errors='replace')
        start = end = None
        for line in text.splitlines():
            if 'Add stuck record: BATTLE_STATUS_S' in line:
                start = line[:23]
            if start is not None and 'Wait too long' in line:
                end = line[:23]
                break
        gap = None
        if start and end:
            gap = (datetime.strptime(end, fmt) - datetime.strptime(start, fmt)).total_seconds()
        ok = gap is not None and gap > 290 and 'GameStuckError: Wait too long' in text
        shown = 'N/A' if gap is None else f'{gap:.1f}'
        check(f'证据复核 {snapshot}：打点→看门狗 {shown}s', ok,
              f'{verdict} — {reason}')


# ------------------------------------------------------------------------------ main
def main() -> int:
    watchdog_log = FakeLogger()
    share_log = FakeLogger()
    wq_log = FakeLogger()
    originals = (device_module.logger, wt.logger, wq.logger)
    device_module.logger = watchdog_log
    wt.logger = share_log
    wq.logger = wq_log
    try:
        print('== 1. 看门狗续期（module/device/device.py） ==')
        test_watchdog_battle_keeps_alive(watchdog_log)
        test_watchdog_frozen_raises(watchdog_log)
        test_watchdog_renew_limit(watchdog_log)
        test_watchdog_registered_prober(watchdog_log)
        test_watchdog_normal_stuck_untouched(watchdog_log)
        test_watchdog_long_wait_token(watchdog_log)
        test_watchdog_empty_record_unchanged(watchdog_log)

        print('\n== 2. WeeklyTrifles 分享降级（tasks/WeeklyTrifles/script_task.py） ==')
        test_weekly_share_timeout(share_log)
        test_weekly_share_done_path(share_log)
        test_weekly_share_section_degrades(share_log)
        test_weekly_share_section_fatal(share_log)

        print('\n== 3. WQ2 面板探针降级（tasks/WantedQuests/ui_wq_v2.py） ==')
        test_wq2_panel_probe_backoff(wq_log)
        test_wq2_panel_progress_probe(wq_log)
        test_wq2_ensure_panel_gives_up(wq_log)
        test_wq2_ensure_panel_ok(wq_log)

        print('\n== 4. 快照证据复核（log/error/<ts>/log.txt） ==')
        verify_snapshot_evidence()
    finally:
        device_module.logger, wt.logger, wq.logger = originals

    failed = [item for item in RESULTS if not item[1]]
    print(f'\n合计 {len(RESULTS)} 项断言：通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}')
    if failed:
        for name, _, detail in failed:
            print(f'  FAILED: {name} {detail}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
