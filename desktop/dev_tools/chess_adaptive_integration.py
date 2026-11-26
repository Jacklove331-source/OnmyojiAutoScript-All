"""离线集成检查：随机应变走完整 get_lineup_strategy / 刷新流程。"""

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tasks.Chess.runtime.recognition import ChessRecognitionMixin  # noqa: E402
from tasks.Chess.strategy.lineup import LINEUP_REGISTRY  # noqa: E402


class Dummy(ChessRecognitionMixin):
    LINEUP_REGISTRY = LINEUP_REGISTRY
    DEFAULT_LINEUP_KEY = 'qijiaoshan'
    conf = SimpleNamespace(adaptive_meta_url='', adaptive_meta_cache_hours=12)
    _board_lineup_names = {'yuki_onna', 'zenhyou_setsunajo'}
    _current_round_no = 5

    def _hand_card_detections(self):
        return []

    def _read_shop_gold(self):
        return 37

    def _shikigami_display_name(self, name):
        return str(name)


dummy = Dummy()
print('adaptive?', dummy.is_adaptive_lineup())

strategy = dummy.select_lineup_strategy('随机应变')
print('selected:', strategy['key'], strategy['display_name'], strategy.get('bond'))
print('positions:', {k: v['position'] for k, v in strategy['shikigami'].items()})
print('deploy positions:', dummy.shikigami_deploy_positions)
print('final level:', dummy._lineup_final_level())
print('adaptive?', dummy.is_adaptive_lineup())
print('cached strategy same:', dummy.get_lineup_strategy() is strategy)
print('refresh (no change) same:', dummy.refresh_adaptive_strategy() is strategy)

# 局面变化：手牌突然来了三张海国
dummy._board_lineup_names = {'kanihime', 'umibouzu', 'kujira', 'ootakemaru'}
dummy._current_round_no = 9
refreshed = dummy.refresh_adaptive_strategy()
print('after state change:', refreshed['bond'], {k: v['position'] for k, v in refreshed['shikigami'].items()})

# 切回固定阵容
fixed = dummy.select_lineup_strategy('qijiaoshan')
print('switch back:', fixed['key'], len(fixed['shikigami']))
