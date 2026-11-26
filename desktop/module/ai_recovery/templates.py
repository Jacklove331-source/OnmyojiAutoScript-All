# This Python file uses the following encoding: utf-8

"""图色模板的候选生成、离线验证、版本化与回滚。

安全原则：
- AI 给出的区域只生成**候选**，绝不直接覆盖原模板；
- 候选必须同时通过「正样本命中」与「负样本误匹配」检查，且与旧版本区分明显才晋级；
- 正式图色资源只在显式开启 ``auto_apply_to_source`` 后才替换，替换前备份、可一键回滚；
- 所有版本与验证报告都写进 ``assets/ai_templates/<task>/<name>/manifest.json``。
"""

from __future__ import annotations

import json
import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from module.ai_recovery.imageutil import crop_region, dhash, load_image, save_png, similarity

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Candidate:
    task: str
    name: str
    path: Path
    region: dict
    resolution: tuple[int, int]
    created: float = field(default_factory=time.time)
    source: str = 'ai'

    def to_dict(self) -> dict:
        return {
            'task': self.task, 'name': self.name, 'path': str(self.path),
            'region': self.region, 'resolution': list(self.resolution),
            'created': self.created, 'source': self.source,
        }


@dataclass
class ValidationReport:
    passed: bool
    reason: str
    positive_min: float = 0.0
    positive_avg: float = 0.0
    negative_max: float = 0.0
    false_positive_rate: float = 0.0
    samples: int = 0
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'passed': self.passed, 'reason': self.reason,
            'positive_min': round(self.positive_min, 4),
            'positive_avg': round(self.positive_avg, 4),
            'negative_max': round(self.negative_max, 4),
            'false_positive_rate': round(self.false_positive_rate, 4),
            'samples': self.samples, 'detail': self.detail,
        }


class TemplateLibrary:
    _active_cache: list = []
    _active_ts: float = 0.0

    def __init__(self, cfg, config_name: str = 'oas', root: Path | None = None):
        self.cfg = cfg.templates
        self.config_name = config_name
        base = Path(self.cfg.dir)
        self.root = Path(root) if root else (base if base.is_absolute() else ROOT / base)

    # ---------- 查询 ----------

    def list_all(self) -> list[dict]:
        items = []
        if not self.root.exists():
            return items
        for manifest in self.root.glob('*/*/manifest.json'):
            try:
                data = json.loads(manifest.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            items.append({'task': data.get('task'), 'name': data.get('name'),
                          'active_version': data.get('active_version'),
                          'versions': len(data.get('versions') or []),
                          'file': self.active_path(data.get('task'), data.get('name'))})
        return items

    def manifest(self, task: str, name: str) -> dict:
        path = self._dir(task, name) / 'manifest.json'
        if path.exists():
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        return {'task': task, 'name': name, 'versions': [], 'active_version': None}

    def active_path(self, task: str, name: str) -> str:
        data = self.manifest(task, name)
        version = (data.get('active_by_config') or {}).get(self.config_name, data.get('active_version'))
        for item in data.get('versions') or []:
            if item.get('version') == version:
                path = Path(item.get('file', ''))
                if not path.is_absolute():
                    path = self.root / path
                if path.exists():
                    return str(path)
        return ''

    # ---------- 候选 ----------

    def create_candidate(self, image: np.ndarray, region: dict, name: str, task: str,
                         *, source: str = 'ai', game_version: str = '') -> Candidate:
        if image is None:
            raise ValueError('image is None')
        height, width = image.shape[:2]
        patch = crop_region(image, region)
        if patch.size == 0 or patch.shape[0] < 8 or patch.shape[1] < 8:
            raise ValueError('candidate region too small')
        if patch.shape[0] > height * 0.8 or patch.shape[1] > width * 0.8:
            raise ValueError('candidate region too large')
        stamp = time.strftime('%Y%m%d_%H%M%S', time.localtime())
        name = _safe_name(name)
        path = self._dir(task, name) / 'candidates' / f'candidate_{stamp}.png'
        save_png(path, patch)
        return Candidate(task=task, name=name, path=path, region=dict(region),
                         resolution=(width, height), source=source)

    # ---------- 验证 ----------

    def validate(self, candidate: Candidate, positives: list[np.ndarray],
                 negatives: list[np.ndarray] | None = None) -> ValidationReport:
        template = load_image(candidate.path, as_rgb=True)
        if float(cv2.cvtColor(template, cv2.COLOR_RGB2GRAY).std()) < 1.0:
            return ValidationReport(False, 'template has insufficient visual detail')
        positives = [item for item in positives or [] if item is not None]
        if len(positives) < max(1, int(self.cfg.min_positive_samples)):
            return ValidationReport(False, 'no positive samples')
        positive_scores = [self._score(template, image) for image in positives]
        positive_scores = [score for score in positive_scores if score is not None and math.isfinite(score)]
        if not positive_scores:
            return ValidationReport(False, 'positive images smaller than template')
        positive_min = float(min(positive_scores))
        positive_avg = float(sum(positive_scores) / len(positive_scores))

        # 负样本里若混进了同一页面（整帧感知哈希高度相似），它其实是正样本，
        # 留在池子里会把「同页面同元素」误判成误匹配。先按整帧相似度剔除。
        positives_hash = [dhash(image) for image in positives]
        filtered_negatives = []
        skipped = 0
        for image in (negatives or []):
            if image is None:
                continue
            frame_hash = dhash(image)
            if any(similarity(frame_hash, positive_hash) >= 0.88 for positive_hash in positives_hash):
                skipped += 1
                continue
            filtered_negatives.append(image)
        negative_scores = self._negative_scores(template, positives, filtered_negatives)
        negative_max = float(max(negative_scores)) if negative_scores else 0.0
        fp_rate = (sum(1 for score in negative_scores if score >= self.cfg.min_match_score)
                   / len(negative_scores)) if negative_scores else 0.0

        passed = (
            positive_min >= self.cfg.min_match_score
            and negative_max <= max(0.5, self.cfg.min_match_score - self.cfg.min_negative_margin)
            and fp_rate <= self.cfg.max_false_positive
        )
        reason = 'ok'
        if positive_min < self.cfg.min_match_score:
            reason = f'positive_min {positive_min:.3f} < {self.cfg.min_match_score}'
        elif negative_max > max(0.5, self.cfg.min_match_score - self.cfg.min_negative_margin):
            reason = f'negative_max {negative_max:.3f} too high (false positive risk)'
        elif fp_rate > self.cfg.max_false_positive:
            reason = f'false_positive_rate {fp_rate:.3f} > {self.cfg.max_false_positive}'
        return ValidationReport(
            passed=passed, reason=reason, positive_min=positive_min, positive_avg=positive_avg,
            negative_max=negative_max, false_positive_rate=fp_rate, samples=len(positive_scores),
            detail={'positive_scores': [round(value, 4) for value in positive_scores],
                    'negative_scores': [round(value, 4) for value in negative_scores[:20]],
                    'negatives_skipped_same_page': skipped},
        )

    # ---------- 晋级 / 回滚 ----------

    def promote(self, candidate: Candidate, report: ValidationReport, *,
                game_version: str = '', note: str = '', replay: dict | None = None) -> dict:
        if not report.passed:
            raise ValueError(f'candidate rejected: {report.reason}')
        task_dir = self._dir(candidate.task, candidate.name)
        task_dir.mkdir(parents=True, exist_ok=True)
        data = self.manifest(candidate.task, candidate.name)
        versions = list(data.get('versions') or [])
        number = max([int(item.get('version', 0)) for item in versions] or [0]) + 1
        rel_path = f'{candidate.task}/{candidate.name}/v{number:03d}.png'
        target = self.root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate.path, target)
        versions.append({
            'version': number,
            'file': rel_path,
            'created': time.time(),
            'created_text': time.strftime('%Y-%m-%d %H:%M:%S'),
            'source': candidate.source,
            'resolution': list(candidate.resolution),
            'game_version': game_version,
            'region': candidate.region,
            'validation': report.to_dict(),
            'note': note[:200],
            'config_name': self.config_name,
            'replay': dict(replay or {}),
        })
        if len(versions) > int(self.cfg.max_versions):
            versions = versions[-int(self.cfg.max_versions):]
        data.update({'task': candidate.task, 'name': candidate.name,
                     'versions': versions, 'active_version': number})
        data.setdefault('active_by_config', {})[self.config_name] = number
        self._write_manifest(candidate.task, candidate.name, data)
        self._active_ts = 0.0
        return data

    def rollback(self, task: str, name: str) -> int | None:
        data = self.manifest(task, name)
        versions = data.get('versions') or []
        if len(versions) < 2:
            return None
        active = (data.get('active_by_config') or {}).get(self.config_name, data.get('active_version'))
        previous = None
        for item in versions:
            if (item.get('version') != active
                    and item.get('config_name', self.config_name) == self.config_name):
                previous = item
        if not previous:
            return None
        data['active_version'] = previous.get('version')
        data.setdefault('active_by_config', {})[self.config_name] = previous.get('version')
        self._write_manifest(task, name, data)
        self._active_ts = 0.0
        return data['active_version']

    # ---------- 正式资源替换（可选、可回滚） ----------

    def apply_to_source(self, task: str, name: str, source_path: str | Path) -> dict:
        active = self.active_path(task, name)
        if not active:
            raise FileNotFoundError(f'no active AI template: {task}/{name}')
        source = Path(source_path)
        if not source.is_absolute():
            source = ROOT / source
        if not source.exists():
            raise FileNotFoundError(str(source))
        backup = self.root / '_backups' / f'{source.stem}_{time.strftime("%Y%m%d_%H%M%S")}{source.suffix}'
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, backup)
        shutil.copyfile(active, source)
        record = {
            'task': task, 'name': name, 'source': str(source), 'active': active,
            'backup': str(backup), 'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        log_path = self.root / '_backups' / 'apply_log.jsonl'
        with log_path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
        return record

    def restore_source(self, backup_path: str | Path, source_path: str | Path) -> None:
        shutil.copyfile(Path(backup_path), Path(source_path))

    # ---------- 内部 ----------

    def list_active(self, *, refresh: bool = False) -> list[dict]:
        """所有已晋级模板（带 60s 缓存），供运行时“先试已学的图色”使用。"""
        now = time.time()
        if (not refresh and self._active_cache and now - self._active_ts < 60.0):
            return self._active_cache
        items: list[dict] = []
        if self.root.exists():
            for manifest in self.root.glob('*/*/manifest.json'):
                try:
                    data = json.loads(manifest.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                active = (data.get('active_by_config') or {}).get(self.config_name, data.get('active_version'))
                for item in data.get('versions') or []:
                    if item.get('version') != active:
                        continue
                    path = Path(item.get('file', ''))
                    if not path.is_absolute():
                        path = self.root / path
                    if path.exists():
                        items.append({'task': data.get('task'), 'name': data.get('name'),
                                      'path': str(path), 'region': item.get('region') or {},
                                      'config_name': item.get('config_name', ''),
                                      'resolution': item.get('resolution', []),
                                      'game_version': item.get('game_version', ''),
                                      'created': item.get('created', 0),
                                      'replay': item.get('replay') or {},
                                      'version': active})
        self._active_cache = items
        self._active_ts = now
        return items

    def match_active(self, image: np.ndarray, *, threshold: float | None = None,
                     task: str = '', game_version: str = '',
                     for_execution: bool = False) -> dict | None:
        """在当前帧上匹配已晋级模板；命中返回 name/score/中心坐标（像素）。"""
        threshold = float(threshold if threshold is not None else self.cfg.min_match_score)
        for item in self.list_active():
            if task and item.get('task') != task:
                continue
            if for_execution:
                replay = item.get('replay') or {}
                if (not replay.get('verified') or replay.get('disabled')
                        or item.get('config_name') != self.config_name
                        or time.time() - item.get('created', 0) > self.cfg.max_age_days * 86400
                        or not task or item.get('task') != task
                        or list(item.get('resolution') or []) != list(image.shape[1::-1])
                        or (item.get('game_version') and item['game_version'] != game_version)):
                    continue
            try:
                template = load_image(item['path'], as_rgb=True)
            except (OSError, FileNotFoundError):
                continue
            match = _match_location(template, image,
                                    min_margin=self.cfg.min_match_margin if for_execution else 0.0)
            if match is None:
                continue
            score, x, y, w, h = match
            if score >= threshold:
                return {'name': item.get('name'), 'score': float(score),
                        'task': item.get('task'), 'replay': item.get('replay') or {},
                        'center': (int(x + w / 2), int(y + h / 2)),
                        'rect': [int(x), int(y), int(w), int(h)],
                        'path': item['path']}
        return None

    def mark_replay(self, task: str, name: str, success: bool) -> None:
        data = self.manifest(task, name)
        active = (data.get('active_by_config') or {}).get(self.config_name, data.get('active_version'))
        for item in data.get('versions') or []:
            if item.get('version') == active:
                replay = item.get('replay') or {}
                replay['failures'] = 0 if success else int(replay.get('failures', 0)) + 1
                replay['disabled'] = replay['failures'] >= 2
                item['replay'] = replay
        self._write_manifest(task, name, data)
        self._active_ts = 0.0

    def _dir(self, task: str, name: str) -> Path:
        return self.root / _safe_name(task) / _safe_name(name)

    def _write_manifest(self, task: str, name: str, data: dict) -> None:
        path = self._dir(task, name) / 'manifest.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)

    def _score(self, template: np.ndarray, image: np.ndarray) -> float | None:
        if image is None:
            return None
        gray_t = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY) if template.ndim == 3 else template
        gray_i = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
        if gray_i.shape[0] < gray_t.shape[0] or gray_i.shape[1] < gray_t.shape[1]:
            return None
        if float(gray_t.std()) < 1.0:
            return None
        result = cv2.matchTemplate(gray_i, gray_t, cv2.TM_CCOEFF_NORMED)
        return float(np.max(np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)))

    def _negative_scores(self, template: np.ndarray, positives: list[np.ndarray],
                         negatives: list[np.ndarray] | None) -> list[float]:
        scores: list[float] = []
        height, width = template.shape[:2]
        pool = [item for item in (negatives or []) if item is not None]
        if pool:
            # Whole frames catch confusing buttons missed by random negative crops.
            return [score for image in pool if (score := self._score(template, image)) is not None]
        # 历史截图里随机裁同尺寸区域作为负样本
        for image in pool:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
            if gray.shape[0] < height or gray.shape[1] < width:
                continue
            for _ in range(6):
                if gray.shape[0] == height and gray.shape[1] == width:
                    patch = gray
                else:
                    y = int(np.random.randint(0, max(1, gray.shape[0] - height + 1)))
                    x = int(np.random.randint(0, max(1, gray.shape[1] - width + 1)))
                    patch = gray[y:y + height, x:x + width]
                score = self._score(template, cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR))
                if score is not None:
                    scores.append(score)
        # 没有负样本时，从正样本截图里裁一块“非目标区域”兜底
        if not scores:
            for image in positives:
                gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
                for _ in range(6):
                    if gray.shape[0] <= height or gray.shape[1] <= width:
                        continue
                    y = int(np.random.randint(0, gray.shape[0] - height))
                    x = int(np.random.randint(0, gray.shape[1] - width))
                    patch = gray[y:y + height, x:x + width]
                    score = self._score(template, cv2.cvtColor(patch, cv2.COLOR_GRAY2BGR))
                    if score is not None:
                        scores.append(score)
        return scores


def _match_location(template: np.ndarray, image: np.ndarray, *, min_margin: float = 0.0):
    if image is None or template is None:
        return None
    gray_t = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY) if template.ndim == 3 else template
    gray_i = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    if gray_i.shape[0] < gray_t.shape[0] or gray_i.shape[1] < gray_t.shape[1]:
        return None
    if float(gray_t.std()) < 1.0:
        return None
    result = cv2.matchTemplate(gray_i, gray_t, cv2.TM_CCOEFF_NORMED)
    result = np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if min_margin > 0:
        other = result.copy()
        x, y = max_loc
        h, w = gray_t.shape[:2]
        other[max(0, y-h//2):min(other.shape[0], y+h//2+1),
              max(0, x-w//2):min(other.shape[1], x+w//2+1)] = -1
        if float(max_val) - float(other.max()) < min_margin:
            return None
    return float(max_val), int(max_loc[0]), int(max_loc[1]), gray_t.shape[1], gray_t.shape[0]


def _safe_name(value: str) -> str:
    text = ''.join(char for char in str(value) if char.isalnum() or char in '_-')
    return text[:60] or 'unnamed'


def build_negative_pool(folder: str | Path, limit: int = 20) -> list[np.ndarray]:
    """从历史截图目录加载负样本（不递归太多，控制 IO）。"""
    folder = Path(folder)
    if not folder.exists():
        return []
    images = []
    for path in sorted(folder.glob('*.png'))[:limit]:
        try:
            images.append(load_image(path, as_rgb=True))
        except (OSError, FileNotFoundError):
            continue
    return images
