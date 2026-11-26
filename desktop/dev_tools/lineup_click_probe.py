"""隔离测试：runner 的点击是否生效（点开阵容助手筛选弹窗）。"""

import os
import sys
import time

import cv2

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
cv2.imwrite('log/lineup_capture/runner_before.png', device.image)
print('before saved')

device.click(365, 99)
time.sleep(2)
runner.screenshot()
cv2.imwrite('log/lineup_capture/runner_after.png', device.image)
print('after saved')
