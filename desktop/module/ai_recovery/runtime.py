"""Nonblocking device lease shared by OAS recovery processes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class RecoveryLease:
    def __init__(self, root: Path, device_key: str):
        digest = hashlib.sha256(device_key.encode('utf-8')).hexdigest()[:24]
        self.path = Path(root) / 'locks' / (digest + '.lock')
        self.file = None
        self.acquired = False

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.file = self.path.open('a+b')
            if self.file.tell() == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            if self.file is not None:
                self.file.close()
                self.file = None
        return self

    def __exit__(self, *args):
        if self.file is not None:
            try:
                if self.acquired:
                    self.file.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None
                self.acquired = False


class RecoveryStateStore:
    """Per-account cooldown and attempt windows survive worker restarts."""
    def __init__(self, root: Path, config_name: str):
        digest = hashlib.sha256(config_name.encode('utf-8')).hexdigest()[:16]
        self.path = Path(root) / 'state' / (digest + '.json')

    def _read(self):
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('invalid recovery state')
        return data

    def load(self, key: str, *, now: float, window_s: float) -> dict:
        state = self._read().get(key, {})
        if not isinstance(state, dict) or now - float(state.get('last_attempt', 0)) >= window_s:
            state = {}
        return {'last_attempt': float(state.get('last_attempt', 0)),
                'incidents': max(0, int(state.get('incidents', 0))),
                'attempted': list(state.get('attempted') or [])[-5:]}

    def save(self, key: str, state: dict):
        data = self._read()
        data[key] = state
        if len(data) > 256:
            newest = sorted(data, key=lambda item: float(data[item].get('last_attempt', 0)), reverse=True)[:256]
            data = {item: data[item] for item in newest}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)
