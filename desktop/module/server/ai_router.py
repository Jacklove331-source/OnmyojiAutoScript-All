"""Read-only AI recovery summaries for the desktop UI.

These routes inspect local records only. They never enable recovery, invoke a
model, operate a device, or create an account/configuration.
"""
from __future__ import annotations

import json
import math
import re
from itertools import islice
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response

from module.ai_recovery.config import ROOT, load_config
from module.ai_recovery.reporting import health_report, pending_repairs


ai_app = APIRouter(prefix='/ai', tags=['ai-recovery'])
CONFIG_DIR = ROOT / 'config'
_REDACTED = '[redacted]'
_PATH = re.compile(r'https?://\S+|[A-Za-z]:[\\/][^\r\n<>"\']*|\\\\[^\r\n<>"\']+|(?<![\w:])/(?:[^\s<>"\']+)')
_RELATIVE_PATH = re.compile(r'(?<!\w)(?:\.{1,2}[\\/])?[\w.-]+[\\/][\w./\\-]+')
_TOKEN = re.compile(r'\b(?:sk|sess)-[A-Za-z0-9_-]+', re.IGNORECASE)
_CREDENTIAL = re.compile(
    r'(?i)\b(?:api[_ -]?key|authorization|access[_ -]?token|password)\s*[:=]\s*'
    r'(?:bearer\s+)?[^\s,;]+')


def _account(name: str) -> str:
    # ConfigManager.all_script_files() creates oas1 when empty: do not call it.
    if (not name or name != name.strip() or len(name) > 80
            or not all(c.isalnum() or c in '_- ' for c in name)):
        raise HTTPException(400, 'Invalid account name')
    if name.casefold() == 'template':
        raise HTTPException(404, 'Account not found')
    try:
        root = Path(CONFIG_DIR).resolve()
        path = (root / (name + '.json')).resolve(strict=True)
        if path.parent != root or not path.is_file():
            raise HTTPException(404, 'Account not found')
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict) or not isinstance(data.get('script'), dict):
            raise HTTPException(404, 'Account not found')
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(404, 'Account not found') from None
    return path.stem


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value, default=0):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return default
    try:
        return max(0, value) if math.isfinite(value) else default
    except (OverflowError, TypeError):
        return default


def _count(value) -> int:
    return int(_number(value))


def _text(value, secrets=(), limit=500) -> str:
    if not isinstance(value, str):
        return ''
    # Redact before truncation so a credential cut at the limit cannot leak.
    for secret in secrets:
        if secret:
            value = value.replace(secret, _REDACTED)
    value = _CREDENTIAL.sub(_REDACTED, value)
    value = _TOKEN.sub(_REDACTED, value)
    value = _PATH.sub(_REDACTED, value)
    value = _RELATIVE_PATH.sub(_REDACTED, value)
    value = ''.join(c for c in value if c >= ' ' and c != '\x7f')
    return value[:limit]


def _verification(value, secrets) -> dict:
    value = _mapping(value)
    result = {key: _text(value[key], secrets, 160) for key in
              ('kind', 'reason', 'page_before', 'page_after', 'expected_page')
              if key in value}
    if 'verified' in value:
        result['verified'] = value['verified'] is True
    for key in ('version', 'observations'):
        if key in value:
            result[key] = _count(value[key])
    return result


def _repair(value, secrets) -> dict:
    value = _mapping(value)
    result = {key: _text(value.get(key), secrets, 160 if key != 'message' else 500)
              for key in ('id', 'task', 'anomaly', 'kind', 'status', 'message')}
    result.update({key: _number(value.get(key))
                   for key in ('created_at', 'updated_at')})
    result['occurrences'] = _count(value.get('occurrences'))
    result['verification'] = _verification(value.get('verification'), secrets)
    return result


def _repairs(values, name, secrets, limit) -> list[dict]:
    if not isinstance(values, list):
        return []
    candidates = (item for item in values if isinstance(item, dict)
                  and item.get('config') == name and item.get('status') == 'pending')
    return [_repair(item, secrets) for item in islice(candidates, limit)]


def _budget(value, name, secrets) -> dict:
    value = _mapping(value)
    state = _mapping(value.get('state'))
    limits = _mapping(value.get('limits'))
    result = {'config': name, 'state': {'day': _text(state.get('day'), secrets, 10)},
              'limits': {}, 'state_readable': value.get('state_readable') is True,
              'incident_calls': _count(value.get('incident_calls')),
              'reservation_cny': _number(value.get('reservation_cny'))}
    for key in ('hour', 'calls_today', 'calls_hour', 'tokens_today'):
        result['state'][key] = _count(state.get(key))
    for key in ('cost_today', 'last_call'):
        result['state'][key] = _number(state.get(key))
    for key in ('per_incident_calls', 'per_hour_calls', 'per_day_calls'):
        result['limits'][key] = _count(limits.get(key))
    result['limits']['per_day_cny'] = _number(limits.get('per_day_cny'))
    return result


def _health(raw, cfg, name, secrets) -> dict:
    raw = _mapping(raw)
    result = {'config': name, 'enabled': cfg.enabled is True,
              'mode': cfg.mode if cfg.mode in ('observe', 'auto') else 'observe',
              'has_key': any(bool(secret) for secret in secrets),
              'period_hours': _number(raw.get('period_hours'), 24)}
    for key in ('incidents', 'verified_recoveries', 'legacy_recoveries_need_review',
                'pending_repairs'):
        result[key] = _count(raw.get(key))
    # Outcome keys are also untrusted log input; expose only known categories.
    outcomes = _mapping(raw.get('outcomes'))
    known = ('recovered', 'observed', 'stopped', 'unavailable', 'needs_human',
             'cooldown', 'skipped', 'unknown')
    result['outcomes'] = {key: _count(outcomes[key]) for key in known if key in outcomes}
    result['budget'] = _budget(raw.get('budget'), name, secrets)
    for section, keys in (
        ('knowledge', ('entries', 'reliable', 'legacy_not_replayed')),
        ('templates', ('active', 'verified_click_plans')),
    ):
        value = _mapping(raw.get(section))
        result[section] = {key: _count(value.get(key)) for key in keys}
    safety = _mapping(raw.get('safety'))
    result['safety'] = {'verification_frames': _count(safety.get('verification_frames')),
                        'max_replans': _count(safety.get('max_replans')),
                        'source_template_overwrite': safety.get('source_template_overwrite') is True}
    result['repair_requests'] = _repairs(raw.get('repair_requests'), name, secrets, 10)
    failures = raw.get('recent_failures')
    result['recent_failures'] = [
        {key: _text(item.get(key), secrets, 500 if key == 'message' else 40)
         for key in ('time', 'outcome', 'message')}
        for item in (failures[-10:] if isinstance(failures, list) else [])
        if isinstance(item, dict)]
    return result


def _context(config):
    name = _account(config)
    # Refresh key presence after a local key-file edit, without modifying it.
    cfg = load_config(config_name=name, force=True)
    return name, cfg, (cfg.api_key,)


def _unavailable():
    # Exception text can contain local paths or credentials.
    return HTTPException(503, 'AI recovery report is temporarily unavailable',
                         headers={'Cache-Control': 'no-store'})


@ai_app.get('/health')
def get_health(response: Response, config: str = Query(..., max_length=80)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        name, cfg, secrets = _context(config)
        return _health(health_report(cfg), cfg, name, secrets)
    except HTTPException:
        raise
    except Exception:
        raise _unavailable() from None


@ai_app.get('/repairs')
def get_repairs(response: Response, config: str = Query(..., max_length=80)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        name, cfg, secrets = _context(config)
        values = pending_repairs(name)
        # The UI only needs a bounded page; total still describes the full queue.
        items = _repairs(values, name, secrets, 100)
        total = sum(isinstance(item, dict) and item.get('config') == name
                    and item.get('status') == 'pending' for item in values)
        return {'config': name, 'total': total, 'items': items}
    except HTTPException:
        raise
    except Exception:
        raise _unavailable() from None
