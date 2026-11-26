"""离线检查「随机应变」决策：给定几种局面，看教练选什么。"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tasks.Chess.strategy.adaptive import (  # noqa: E402
    Observation,
    build_strategy,
    describe,
    plan,
)
from tasks.Chess.strategy.grigri import lineup_bond_context  # noqa: E402


def show(title, observation):
    result = plan(observation)
    strategy = build_strategy(result)
    ctx = lineup_bond_context(strategy)
    print(f'== {title}')
    print('  ', describe(result))
    print('   positions:',
          {k: v['position'] for k, v in strategy['shikigami'].items()})
    print('   bond ctx: primary=%s(%s) secondary=%s yixing=%s' % (
        ctx['primary'], ctx['primary_count'], sorted(ctx['secondary']),
        ctx['has_yixing']))
    print('   roster size:', len(strategy['shikigami']))
    print()


show('空局面', Observation())

# 手牌已经摸到三张寒霜，应当主攻寒霜
show('手里三张寒霜', Observation(
    hand=frozenset({'yuki_onna', 'zenhyou_setsunajo', 'keisei_chihime'}),
))

# 对手四家都在抢寒霜，应当避开
show('对手抢寒霜', Observation(
    hand=frozenset({'yuki_onna', 'zenhyou_setsunajo'}),
    opponents={'寒霜': 4, '易形': 3},
))

# 已有投入（4 张）时保持惯性，不轻易换
show('已投入寒霜4张', Observation(
    owned=frozenset({'yuki_onna', 'zenhyou_setsunajo', 'keisei_chihime', 'yuki_gozen'}),
    current_primary='寒霜',
))

# 商店只有海国卡
show('商店全是海国', Observation(
    shop=('kanihime', 'umibouzu', 'kujira', 'ootakemaru', 'suzuka_gozen'),
))
