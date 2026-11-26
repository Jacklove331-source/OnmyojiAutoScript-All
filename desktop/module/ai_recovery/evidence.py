# This Python file uses the following encoding: utf-8

"""异常证据仓库：每次接管都留下可复盘材料（截图、上下文、AI 原始结果、动作与验证）。

目录：``log/ai_recovery/incidents/<时间>_<id>/``
清单：``log/ai_recovery/incidents.jsonl``（一行一次接管结论，便于统计）
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np

from module.ai_recovery.imageutil import draw_actions, save_jpeg, save_png

ROOT = Path(__file__).resolve().parents[2]


class EvidenceStore:
    def __init__(self, config_name: str = 'oas', root: Path | None = None):
        base = Path(os.environ.get('OAS_AI_LOG_DIR') or (ROOT / 'log' / 'ai_recovery'))
        self.root = Path(root) if root else base
        self.config_name = config_name
        self.index_path = self.root / 'incidents.jsonl'

    def begin(self, incident_id: str, anomaly) -> Path:
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime())
        folder = self.root / 'incidents' / f'{stamp}_{incident_id}'
        folder.mkdir(parents=True, exist_ok=True)
        self.save_json(folder, 'anomaly.json', anomaly.to_dict() if hasattr(anomaly, 'to_dict') else anomaly)
        return folder

    def save_png(self, folder: Path, name: str, image: np.ndarray) -> None:
        if image is None:
            return
        try:
            save_png(folder / name, image)
        except Exception:  # noqa: BLE001 - 证据失败不能影响恢复
            pass

    def save_annotated(self, folder: Path, name: str, image: np.ndarray, actions: list[dict]) -> None:
        if image is None:
            return
        try:
            canvas = draw_actions(image, actions)
            save_png(folder / name, canvas)
            save_jpeg(folder / name.replace('.png', '.jpg'), canvas, quality=70)
        except Exception:  # noqa: BLE001
            pass

    def save_json(self, folder: Path, name: str, data) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                                       encoding='utf-8')
        except (OSError, TypeError):
            pass

    def finish(self, folder: Path, result, extra: dict | None = None) -> None:
        payload = result.to_dict() if hasattr(result, 'to_dict') else result
        if extra:
            payload['extra'] = extra
        self.save_json(folder, 'result.json', payload)
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            line = {
                'time': time.strftime('%Y-%m-%d %H:%M:%S'),
                'config': self.config_name,
                'incident_id': payload.get('incident_id'),
                'outcome': payload.get('outcome'),
                'ai_called': payload.get('ai_called'),
                'verified': payload.get('verified'),
                'verification_version': (payload.get('verification') or {}).get('version', 0),
                'verification_reason': (payload.get('verification') or {}).get('reason', ''),
                'tokens': payload.get('tokens'),
                'cost_cny': payload.get('cost_cny'),
                'evidence_dir': str(folder),
                'message': payload.get('message'),
            }
            with self.index_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def negative_pool(self, limit: int = 12) -> list[np.ndarray]:
        """历史真实游戏截图，作为模板验证的负样本池。"""
        from module.ai_recovery.imageutil import load_image
        images = []
        candidates: list[Path] = []
        error_dir = ROOT / 'log' / 'error'
        if error_dir.exists():
            for folder in sorted(error_dir.iterdir(), reverse=True)[:30]:
                if folder.is_dir():
                    candidates.extend(sorted(folder.glob('*.png'), reverse=True)[:2])
        incidents = self.root / 'incidents'
        if incidents.exists():
            for folder in sorted(incidents.iterdir(), reverse=True)[:20]:
                candidates.extend(sorted(folder.glob('*.png'))[:2])
        for path in candidates:
            if len(images) >= limit:
                break
            try:
                images.append(load_image(path, as_rgb=True))
            except (OSError, FileNotFoundError):
                continue
        return images
