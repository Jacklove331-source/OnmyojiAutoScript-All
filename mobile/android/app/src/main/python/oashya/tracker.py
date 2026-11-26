"""Portable detection and frame-to-frame tracking for the OAS Hyakkiyakou task."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import importlib.resources
from pathlib import Path
import threading
import time

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

MODEL_SHA256 = 'b3f91d0db99f50b62df3d1e70bc2d573e54bc63bbd27f540c831716ca4e02aea'
_MODEL_LOCK = threading.Lock()


def _model_file() -> str:
    from oas_mobile.bridge import _files_dir
    root = Path(_files_dir()) / 'models'
    path = root / 'oashya_fp32.onnx'
    with _MODEL_LOCK:
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
            content = importlib.resources.files('oashya').joinpath('oashya_fp32.onnx').read_bytes()
            if hashlib.sha256(content).hexdigest() != MODEL_SHA256:
                raise RuntimeError('Hyakkiyakou model checksum mismatch')
            root.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(content)
            temporary.replace(path)
    return str(path)


class Predictor:
    def __init__(self, args=None):
        args = args or {}
        self.threshold = float(args.get('conf_threshold', .5))
        if not 0 < self.threshold <= 1:
            raise ValueError('invalid detection confidence threshold')
        self._bridge = None
        self._path = None

    def preprocess(self, image):
        image = np.asarray(image)
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError('expected an RGB image')
        if image.shape[0] < 640 or image.shape[1] < 640:
            raise ValueError('expected a 640x640 detector window')
        window = image[:640, :640]
        return np.ascontiguousarray(window[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / np.float32(255)

    def postprocess(self, output):
        data = np.asarray(output, np.float32).reshape(-1, 6)
        results = []
        for x1, y1, x2, y2, confidence, label in data:
            if not np.isfinite([x1, y1, x2, y2, confidence, label]).all() or confidence < self.threshold:
                continue
            if x2 <= x1 or y2 <= y1:
                continue
            results.append((int(label), float(confidence),
                            [int((x1 + x2) / 2), int((y1 + y2) / 2), int(x2 - x1), int(y2 - y1)]))
        return results

    def predict(self, image):
        if self._bridge is None:
            from java import jclass
            self._bridge = jclass('com.oas.oasx.OnnxBridge')
            self._path = _model_file()
        tensor = self.preprocess(image).astype('<f4', copy=False)
        raw = self._bridge.infer(self._path, tensor.tobytes())
        return self.postprocess(np.array(raw, dtype=np.float32))


class Tracker:
    """Keep IDs stable and estimate horizontal velocity in pixels per second.

    Inputs and output tuples match the task's existing Tracker interface. A click
    alone never proves capture: a track expires only after it disappears.
    """
    def __init__(self, args=None):
        self.predictor = Predictor(args)
        self.detect_time = timedelta(0)
        self.track_time = timedelta(0)
        self._tracks = {}
        self._next_id = 1
        self._last_at = None

    def clear_tracks(self):
        self._tracks.clear()
        self._last_at = None

    def detect(self, image):
        if image.shape[:2] != (720, 1280):
            raise ValueError('expected a normalized 1280x720 game frame')
        # The desktop tracker detects the left and right 640x640 windows separately.
        # Stretching the full 16:9 frame changes its trained object geometry.
        detected = []
        for offset in (0, 640):
            for label, score, box in self.predictor.predict(image[:640, offset:offset + 640]):
                cx, cy, width, height = box
                detected.append((label, score, [cx + offset, cy, width, height]))
        return detected

    @staticmethod
    def _overlap(one, two):
        ax, ay, aw, ah = one
        bx, by, bw, bh = two
        left, top = max(ax - aw / 2, bx - bw / 2), max(ay - ah / 2, by - bh / 2)
        right, bottom = min(ax + aw / 2, bx + bw / 2), min(ay + ah / 2, by + bh / 2)
        overlap = max(0, right - left) * max(0, bottom - top)
        return overlap / max(1, aw * ah + bw * bh - overlap)

    def update(self, detections, delta_time):
        delta_time = max(.001, min(float(delta_time), 2))
        identities = list(self._tracks)
        costs = np.full((len(identities), len(detections)), 1000.0)
        for row, identity in enumerate(identities):
            old = self._tracks[identity]
            predicted = list(old['box'])
            predicted[0] += old['velocity'] * (delta_time + old['missing'])
            for column, (label, _, box) in enumerate(detections):
                if label != old['label']:
                    continue
                distance = abs(predicted[0] - box[0]) / max(40, predicted[2], box[2])
                vertical = abs(predicted[1] - box[1]) / max(40, predicted[3], box[3])
                if distance < 2 and vertical < 1.5:
                    costs[row, column] = 1 - self._overlap(predicted, box) + distance * .25 + vertical * .25
        matched_tracks, matched_detections = set(), set()
        if costs.size:
            rows, columns = linear_sum_assignment(costs)
            for row, column in zip(rows, columns):
                if costs[row, column] >= 1000:
                    continue
                identity = identities[row]
                old = self._tracks[identity]
                label, score, box = detections[column]
                velocity = (box[0] - old['box'][0]) / (delta_time + old['missing'])
                old.update({'label': label, 'score': score, 'box': list(box),
                            'velocity': velocity if old['hits'] == 1 else .6 * old['velocity'] + .4 * velocity,
                            'missing': 0, 'hits': old['hits'] + 1})
                matched_tracks.add(identity)
                matched_detections.add(column)
        for identity in identities:
            if identity not in matched_tracks:
                old = self._tracks[identity]
                old['missing'] += delta_time
                # Predict briefly through one missed detection; do not invent a visible target.
                if old['missing'] > .8:
                    del self._tracks[identity]
        for index, (label, score, box) in enumerate(detections):
            if index in matched_detections:
                continue
            identity = self._next_id
            self._next_id += 1
            self._tracks[identity] = {'label': label, 'score': score, 'box': list(box),
                                      'velocity': 0.0, 'missing': 0.0, 'hits': 1}
        return [(identity, item['label'], item['score'], *map(int, item['box']), float(item['velocity']))
                for identity, item in self._tracks.items() if item['missing'] == 0]

    def __call__(self, image, response=None):
        now = time.monotonic()
        delta_time = .2 if self._last_at is None else now - self._last_at
        self._last_at = now
        started = time.perf_counter()
        detections = self.detect(image)
        self.detect_time = timedelta(seconds=time.perf_counter() - started)
        started = time.perf_counter()
        tracks = self.update(detections, delta_time)
        self.track_time = timedelta(seconds=time.perf_counter() - started)
        return tracks
