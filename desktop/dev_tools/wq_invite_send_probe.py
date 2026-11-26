# This Python file uses the following encoding: utf-8

"""协作邀请「选中 → 点邀请 → 看结果」的最小探查（固定按钮坐标）。"""

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
C_ENSURE = (536, 557)
FRIEND = (271, 209)


def dump(device, tag):
    image = device.screenshot()
    try:
        items = FULL.detect_and_ocr(image) or []
    except Exception as error:  # noqa: BLE001
        print(f'{tag}: OCR failed {error!r}')
        return []
    texts = [str(getattr(item, 'ocr_text', '') or '').strip() for item in items]
    texts = [text for text in texts if text]
    print(f'== {tag}: {len(texts)} 块')
    print('   ' + ' | '.join(texts[:24]))
    return texts


def main():
    device = Device(Config('oas3'))
    dump(device, 'before')
    print(f'→ 点好友 {FRIEND}')
    device.click(*FRIEND, control_check=False)
    time.sleep(1.2)
    dump(device, 'after_select')
    print(f'→ 点邀请 {C_ENSURE}')
    device.click(*C_ENSURE, control_check=False)
    time.sleep(2.5)
    texts = dump(device, 'after_invite')
    joined = ' '.join(texts)
    print('== 判定:',
          'still_dialog=' + str('邀请7级以上好友' in joined),
          'toast=' + str('已' in joined and ('邀请' in joined or '发送' in joined)),
          'back_panel=' + str('下次刷新' in joined))
    image = device.screenshot()
    import cv2
    cv2.imwrite(str(ROOT / 'log' / 'wq_probe' / f'{time.strftime("%H%M%S")}_final.png'),
                cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    return 0


if __name__ == '__main__':
    sys.exit(main())
