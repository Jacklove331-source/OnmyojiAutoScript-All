# -*- coding: utf-8 -*-
"""账号数据扫描辅助工具：控制 MuMu 上的阴阳师，截图/点击/滑动，供后续 OCR 建花名册。

用法（仓库根目录）：
    .\\toolkit\\python.exe dev_tools\\roster_scan.py shot
    .\\toolkit\\python.exe dev_tools\\roster_scan.py launch
    .\\toolkit\\python.exe dev_tools\\roster_scan.py click 1128 405
    .\\toolkit\\python.exe dev_tools\\roster_scan.py swipe 640 600 640 200
    .\\toolkit\\python.exe dev_tools\\roster_scan.py wait 10
    .\\toolkit\\python.exe dev_tools\\roster_scan.py ocr log\\lineup_capture\\shrin_page.png
    .\\toolkit\\python.exe dev_tools\\roster_scan.py ocr log\\lineup_capture\\shrin_page.png 548 88 78 392

所有截图保存到 log/lineup_capture/。
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

CONFIG_NAME = os.environ.get('OAS_DEV_CONFIG', 'oas1')
OUT_DIR = ROOT / 'log' / 'lineup_capture'
PACKAGE = 'com.netease.onmyoji.wyzymnqsd_cps'


def get_device():
    from module.config.config import Config
    from module.device.device import Device
    return Device(Config(CONFIG_NAME))


def save_shot(device, prefix='scan') -> Path:
    import cv2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    image = device.screenshot()
    path = OUT_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    cv2.imwrite(str(path), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    print(f'saved: {path}')
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('shot')
    sub.add_parser('launch')
    p_click = sub.add_parser('click')
    p_click.add_argument('x', type=int)
    p_click.add_argument('y', type=int)
    p_click.add_argument('--wait', type=float, default=1.0)
    p_swipe = sub.add_parser('swipe')
    p_swipe.add_argument('x1', type=int)
    p_swipe.add_argument('y1', type=int)
    p_swipe.add_argument('x2', type=int)
    p_swipe.add_argument('y2', type=int)
    p_swipe.add_argument('--duration', type=float, default=0.5)
    p_wait = sub.add_parser('wait')
    p_wait.add_argument('seconds', type=float)
    p_name = sub.add_parser('name')
    p_name.add_argument('x', type=int)
    p_name.add_argument('y', type=int)
    p_ocr = sub.add_parser('ocr', help='离线识别已保存的式神录截图（不连设备）')
    p_ocr.add_argument('png')
    p_ocr.add_argument('roi', nargs='*', type=int, help='可选 x y w h，默认用 scan.NAME_ROIS')
    args = parser.parse_args()

    if args.cmd == 'wait':
        time.sleep(args.seconds)
        return

    if args.cmd == 'ocr':
        # 离线模式：直接对已保存的截图跑扫描用的 OCR，方便调 ROI，不连模拟器
        import cv2
        from module.lineup.scan import NAME_ROIS, _ocr_vertical, name_from_image
        image = cv2.imread(args.png)
        if image is None:
            print(f'cannot read image: {args.png}')
            return
        rois = (tuple(args.roi),) if len(args.roi) == 4 else NAME_ROIS
        for roi in rois:
            print(f'roi {roi}: name={_ocr_vertical(image, roi)!r}')
        print(f'pick: {name_from_image(image, rois)!r}')
        return

    device = get_device()
    if args.cmd == 'launch':
        device.app_start()
        print('app_start sent:', PACKAGE)
        time.sleep(2)
        save_shot(device, 'launch')
    elif args.cmd == 'shot':
        save_shot(device, 'scan')
    elif args.cmd == 'name':
        from module.lineup.scan import name_from_image
        device.click(args.x, args.y, control_check=False)
        time.sleep(1.2)
        image = device.screenshot()
        print(f'name: {name_from_image(image)!r}')
        save_shot(device, 'name')
    elif args.cmd == 'click':
        device.click(args.x, args.y, control_check=False)
        time.sleep(args.wait)
        save_shot(device, 'click')
    elif args.cmd == 'swipe':
        device.swipe((args.x1, args.y1), (args.x2, args.y2), duration=args.duration)
        time.sleep(0.8)
        save_shot(device, 'swipe')


if __name__ == '__main__':
    main()
