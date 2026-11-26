"""离线检查：锁定阵容时自动阵容如何接管预设队伍。"""

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from module.lineup import apply as lineup_apply  # noqa: E402


def make_conf(lock_team: bool):
    return SimpleNamespace(
        general_battle_config=SimpleNamespace(
            preset_enable=False, preset_group=4, preset_team=1,
            lock_team_enable=lock_team,
        ),
        switch_soul=SimpleNamespace(
            enable=True, enable_switch_by_name=False, group_name='', team_name='',
        ),
    )


entry = SimpleNamespace(preset_group=1, preset_team=1, name='御魂-通用',
                        soul_group='', soul_team='')

for lock in (True, False):
    conf = make_conf(lock)
    disabled = lineup_apply._disable_task_overrides(conf)
    applied = lineup_apply._apply_preset(conf, entry, overwrite=True)
    gb = conf.general_battle_config
    print(f'lock={lock}: disabled={disabled} applied={applied} '
          f'-> preset_enable={gb.preset_enable} lock_team={gb.lock_team_enable} '
          f'group={gb.preset_group} team={gb.preset_team}')
