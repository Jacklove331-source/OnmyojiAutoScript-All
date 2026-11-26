# This Python file uses the following encoding: utf-8

"""图像工具：感知哈希、变化比例、截图编码（含裁剪/脱敏）。

不依赖 OAS 任何模块，可离线单测。OAS 的 `device.image` 是 RGB 顺序。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import cv2
import numpy as np


def to_gray(image: np.ndarray) -> np.ndarray:
    if image is None:
        raise ValueError('image is None')
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def dhash(image: np.ndarray, hash_size: int = 8) -> str:
    """64 位差值哈希，十六进制 16 字符。对亮度/轻微动画稳健。"""
    gray = to_gray(image)
    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bool(bit))
    return f'{value:0{hash_size * hash_size // 4}x}'


def ahash(image: np.ndarray, hash_size: int = 8) -> str:
    gray = to_gray(image)
    resized = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
    mean = float(resized.mean())
    value = 0
    for bit in (resized > mean).flatten():
        value = (value << 1) | int(bool(bit))
    return f'{value:0{hash_size * hash_size // 4}x}'


def hamming(hex_a: str, hex_b: str) -> int:
    try:
        return bin(int(hex_a, 16) ^ int(hex_b, 16)).count('1')
    except (TypeError, ValueError):
        return 999


def similarity(hash_a: str, hash_b: str) -> float:
    """1.0 = 完全相同，0.0 = 完全不同。"""
    if not hash_a or not hash_b:
        return 0.0
    return 1.0 - hamming(hash_a, hash_b) / 64.0


def change_ratio(image_a: np.ndarray, image_b: np.ndarray, size=(64, 36)) -> float:
    """两帧平均像素差（0~1）。用于判断“画面是否发生可见变化”。"""
    if image_a is None or image_b is None:
        return 1.0
    try:
        gray_a = cv2.resize(to_gray(image_a), size, interpolation=cv2.INTER_AREA).astype(np.float32)
        gray_b = cv2.resize(to_gray(image_b), size, interpolation=cv2.INTER_AREA).astype(np.float32)
    except cv2.error:
        return 1.0
    return float(np.abs(gray_a - gray_b).mean() / 255.0)


def content_hash(image: np.ndarray) -> str:
    """截图字节级内容哈希（去重用，不做相似匹配）。"""
    if image is None:
        return ''
    small = cv2.resize(to_gray(image), (128, 72), interpolation=cv2.INTER_AREA)
    return hashlib.sha1(small.tobytes()).hexdigest()[:16]


def region_to_pixels(region: dict, width: int, height: int) -> tuple[int, int, int, int]:
    """归一化 {x,y,w,h} → 像素 (x, y, w, h)，裁到画面内。"""
    x = float(region.get('x', 0.0))
    y = float(region.get('y', 0.0))
    w = float(region.get('w', 0.0))
    h = float(region.get('h', 0.0))
    x0 = max(0, min(width - 1, int(round(x * width))))
    y0 = max(0, min(height - 1, int(round(y * height))))
    x1 = max(x0 + 1, min(width, int(round((x + w) * width))))
    y1 = max(y0 + 1, min(height, int(round((y + h) * height))))
    return x0, y0, x1 - x0, y1 - y0


def crop_region(image: np.ndarray, region: dict) -> np.ndarray:
    height, width = image.shape[:2]
    x, y, w, h = region_to_pixels(region, width, height)
    return image[y:y + h, x:x + w]


def crop_pixels(image: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    height, width = image.shape[:2]
    x = max(0, min(width - 1, int(x)))
    y = max(0, min(height - 1, int(y)))
    w = max(1, min(width - x, int(w)))
    h = max(1, min(height - y, int(h)))
    return image[y:y + h, x:x + w]


def encode_jpeg(image: np.ndarray, max_side: int = 960, quality: int = 68,
                is_rgb: bool = True, redact_top_px: int = 0) -> bytes:
    """把截图编码成体积可控的 JPEG。redact_top_px > 0 时把顶部状态栏涂黑。"""
    if image is None:
        raise ValueError('image is None')
    frame = image
    if redact_top_px > 0 and frame.ndim == 3 and frame.shape[0] > redact_top_px + 10:
        frame = frame.copy()
        frame[:redact_top_px, :, :] = 0
    height, width = frame.shape[:2]
    scale = min(1.0, float(max_side) / float(max(width, height)))
    if scale < 1.0:
        frame = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))),
                           interpolation=cv2.INTER_AREA)
    if frame.ndim == 3 and is_rgb:
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError('jpeg encode failed')
    return buffer.tobytes()


def save_png(path: str | Path, image: np.ndarray, is_rgb: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = cv2.cvtColor(image, cv2.COLOR_RGB2BGR) if (is_rgb and image.ndim == 3) else image
    ok, buffer = cv2.imencode('.png', frame)
    if not ok:
        raise RuntimeError(f'png encode failed: {path}')
    path.write_bytes(buffer.tobytes())
    return path


def save_jpeg(path: str | Path, image: np.ndarray, quality: int = 82, is_rgb: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = cv2.cvtColor(image, cv2.COLOR_RGB2BGR) if (is_rgb and image.ndim == 3) else image
    ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError(f'jpeg encode failed: {path}')
    path.write_bytes(buffer.tobytes())
    return path


def load_image(path: str | Path, as_rgb: bool = True) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(str(path))
    if as_rgb:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return image


def draw_actions(image: np.ndarray, actions: list[dict]) -> np.ndarray:
    """在证据截图上标出 AI 点击点，便于人工复盘。"""
    canvas = image.copy()
    height, width = canvas.shape[:2]
    for index, action in enumerate(actions or []):
        if action.get('x') is None or action.get('y') is None:
            continue
        x = int(max(0, min(width - 1, action['x'] * width)))
        y = int(max(0, min(height - 1, action['y'] * height)))
        cv2.circle(canvas, (x, y), 26, (255, 0, 0), 3)
        cv2.putText(canvas, str(index + 1), (x + 28, y + 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (255, 0, 0), 2, cv2.LINE_AA)
    return canvas


def now_stamp() -> str:
    return time.strftime('%Y%m%d_%H%M%S', time.localtime())
