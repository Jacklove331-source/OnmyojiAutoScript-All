"""PP-OCRv6 recognition with the existing ppocronnx recognizer protocol."""
import hashlib
import json
import math
from pathlib import Path
import time

import cv2
import numpy as np
import onnxruntime as ort

from module.logger import logger as LOG


class V6Recognizer:
    def __init__(self, model_dir, legacy, providers=None):
        directory = Path(model_dir)
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        for name in ('inference.onnx', 'characters.json'):
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != manifest['sha256'][name]:
                raise ValueError('OCR model integrity check failed: ' + name)
        self.characters = ['blank'] + json.loads((directory / 'characters.json').read_text(encoding='utf-8')) + [' ']
        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.predictor = ort.InferenceSession(str(directory / 'inference.onnx'), options,
                                             providers=providers or ['CPUExecutionProvider'])
        self.input_name = self.predictor.get_inputs()[0].name
        shape = self.predictor.get_outputs()[0].shape
        if shape[-1] != len(self.characters):
            raise ValueError('OCR model and character dictionary disagree')
        self.legacy = legacy
        self.char_mask = None
        self.stats = {'calls': 0, 'lines': 0, 'fallback_lines': 0, 'errors': 0}

    def set_char_whitelist(self, chars):
        chars = None if chars is None else tuple(chars)
        self.legacy.set_char_whitelist(chars)
        if chars is None:
            self.char_mask = None
        else:
            allowed = set(chars)
            self.char_mask = np.array([True] + [char in allowed for char in self.characters[1:]], dtype=bool)

    @staticmethod
    def _normalize(image, width):
        if image is None or image.size == 0 or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError('OCR requires a nonempty three-channel RGB image')
        height, source_width = image.shape[:2]
        resized_width = min(width, max(1, math.ceil(48 * source_width / height)))
        # OAS device.image is RGB; the official model is trained on BGR.
        resized = cv2.resize(image[:, :, ::-1], (resized_width, 48)).astype(np.float32)
        tensor = np.zeros((3, 48, width), dtype=np.float32)
        tensor[:, :, :resized_width] = (resized.transpose(2, 0, 1) / 255.0 - .5) / .5
        return tensor

    def _decode(self, output):
        if output.ndim != 3 or output.shape[-1] != len(self.characters) or not np.isfinite(output).all():
            raise ValueError('Invalid OCR model output')
        if self.char_mask is not None:
            output = output.copy()
            output[:, :, ~self.char_mask] = 0
        indices = output.argmax(axis=2)
        scores = output.max(axis=2)
        results = []
        for line, probabilities in zip(indices, scores):
            keep = np.ones(len(line), dtype=bool)
            keep[1:] = line[1:] != line[:-1]
            keep &= line != 0
            text = ''.join(self.characters[int(index)] for index in line[keep])
            score = float(probabilities[keep].mean()) if keep.any() else 0.0
            results.append((text, score))
        return results

    def recognize(self, images):
        results = [('', 0.0) for _ in images]
        indices = sorted(range(len(images)), key=lambda i: images[i].shape[1] / max(1, images[i].shape[0]))
        for start in range(0, len(indices), 6):
            batch = indices[start:start + 6]
            width = min(3200, max(320, max(math.ceil(48 * images[i].shape[1] / max(1, images[i].shape[0])) for i in batch)))
            tensors = np.stack([self._normalize(images[i], width) for i in batch])
            decoded = self._decode(self.predictor.run(None, {self.input_name: tensors})[0])
            for index, result in zip(batch, decoded):
                results[index] = result
        return results

    def __call__(self, images):
        start = time.perf_counter()
        if not images:
            return [], 0.0
        self.stats['calls'] += 1
        self.stats['lines'] += len(images)
        try:
            results = self.recognize(images)
        except Exception:
            self.stats['errors'] += 1
            if self.stats['errors'] <= 3:
                LOG.exception('PP-OCRv6 failed; use the installed legacy recognizer')
            results, _ = self.legacy(images)
            self.stats['fallback_lines'] += len(images)
            return results, time.perf_counter() - start
        uncertain = [i for i, (text, score) in enumerate(results) if not text.strip() or score < .6]
        if uncertain:
            try:
                old, _ = self.legacy([images[i] for i in uncertain])
            except Exception:
                self.stats['errors'] += 1
                if self.stats['errors'] <= 3:
                    LOG.exception('Legacy OCR retry failed; retain the PP-OCRv6 results')
                return results, time.perf_counter() - start
            for index, result in zip(uncertain, old):
                # Scores from different models are not calibrated against each
                # other. Only a confident legacy reading rescues an uncertain one.
                if result[0].strip() and np.isfinite(result[1]) and result[1] >= .8:
                    results[index] = result
                    self.stats['fallback_lines'] += 1
        return results, time.perf_counter() - start
