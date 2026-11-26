# This Python file uses the following encoding: utf-8

"""悬赏封印「新版界面」适配的实机验证：面板判定兜底 + 前往探索直达。

覆盖本轮新加的两处（借鉴上游 PR #1987）：
1. `wq2_panel_visible()` 的模板兜底 `_wq2_panel_by_templates()`：
   面板上应能看到「追踪」按钮，且没有详情/邀请弹窗；
2. `_find_exploration_button()` / `wq2_goto_exploration()`：
   面板右下角能 OCR 到「前往探索」，点击后到达探索页（`I_CHECK_EXPLORATION`）。

用法：
    .\\toolkit\\python.exe dev_tools\\wq_v2_live_test.py [config]
前置：游戏已登录并停在主界面（可先跑 dev_tools/oas_home.py）。
"""

import sys
from pathlib import Path
from time import sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402
from tasks.WantedQuests.script_task import ScriptTask  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def _click_text(task, needle: str) -> bool:
    """全屏 OCR 找文本并点击其中心（用于「追踪」这类面板按钮）。"""
    from module.atom.ocr import RuleOcr as _RuleOcr
    scan = _RuleOcr(roi=(0, 0, 1280, 720), area=(0, 0, 1280, 720),
                    mode='Full', method='Default', keyword='', name='live_probe')
    task.screenshot()
    for item in scan.detect_and_ocr(task.device.image) or []:
        if needle not in str(getattr(item, 'ocr_text', '') or ''):
            continue
        box = getattr(item, 'box', None)
        if box is None:
            continue
        x = (int(box[0][0]) + int(box[2][0])) // 2
        y = (int(box[0][1]) + int(box[2][1])) // 2
        print(f'   点击「{needle}」({x},{y})')
        task.device.click(x, y, control_check=False)
        return True
    return False


def main() -> int:
    config_name = sys.argv[1] if len(sys.argv) > 1 else 'oas3'
    config = Config(config_name)
    device = Device(config)
    task = ScriptTask(config, device)

    print('== 0. 归一化到主界面（用导航器，避免乱按）')
    from tasks.GameUi.page import page_main as _page_main
    for attempt in range(3):
        task.screenshot()
        if task.wq2_panel_visible():
            print('   已在悬赏面板，按返回退回')
            task.device.adb_shell(['input', 'keyevent', '4'])
            sleep(1.8)
            continue
        if task.appear(task.I_CHECK_MAIN):
            break
        try:
            task.goto_page(_page_main)
        except Exception as error:  # noqa: BLE001
            print(f'   goto_page(page_main) 失败：{type(error).__name__}: {error}')
            sleep(1.5)
    task.screenshot()

    print('== 1. 主界面基线')
    on_main = task.appear(task.I_CHECK_MAIN)
    check('当前在主界面', on_main)
    check('主界面不应被认成悬赏面板', not task.wq2_panel_visible())

    print('== 2. 打开悬赏面板（OCR 入口）')
    opened = task.wq2_open_panel()
    check('面板已打开（wq2_open_panel）', opened)

    print('== 3. 面板判定（OCR 与模板两条路径）')
    check('wq2_panel_visible 判定为面板', task.wq2_panel_visible())
    check('模板兜底 _wq2_panel_by_templates 也判定为面板',
          task._wq2_panel_by_templates())

    print('== 3.5 任务卡解析（新版四张）')
    cards = task.wq2_cards()
    check('读到四张任务卡', len(cards) == 4, str(len(cards)))
    parsed = [c for c in cards if c['current'] is not None and c['total']]
    check('至少三张卡进度可解析', len(parsed) >= 3,
          str([(c['index'], c['progress_text']) for c in cards]))

    print('== 4. 点「追踪」后出现「前往探索」')
    # 面板初始只有「追踪」；追踪后才出现「前往探索」入口
    task.screenshot()
    point = task._find_exploration_button() if task.wq2_panel_visible() else None
    if point is None and task.wq2_panel_visible():
        tracked = _click_text(task, '追踪')
        check('点击面板「追踪」', tracked)
        for _ in range(6):
            sleep(1.5)
            task.screenshot()
            point = task._find_exploration_button()
            if point is not None:
                break
    check('面板上找到「前往探索」按钮', point is not None, str(point))

    print('== 5. 直达探索页')
    arrived = task.wq2_goto_exploration()
    check('点击「前往探索」后到达探索页', arrived)
    check('探索页标志 I_CHECK_EXPLORATION 命中',
          task.appear(task.I_CHECK_EXPLORATION))

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('WQ V2 LIVE: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
