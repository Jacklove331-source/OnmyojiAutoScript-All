"""离线检查：自动阵容忽略任务自带战斗配置 / 切换御魂。"""

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from module.lineup import apply as lineup_apply  # noqa: E402


def make_conf(field_names):
    gb_name, soul_name = field_names
    conf = SimpleNamespace()
    setattr(conf, gb_name, SimpleNamespace(
        preset_enable=True, preset_group=3, preset_team=2))
    setattr(conf, soul_name, SimpleNamespace(
        enable=True, enable_switch_by_name=True,
        group_name='旧组', team_name='旧队'))
    return conf


for names in (('general_battle', 'switch_soul'),          # AreaBoss 风格
              ('general_battle_config', 'switch_soul')):  # Orochi 风格
    conf = make_conf(names)
    disabled = lineup_apply._disable_task_overrides(conf)
    gb = lineup_apply._general_battle(conf)
    soul = lineup_apply._switch_soul(conf)
    print(f'{names}: disabled={disabled} preset_enable={gb.preset_enable} '
          f'soul.enable={soul.enable} by_name={soul.enable_switch_by_name}')

# 应用预设时两种字段名都能生效
entry = SimpleNamespace(preset_group=4, preset_team=1, name='测试阵容',
                        soul_group='', soul_team='')
for names in (('general_battle', 'switch_soul'),
              ('general_battle_config', 'switch_soul')):
    conf = make_conf(names)
    lineup_apply._disable_task_overrides(conf)
    ok = lineup_apply._apply_preset(conf, entry, overwrite=False)
    gb = lineup_apply._general_battle(conf)
    print(f'{names}: apply_preset={ok} -> group {gb.preset_group} team {gb.preset_team}')
