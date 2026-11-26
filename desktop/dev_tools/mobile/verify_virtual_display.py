# This Python file uses the following encoding: utf-8

"""虚拟屏「后台挂机」链路验证（开发机通过 adb 模拟 shell 能力）。

背景：Android 无 Root 时，应用只能对**当前焦点窗口**注入手势，所以"前台用微信、后台挂游戏"做不到。
开源项目（ALAS-AOS、XYZ/PVZ2）的做法是：用 shell 级权限（Shizuku）建一块**虚拟屏**，
把游戏 `am start --display N` 搬上去，再用 `screencap -d N` / `input -d N` 对那块屏截图和注入。
手机主屏完全不受影响 → 这才是真正意义上的"后台跑"。

本脚本用 adb（等价于 Shizuku 的 shell）验证这条链路：
    建虚拟屏 → 在虚拟屏启动 App → 截虚拟屏 → 注入点击 → 再截图对比 → 清理

用法：
    .\\toolkit\\python.exe dev_tools\\mobile\\verify_virtual_display.py [--serial 127.0.0.1:16416] [--package com.android.settings]
"""

import argparse
import hashlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'log' / 'mobile'
OUT.mkdir(parents=True, exist_ok=True)


def adb(serial: str, *args: str) -> str:
    result = subprocess.run(
        [ADB, '-s', serial, *args], capture_output=True, text=True,
        encoding='utf-8', errors='replace')
    return result.stdout


def display_ids(serial: str) -> set:
    out = adb(serial, 'shell', "dumpsys display | grep -o 'mDisplayId=[0-9]*'")
    ids = set()
    for token in out.split():
        if token.startswith('mDisplayId='):
            try:
                ids.add(int(token.split('=')[1]))
            except ValueError:
                pass
    return ids


def main() -> int:
    global ADB
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', default='127.0.0.1:16416')
    parser.add_argument('--package', default='com.android.settings')
    parser.add_argument('--adb', default=r'E:\OASX-Android\sdk\platform-tools\adb.exe')
    parser.add_argument('--width', default='1280x720')
    args = parser.parse_args()
    ADB = args.adb

    results = []

    def check(name, ok, detail=''):
        results.append((name, ok))
        print(f'[{"PASS" if ok else "FAIL"}] {name}' + (f'  ({detail})' if detail and not ok else ''))

    before = display_ids(args.serial)
    print(f'建屏前 display ids: {sorted(before)}')
    adb(args.serial, 'shell', f"settings put global overlay_display_devices '{args.width}/240'")
    time.sleep(4)
    after = display_ids(args.serial)
    created = after - before
    check('虚拟屏已创建', bool(created), f'{sorted(before)} -> {sorted(after)}')
    if not created:
        return 1
    display = max(created)
    print(f'   虚拟屏 displayId = {display}')

    launch = adb(args.serial, 'shell',
                 f'am start --display {display} -n {args.package}/.Settings')
    if 'Error' in launch:
        launch = adb(args.serial, 'shell', f'am start --display {display} -a android.intent.action.MAIN '
                                          f'-c android.intent.category.LAUNCHER {args.package}')
    time.sleep(5)
    check('App 已在虚拟屏启动', 'Error' not in launch, launch.strip()[:120])

    png1 = OUT / 'vd_before.png'
    adb(args.serial, 'shell', f'screencap -p -d {display} > /sdcard/vd_before.png')
    adb(args.serial, 'pull', '/sdcard/vd_before.png', str(png1))
    data1 = png1.read_bytes() if png1.exists() else b''
    check('能截到虚拟屏画面（PNG）', data1[:4] == b'\x89PNG' and len(data1) > 5000,
          f'bytes={len(data1)}')

    adb(args.serial, 'shell', f'input -d {display} tap 200 100')
    time.sleep(2)
    png2 = OUT / 'vd_after.png'
    adb(args.serial, 'shell', f'screencap -p -d {display} > /sdcard/vd_after.png')
    adb(args.serial, 'pull', '/sdcard/vd_after.png', str(png2))
    data2 = png2.read_bytes() if png2.exists() else b''
    changed = hashlib.sha256(data1).hexdigest() != hashlib.sha256(data2).hexdigest()
    check('注入点击后虚拟屏画面变化（证明点击落到虚拟屏）', changed,
          f'{len(data1)}B -> {len(data2)}B')

    focus = adb(args.serial, 'shell', "dumpsys window | grep -E 'mCurrentFocus'").strip()
    check('主屏焦点未指向被自动化 App（用户仍可自用）',
          args.package not in focus.split('\n')[0], focus[:120])

    adb(args.serial, 'shell', "settings put global overlay_display_devices ''")
    time.sleep(2)
    check('虚拟屏已清理', not (display_ids(args.serial) - before))

    failed = [name for name, ok in results if not ok]
    print()
    print(f'TOTAL {len(results)}  PASS {len(results) - len(failed)}  FAIL {len(failed)}')
    print(f'截图：{png1} / {png2}')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
