"""离线检查对手阵容侦察：用真实战斗截图跑模板匹配。"""

import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tasks.Chess.runtime.scout import ChessScoutMixin  # noqa: E402
from tasks.Chess.strategy.shikigami_catalog import SHIKIGAMI_BY_ROMAJI  # noqa: E402


class Dummy(ChessScoutMixin):
    def __init__(self, image):
        self.device = type('Dev', (), {'image': image})()

    def screenshot(self):
        pass


for name in ('battle2_scan_20261025_060838.png', 'battle_scan_20261025_060830.png',
             'scan_20261025_060708.png'):
    path = os.path.join(ROOT, 'log', 'chess_ref', name)
    if not os.path.exists(path):
        path = os.path.join(ROOT, 'log', 'lineup_capture', name)
    if not os.path.exists(path):
        print(name, 'missing')
        continue
    dummy = Dummy(cv2.imread(path))
    lineup = dummy.scout_opponent_lineup()
    display = [SHIKIGAMI_BY_ROMAJI[r].chinese_name for r in lineup]
    bonds = dummy.bonds_from_lineup(lineup)
    print(f'{name}: lineup={display} bonds={bonds}')
