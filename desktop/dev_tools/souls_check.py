"""离线检查御魂配装计算：套装筛选、主属性匹配、替代降级、速度顺序（不联网）。"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from module.lineup.souls import Soul, best_plan, check_speed_order  # noqa: E402

inv = [
    Soul('a1', '破势', 2, 15, '速度', 57, {'暴击': 8, '攻击加成': 5}),
    Soul('a2', '破势', 2, 15, '攻击加成', 55, {'速度': 3}),
    Soul('a3', '破势', 4, 15, '攻击加成', 55, {'暴击': 9}),
    Soul('a4', '破势', 6, 15, '暴击', 55, {'暴击伤害': 6}),
    Soul('b1', '针女', 4, 15, '效果命中', 55, {'速度': 6}),
]

plan = best_plan('破势', {2: '速度', 4: '攻击加成', 6: '暴击'}, inv, min_speed=50)
print('破势 plan:', plan.describe(), '| missing:', plan.missing, '| score:', plan.score)
assert plan.speed() >= 50 and not plan.missing, plan.missing

# 缺六号位暴击时降级并记录
plan2 = best_plan('破势', {2: '速度', 4: '攻击加成', 6: '暴击伤害'}, inv)
print('降级 plan:', plan2.describe(), '| missing:', plan2.missing)
assert plan2.missing

# 套装不存在 → 明确缺失
plan3 = best_plan('狂骨', {2: '速度'}, inv)
print('无套装 plan:', plan3.missing)
assert plan3.missing and not plan3.picks

# 速度顺序校验
p1 = best_plan('破势', {2: '速度'}, inv)
p2 = best_plan('针女', {4: '效果命中'}, inv, min_speed=6)
problems = check_speed_order({'山兔': p1, '鬼使黑': p2}, ['鬼使黑', '山兔'])
print('speed order problems:', problems)
assert problems

print('souls check ok')
