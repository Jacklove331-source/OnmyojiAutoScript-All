# This Python file uses the following encoding: utf-8

"""协作邀请流程探针：一步步点，每步把屏幕 OCR 打出来，确认新 UI 的真实交互。

用法：
    .\\toolkit\\python.exe dev_tools\\wq_invite_probe.py            # 走到「点好友」为止
    .\\toolkit\\python.exe dev_tools\\wq_invite_probe.py --send     # 继续点「邀请」并观察结果

每步都会把截图存到 log/wq_probe/（不能看图时靠 OCR 文本判断）。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.atom.ocr import RuleOcr  # noqa: E402
from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402

DUMP = ROOT / 'log' / 'wq_probe'
DUMP.mkdir(parents=True, exist_ok=True)

FULL = RuleOcr(roi=(0, 0, 1280, 720), area=(0, 0, 1280, 720),
               mode='Full', method='Default', keyword='', name='probe_full')


def snapshot(device, tag: str) -> list[dict]:
    image = device.screenshot()
    import cv2
    cv2.imwrite(str(DUMP / f'{time.strftime("%H%M%S")}_{tag}.png'),
                cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    try:
        items = FULL.detect_and_ocr(image)
    except Exception as error:  # noqa: BLE001
        print(f'  [OCR failed: {error!r}]')
        return []
    rows = []
    for item in items or []:
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
                     'x': x1, 'y': y1, 'w': x2 - x1, 'h': y2 - y1})
    print(f'== {tag}: {len(rows)} 块文本')
    for row in rows:
        print(f"   ({row['x']:>4},{row['y']:>3}) {row['text'][:36]}")
    return rows


def find(rows, needle):
    return next((row for row in rows if needle in row['text']), None)


def main() -> int:
    send = '--send' in sys.argv
    device = Device(Config('oas3'))
    device.click(85, 340, control_check=False)   # 町中 → 悬赏封印
    time.sleep(2.5)
    rows = snapshot(device, 'panel')
    entry = find(rows, '邀请好友')
    if not entry:
        print('没找到「邀请好友」入口')
        return 1
    print(f"→ 点邀请好友 ({entry['cx']},{entry['cy']})")
    device.click(entry['cx'], entry['cy'], control_check=False)
    time.sleep(3)
    rows = snapshot(device, 'invite_dialog')
    names = [row for row in rows
             if 220 <= row['x'] <= 500 and 170 <= row['y'] <= 520
             and '邀请' not in row['text'] and '好友' not in row['text']]
    print(f'== 左列候选好友 {len(names)}: '
          + ', '.join(f"{row['text']}@{row['cx']},{row['cy']}" for row in names[:6]))
    if not names:
        print('左列没识别到好友，停止')
        return 1
    target = names[0]
    print(f"→ 点好友 {target['text']} ({target['cx']},{target['cy']})")
    device.click(target['cx'], target['cy'], control_check=False)
    time.sleep(1.5)
    rows = snapshot(device, 'friend_selected')
    ensure = find(rows, '邀请')
    print(f"→ 「邀请」按钮: {ensure}")
    if not send:
        print('（未加 --send，停在选中状态）')
        return 0
    if not ensure:
        print('没找到邀请按钮，停止')
        return 1
    print(f"→ 点邀请 ({ensure['cx']},{ensure['cy']})")
    device.click(ensure['cx'], ensure['cy'], control_check=False)
    time.sleep(2)
    rows = snapshot(device, 'after_invite')
    print('== 邀请后是否还有弹窗:',
          'cancel=' + str(bool(find(rows, '取消'))),
          'ensure=' + str(bool(find(rows, '邀请'))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
