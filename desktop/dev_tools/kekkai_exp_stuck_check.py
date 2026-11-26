# This Python file uses the following encoding: utf-8

"""结界「经验箱提取」防连点的免设备验证。

背景（2026-11-23 11:48，oas3）：`BOX_EXP` 一直停在 59374/160000，`I_EXP_EXTRACT` 却每轮都命中，
于是每轮点一次「提取」，连点 10 次 → `GameTooManyClickError: Too many click for a button:
REALM_EXP_EXTRACT`，整个结界任务报错（log/error/1795405701268）。

`exp_extract_stuck()` 的判据是「自上次见到真实进展以来的轮数」：cur 明显下降（提取生效）或明显
上升（经验箱被灌满）才算变化，OCR 抖动/不动/读不到都累加计数，到上限就叫停。

用法：
    .\\toolkit\\python.exe dev_tools\\kekkai_exp_stuck_check.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.KekkaiUtilize.script_task import (  # noqa: E402
    EXP_EXTRACT_CLICK_LIMIT,
    EXP_PROGRESS_MIN_DELTA,
    exp_extract_stuck,
)


def main() -> int:
    failures = []

    def check(name, ok, detail=''):
        print(f"   [{'PASS' if ok else 'FAIL'}] {name} {detail}")
        if not ok:
            failures.append(name)

    EXP = 160000
    base = (59374, EXP - 59374, EXP)
    limit = EXP_EXTRACT_CLICK_LIMIT
    delta = EXP_PROGRESS_MIN_DELTA

    print('== 基线建立')
    baseline, count, stuck = exp_extract_stuck(base, None, 0)
    check('首个有效读数成为基线、不计数', (baseline, count, stuck) == (59374, 0, False),
          str((baseline, count, stuck)))

    print('== 读数不动（本次要修的真实场景）')
    baseline, count, stuck = exp_extract_stuck(base, 59374, 0)
    check('同读数计数 +1', (count, stuck) == (1, False), str((count, stuck)))
    baseline, count, stuck = exp_extract_stuck(base, 59374, limit - 1)
    check(f'到上限 {limit} 判卡住', stuck is True, str((count, stuck)))

    print('== OCR 抖动（±1 / ±20）不该清零')
    for jitter in (-1, 1, -20, 20):
        reading = (base[0] + jitter, base[1], EXP)
        baseline, count, stuck = exp_extract_stuck(reading, 59374, limit - 1)
        check(f'抖动 {jitter:+d} 仍判卡住', stuck is True, str((baseline, count, stuck)))

    print('== 真实进展')
    lower = (59374 - delta - 1, EXP, EXP)
    baseline, count, stuck = exp_extract_stuck(lower, 59374, limit - 1)
    check('cur 明显下降 → 清零并跟随基线',
          (baseline, count, stuck) == (lower[0], 0, False), str((baseline, count, stuck)))
    higher = (59374 + delta + 1, EXP, EXP)
    baseline, count, stuck = exp_extract_stuck(higher, 59374, limit - 1)
    check('cur 明显上升（箱被灌满）→ 清零并换基线',
          (baseline, count, stuck) == (higher[0], 0, False), str((baseline, count, stuck)))
    edge = (59374 - delta, EXP, EXP)
    baseline, count, stuck = exp_extract_stuck(edge, 59374, limit - 1)
    check(f'刚好差 {delta} 算变化（<= 边界）',
          (baseline, count, stuck) == (edge[0], 0, False), str((baseline, count, stuck)))

    under = (59374 - delta + 1, EXP, EXP)
    baseline, count, stuck = exp_extract_stuck(under, 59374, limit - 1)
    check(f'差 {delta - 1} 仍算没进展', stuck is True, str((baseline, count, stuck)))
    baseline, count, stuck = exp_extract_stuck(('59323', EXP, EXP), 59374, 0)
    check('字符串数字按 int 解析并识别为进展', (baseline, count, stuck) == (59323, 0, False), str((baseline, count, stuck)))
    print('== 读不到数字（保护最该生效的场景）')
    baseline, count, stuck = exp_extract_stuck(None, 59374, limit - 1)
    check('None 也累加计数并保留基线', stuck is True and baseline == 59374,
          str((baseline, count, stuck)))
    baseline, count, stuck = exp_extract_stuck(('', None, None), 59374, limit - 1)
    check('空串/None 分量同样累加', stuck is True and baseline == 59374,
          str((baseline, count, stuck)))
    baseline, count, stuck = exp_extract_stuck(None, None, 0)
    check('还没基线时读不到 → 不计数、不抛', (baseline, count, stuck) == (None, 0, False),
          str((baseline, count, stuck)))

    print('== 上限必须小于设备熔断阈值（同一按钮 10 次）')
    check('EXP_EXTRACT_CLICK_LIMIT < 10', limit < 10, str(limit))

    print(f'RESULT {"PASS" if not failures else "FAIL"} ({len(failures)} failed)')
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
