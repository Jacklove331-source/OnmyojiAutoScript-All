# This Python file uses the following encoding: utf-8

"""集体任务「材料不足」收尾的免设备验证。

背景（2026-11-23 14:37，oas1）：_donate 读到四行材料 21/26/29/26，上游直接
`raise RequestHumanTakeover` → `script oas1 process exit (Error: 1)`，当天后面所有任务
都不再执行。改成「skip 收尾（success=False + finish=True + TaskEnd）」后，这个分支
必须能在没有设备的情况下被证明：不抛 RequestHumanTakeover、不落进捐材料逻辑、
按预期参数记账、并尝试关闭提交框。

用法：
    .\\toolkit\\python.exe dev_tools\\collective_missions_skip_check.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.exception import RequestHumanTakeover, TaskEnd  # noqa: E402
from tasks.CollectiveMissions.script_task import (  # noqa: E402
    MIN_MATTER_TO_DONATE,
    ScriptTask,
    matter_verdict,
)


class _MatterOcr:
    def __init__(self, total):
        self.total = total

    def ocr(self, image):
        if self.total is None:
            return None, None, None
        return 0, self.total, self.total


def _stub_task(readings, on_set_next_run, close_calls):
    """构造一个只够跑 _donate 前半段的替身：不发 adb、不点屏幕、不读写 config。"""
    task = ScriptTask.__new__(ScriptTask)
    task.device = SimpleNamespace(image=None)
    task.screenshot = lambda *a, **k: None
    task.appear = lambda rule, **k: True          # I_CM_PRESENT / 收尾判据都当成可见
    task.click = lambda rule, **k: False
    task.appear_then_click = lambda rule, **k: False
    task.set_next_run = on_set_next_run
    task.O_CM_1_MATTER = _MatterOcr(readings[0])
    task.O_CM_2_MATTER = _MatterOcr(readings[1])
    task.O_CM_3_MATTER = _MatterOcr(readings[2])
    task.O_CM_4_MATTER = _MatterOcr(readings[3])
    for name in ('C_CM_1', 'C_CM_2', 'C_CM_3', 'I_CM_PRESENT', 'I_CM_RECORDS',
                 'I_CM_SHRINE', 'I_CHECK_MAIN', 'I_UI_BACK_RED', 'I_UI_BACK_YELLOW'):
        setattr(task, name, SimpleNamespace(name=name))
    # 记录「有没有尝试关闭提交框」：真实现会走 _cm_close_dialogs
    real_close = task._cm_close_dialogs.__get__(task)

    def close(tries=8, budget=15.0):
        close_calls.append({'tries': tries, 'budget': budget})
        return real_close(tries=tries, budget=budget)

    task._cm_close_dialogs = close
    return task


def main() -> int:
    failures = []

    def check(name, ok, detail=''):
        print(f"   [{'PASS' if ok else 'FAIL'}] {name} {detail}")
        if not ok:
            failures.append(name)

    print('== 纯函数 matter_verdict')
    check('21/26/29/26 -> skip',
          matter_verdict([21, 26, 29, 26]) == ('skip', 29), str(matter_verdict([21, 26, 29, 26])))
    check('0/0/0/0 真没材料 -> skip（不能拖死进程）',
          matter_verdict([0, 0, 0, 0]) == ('skip', 0), str(matter_verdict([0, 0, 0, 0])))
    check('31/0/0/0 -> donate',
          matter_verdict([31, 0, 0, 0]) == ('donate', 31), str(matter_verdict([31, 0, 0, 0])))
    check('恰好 30 -> skip（需 >30 才能捐满）',
          matter_verdict([30, 30, 30, 30]) == ('skip', 30), str(matter_verdict([30, 30, 30, 30])))
    check('四行 unreadable -> takeover',
          matter_verdict([None, None, None, None]) == ('takeover', 0),
          str(matter_verdict([None, None, None, None])))
    check('混合 [None,0,0,0] -> skip（有读数、只是 0）',
          matter_verdict([None, 0, 0, 0]) == ('skip', 0),
          str(matter_verdict([None, 0, 0, 0])))
    check('混合 [None,31,None,0] -> donate',
          matter_verdict([None, 31, None, 0]) == ('donate', 31),
          str(matter_verdict([None, 31, None, 0])))
    check('四行全 0 -> skip（今天真没材料也要优雅收尾，不能带走进程）',
          matter_verdict([0, 0, 0, 0]) == ('skip', 0),
          str(matter_verdict([0, 0, 0, 0])))
    check(f'阈值常量 = 30', MIN_MATTER_TO_DONATE == 30, str(MIN_MATTER_TO_DONATE))

    print('== 真实类上这些资产名必须存在（防止替身把 AttributeError 掩盖掉）')
    for name in ('I_CM_PRESENT', 'I_CM_RECORDS', 'I_CM_SHRINE', 'I_CHECK_MAIN',
                 'I_UI_BACK_RED', 'I_UI_BACK_YELLOW', 'C_CM_1', 'C_CM_2', 'C_CM_3',
                 'O_CM_1_MATTER', 'O_CM_2_MATTER', 'O_CM_3_MATTER', 'O_CM_4_MATTER'):
        check(f'ScriptTask.{name} 存在', hasattr(ScriptTask, name))

    print('== skip 分支（_donate 前半段，材料 21/26/29/26）')
    calls, close_calls = [], []
    task = _stub_task([21, 26, 29, 26], lambda **kwargs: calls.append(kwargs), close_calls)
    raised = None
    try:
        ScriptTask._donate(task, 0)
    except BaseException as error:  # noqa: BLE001 - 这里就是要看抛了什么
        raised = error
    check('抛 TaskEnd（不是 RequestHumanTakeover）', isinstance(raised, TaskEnd),
          type(raised).__name__ if raised else 'nothing raised')
    check('没有 RequestHumanTakeover', not isinstance(raised, RequestHumanTakeover))
    check('set_next_run 恰好一次且 success=False/finish=True',
          calls == [{'task': 'CollectiveMissions', 'success': False, 'finish': True}], str(calls))
    check('尝试关闭提交框', len(close_calls) == 1, str(close_calls))

    print('== 全 0 分支（重读 2 次仍全 0 -> 优雅 skip，不 RequestHumanTakeover）')
    calls2, close_calls2 = [], []
    task2 = _stub_task([0, 0, 0, 0], lambda **kwargs: calls2.append(kwargs), close_calls2)
    raised2 = None
    try:
        ScriptTask._donate(task2, 0)
    except BaseException as error:  # noqa: BLE001
        raised2 = error
    check('抛 TaskEnd（全 0 也不拖死进程）', isinstance(raised2, TaskEnd),
          type(raised2).__name__ if raised2 else 'nothing raised')
    check('没有 RequestHumanTakeover', not isinstance(raised2, RequestHumanTakeover))
    check('set_next_run 恰好一次且 success=False/finish=True',
          calls2 == [{'task': 'CollectiveMissions', 'success': False, 'finish': True}], str(calls2))
    check('全 0 也尝试关闭提交框', len(close_calls2) == 1, str(close_calls2))

    print('== 全部读不出数字（None）-> 仍 RequestHumanTakeover，且不记账')
    calls4 = []
    task4 = _stub_task([None, None, None, None], lambda **kwargs: calls4.append(kwargs), [])
    raised4 = None
    try:
        ScriptTask._donate(task4, 0)
    except BaseException as error:  # noqa: BLE001
        raised4 = error
    check('raise RequestHumanTakeover（截图/ROI/分辨率真失效）',
          isinstance(raised4, RequestHumanTakeover),
          type(raised4).__name__ if raised4 else 'nothing raised')
    check('takeover 分支不写 next_run', calls4 == [], str(calls4))

    print('== 关弹窗失败时不抛异常、返回 False')
    task3 = _stub_task([21, 26, 29, 26], lambda **kwargs: None, [])
    task3.appear = lambda rule, **k: False
    task3.appear_then_click = lambda rule, **k: False
    closed = task3._cm_close_dialogs(tries=2, budget=0.1)
    check('返回 False 且不抛异常', closed is False, repr(closed))

    print(f'RESULT {"PASS" if not failures else "FAIL"} ({len(failures)} failed)')
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
