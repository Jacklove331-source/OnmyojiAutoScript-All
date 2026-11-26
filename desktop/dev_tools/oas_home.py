# This Python file uses the following encoding: utf-8

"""把游戏恢复到主界面（页 main），供手动验证/测试前清理现场。

用法：.\\toolkit\\python.exe dev_tools\\oas_home.py [config]

行为：最多 8 轮——识别到主界面标志即返回成功；遇到「退出游戏」弹窗点取消；
其余情况按返回键；最后打印当前页标志命中情况。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.atom.ocr import RuleOcr  # noqa: E402
from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402
from tasks.GameUi.assets import GameUiAssets as A  # noqa: E402

FULL = RuleOcr(roi=(0, 0, 1280, 720), area=(0, 0, 1280, 720),
               mode='Full', method='Default', keyword='', name='home_probe')


def dialog_cancel_point(device):
    try:
        items = FULL.detect_and_ocr(device.screenshot()) or []
    except Exception:  # noqa: BLE001
        return None
    boxes = {}
    for item in items:
        text = str(getattr(item, 'ocr_text', '') or '').strip()
        box = getattr(item, 'box', None)
        if text and box is not None:
            boxes.setdefault(text, box)
    joined = ' '.join(boxes)
    if '退出游戏' not in joined and '是否再次打开' not in joined:
        return None
    target = boxes.get('取消')
    if target is None:
        target = boxes.get('确认')
    if target is None:
        return None
    return ((int(target[0][0]) + int(target[2][0])) // 2,
            (int(target[0][1]) + int(target[2][1])) // 2)


def main() -> int:
    config_name = sys.argv[1] if len(sys.argv) > 1 else 'oas3'
    device = Device(Config(config_name))
    for attempt in range(8):
        image = device.screenshot()
        # 注意顺序：弹窗盖在主界面上时 I_CHECK_MAIN 依然匹配，必须先判弹窗，
        # 否则会误报「已在主界面」而把弹窗留在那里，后续点击全部无效。
        point = dialog_cancel_point(device)
        if point:
            print(f'第 {attempt + 1} 轮：关闭弹窗 {point}')
            device.click(point[0], point[1], control_check=False)
            time.sleep(1.2)
            continue
        if A.I_CHECK_MAIN.match(image, threshold=A.I_CHECK_MAIN.threshold):
            print(f'第 {attempt + 1} 轮：已在主界面')
            return 0
        if A.I_CHECK_TEAM.match(image, threshold=A.I_CHECK_TEAM.threshold):
            print(f'第 {attempt + 1} 轮：在组队页，按返回')
        else:
            print(f'第 {attempt + 1} 轮：未知页面，按返回')
        device.adb_shell(['input', 'keyevent', '4'])
        time.sleep(2.0)
    image = device.screenshot()
    print('未能回到主界面，当前:',
          'main=', A.I_CHECK_MAIN.match(image, threshold=A.I_CHECK_MAIN.threshold),
          'team=', A.I_CHECK_TEAM.match(image, threshold=A.I_CHECK_TEAM.threshold))
    return 1


if __name__ == '__main__':
    sys.exit(main())
