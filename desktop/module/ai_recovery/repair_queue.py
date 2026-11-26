"""Persist unresolved incidents for a later code/template repair workflow."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


def enqueue(root: Path, config_name: str, anomaly, result) -> dict:
    config_file = hashlib.sha256(config_name.encode('utf-8')).hexdigest()[:12]
    path = Path(root) / ('repair_requests_' + config_file + '.jsonl')
    from module.ai_recovery.learning import error_fingerprint
    fingerprint = hashlib.sha256(
        (config_name + '|' + error_fingerprint(anomaly)).encode('utf-8')).hexdigest()[:20]
    previous = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(item, dict) and item.get('id') == fingerprint:
                previous = item
    kind = 'workflow'
    recovered = result.outcome.value == 'recovered'
    if recovered and not previous:
        return {}
    if anomaly.type in {'template_miss', 'recognition_conflict', 'screen_changed'}:
        kind = 'ui_template'
    elif anomaly.type == 'ocr_fail':
        kind = 'ocr'
    elif result.outcome.value == 'unavailable':
        kind = 'connectivity_or_model'
    item = {
        'id': fingerprint, 'config': config_name, 'task': anomaly.task,
        'anomaly': anomaly.type, 'kind': kind, 'status': 'resolved' if recovered else 'pending',
        'occurrences': int(previous.get('occurrences', 0)) + (0 if recovered else 1),
        'created_at': previous.get('created_at', time.time()), 'updated_at': time.time(),
        'latest_incident': anomaly.incident_id, 'evidence_dir': result.evidence_dir,
        'message': result.message[:400], 'verification': result.verification,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + '\n')
    try:
        from module.ai_recovery.config import load_config
        from module.ai_recovery.repair_worker import enqueue_fault
        enqueue_fault(load_config(config_name=config_name),root,anomaly,result)
    except Exception:
        # Code maintenance availability must not interrupt native UI recovery.
        pass
    return item
