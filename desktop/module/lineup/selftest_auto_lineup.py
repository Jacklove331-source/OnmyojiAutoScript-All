# This file uses the following encoding: utf-8
"""自动阵容全链路离线回归测试。

用法：
    .\\toolkit\\python.exe module\\lineup\\selftest_auto_lineup.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from module.lineup import deepseek, planner, roster, souls  # noqa: E402
from module.lineup.spec import (  # noqa: E402
    DEFAULT_PRESET_GROUP,
    LineupCache,
    LineupSpec,
    MemberSpec,
    dungeon_key_of,
    resolve_task_dungeon,
)
from tasks.Script.config_auto_lineup import AutoLineup  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = '') -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'PASS  {name}')
    else:
        FAIL += 1
        print(f'FAIL  {name}  {detail}')


def main() -> int:
    # 1. 配置默认值必须一律落第 10 组「适配」
    cfg = AutoLineup()
    check('config.default_group_10', cfg.default_preset_group == 10 and DEFAULT_PRESET_GROUP == 10,
          str(cfg.default_preset_group))
    check('config.default_team_group', cfg.team_group == '适配', cfg.team_group)
    check('config.save_to_preset', cfg.save_to_preset is True and cfg.cache_hours == 24)

    # 2. 真实式神池与御魂库存加载
    acc_roster = roster.load_account_roster()
    check('roster.loaded_real', len(acc_roster) >= 100, str(len(acc_roster)))
    check('roster.has_skills_and_rarity',
          '天照' in acc_roster and acc_roster['天照'].rarity == 'SSR' and len(acc_roster['天照'].skills) == 3,
          str(acc_roster.get('天照')))
    check('roster.distinguish_bujian',
          '不见岳' in acc_roster and acc_roster['不见岳'].rarity == 'SSR',
          str(acc_roster.get('不见岳')))

    inv = souls.load_inventory()
    check('souls.loaded_workbench_fallback', len(inv) >= 500, str(len(inv)))
    check('souls.attr_cn_normalized',
          any(s.main_stat in ('暴击', '暴击伤害', '速度', '攻击加成') for s in inv),
          str([s.main_stat for s in inv[:5]]))

    # 3. 任务 -> 副本三元组解析（含 Orochi 动态层数）
    fake_cfg_10 = SimpleNamespace(orochi=SimpleNamespace(orochi_config=SimpleNamespace(layer='拾层')))
    fake_cfg_12 = SimpleNamespace(orochi=SimpleNamespace(orochi_config=SimpleNamespace(layer='神罚')))
    check('dungeon.orochi_10', resolve_task_dungeon('Orochi', fake_cfg_10) == ('御魂', '八岐大蛇', '拾层'))
    check('dungeon.orochi_12', resolve_task_dungeon('Orochi', fake_cfg_12) == ('御魂', '八岐大蛇', '神罚'))
    check('dungeon.override', resolve_task_dungeon('Orochi', override='八岐大蛇·悲鸣') == ('御魂', '八岐大蛇', '悲鸣'))
    check('dungeon.sougenbi', resolve_task_dungeon('Sougenbi') == ('御魂', '业原火', None))

    # 4. 本地规划：为本机账号生成八岐大蛇·拾层极速阵容
    res = planner.plan_lineup('Orochi', dungeon='八岐大蛇·拾层', roster=acc_roster, inventory=inv, mode='local')
    check('plan.orochi10_ok', res['ok'] is True and res['spec'] is not None, str(res))
    spec: LineupSpec = res['spec']
    names = [m.name for m in spec.members]
    check('plan.orochi10_members',
          names == ['天照', '阿修罗', '因幡辉夜姬', '不见岳', '食灵'],
          str(names))
    check('plan.orochi10_group10', spec.preset_group == 10 and spec.preset_team == 1,
          f'{spec.preset_group}/{spec.preset_team}')
    check('plan.orochi10_bujian_ssr',
          spec.members[3].name == '不见岳' and spec.members[3].rarity == 'SSR',
          str(spec.members[3]))
    check('plan.orochi10_validate', spec.validate(acc_roster) == [], str(spec.validate(acc_roster)))

    # 5. 第 10 组缓存读写与队号分配
    tmp = tempfile.mkdtemp(prefix='alcache_')
    try:
        cache = LineupCache(os.path.join(tmp, 'cache.json'))
        t1 = cache.allocate_team_slot('oas3', 'Orochi', '御魂/八岐大蛇/拾层', preset_group=10)
        spec.preset_team = t1
        cache.put('oas3', 'Orochi', '御魂/八岐大蛇/拾层', spec)
        hit = cache.get('oas3', 'Orochi', '御魂/八岐大蛇/拾层', cache_hours=24)
        check('cache.hit_same_dungeon', hit is not None and hit['preset_group'] == 10 and hit['preset_team'] == 1)
        t1_again = cache.allocate_team_slot('oas3', 'Orochi', '御魂/八岐大蛇/拾层', preset_group=10)
        t2 = cache.allocate_team_slot('oas3', 'Sougenbi', '御魂/业原火', preset_group=10)
        check('cache.allocate_reuse_and_next', t1_again == 1 and t2 == 2, f'{t1_again}, {t2}')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 6. DeepSeek ask_lineup_spec 校验（mock 返回合法 & 非法未拥有式神）
    mock_ok = {
        'name': '[OAS] AI魂十',
        'onmyoji': '晴明',
        'description': 'AI测试',
        'members': [
            {'name': '天照', 'rarity': 'SSR', 'soul4': '破势', 'min_speed': 171, 'full_crit': True, 'lock_skill': 3},
            {'name': '阿修罗', 'rarity': 'SSR', 'soul4': '心眼', 'min_speed': 168, 'full_crit': True, 'lock_skill': 3},
        ],
    }
    with patch.object(deepseek, 'chat_json', return_value=mock_ok):
        ai_spec = deepseek.ask_lineup_spec('Orochi', '御魂/八岐大蛇/拾层', acc_roster, inv, preset_group=10)
        check('deepseek.spec_ok', ai_spec.preset_group == 10 and len(ai_spec.members) == 2)

    mock_bad = {
        'name': '[OAS] 幻觉阵容',
        'members': [{'name': '根本不存在的式神XYZ', 'soul4': '破势'}],
    }
    rejected = False
    with patch.object(deepseek, 'chat_json', return_value=mock_bad):
        try:
            deepseek.ask_lineup_spec('Orochi', '御魂/八岐大蛇/拾层', acc_roster, inv)
        except ValueError:
            rejected = True
    check('deepseek.reject_unowned', rejected)

    # 7. 任务资格过滤（百鬼棋局/重启/每周购买等必须跳过）与 overwrite_manual 保护
    from script import Script

    fake_script = Script.__new__(Script)
    fake_script.config = SimpleNamespace(
        model=SimpleNamespace(
            chess=SimpleNamespace(chess_config=SimpleNamespace()),
            weekly_purchase=SimpleNamespace(scheduler=SimpleNamespace()),
            orochi=SimpleNamespace(
                general_battle_config=SimpleNamespace(preset_enable=True, preset_group=2, preset_team=1),
                switch_soul=SimpleNamespace(enable=False, enable_switch_by_name=False, auto_switch_soul=False),
            ),
            sougenbi=SimpleNamespace(
                general_battle_config=SimpleNamespace(preset_enable=False, preset_group=1, preset_team=1),
                switch_soul=SimpleNamespace(enable=False, enable_switch_by_name=False, auto_switch_soul=False),
            ),
        )
    )
    auto_keep = SimpleNamespace(overwrite_manual=False)
    auto_force = SimpleNamespace(overwrite_manual=True)
    ok_chess, _ = fake_script._is_lineup_eligible_task('Chess', auto_force, 10)
    ok_restart, _ = fake_script._is_lineup_eligible_task('Restart', auto_force, 10)
    ok_wp, _ = fake_script._is_lineup_eligible_task('WeeklyPurchase', auto_force, 10)
    ok_orochi_keep, _ = fake_script._is_lineup_eligible_task('Orochi', auto_keep, 10)
    ok_orochi_force, _ = fake_script._is_lineup_eligible_task('Orochi', auto_force, 10)
    ok_sougenbi_keep, _ = fake_script._is_lineup_eligible_task('Sougenbi', auto_keep, 10)
    check('gate.skip_chess', not ok_chess)
    check('gate.skip_restart', not ok_restart)
    check('gate.skip_weekly_purchase', not ok_wp)
    check('gate.preserve_manual_preset', not ok_orochi_keep)
    check('gate.allow_overwrite_manual', ok_orochi_force)
    check('gate.allow_unconfigured_battle', ok_sougenbi_keep)

    print(f'\nAUTO LINEUP SELFTEST: {"PASSED" if FAIL == 0 else "FAILED"}   TOTAL {PASS + FAIL} PASS {PASS} FAIL {FAIL}')
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
