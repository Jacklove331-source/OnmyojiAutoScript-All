# This Python file uses the following encoding: utf-8

"""探针：从「町中」底部导航点开「组队」，把新组队页面的文本块打出来。

用途：page_team 的检测模板（page_check_team.png）在新 UI 上不再命中，
需要先看清组队页长什么样，才能决定是更新模板还是改入口。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.atom.ocr import RuleOcr  # noqa: E402
from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402

FULL = RuleOcr(roi=(0, 0, 1280, 720), area=(0, 0, 1280, 720),
               mode='Full', method='Default', keyword='', name='probe_full')


def boxes(device):
    image = device.screenshot()
    try:
        items = FULL.detect_and_ocr(image) or []
    except Exception as error:  # noqa: BLE001
        print(f'OCR failed: {error!r}')
        return []
    rows = []
    for item in items:
        text = str(getattr(item, 'ocr_text', '') or '').strip()
        box = getattr(item, 'box', None)
        if not text or box is None:
            continue
        try:
            x1, y1 = int(box[0][0]), int(box[0][1])
            x2, y2 = int(box[2][0]), int(box[2][1])
        except (IndexError, TypeError):
            continue
        rows.append({'text': text, 'cx': (x1 + x2) // 2, 'cy': (y1 + y2) // 2,
                     'x': x1, 'y': y1})
    return rows


def find(rows, needle):
    return next((row for row in rows if needle in row['text']), None)


def main() -> int:
    device = Device(Config('oas3'))
    for attempt in range(8):
        rows = boxes(device)
        joined = ' '.join(row['text'] for row in rows)
        print(f'--- 第{attempt + 1}次: {len(rows)} 块 | 关键: '
              + ', '.join(t for t in ('町中', '组队', '取消', '退出游戏', '庭院')
                          if t in joined))
        cancel = find(rows, '取消')
        if cancel and ('退出游戏' in joined or '确认' in joined):
            print('   关闭确认弹窗')
            device.click(cancel['cx'], cancel['cy'], control_check=False)
            time.sleep(2)
            continue
        team = find(rows, '组队')
        if team is None:
            print('   当前屏没有「组队」，等 3 秒重试')
            time.sleep(3)
            continue
        print(f"   点击 组队 ({team['cx']},{team['cy']})")
        device.click(team['cx'], team['cy'], control_check=False)
        time.sleep(3)
        after = boxes(device)
        print(f'   组队页文本块 {len(after)}:')
        for row in after[:26]:
            print(f"     ({row['x']:>4},{row['y']:>3}) {row['text'][:40]}")
        image = device.screenshot()
        import cv2
        cv2.imwrite(str(ROOT / 'log' / 'wq_probe' / f'team_{time.strftime("%H%M%S")}.png'),
                    cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        return 0
    print('未能在 8 次内点到组队')
    return 1


if __name__ == '__main__':
    sys.exit(main())
