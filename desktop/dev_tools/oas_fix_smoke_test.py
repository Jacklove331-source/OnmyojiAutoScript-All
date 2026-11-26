# This Python file uses the following encoding: utf-8

"""修复后的接口级冒烟测试（不启动任务、不改动游戏状态）。

验证 reset 事故修复项在真实设备截图上可用：
- Device.image_frame_id 计数
- RuleImage.match / match_all / match_all_any 接受 frame_id
- 棋局相关属性与方法存在
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402


def main() -> int:
    config = Config('oas3')
    device = Device(config)
    image = device.screenshot()

    first = device.image_frame_id
    assert isinstance(first, int), f'image_frame_id 不是 int: {first!r}'
    device.screenshot()
    second = device.image_frame_id
    assert second > first, f'image_frame_id 未自增: {first} -> {second}'
    print(f'[ok] image_frame_id: {first} -> {second}')

    from tasks.GameUi.page_check_assets import PageCheckAssets
    rule = PageCheckAssets.I_CHECK_CHESS
    ok = rule.match(image, frame_id=second)
    all1 = rule.match_all(image, frame_id=second)
    all2 = rule.match_all_any(image, frame_id=second)
    print(f'[ok] match(frame_id)= {ok}, match_all= {len(all1)}, match_all_any= {len(all2)}')

    from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
    from tasks.GameUi.assets import GameUiAssets
    assert hasattr(GeneralBattleAssets, 'I_EXIT_ENSURE'), 'I_EXIT_ENSURE 缺失'
    assert hasattr(GameUiAssets, 'I_CHESS_EXIT'), 'I_CHESS_EXIT 缺失'
    print('[ok] I_EXIT_ENSURE / I_CHESS_EXIT 存在')

    from tasks.Chess.script_task import ScriptTask as ChessTask
    for name in ('chess_result_flow_visible', 'runframe', 'ui_click_until_appear',
                 'ui_click_until_appear_or_timeout', 'I_CHESS_EXIT'):
        assert hasattr(ChessTask, name), f'Chess 缺少 {name}'
    print('[ok] Chess 任务关键属性/方法齐全')

    from tasks.MartialArts.script_task import ScriptTask as MartialArtsTask
    assert hasattr(MartialArtsTask, 'verify_zero_ticket'), 'MartialArts 缺少 verify_zero_ticket'
    from tasks.LBS.script_task import ScriptTask as LBSTask
    assert hasattr(LBSTask, 'room_check_can_fire'), 'LBS 缺少 room_check_can_fire'
    print('[ok] MartialArts.verify_zero_ticket / LBS.room_check_can_fire 存在')

    from tasks.GameUi.navigator import GameUi
    assert hasattr(GameUi, 'close_unknown_pages'), 'close_unknown_pages 缺失'
    print('[ok] GameUi 导航兜底可用')

    print('SMOKE TEST PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
