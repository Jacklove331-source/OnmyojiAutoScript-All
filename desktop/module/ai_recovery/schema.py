# This Python file uses the following encoding: utf-8

"""DeepSeek 返回结构的严格校验。

仓库没有 jsonschema 依赖，这里实现一个只覆盖本模块用到的关键字的小型校验器：
type / required / properties / items / enum / const / minimum / maximum /
minItems / maxItems / minLength / maxLength / pattern / additionalProperties。

校验分两层：
1. 结构层：字段类型与范围必须合法，否则整条结果作废（不允许“猜着执行”）。
2. 语义层：动作类型必须在白名单内；click 必须带坐标；坐标必须 0~1。

但单个动作非法不再否掉整次诊断（2026-11 复盘 log/error/1794913297885：
`EvoZone/device_stuck -> unavailable: AI 分析不可用：schema: $.actions[1].x: expected`）：
逐条动作校验，坏的丢掉并记 WARNING，好的照常执行；只有整体不可解析才算失败。
"""

from __future__ import annotations

import re
import math
from typing import Any

from module.logger import logger
from module.ai_recovery.types import Action, ActionType, Diagnosis, Risk, _opt_float

ACTION_SCHEMA: dict[str, Any] = {
    'type': 'object',
    'required': ['type'],
    'properties': {
        'type': {'type': 'string', 'enum': list(ActionType.values())},
        'x': {'type': 'number', 'minimum': 0.0, 'maximum': 1.0},
        'y': {'type': 'number', 'minimum': 0.0, 'maximum': 1.0},
        'text': {'type': 'string', 'maxLength': 80},
        'wait_ms': {'type': 'integer', 'minimum': 0, 'maximum': 15000},
        'page_key': {'type': 'string', 'maxLength': 80},
        'reason': {'type': 'string', 'maxLength': 200},
    },
}

DIAGNOSIS_SCHEMA: dict[str, Any] = {
    'type': 'object',
    'required': ['page', 'page_known', 'confidence', 'cause', 'actions', 'risk'],
    'properties': {
        'page': {'type': 'string', 'minLength': 1, 'maxLength': 80},
        'page_known': {'type': 'boolean'},
        'page_category': {
            'type': 'string',
            'enum': ['main', 'lobby', 'battle', 'battle_prepare', 'battle_result', 'dialog',
                     'loading', 'shop', 'task', 'login', 'unknown'],
        },
        'confidence': {'type': 'number', 'minimum': 0.0, 'maximum': 1.0},
        'cause': {'type': 'string', 'minLength': 1, 'maxLength': 400},
        'risk': {'type': 'string', 'enum': ['low', 'medium', 'high']},
        # 动作逐条校验（见 parse_diagnosis），这里只约束外层容器
        'actions': {
            'type': 'array',
            'maxItems': 4,
        },
        'expected_change': {'type': 'string', 'maxLength': 300},
        'expected_page': {'type': 'string', 'maxLength': 80},
        'needs_human': {'type': 'boolean'},
    },
}


TEMPLATE_SCHEMA = {
    'type': 'object',
    'required': ['needed', 'name', 'region'],
    'properties': {
        'needed': {'type': 'boolean'},
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 40},
        'note': {'type': 'string', 'maxLength': 200},
        'region': {
            'type': 'object',
            'required': ['x', 'y', 'w', 'h'],
            'properties': {
                'x': {'type': 'number', 'minimum': 0.0, 'maximum': 1.0},
                'y': {'type': 'number', 'minimum': 0.0, 'maximum': 1.0},
                'w': {'type': 'number', 'minimum': 0.008, 'maximum': 1.0},
                'h': {'type': 'number', 'minimum': 0.008, 'maximum': 1.0},
            },
        },
    },
}


def _parse_template(value) -> dict:
    """可选模板建议的独立校验；非法时返回空 dict（丢弃，不影响恢复）。"""
    if not isinstance(value, dict) or not value.get('needed'):
        return {}
    errors = validate(value, TEMPLATE_SCHEMA)
    if errors:
        return {}
    region = value.get('region') or {}
    x, y, w, h = float(region['x']), float(region['y']), float(region['w']), float(region['h'])
    if x + w > 1.001 or y + h > 1.001:
        return {}
    return {
        'needed': True,
        'name': str(value.get('name') or '')[:40],
        'note': str(value.get('note') or '')[:200],
        'region': {'x': x, 'y': y, 'w': w, 'h': h},
    }


class SchemaError(ValueError):
    pass


def validate(data: Any, schema: dict, path: str = '$') -> list[str]:
    """返回错误列表；空列表表示通过。"""
    errors: list[str] = []
    _validate(data, schema, path, errors)
    return errors


def _validate(data: Any, schema: dict, path: str, errors: list[str]) -> None:
    expected = schema.get('type')
    if expected:
        if not _type_ok(data, expected):
            errors.append(f'{path}: expected {expected}, got {type(data).__name__}')
            return
    if 'const' in schema and data != schema['const']:
        errors.append(f'{path}: expected const {schema["const"]!r}')
    if 'enum' in schema and data not in schema['enum']:
        errors.append(f'{path}: {data!r} not in enum')
    if isinstance(data, str):
        if len(data) < schema.get('minLength', 0):
            errors.append(f'{path}: shorter than minLength')
        if 'maxLength' in schema and len(data) > schema['maxLength']:
            errors.append(f'{path}: longer than maxLength {schema["maxLength"]}')
        if 'pattern' in schema and not re.search(schema['pattern'], data):
            errors.append(f'{path}: does not match pattern')
    elif isinstance(data, (int, float)) and not isinstance(data, bool):
        if isinstance(data, float) and not math.isfinite(data):
            errors.append(f'{path}: number must be finite')
            return
        if 'minimum' in schema and data < schema['minimum']:
            errors.append(f'{path}: below minimum {schema["minimum"]}')
        if 'maximum' in schema and data > schema['maximum']:
            errors.append(f'{path}: above maximum {schema["maximum"]}')
    elif isinstance(data, list):
        if 'minItems' in schema and len(data) < schema['minItems']:
            errors.append(f'{path}: fewer than minItems')
        if 'maxItems' in schema and len(data) > schema['maxItems']:
            errors.append(f'{path}: more than maxItems {schema["maxItems"]}')
        item_schema = schema.get('items')
        if item_schema:
            for index, item in enumerate(data):
                _validate(item, item_schema, f'{path}[{index}]', errors)
    elif isinstance(data, dict):
        for key in schema.get('required', []):
            if key not in data:
                errors.append(f'{path}: missing required {key!r}')
        properties = schema.get('properties') or {}
        for key, value in data.items():
            if key in properties:
                _validate(value, properties[key], f'{path}.{key}', errors)
            elif schema.get('additionalProperties') is False:
                errors.append(f'{path}: unexpected property {key!r}')


def _type_ok(data: Any, expected: str) -> bool:
    if expected == 'object':
        return isinstance(data, dict)
    if expected == 'array':
        return isinstance(data, list)
    if expected == 'string':
        return isinstance(data, str)
    if expected == 'number':
        return isinstance(data, (int, float)) and not isinstance(data, bool)
    if expected == 'integer':
        return isinstance(data, int) and not isinstance(data, bool)
    if expected == 'boolean':
        return isinstance(data, bool)
    if expected == 'null':
        return data is None
    return True


def _parse_actions(payload: Any, max_actions: int) -> tuple[list[Action], list[dict]]:
    """逐条校验动作：坏的丢弃并返回明细，好的保留。

    返回 (actions, dropped)。dropped 形如
    ``{'index': 1, 'action': {...}, 'reasons': ['$.actions[1].x: expected number, got str']}``。
    """
    actions: list[Action] = []
    dropped: list[dict] = []
    items = payload.get('actions') or []
    if not isinstance(items, list):
        raise SchemaError(f'$.actions: expected array, got {type(items).__name__}')
    for index, item in enumerate(items):
        reasons = validate(item, ACTION_SCHEMA, f'$.actions[{index}]')
        if not reasons and isinstance(item, dict):
            # 语义层：这些坏字段在 JSON Schema 里表达不出来，单独判
            if item.get('type') == ActionType.CLICK.value and (item.get('x') is None or item.get('y') is None):
                reasons = [f'$.actions[{index}]: click action requires x/y']
            elif item.get('type') == ActionType.GOTO_PAGE.value and not item.get('page_key'):
                reasons = [f'$.actions[{index}]: goto_page action requires page_key']
        if reasons:
            dropped.append({'index': index, 'action': item, 'reasons': reasons[:4]})
            logger.warning(f'[AI] 丢弃非法动作 #{index}: {reasons[0]}（其余动作继续执行）')
            continue
        actions.append(Action.from_dict(item))
    if len(actions) > max_actions:
        actions = actions[:max_actions]
    return actions, dropped


def parse_diagnosis(payload: Any, *, max_actions: int = 4) -> Diagnosis:
    """校验并解析成 Diagnosis；结构不可解析时才抛 SchemaError（调用方退回本地逻辑）。"""
    errors = validate(payload, DIAGNOSIS_SCHEMA)
    if errors:
        raise SchemaError('; '.join(errors[:6]))
    if not isinstance(payload, dict):
        raise SchemaError('diagnosis must be an object')

    actions, dropped = _parse_actions(payload, max_actions)

    risk = str(payload.get('risk') or Risk.MEDIUM.value)
    if risk not in {item.value for item in Risk}:
        risk = Risk.MEDIUM.value
    # 可选字段单独校验：坏了就丢弃该字段，不因一个坏的可选字段否掉整条诊断
    template = _parse_template(payload.get('template_update'))

    return Diagnosis(
        page=str(payload.get('page') or '').strip()[:80],
        page_known=bool(payload.get('page_known')),
        page_category=str(payload.get('page_category') or 'unknown'),
        confidence=float(payload.get('confidence') or 0.0),
        cause=str(payload.get('cause') or '')[:400],
        actions=actions,
        expected_change=str(payload.get('expected_change') or '')[:300],
        expected_page=str(payload.get('expected_page') or '').strip()[:80],
        risk=risk,
        template_update=template,
        needs_human=bool(payload.get('needs_human')),
        raw=payload,
        dropped_actions=dropped,
    )
