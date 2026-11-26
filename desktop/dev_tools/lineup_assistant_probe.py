"""离线探测：oas3 当前页面能否被阵容助手的 OCR 规则识别。"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402
from tasks.Component.LineupAssistant.assistant import LineupAssistantMixin  # noqa: E402
from tasks.GameUi.game_ui import GameUi  # noqa: E402


class Runner(LineupAssistantMixin, GameUi):
    pass


config = Config('oas3')
device = Device(config)
runner = Runner(config=config, device=device)
runner.screenshot()
image = device.image

probes = {
    '标题': ((40, 0, 420, 70), ''),
    '页签': ((20, 100, 80, 320), ''),
    '筛选': ((100, 70, 500, 130), ''),
    '空列表': ((330, 200, 950, 620), ''),
    '第一张卡计算': ((1100, 180, 1270, 300), ''),
}
from module.atom.ocr import RuleOcr

for name, (roi, keyword) in probes.items():
    rule = RuleOcr(roi=roi, area=(0, 0, 100, 100), mode='Full',
                   method='Default', keyword=keyword, name=name)
    print(name, '->', repr(rule.ocr(image))[:120])
