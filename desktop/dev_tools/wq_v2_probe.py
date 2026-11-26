# This Python file uses the following encoding: utf-8

"""悬赏封印新 UI 适配的独立验证：读面板 → 读任务卡 → 打开详情 → 读推荐行。

不点「前往」（避免真的进战斗），只验证识别与坐标是否正确。

用法：
    .\\toolkit\\python.exe dev_tools\\wq_v2_probe.py           # 只读验证
    .\\toolkit\\python.exe dev_tools\\wq_v2_probe.py --click   # 额外点一次第一条「前往」
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402
from tasks.WantedQuests.script_task import ScriptTask  # noqa: E402


def main() -> int:
    config_name = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else 'oas3'
    do_click = '--click' in sys.argv
    config = Config(config_name)
    device = Device(config)
    task = ScriptTask(config, device)

    print('== 打开面板')
    ok = task.wq2_open_panel()
    print(f'   panel_visible={ok}')
    if not ok:
        print('   打不开面板：请确认当前在町中/庭院')
        return 1

    print('== 任务卡')
    cards = task.wq2_cards()
    for card in cards:
        print(f"   [{card['index']}] text=[{card['text']}] "
              f"progress=[{card['progress_text']}] "
              f"({card['current']}/{card['total']}) center={card['center']}")

    unfinished = task.wq2_unfinished_cards()
    print(f'   未完成 {len(unfinished)} 张')
    if not unfinished:
        print('   没有未完成任务，结束')
        return 0

    card = unfinished[0]
    print(f"== 打开第 {card['index']} 张卡（{card['text']}）")
    if not task.wq2_open_card(card):
        print('   详情弹窗没打开')
        return 1

    print('== 推荐地点行')
    rows = task.wq2_rows()
    for row in rows:
        print(f"   row={row['row']} type=[{row['type']}] info=[{row['info']}] "
              f"dest=[{row['destination']}] number={row['number']} "
              f"goto={row['goto']}")
    if not rows:
        print('   没读到推荐行（坐标可能需要微调）')

    if do_click and rows:
        target = next((row for row in rows if row['goto']), None)
        if target:
            print(f"== 点击前往 row={target['row']} goto={target['goto']}")
            task.wq2_click_goto(target['goto'])
            task.screenshot()
            print('   点击后页面已变化（新页面截图已抓取）')

    task.wq2_close_detail()
    print('== 完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
