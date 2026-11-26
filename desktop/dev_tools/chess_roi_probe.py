# This Python file uses the following encoding: utf-8

"""棋盘顶部布局探针：对指定截图跑各 OCR ROI，并导出裁剪图供人工核对。

用途：新赛季界面变化后，「主布局 ROI 读到数字」这类问题光看日志无法定位——
需要知道每个 ROI 实际框住了什么。本工具只读截图，不碰模拟器。

用法：
    # 用运行中任务写出的实时帧（无需连设备）
    .\\toolkit\\python.exe dev_tools\\chess_roi_probe.py --live oas3
    # 或用指定文件
    .\\toolkit\\python.exe dev_tools\\chess_roi_probe.py log\\chess_ref\\shot.png
    # 额外扫描顶部横幅，粗定位「第N回合」「战/备」文字在哪
    .\\toolkit\\python.exe dev_tools\\chess_roi_probe.py --live oas3 --scan-top

输出：每个 ROI 的 OCR 结果 + `log/chess_ref/roi_<name>.png` 裁剪图。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from module.atom.ocr import RuleOcr  # noqa: E402

OUT_DIR = ROOT / 'log' / 'chess_ref'

# 名字 -> (roi, mode)；与 tasks/Chess/assets.py 保持一致
ROIS = {
    'round': ((469, 0, 67, 30), 'Single'),
    'round_2': ((507, 0, 62, 30), 'Single'),
    'mode': ((741, 0, 32, 28), 'Single'),
    'mode_2': ((706, 2, 32, 28), 'Single'),
    'now_time': ((768, 0, 32, 28), 'Single'),
    'level': ((131, 680, 47, 30), 'Single'),
    'gold': ((1198, 640, 43, 32), 'Digit'),
}


def load_live(config_name='oas3'):
    path = ROOT / 'log' / 'live' / f'{config_name}.jpg'
    if not path.exists():
        raise SystemExit(f'实时帧不存在: {path}（任务未运行？）')
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f'无法解码 {path}')
    # OAS 内部统一 RGB
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB), path


def load_file(name):
    path = Path(name)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise SystemExit(f'截图不存在: {path}')
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f'无法解码 {path}')
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB), path


def probe(image, save_crops=True):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    for name, (roi, mode) in ROIS.items():
        x, y, w, h = roi
        crop = image[y:y + h, x:x + w]
        rule = RuleOcr(
            roi=roi,
            area=roi,
            mode=mode,
            method='Default',
            keyword='',
            name=name,
        )
        try:
            value = rule.ocr(image)
        except Exception as error:  # noqa: BLE001
            value = f'<error: {error!r}>'
        results[name] = {'roi': roi, 'mode': mode, 'value': value,
                         'crop_shape': list(crop.shape)}
        if save_crops and crop.size:
            target = OUT_DIR / f'roi_{name}.png'
            cv2.imwrite(str(target), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
    return results


def scan_top(image, step=200):
    """把顶部横幅切成若干块 OCR，粗定位文字出现的位置。"""
    height, width = image.shape[:2]
    found = []
    width_now = width
    for index in range(step, width_now, step):
        x0 = max(0, index - step)
        roi = (x0, 0, min(step, width_now - x0), 40)
        rule = RuleOcr(
            roi=roi, area=roi, mode='Full', method='Default',
            keyword='', name=f'top_{x0}',
        )
        try:
            value = rule.ocr(image)
        except Exception as error:  # noqa: BLE001
            value = f'<error: {error!r}>'
        found.append((roi, value))
    return found


def main() -> int:
    args = sys.argv[1:]
    if '--live' in args:
        index = args.index('--live')
        config_name = args[index + 1] if index + 1 < len(args) else 'oas3'
        image, path = load_live(config_name)
    elif args and not args[0].startswith('--'):
        image, path = load_file(args[0])
    else:
        print(__doc__)
        return 2

    print(f'image: {path}  shape={image.shape}')
    results = probe(image)
    for name, info in results.items():
        print(f'  {name:<10} roi={info["roi"]} mode={info["mode"]:<7} '
              f'-> [{info["value"]}]')

    if '--scan-top' in args:
        print('--- top banner scan')
        for roi, value in scan_top(image):
            if value:
                print(f'  roi={roi} -> [{value}]')
    return 0


if __name__ == '__main__':
    sys.exit(main())
