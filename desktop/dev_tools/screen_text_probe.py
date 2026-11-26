# This Python file uses the following encoding: utf-8

"""把整屏按三条横带做 Single 模式 OCR，打印文本（不能看图时的「眼睛」）。

Single 模式返回字符串（与任务里的名称 OCR 同一链路）；Full 模式返回的是检测框，
不能用来读文本。

用法：
    .\\toolkit\\python.exe dev_tools\\screen_text_probe.py [config]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.atom.ocr import RuleOcr  # noqa: E402
from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402


def main() -> int:
    config_name = sys.argv[1] if len(sys.argv) > 1 else 'oas3'
    device = Device(Config(config_name))
    image = device.screenshot()
    print('frame', getattr(image, 'shape', None))
    height, width = image.shape[:2]
    bands = (
        ('top', 0, height // 3),
        ('mid', height // 3, height * 2 // 3),
        ('bottom', height * 2 // 3, height),
    )
    for name, top, bottom in bands:
        rule = RuleOcr(
            roi=(0, top, width, bottom - top),
            area=(0, top, width, bottom - top),
            mode='Single',
            method='Default',
            keyword='',
            name=f'probe_{name}',
        )
        try:
            text = rule.ocr(image)
        except Exception as error:  # noqa: BLE001
            text = f'<ocr error {type(error).__name__}>'
        print(f'{name}: {str(text).strip()[:200]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
