# This file uses the following encoding: utf-8
"""自动阵容混合规划器：本地高练度模板择优 + DeepSeek AI 补全，统一落第 10 组「适配」。"""
from __future__ import annotations

from typing import Any

from module.lineup import roster as roster_mod
from module.lineup import souls as souls_mod
from module.lineup.spec import (
    DEFAULT_PRESET_GROUP,
    LineupSpec,
    MemberSpec,
    dungeon_key_of,
    resolve_task_dungeon,
)
from module.logger import logger

# 高质量实战模板库：每个槽位支持 (首选, 备选...) 与完整建阵参数
CURATED_TEMPLATES: list[dict[str, Any]] = [
    {
        'name': '[OAS] 魂十极速天照修',
        'dungeons': ('御魂/八岐大蛇/拾层',),
        'tasks': ('Orochi', 'SoulZones'),
        'strength': 98,
        'onmyoji': '晴明',
        'description': '天照破势171速满暴 阿修罗心眼168速满暴 晴明星灭 因幡火灵不见岳食灵尾速增伤',
        'slots': [
            {
                'role': '1速主C',
                'candidates': [('天照', 'SSR'), ('阿修罗', 'SSR'), ('玉藻前', 'SSR')],
                'soul4': '破势', 'soul2': '暴击', 'slot2': ['攻击加成', '速度'],
                'min_speed': 171, 'full_crit': True, 'lock_skill': 3, 'weight': 3.5,
            },
            {
                'role': '2速收尾',
                'candidates': [('阿修罗', 'SSR'), ('须佐之男', 'SSR'), ('烬天玉藻前', 'SP'), ('神堕八岐大蛇', 'SP')],
                'soul4': '心眼', 'soul2': '', 'slot2': ['攻击加成', '速度'],
                'min_speed': 168, 'full_crit': True, 'lock_skill': 3, 'weight': 3.0,
            },
            {
                'role': '火灵增伤',
                'candidates': [('因幡辉夜姬', 'SP'), ('辉夜姬', 'SSR'), ('座敷童子', 'R')],
                'soul4': '火灵', 'soul2': '', 'slot2': ['攻击加成'],
                'lock_skill': 2, 'weight': 2.0,
            },
            {
                'role': '被动增伤1',
                'candidates': [('不见岳', 'SSR'), ('纺愿缘结神', 'SP'), ('绘世花鸟卷', 'SP')],
                'soul4': '', 'soul2': '', 'slot2': ['攻击加成'],
                'lock_skill': 1, 'weight': 1.5,
            },
            {
                'role': '被动增伤2',
                'candidates': [('食灵', 'SSR'), ('铁鼠', 'R'), ('鬼王酒吞童子', 'SP'), ('座敷童子', 'R')],
                'soul4': '', 'soul2': '', 'slot2': ['攻击加成'],
                'lock_skill': 1, 'weight': 1.5,
            },
        ],
    },
    {
        'name': '[OAS] 魂土极速天照修',
        'dungeons': ('御魂/八岐大蛇/悲鸣',),
        'tasks': ('Orochi', 'OrochiAdventure', 'SoulZones'),
        'strength': 96,
        'onmyoji': '晴明',
        'description': '天照破势158速满暴 阿修罗/须佐心眼156速满暴 因幡火灵不见岳食灵尾速增伤',
        'slots': [
            {
                'role': '1速主C',
                'candidates': [('天照', 'SSR'), ('阿修罗', 'SSR'), ('神堕八岐大蛇', 'SP')],
                'soul4': '破势', 'soul2': '暴击', 'slot2': ['攻击加成', '速度'],
                'min_speed': 158, 'full_crit': True, 'lock_skill': 3, 'weight': 3.5,
            },
            {
                'role': '2速收尾',
                'candidates': [('阿修罗', 'SSR'), ('须佐之男', 'SSR'), ('烬天玉藻前', 'SP')],
                'soul4': '心眼', 'soul2': '', 'slot2': ['攻击加成', '速度'],
                'min_speed': 156, 'full_crit': True, 'lock_skill': 3, 'weight': 3.0,
            },
            {
                'role': '火灵供火',
                'candidates': [('因幡辉夜姬', 'SP'), ('辉夜姬', 'SSR'), ('座敷童子', 'R')],
                'soul4': '火灵', 'soul2': '', 'lock_skill': 2, 'weight': 2.0,
            },
            {
                'role': '增伤1',
                'candidates': [('不见岳', 'SSR'), ('绘世花鸟卷', 'SP'), ('纺愿缘结神', 'SP')],
                'soul4': '', 'soul2': '', 'lock_skill': 1, 'weight': 1.5,
            },
            {
                'role': '增伤2',
                'candidates': [('食灵', 'SSR'), ('饭笥', 'SSR'), ('鬼王酒吞童子', 'SP')],
                'soul4': '', 'soul2': '', 'lock_skill': 1, 'weight': 1.5,
            },
        ],
    },
    {
        'name': '[OAS] 魂王天照须佐',
        'dungeons': ('御魂/八岐大蛇/神罚',),
        'tasks': ('Orochi', 'SoulZones'),
        'strength': 95,
        'onmyoji': '晴明',
        'description': '天照隐念/海月145速满暴 须佐狂骨143速满暴 因幡火灵不见岳食灵增伤',
        'slots': [
            {
                'role': '群攻主C',
                'candidates': [('天照', 'SSR'), ('阿修罗', 'SSR'), ('神堕八岐大蛇', 'SP')],
                'soul4': '隐念', 'soul2': '暴击', 'slot2': ['攻击加成', '速度'],
                'min_speed': 145, 'full_crit': True, 'lock_skill': 3, 'weight': 3.5,
            },
            {
                'role': '单体收尾',
                'candidates': [('须佐之男', 'SSR'), ('阿修罗', 'SSR'), ('烬天玉藻前', 'SP')],
                'soul4': '狂骨', 'soul2': '', 'slot2': ['攻击加成', '速度'],
                'min_speed': 143, 'full_crit': True, 'lock_skill': 3, 'weight': 3.5,
            },
            {
                'role': '供火增伤',
                'candidates': [('因幡辉夜姬', 'SP'), ('辉夜姬', 'SSR')],
                'soul4': '火灵', 'soul2': '', 'lock_skill': 2, 'weight': 2.0,
            },
            {
                'role': '增伤1',
                'candidates': [('不见岳', 'SSR'), ('纺愿缘结神', 'SP')],
                'soul4': '', 'soul2': '', 'lock_skill': 1, 'weight': 1.5,
            },
            {
                'role': '增伤2',
                'candidates': [('伊邪那美', 'SSR'), ('食灵', 'SSR'), ('绘世花鸟卷', 'SP')],
                'soul4': '遗念火', 'soul2': '', 'lock_skill': 3, 'weight': 1.5,
            },
        ],
    },
    {
        'name': '[OAS] 业原火速刷',
        'dungeons': ('御魂/业原火',),
        'tasks': ('Sougenbi',),
        'strength': 92,
        'onmyoji': '晴明',
        'description': '天照破势+阿修罗心眼双核超星速刷业原火，因幡火灵不见岳食灵增伤',
        'slots': [
            {
                'role': '1速主C',
                'candidates': [('天照', 'SSR'), ('阿修罗', 'SSR'), ('玉藻前', 'SSR')],
                'soul4': '破势', 'soul2': '暴击', 'slot2': ['攻击加成', '速度'],
                'super_star': True, 'full_crit': True, 'lock_skill': 3, 'weight': 3.5,
            },
            {
                'role': '2速收尾',
                'candidates': [('阿修罗', 'SSR'), ('须佐之男', 'SSR'), ('烬天玉藻前', 'SP')],
                'soul4': '心眼', 'soul2': '', 'slot2': ['攻击加成', '速度'],
                'super_star': True, 'full_crit': True, 'lock_skill': 3, 'weight': 3.0,
            },
            {
                'role': '供火',
                'candidates': [('因幡辉夜姬', 'SP'), ('辉夜姬', 'SSR'), ('座敷童子', 'R')],
                'soul4': '火灵', 'soul2': '', 'lock_skill': 2, 'weight': 2.0,
            },
            {
                'role': '增伤1',
                'candidates': [('不见岳', 'SSR'), ('绘世花鸟卷', 'SP')],
                'soul4': '', 'soul2': '', 'lock_skill': 1, 'weight': 1.5,
            },
            {
                'role': '增伤2',
                'candidates': [('食灵', 'SSR'), ('铁鼠', 'R')],
                'soul4': '', 'soul2': '', 'lock_skill': 1, 'weight': 1.5,
            },
        ],
    },
    {
        'name': '[OAS] 觉醒/通用速刷',
        'dungeons': ('觉醒材料', '御魂/日轮之陨', '御魂/永生之海', '探查/契灵', '逢魔之时', ''),
        'tasks': ('EvoZone', 'FallenSun', 'EternitySea', 'BondlingFairyland', 'DemonEncounter', 'AreaBoss'),
        'strength': 88,
        'onmyoji': '晴明',
        'description': '天照破势+阿修罗心眼高爆双核通用速刷，因幡火灵不见岳食灵尾速增伤',
        'slots': [
            {
                'role': '主C',
                'candidates': [('天照', 'SSR'), ('阿修罗', 'SSR'), ('须佐之男', 'SSR'), ('玉藻前', 'SSR')],
                'soul4': '破势', 'soul2': '暴击', 'slot2': ['攻击加成', '速度'],
                'super_star': True, 'full_crit': True, 'lock_skill': 3, 'weight': 3.5,
            },
            {
                'role': '副C',
                'candidates': [('阿修罗', 'SSR'), ('须佐之男', 'SSR'), ('烬天玉藻前', 'SP'), ('神堕八岐大蛇', 'SP')],
                'soul4': '心眼', 'soul2': '', 'slot2': ['攻击加成', '速度'],
                'super_star': True, 'full_crit': True, 'lock_skill': 3, 'weight': 3.0,
            },
            {
                'role': '供火',
                'candidates': [('因幡辉夜姬', 'SP'), ('辉夜姬', 'SSR'), ('座敷童子', 'R')],
                'soul4': '火灵', 'soul2': '', 'lock_skill': 2, 'weight': 2.0,
            },
            {
                'role': '增伤1',
                'candidates': [('不见岳', 'SSR'), ('纺愿缘结神', 'SP'), ('绘世花鸟卷', 'SP')],
                'soul4': '', 'soul2': '', 'lock_skill': 1, 'weight': 1.5,
            },
            {
                'role': '增伤2',
                'candidates': [('食灵', 'SSR'), ('铁鼠', 'R'), ('鬼王酒吞童子', 'SP')],
                'soul4': '', 'soul2': '', 'lock_skill': 1, 'weight': 1.5,
            },
        ],
    },
]


def _soul_set_counts(inventory: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in inventory or []:
        name = getattr(s, 'set_name', '')
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _evaluate_template(tpl: dict[str, Any], dungeon_key: str, task_name: str,
                       roster: dict[str, roster_mod.Shikigami],
                       soul_counts: dict[str, int],
                       preset_group: int, preset_team: int) -> dict[str, Any]:
    aliases = roster_mod.alias_map()
    used_names: set[str] = set()
    members: list[MemberSpec] = []
    missing: list[dict[str, Any]] = []
    total_w = 0.0
    earned_w = 0.0

    for slot in tpl['slots']:
        w = float(slot.get('weight', 1.0))
        total_w += w
        picked = None
        picked_rarity = ''
        for cand_name, cand_rarity in slot['candidates']:
            if cand_name in used_names:
                continue
            shiki = roster_mod.find_shikigami(roster, cand_name, aliases)
            if shiki is not None:
                picked = shiki
                picked_rarity = cand_rarity or shiki.rarity
                break
        if picked is None:
            missing.append({
                'role': slot.get('role', ''),
                'candidates': [c[0] for c in slot['candidates']],
            })
            continue

        used_names.add(picked.name)
        lv_factor = 1.0 if picked.level >= 40 else max(0.5, picked.level / 40.0)
        star_factor = 1.0 if picked.star >= 6 else max(0.6, picked.star / 6.0)
        skill_factor = 1.0 if (not picked.skills or picked.skill_total >= 12) else max(0.7, picked.skill_total / 15.0)
        soul4 = slot.get('soul4', '')
        soul_factor = 1.0
        if soul4 and soul_counts and soul_counts.get(soul4, 0) < 4:
            soul_factor = 0.85

        earned_w += w * lv_factor * star_factor * skill_factor * soul_factor
        members.append(MemberSpec(
            name=picked.name,
            rarity=(picked_rarity or picked.rarity or '').upper(),
            soul4=soul4,
            soul2=slot.get('soul2', ''),
            effect_metric=slot.get('effect_metric', '伤害输出'),
            slot2=list(slot.get('slot2') or ['攻击加成']),
            slot4=list(slot.get('slot4') or ['攻击加成']),
            slot6=list(slot.get('slot6') or ['暴击', '暴击伤害']),
            min_speed=slot.get('min_speed'),
            max_speed=slot.get('max_speed'),
            super_star=bool(slot.get('super_star', False)),
            full_crit=bool(slot.get('full_crit', False)),
            lock_skill=slot.get('lock_skill', 'auto'),
            role=slot.get('role', ''),
        ))

    fit = earned_w / total_w if total_w > 0 else 0.0
    exact_dungeon = 1 if dungeon_key and dungeon_key in tpl.get('dungeons', ()) else 0
    task_match = 1 if task_name in tpl.get('tasks', ()) else 0
    score = round(0.65 * fit * 100.0 + 0.35 * float(tpl.get('strength', 80)), 1)

    short_dg = dungeon_key.split('/')[-1] if dungeon_key else task_name
    spec_name = tpl['name'] if exact_dungeon else f'[OAS] {short_dg}速刷'
    spec = LineupSpec(
        name=spec_name,
        dungeon=dungeon_key or (tpl['dungeons'][0] if tpl.get('dungeons') else ''),
        task_name=task_name,
        onmyoji=tpl.get('onmyoji', '晴明'),
        members=members,
        description=tpl.get('description', ''),
        preset_group=int(preset_group),
        preset_team=int(preset_team),
        source='local-template',
        score=score,
    )
    return {
        'spec': spec,
        'score': score,
        'fit': round(fit, 3),
        'exact_dungeon': exact_dungeon,
        'task_match': task_match,
        'missing': missing,
    }


def plan_lineup(task_name: str, dungeon: str = '',
                roster: dict[str, roster_mod.Shikigami] | None = None,
                inventory: list | None = None,
                mode: str = 'hybrid',
                preset_group: int = DEFAULT_PRESET_GROUP,
                preset_team: int = 1,
                config_model=None,
                ai_config: dict | None = None,
                roster_file: str | None = None) -> dict[str, Any]:
    """按账号真实式神+技能+御魂规划最优 LineupSpec，统一落指定预设组（默认第 10 组）。"""
    dungeon_tuple = resolve_task_dungeon(task_name, config_model=config_model, override=dungeon)
    dungeon_key = dungeon_key_of(dungeon_tuple)

    if roster is None:
        roster = roster_mod.load_account_roster(roster_file)
    if inventory is None:
        inventory = souls_mod.load_inventory()
    soul_counts = _soul_set_counts(inventory)

    # 先对所有本地模板打分
    ranked = [
        _evaluate_template(tpl, dungeon_key, task_name, roster, soul_counts, preset_group, preset_team)
        for tpl in CURATED_TEMPLATES
    ]
    ranked.sort(key=lambda r: (r['exact_dungeon'], r['task_match'], r['score']), reverse=True)
    best_local = ranked[0] if ranked else None

    # 若要求 AI 模式，或 hybrid 模式下本地未精确命中/得分偏低，尝试 DeepSeek 规划
    want_ai = (
        mode == 'ai'
        or (mode == 'hybrid' and (best_local is None or best_local['score'] < 75 or not best_local['exact_dungeon']))
    )
    if want_ai and mode != 'local':
        try:
            from module.lineup import deepseek
            spec = deepseek.ask_lineup_spec(
                task_name=task_name,
                dungeon=dungeon_key,
                roster=roster,
                inventory=inventory,
                preset_group=preset_group,
                preset_team=preset_team,
                config=ai_config,
            )
            spec.preset_group = int(preset_group)
            spec.preset_team = int(preset_team)
            return {
                'ok': True,
                'spec': spec,
                'dungeon_tuple': dungeon_tuple,
                'dungeon_key': dungeon_key,
                'source': 'deepseek-ai',
                'reason': f'DeepSeek AI 定制阵容: {spec.name}',
                'missing': [],
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(f'[AutoLineup] DeepSeek 规划失败，回退本地最优模板: {exc}')

    if best_local is None or not best_local['spec'].members:
        return {
            'ok': False,
            'spec': None,
            'dungeon_tuple': dungeon_tuple,
            'dungeon_key': dungeon_key,
            'source': 'none',
            'reason': '账号式神池为空或无匹配阵容模板',
            'missing': [],
        }

    spec = best_local['spec']
    spec.preset_group = int(preset_group)
    spec.preset_team = int(preset_team)
    return {
        'ok': True,
        'spec': spec,
        'dungeon_tuple': dungeon_tuple,
        'dungeon_key': dungeon_key,
        'source': 'local-template',
        'reason': (f"本地模板择优: {spec.name} (score={best_local['score']}, "
                   f"fit={best_local['fit']}, exact={bool(best_local['exact_dungeon'])})"),
        'missing': best_local['missing'],
    }
