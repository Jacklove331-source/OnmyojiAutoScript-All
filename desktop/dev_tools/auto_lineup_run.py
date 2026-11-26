# This file uses the following encoding: utf-8
"""一键运行/测试 OAS 自动阵容改造链路（规划 -> 建阵/复用 -> 存第10组预设 -> 穿魂 -> 可选实战验证）。

用法：
  # 仅离线预览规划结果
  toolkit\\python.exe dev_tools\\auto_lineup_run.py --task Orochi --dungeon 八岐大蛇·拾层 --plan-only

  # 在 oas3 上极速建阵/复用 + 存第10组「适配」预设 + 一键穿魂
  toolkit\\python.exe dev_tools\\auto_lineup_run.py --config oas3 --task Orochi --dungeon 八岐大蛇·拾层

  # 顺手进八岐大蛇拾层实战打一把检验通关耗时
  toolkit\\python.exe dev_tools\\auto_lineup_run.py --config oas3 --task Orochi --dungeon 八岐大蛇·拾层 --test-battle
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from module.lineup.planner import plan_lineup  # noqa: E402
from module.lineup.spec import LineupCache  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='OAS 自动阵容一键规划/建阵/实战测试工具')
    parser.add_argument('--config', default='oas3', help='OAS 配置名（如 oas3）')
    parser.add_argument('--task', default='Orochi', help='任务名（如 Orochi / EvoZone / Sougenbi）')
    parser.add_argument('--dungeon', default='', help='副本覆写（如 八岐大蛇·拾层 / 御魂/八岐大蛇/悲鸣）')
    parser.add_argument('--mode', default='hybrid', choices=['hybrid', 'local', 'ai'], help='规划模式')
    parser.add_argument('--preset-group', type=int, default=10, help='预设分组号（默认第 10 组）')
    parser.add_argument('--team-group', default='适配', help='预设分组名（默认「适配」）')
    parser.add_argument('--preset-team', type=int, default=0, help='预设队号（0 表示自动分配）')
    parser.add_argument('--rebuild', action='store_true', help='强制新建，不复用「我的」已有方案')
    parser.add_argument('--plan-only', action='store_true', help='只做离线规划预览，不操作模拟器')
    parser.add_argument('--test-battle', action='store_true', help='建阵穿魂后实际去打一把验收')
    args = parser.parse_args()

    cache = LineupCache()
    planned = plan_lineup(
        task_name=args.task,
        dungeon=args.dungeon,
        mode=args.mode,
        preset_group=args.preset_group,
        preset_team=args.preset_team or 1,
    )
    if not planned.get('ok'):
        print('PLAN FAILED:', planned.get('reason'))
        return 1

    spec = planned['spec']
    dungeon_key = planned['dungeon_key']
    if args.preset_team <= 0:
        spec.preset_team = cache.allocate_team_slot(
            args.config, args.task, dungeon_key, preset_group=args.preset_group, default_team=1
        )

    print(json.dumps({
        'ok': True,
        'source': planned['source'],
        'reason': planned['reason'],
        'spec': spec.to_dict(),
    }, ensure_ascii=False, indent=2))

    if args.plan_only:
        return 0

    from module.config.config import Config
    from module.device.device import Device
    from tasks.Component.LineupAssistant.assistant import LineupAssistantMixin
    from tasks.GameUi.game_ui import GameUi

    class _Runner(LineupAssistantMixin, GameUi):
        pass

    cfg = Config(args.config)
    dev = Device(cfg)
    runner = _Runner(config=cfg, device=dev)

    t0 = time.monotonic()
    res = runner.apply_or_build_spec(
        spec=spec,
        dungeon_tuple=planned['dungeon_tuple'],
        reuse_existing=not args.rebuild,
        save_to_preset=True,
        team_group=args.team_group,
        preset_group=spec.preset_group,
        preset_team=spec.preset_team,
    )
    print(f'APPLY RESULT ({time.monotonic() - t0:.1f}s):', json.dumps(res, ensure_ascii=False))
    if not res.get('ok'):
        return 2
    cache.put(args.config, args.task, dungeon_key, spec)

    if args.test_battle:
        from tasks.Orochi.config import Layer, UserStatus
        from tasks.Orochi.script_task import ScriptTask as OrochiTask
        print(f'>>> 开始实战测试: {args.task} (第 {spec.preset_group} 组「{args.team_group}」第 {spec.preset_team} 队)')
        t1 = time.monotonic()
        # 仅打 1 局验证：在内存中临时调整参数并屏蔽 cfg.save()，绝不改动用户磁盘上的 oas3.json
        cfg.save = lambda *a, **kw: None
        gb = cfg.model.orochi.general_battle_config
        gb.lock_team_enable = False
        gb.preset_enable = True
        gb.preset_group = spec.preset_group
        gb.preset_team = spec.preset_team
        cfg.model.orochi.orochi_config.user_status = UserStatus.ALONE
        if '拾层' in dungeon_key or '十层' in dungeon_key:
            cfg.model.orochi.orochi_config.layer = Layer.TEN
        elif '悲鸣' in dungeon_key:
            cfg.model.orochi.orochi_config.layer = Layer.ELEVEN
        elif '神罚' in dungeon_key:
            cfg.model.orochi.orochi_config.layer = Layer.TWELVE
        cfg.model.orochi.orochi_config.limit_count = 1
        cfg.model.orochi.orochi_config.check_true_orochi_enable = False
        cfg.model.orochi.switch_soul.enable = False
        cfg.model.orochi.switch_soul.enable_switch_by_name = False
        cfg.model.orochi.switch_soul.auto_switch_soul = False
        task = OrochiTask(cfg, dev)
        try:
            task.run()
        except Exception as exc:  # noqa: BLE001 - TaskEnd 视为正常通关结束
            if exc.__class__.__name__ != 'TaskEnd':
                print('BATTLE ERROR:', repr(exc))
                return 3
        print(f'>>> 实战通关完成！总耗时: {time.monotonic() - t1:.1f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
