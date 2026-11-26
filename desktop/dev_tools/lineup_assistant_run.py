"""实测：在 oas3 上跑一遍游戏内「阵容助手」流程。"""

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


config_name = os.environ.get('OAS_DEV_CONFIG', 'oas3')
config = Config(config_name)
device = Device(config)
runner = Runner(config=config, device=device)
ok = runner.apply_from_assistant('Orochi')
print('apply_from_assistant ->', ok)
