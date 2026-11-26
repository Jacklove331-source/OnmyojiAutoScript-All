# This Python file uses the following encoding: utf-8

"""DeepSeek 客户端：给 AI 阵容模式提供结构化决策。

密钥只从本地读（``config/deepseek.json`` 或环境变量 ``DEEPSEEK_API_KEY``），
源码里不出现任何密钥。

调用方拿到的永远是已解析的 dict；解析失败/超时会按配置重试，仍失败则抛异常，
由上层决定回退方案（不能把未验证的阵容当成算好了）。
"""

import json
import os
import time
import urllib.request

DEFAULT_BASE_URL = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-chat'
CONFIG_PATH = 'config/deepseek.json'
AI_RECOVERY_YAML = 'config/ai_recovery.yaml'


def _fallback_from_ai_recovery() -> dict:
    """当 config/deepseek.json 不存在时，尝试复用 config/ai_recovery.yaml 中已配好的密钥与地址。"""
    if not os.path.exists(AI_RECOVERY_YAML):
        return {}
    try:
        import yaml
        with open(AI_RECOVERY_YAML, encoding='utf-8') as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            return {}
        for section in (raw, raw.get('llm') or {}, raw.get('deepseek') or {}, raw.get('provider') or {}):
            if isinstance(section, dict) and section.get('api_key'):
                base = str(section.get('base_url') or DEFAULT_BASE_URL)
                if 'deepseek' in base.lower() or not section.get('base_url'):
                    return {
                        'api_key': str(section.get('api_key')),
                        'base_url': base,
                        'model': str(section.get('model') or DEFAULT_MODEL),
                    }
    except Exception:  # noqa: BLE001
        pass
    return {}


def load_config(path: str = CONFIG_PATH) -> dict:
    """读取本地配置；文件不存在时依次回退环境变量与 ai_recovery.yaml。"""
    data = {}
    if path and os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as file:
                data = json.load(file) or {}
        except (OSError, ValueError):
            data = {}
    if not data.get('api_key') and not os.environ.get('DEEPSEEK_API_KEY'):
        fallback = _fallback_from_ai_recovery()
        for k, v in fallback.items():
            data.setdefault(k, v)
    return {
        'api_key': str(data.get('api_key') or os.environ.get('DEEPSEEK_API_KEY', '') or ''),
        'base_url': str(data.get('base_url') or DEFAULT_BASE_URL),
        'model': str(data.get('model') or DEFAULT_MODEL),
        'timeout': int(data.get('timeout') or 60),
        'retries': int(data.get('retries') if data.get('retries') is not None else 2),
        'temperature': float(data.get('temperature') or 0.2),
    }


def _extract_json(text: str) -> str:
    """从模型回复里抠出 JSON（兼容 ```json 围栏和前后废话）。"""
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = raw.split('```', 2)[1] if raw.count('```') >= 2 else raw
        if raw.lower().startswith('json'):
            raw = raw[4:]
        raw = raw.strip()
    start = raw.find('{')
    end = raw.rfind('}')
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    return raw


def chat_json(messages, config: dict | None = None) -> dict:
    """调用 chat/completions 并返回解析后的 JSON dict。"""
    cfg = config or load_config()
    if not cfg['api_key']:
        raise RuntimeError(
            'DeepSeek api_key 未配置：请在 config/deepseek.json 填写 api_key，'
            '或设置环境变量 DEEPSEEK_API_KEY')
    body = json.dumps({
        'model': cfg['model'],
        'messages': messages,
        'temperature': cfg['temperature'],
        'response_format': {'type': 'json_object'},
    }).encode('utf-8')
    url = cfg['base_url'].rstrip('/') + '/chat/completions'

    last_error = None
    for attempt in range(cfg['retries'] + 1):
        try:
            request = urllib.request.Request(
                url, data=body,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f"Bearer {cfg['api_key']}",
                },
            )
            with urllib.request.urlopen(request, timeout=cfg['timeout']) as response:
                payload = json.loads(response.read().decode('utf-8'))
            content = payload['choices'][0]['message']['content']
            return json.loads(_extract_json(content))
        except Exception as error:  # noqa: BLE001 - 网络/解析错误统一重试
            last_error = error
            if attempt >= cfg['retries']:
                break
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f'DeepSeek 调用失败: {last_error}')


def summarize_inventory(inventory, limit: int = 12) -> list:
    """把御魂库存压成「套装 → 可用号位」摘要，避免把几千条塞进提示词。"""
    if not inventory:
        return []
    by_set = {}
    for soul in inventory:
        entry = by_set.setdefault(soul.set_name, {'slots': set(), 'count': 0, 'mains': set()})
        entry['slots'].add(soul.slot)
        entry['mains'].add(soul.main_stat)
        entry['count'] += 1
    rows = []
    for name, entry in by_set.items():
        rows.append({
            'set': name,
            'count': entry['count'],
            'slots': sorted(s for s in entry['slots'] if s),
            'main_stats': sorted(m for m in entry['mains'] if m),
        })
    rows.sort(key=lambda row: -row['count'])
    return rows[:limit]


def ask_lineup_plan(task: str, roster: list, inventory, meta: dict | None = None,
                    objective: str = 'stable_and_fast', config: dict | None = None) -> dict:
    """阵容 + 御魂联合计算。

    返回结构：
    {"name": "...", "objective": "...", "reason": "...",
     "speed_order": ["先手式神", ...],
     "shikigami": [{"name": "式神", "min_speed": 0,
                    "souls": [{"set": "破势", "slot": 2, "main_stat": "速度"}]}]}
    """
    system = (
        '你是阴阳师阵容与御魂规划器。只用用户拥有的式神与御魂；'
        '必须给出每个式神的御魂套装/号位/主属性与最低速度要求；'
        '输出 JSON：{"name":"【AI】玩法-特点","objective":"...","reason":"...",'
        '"speed_order":["式神名"],'
        '"shikigami":[{"name":"式神名","min_speed":0,'
        '"souls":[{"set":"套装名","slot":2,"main_stat":"速度"}]}]}'
    )
    payload = {
        'task': task,
        'objective': objective,
        'roster': roster,
        'soul_inventory': summarize_inventory(inventory),
        'version_note': (meta or {}).get('text', '')[:2000],
    }
    data = chat_json(
        [{'role': 'system', 'content': system},
         {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
        config=config,
    )
    units = data.get('shikigami') or []
    if not isinstance(units, list) or not units:
        raise ValueError(f'模型返回的阵容为空: {data}')
    owned = {item.get('name') for item in roster if isinstance(item, dict)}
    used = {item.get('name') for item in units if isinstance(item, dict)}
    missing = {name for name in used if owned and name not in owned}
    if missing:
        raise ValueError(f'模型使用了未拥有的式神: {missing}')

    order = data.get('speed_order') or []
    unknown_order = [name for name in order if name not in used]
    if unknown_order:
        raise ValueError(f'配速顺序包含阵容外式神: {unknown_order}')
    for unit in units:
        if not unit.get('souls'):
            raise ValueError(f"{unit.get('name')} 缺少御魂配置")
    return data


def ask_lineup(task_name: str, roster: list, meta: dict,
               config: dict | None = None) -> dict:
    """让模型给出候选阵容，并做最基本的结构校验。"""
    system = (
        '你是阴阳师的阵容规划器。只能使用用户拥有的式神和御魂，'
        '输出 JSON：{"name": "【AI】玩法-特点", "reason": "...", '
        '"shikigami": [{"name": "式神名", "soul": "御魂名", "position": 1}]}'
    )
    user = json.dumps({
        'task': task_name,
        'roster': roster,
        'meta': meta,
    }, ensure_ascii=False)
    data = chat_json(
        [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        config=config,
    )
    units = data.get('shikigami') or []
    if not isinstance(units, list) or not units:
        raise ValueError(f'模型返回的阵容为空: {data}')
    owned = {item.get('name') for item in roster if isinstance(item, dict)}
    used = {item.get('name') for item in units if isinstance(item, dict)}
    missing = {name for name in used if owned and name not in owned}
    if missing:
        raise ValueError(f'模型使用了未拥有的式神: {missing}')
    return data


def ask_lineup_spec(task_name: str, dungeon: str, roster: dict, inventory: list,
                    preset_group: int = 10, preset_team: int = 1,
                    config: dict | None = None):
    """让 DeepSeek 输出可直接送入游戏「阵容助手」建阵的结构化 LineupSpec。"""
    from module.lineup.spec import LineupSpec

    top_units = sorted(
        roster.values(),
        key=lambda s: (getattr(s, 'level', 0), getattr(s, 'star', 0), getattr(s, 'skill_total', 0)),
        reverse=True,
    )[:60]
    roster_brief = [
        {
            'name': s.name,
            'rarity': getattr(s, 'rarity', ''),
            'level': s.level,
            'star': getattr(s, 'star', 6),
            'skills': list(s.skills),
        }
        for s in top_units
    ]
    system = (
        '你是阴阳师自动阵容专家。只能从用户已拥有的高练度式神与御魂套装里挑选 5 位式神组成极速稳定阵容。'
        '必须严格输出合法 JSON：'
        '{"name":"[OAS] 副本简称","onmyoji":"晴明","description":"简短配速说明",'
        '"members":[{"name":"式神名","rarity":"SSR","soul4":"破势","soul2":"暴击",'
        '"slot2":["攻击加成","速度"],"slot4":["攻击加成"],"slot6":["暴击","暴击伤害"],'
        '"min_speed":171,"super_star":false,"full_crit":true,"lock_skill":3,"role":"主C"}]}'
    )
    user = json.dumps({
        'task': task_name,
        'dungeon': dungeon,
        'owned_shikigami': roster_brief,
        'soul_inventory': summarize_inventory(inventory, limit=20),
    }, ensure_ascii=False)
    raw = chat_json(
        [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        config=config,
    )
    raw['dungeon'] = dungeon or raw.get('dungeon') or ''
    raw['task_name'] = task_name
    raw['preset_group'] = int(preset_group)
    raw['preset_team'] = int(preset_team)
    raw['source'] = 'deepseek-ai'
    spec = LineupSpec.from_dict(raw)
    errors = spec.validate(roster)
    if errors:
        raise ValueError('AI 阵容校验失败: ' + '; '.join(errors))
    return spec
