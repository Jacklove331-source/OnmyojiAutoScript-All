"""Local health and pending-repair summaries; no model calls or device actions."""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter, deque
from pathlib import Path

from module.ai_recovery.budget import BudgetGuard
from module.ai_recovery.config import ROOT
from module.ai_recovery.kb import KnowledgeBase
from module.ai_recovery.templates import TemplateLibrary
from module.ai_recovery.learning import LearningMemory


def log_root() -> Path:
    return Path(os.environ.get('OAS_AI_LOG_DIR') or ROOT / 'log' / 'ai_recovery')


def pending_repairs(config_name: str) -> list[dict]:
    digest = hashlib.sha256(config_name.encode('utf-8')).hexdigest()[:12]
    path = log_root() / f'repair_requests_{digest}.jsonl'
    latest = {}
    if path.exists():
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(item, dict) and item.get('id'):
                    latest[item['id']] = item
    return sorted((item for item in latest.values() if item.get('status') == 'pending'),
                  key=lambda item: item.get('updated_at', 0), reverse=True)


def health_report(cfg, hours: float = 24) -> dict:
    since = time.time() - max(0.0, hours) * 3600
    outcomes = Counter()
    failures = []
    records = []
    index = log_root() / 'incidents.jsonl'
    if index.exists():
        with index.open(encoding='utf-8') as stream:
            lines = deque(stream, maxlen=10000)
        for line in lines:
            try:
                item = json.loads(line)
                stamp = time.mktime(time.strptime(item['time'], '%Y-%m-%d %H:%M:%S'))
            except (ValueError, TypeError, KeyError):
                continue
            if item.get('config') != cfg.config_name or stamp < since:
                continue
            records.append(item)
            outcomes[item.get('outcome', 'unknown')] += 1
            if item.get('outcome') in {'stopped', 'unavailable', 'needs_human'}:
                failures.append({key: item.get(key) for key in
                                 ('time', 'outcome', 'message', 'evidence_dir')})
    library = TemplateLibrary(cfg, config_name=cfg.config_name)
    active = library.list_active(refresh=True)
    repairs = pending_repairs(cfg.config_name)
    from module.ai_recovery.repair_store import RepairStore
    code_repairs = RepairStore(log_root()).status(cfg.config_name) if cfg.code_repair.enabled else {'enabled':False}
    return {
        'config': cfg.config_name, 'enabled': cfg.enabled, 'mode': cfg.mode,
        'period_hours': hours, 'outcomes': dict(outcomes),
        'incidents': len(records), 'recent_failures': failures[-10:],
        'verified_recoveries': sum(item.get('outcome') == 'recovered' and
                                   item.get('verification_version', 0) >= 1 for item in records),
        'legacy_recoveries_need_review': sum(item.get('outcome') == 'recovered' and
                                              item.get('verification_version', 0) < 1 for item in records),
        'budget': BudgetGuard(cfg, config_name=cfg.config_name).snapshot(),
        'knowledge': KnowledgeBase(cfg, config_name=cfg.config_name).stats(),
        'learning': LearningMemory(cfg, log_root()).stats(),
        'code_repair': code_repairs,
        'templates': {'active': len(active),
                      'verified_click_plans': sum(bool((item.get('replay') or {}).get('verified'))
                                                  and not (item.get('replay') or {}).get('disabled')
                                                  and item.get('config_name') == cfg.config_name
                                                  for item in active)},
        'pending_repairs': len(repairs), 'repair_requests': repairs[:10],
        'safety': {'verification_frames': cfg.safety.verification_frames,
                   'max_replans': cfg.safety.replan_attempts,
                   'source_template_overwrite': cfg.templates.auto_apply_to_source},
    }
