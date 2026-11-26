# This file uses the following encoding: utf-8
"""结构化自动阵容规格（LineupSpec）、副本层数解析与第十组预设缓存管理。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PRESET_GROUP = 10
DEFAULT_TEAM_GROUP = '适配'
DEFAULT_CACHE_FILE = 'config/auto_lineup_cache.json'

VALID_LOCK_SKILLS = {1, 2, 3, '1', '2', '3', 'auto', '智能', ''}

# 中文数字/别名 -> 阵容助手「八岐大蛇」小副本名
OROCHI_LAYER_MAP = {
    '1': '壹层', '一': '壹层', '壹': '壹层', '一层': '壹层', '壹层': '壹层',
    '2': '贰层', '二': '贰层', '贰': '贰层', '二层': '贰层', '贰层': '贰层',
    '3': '叁层', '三': '叁层', '叁': '叁层', '三层': '叁层', '叁层': '叁层',
    '4': '肆层', '四': '肆层', '肆': '肆层', '四层': '肆层', '肆层': '肆层',
    '5': '伍层', '五': '伍层', '伍': '伍层', '五层': '伍层', '伍层': '伍层',
    '6': '陆层', '六': '陆层', '陆': '陆层', '六层': '陆层', '陆层': '陆层',
    '7': '柒层', '七': '柒层', '柒': '柒层', '七层': '柒层', '柒层': '柒层',
    '8': '捌层', '八': '捌层', '捌': '捌层', '八层': '捌层', '捌层': '捌层',
    '9': '玖层', '九': '玖层', '玖': '玖层', '九层': '玖层', '玖层': '玖层',
    '10': '拾层', '十': '拾层', '拾': '拾层', '十层': '拾层', '拾层': '拾层', '魂十': '拾层',
    '11': '悲鸣', '十一': '悲鸣', '悲鸣': '悲鸣', '魂十一': '悲鸣', '魂土': '悲鸣',
    '12': '神罚', '十二': '神罚', '神罚': '神罚', '魂十二': '神罚', '魂王': '神罚',
    '虚无': '虚无',
}

TASK_DUNGEON_MAP: dict[str, tuple[str | None, str | None, str | None]] = {
    'Orochi': ('御魂', '八岐大蛇', '拾层'),
    'OrochiAdventure': ('御魂', '八岐大蛇', '悲鸣'),
    'SoulZones': ('御魂', '八岐大蛇', '拾层'),
    'Sougenbi': ('御魂', '业原火', None),
    'FallenSun': ('御魂', '日轮之陨', None),
    'EternitySea': ('御魂', '永生之海', None),
    'TrueOrochi': ('御魂', '真·八岐大蛇', None),
    'EvoZone': ('觉醒材料', None, None),
    'BondlingFairyland': ('探查', '契灵', None),
    'DemonEncounter': ('逢魔之时', None, None),
    'DemonicEncounter': ('逢魔之时', None, None),
    'AreaBoss': ('地域鬼王', None, None),
    'Duel': ('斗技', None, None),
}


@dataclass
class MemberSpec:
    name: str
    rarity: str = ''
    soul4: str = ''
    soul2: str = ''
    effect_metric: str = '伤害输出'
    slot2: list[str] = field(default_factory=lambda: ['攻击加成'])
    slot4: list[str] = field(default_factory=lambda: ['攻击加成'])
    slot6: list[str] = field(default_factory=lambda: ['暴击', '暴击伤害'])
    min_speed: float | None = None
    max_speed: float | None = None
    super_star: bool = False
    full_crit: bool = False
    lock_skill: int | str = 'auto'
    role: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'rarity': self.rarity,
            'soul4': self.soul4,
            'soul2': self.soul2,
            'effect_metric': self.effect_metric,
            'slot2': list(self.slot2),
            'slot4': list(self.slot4),
            'slot6': list(self.slot6),
            'min_speed': self.min_speed,
            'max_speed': self.max_speed,
            'super_star': bool(self.super_star),
            'full_crit': bool(self.full_crit),
            'lock_skill': self.lock_skill,
            'role': self.role,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'MemberSpec':
        lock = raw.get('lock_skill', 'auto')
        if isinstance(lock, str) and lock.isdigit():
            lock = int(lock)
        return cls(
            name=str(raw.get('name') or '').strip(),
            rarity=str(raw.get('rarity') or '').strip().upper(),
            soul4=str(raw.get('soul4') or raw.get('soul') or '').strip(),
            soul2=str(raw.get('soul2') or '').strip(),
            effect_metric=str(raw.get('effect_metric') or '伤害输出').strip(),
            slot2=list(raw.get('slot2') or ['攻击加成']),
            slot4=list(raw.get('slot4') or ['攻击加成']),
            slot6=list(raw.get('slot6') or ['暴击', '暴击伤害']),
            min_speed=float(raw['min_speed']) if raw.get('min_speed') is not None else None,
            max_speed=float(raw['max_speed']) if raw.get('max_speed') is not None else None,
            super_star=bool(raw.get('super_star', False)),
            full_crit=bool(raw.get('full_crit', False)),
            lock_skill=lock if lock in VALID_LOCK_SKILLS else 'auto',
            role=str(raw.get('role') or '').strip(),
        )


@dataclass
class LineupSpec:
    name: str
    dungeon: str
    task_name: str = ''
    onmyoji: str = '晴明'
    onmyoji_skills: list[str] = field(default_factory=list)
    members: list[MemberSpec] = field(default_factory=list)
    description: str = ''
    preset_group: int = DEFAULT_PRESET_GROUP
    preset_team: int = 1
    source: str = 'local-template'
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'dungeon': self.dungeon,
            'task_name': self.task_name,
            'onmyoji': self.onmyoji,
            'onmyoji_skills': list(self.onmyoji_skills),
            'members': [m.to_dict() for m in self.members],
            'description': self.description,
            'preset_group': int(self.preset_group),
            'preset_team': int(self.preset_team),
            'source': self.source,
            'score': round(float(self.score), 2),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'LineupSpec':
        members_raw = raw.get('members') or raw.get('shikigami') or []
        members = [
            m if isinstance(m, MemberSpec) else MemberSpec.from_dict(m)
            for m in members_raw if isinstance(m, (dict, MemberSpec))
        ]
        return cls(
            name=str(raw.get('name') or '[OAS] 自动阵容').strip(),
            dungeon=str(raw.get('dungeon') or '').strip(),
            task_name=str(raw.get('task_name') or '').strip(),
            onmyoji=str(raw.get('onmyoji') or '晴明').strip(),
            onmyoji_skills=list(raw.get('onmyoji_skills') or []),
            members=members,
            description=str(raw.get('description') or raw.get('reason') or '').strip(),
            preset_group=int(raw.get('preset_group') or DEFAULT_PRESET_GROUP),
            preset_team=int(raw.get('preset_team') or 1),
            source=str(raw.get('source') or 'local-template').strip(),
            score=float(raw.get('score') or 0.0),
        )

    def fingerprint(self) -> str:
        core = {
            'dungeon': self.dungeon,
            'onmyoji': self.onmyoji,
            'members': [
                (m.name, m.rarity, m.soul4, m.soul2, m.min_speed, m.full_crit, m.lock_skill)
                for m in self.members
            ],
        }
        raw = json.dumps(core, ensure_ascii=False, sort_keys=True).encode('utf-8')
        return hashlib.sha256(raw).hexdigest()[:12]

    def validate(self, roster: dict | None = None) -> list[str]:
        """校验阵容规格合法性；若提供 roster 则校验所有式神均已拥有并补齐稀有度。"""
        errors: list[str] = []
        if not self.members or not (1 <= len(self.members) <= 5):
            errors.append(f'式神数量必须在 1..5 之间，当前为 {len(self.members)}')
        if self.preset_group < 1 or self.preset_team < 1:
            errors.append(f'预设组/队必须为正整数: group={self.preset_group}, team={self.preset_team}')
        for m in self.members:
            if not m.name:
                errors.append('存在空式神名称')
                continue
            if m.lock_skill not in VALID_LOCK_SKILLS:
                errors.append(f'{m.name} 的锁技能配置无效: {m.lock_skill}')
            if roster:
                owned = roster.get(m.name)
                if owned is None:
                    errors.append(f'账号未拥有式神: {m.name}')
                else:
                    if not m.rarity and getattr(owned, 'rarity', ''):
                        m.rarity = str(owned.rarity).upper()
        return errors


def _norm_orochi_layer(raw: Any) -> str | None:
    val = getattr(raw, 'value', raw)
    text = str(val or '').strip()
    if not text:
        return None
    for k, target in OROCHI_LAYER_MAP.items():
        if text == k or k in text:
            return target
    return text


def resolve_task_dungeon(task_name: str, config_model=None,
                         override: str = '') -> tuple[str | None, str | None, str | None]:
    """解析 OAS 任务对应的游戏「阵容助手」副本三元组 (分类, 大副本, 小副本)。"""
    if override and str(override).strip():
        text = str(override).strip().replace('·', '/')
        parts = [p.strip() for p in text.split('/') if p.strip()]
        if len(parts) == 1:
            sub = _norm_orochi_layer(parts[0])
            if sub in set(OROCHI_LAYER_MAP.values()):
                return ('御魂', '八岐大蛇', sub)
            return (None, None, parts[0])
        if len(parts) == 2:
            if parts[0] == '八岐大蛇':
                return ('御魂', '八岐大蛇', _norm_orochi_layer(parts[1]) or parts[1])
            return (parts[0], parts[1], None)
        return (parts[0], parts[1], _norm_orochi_layer(parts[2]) if parts[1] == '八岐大蛇' else parts[2])

    cat, grp, sub = TASK_DUNGEON_MAP.get(task_name, (None, None, None))
    if task_name in ('Orochi', 'SoulZones') and config_model is not None:
        orochi_cfg = getattr(config_model, 'orochi', None)
        inner = getattr(orochi_cfg, 'orochi_config', None) if orochi_cfg is not None else None
        layer = getattr(inner, 'layer', None) if inner is not None else None
        parsed = _norm_orochi_layer(layer)
        if parsed:
            sub = parsed
    return (cat, grp, sub)


def dungeon_key_of(dungeon_tuple: tuple[str | None, str | None, str | None]) -> str:
    return '/'.join(p for p in dungeon_tuple if p)


class LineupCache:
    """自动阵容本地缓存：记录各副本已写入第 10 组的预设队号与时效。"""

    def __init__(self, path: str | Path = DEFAULT_CACHE_FILE):
        p = Path(path)
        self.path = p if p.is_absolute() else Path.cwd() / p

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {'version': 1, 'entries': {}}
        try:
            with io.open(self.path, encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('entries'), dict):
                return data
        except (OSError, ValueError):
            pass
        return {'version': 1, 'entries': {}}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + '.tmp')
        with io.open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(str(tmp), str(self.path))

    @staticmethod
    def _entry_key(config_name: str, task_name: str, dungeon_key: str) -> str:
        return f'{config_name or "default"}::{task_name}::{dungeon_key}'

    def get(self, config_name: str, task_name: str, dungeon_key: str,
            cache_hours: float = 24) -> dict[str, Any] | None:
        data = self._load()
        entry = data['entries'].get(self._entry_key(config_name, task_name, dungeon_key))
        if not isinstance(entry, dict):
            return None
        if cache_hours <= 0:
            return None
        updated_ts = float(entry.get('updated_ts') or 0)
        if time.time() - updated_ts > float(cache_hours) * 3600.0:
            return None
        return entry

    def allocate_team_slot(self, config_name: str, task_name: str, dungeon_key: str,
                           preset_group: int = DEFAULT_PRESET_GROUP,
                           default_team: int = 1, max_teams: int = 5) -> int:
        data = self._load()
        key = self._entry_key(config_name, task_name, dungeon_key)
        existing = data['entries'].get(key)
        if isinstance(existing, dict) and existing.get('preset_group') == preset_group:
            team = int(existing.get('preset_team') or default_team)
            if 1 <= team <= max_teams:
                return team
        prefix = f'{config_name or "default"}::'
        used = {
            int(v.get('preset_team') or 0)
            for k, v in data['entries'].items()
            if k.startswith(prefix) and isinstance(v, dict)
            and int(v.get('preset_group') or 0) == preset_group
        }
        for slot in range(default_team, max_teams + 1):
            if slot not in used:
                return slot
        for slot in range(1, max_teams + 1):
            if slot not in used:
                return slot
        return default_team

    def put(self, config_name: str, task_name: str, dungeon_key: str, spec: LineupSpec) -> dict[str, Any]:
        data = self._load()
        key = self._entry_key(config_name, task_name, dungeon_key)
        entry = {
            'config_name': config_name,
            'task_name': task_name,
            'dungeon_key': dungeon_key,
            'lineup_name': spec.name,
            'preset_group': int(spec.preset_group),
            'preset_team': int(spec.preset_team),
            'fingerprint': spec.fingerprint(),
            'source': spec.source,
            'updated_ts': time.time(),
            'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'spec': spec.to_dict(),
        }
        data['entries'][key] = entry
        self._save(data)
        return entry
