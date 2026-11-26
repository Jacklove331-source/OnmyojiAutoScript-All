"""Check the actual APK image stack using a nonconstant, located template."""
import json
import platform
import sys


def run() -> str:
    result = {'ok': False, 'python': sys.version.split()[0], 'machine': platform.machine(), 'checks': {}}
    try:
        import numpy as np
        import cv2
        result.update({'numpy': np.__version__, 'cv2': cv2.__version__})
        frame = np.random.RandomState(112).randint(0, 256, (360, 640, 3), dtype=np.uint8)
        template = frame[100:140, 200:240].copy()
        matches = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(matches)
        result['checks']['template_location'] = {'ok': score > .99 and location == (200, 100),
                                                  'score': float(score), 'location': location}
        resized = cv2.resize(frame, (320, 180))
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        result['checks']['resize_color'] = {'ok': resized.shape == (180, 320, 3) and gray.shape == (360, 640)}
        encoded, data = cv2.imencode('.png', frame)
        decoded = cv2.imdecode(data, cv2.IMREAD_COLOR) if encoded else None
        result['checks']['png_roundtrip'] = {'ok': bool(encoded and np.array_equal(decoded, frame))}
        result['ok'] = all(item['ok'] for item in result['checks'].values())
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    return json.dumps(result, ensure_ascii=False)


def run_detector() -> dict:
    """Compare Android native inference with the original desktop model runtime."""
    import importlib.resources
    import io
    import time
    import numpy as np
    from java import jclass
    from oashya.tracker import Predictor, _model_file

    started = time.monotonic()
    try:
        y, x = np.indices((720, 1280), dtype=np.int32)
        frame = np.stack(((x * 3 + y * 7) % 256, (x * 11 + y * 5) % 256,
                          (x * 7 + y * 13) % 256), axis=2).astype(np.uint8)
        preprocessed = Predictor().preprocess(frame).astype('<f4', copy=False)
        fixture = importlib.resources.files('oashya').joinpath('detector_reference_input.npz').read_bytes()
        with np.load(io.BytesIO(fixture), allow_pickle=False) as data:
            tensor = data['input']
        preprocess_error = float(np.max(np.abs(preprocessed - tensor)))
        raw = jclass('com.oas.oasx.OnnxBridge').infer(_model_file(), tensor.tobytes())
        actual = np.asarray(raw, dtype=np.float32).reshape(1, 300, 6)
        reference = np.load(io.BytesIO(importlib.resources.files('oashya').joinpath('detector_reference.npy').read_bytes()),
                            allow_pickle=False)
        # The model emits 300 rows even when no target is present. Scores around
        # 1e-6 can exchange rank across CPU kernels; only their score distribution
        # is stable. The five distinct leading rows also verify boxes and classes.
        difference = float(np.max(np.abs(actual[:, :5] - reference[:, :5])))
        score_difference = float(np.max(np.abs(np.sort(actual[..., 4]) - np.sort(reference[..., 4]))))
        consistent = (np.isfinite(actual).all() and preprocess_error == 0
                      and np.allclose(actual[:, :5], reference[:, :5], rtol=.001, atol=.03)
                      and score_difference < 2e-7)
        if not consistent:
            from pathlib import Path
            from .bridge import _files_dir
            np.savez_compressed(Path(_files_dir()) / 'detector-native-debug.npz', actual=actual,
                                reference=reference, input=tensor, preprocessed=preprocessed)
        return {'ok': bool(consistent),
                'shape': list(actual.shape), 'max_abs_difference': difference, 'preprocess_max_error': preprocess_error,
                'score_max_abs_difference': score_difference, 'checked_top_rows': 5,
                'elapsed_ms': round((time.monotonic() - started) * 1000),
                'model_sha256': 'b3f91d0db99f50b62df3d1e70bc2d573e54bc63bbd27f540c831716ca4e02aea'}
    except Exception as error:
        return {'ok': False, 'error': f'{type(error).__name__}: {error}'}
