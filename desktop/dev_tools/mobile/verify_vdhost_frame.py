# This Python file uses the following encoding: utf-8

"""无头虚拟屏（shell 宿主）端到端验证：建屏 → 游戏投屏 → 取帧 → 主屏检查。

用法：.\\toolkit\\python.exe dev_tools\\mobile\\verify_vdhost_frame.py
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request

ADB = r'E:\OASX-Android\sdk\platform-tools\adb.exe'
SERIAL = '127.0.0.1:16416'
BASE = 'http://127.0.0.1:22267'
GAME = 'com.netease.onmyoji.wyzymnqsd_cps'
LAUNCHER = f'{GAME}/com.netease.onmyoji.tag0'


def adb(*args: str, timeout: int = 120) -> str:
    result = subprocess.run([ADB, '-s', SERIAL, *args], capture_output=True, text=True,
                            timeout=timeout, encoding='utf-8', errors='replace')
    return (result.stdout or '') + (result.stderr or '')


def post(path: str, timeout: int = 240) -> dict:
    request = urllib.request.Request(BASE + path, data=b'', method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--wait', type=int, default=14)
    args = parser.parse_args()

    results = []

    def check(name, ok, detail=''):
        results.append((name, bool(ok)))
        print(f'[{"PASS" if ok else "FAIL"}] {name}' + (f'  ({detail})' if detail else ''))

    started = post('/vd/host?action=start')
    display = int(started.get('display') or -1)
    check('无头屏创建（shell 宿主）', display > 0, str(started))
    if display <= 0:
        print(f'\nTOTAL {len(results)}  PASS 0  FAIL {len(results)}')
        return 1

    adb('shell', f'am force-stop {GAME}')
    time.sleep(1)
    adb('shell', f'am start --display {display} -n {LAUNCHER}')
    time.sleep(args.wait)

    placement = adb('shell', f'dumpsys activity activities | grep -A2 "Display #{display}" | head -4')
    check('游戏已在该屏', GAME in placement, placement.strip()[:80])

    focus = adb('shell', 'dumpsys window | grep mCurrentFocus | head -1')
    check('主屏前台不是游戏', 'onmyoji' not in focus, focus.strip()[:60])

    png_path = r'D:\3D\YYS2\_oasx_src\logs\vdhost_frame_live.png'
    try:
        with urllib.request.urlopen(BASE + '/vd/frame', timeout=60) as response:
            data = response.read()
        with open(png_path, 'wb') as handle:
            handle.write(data)
        check('宿主帧服务取到 PNG', len(data) > 10_000, f'{len(data)} bytes')
        print('   截图：', png_path)
    except Exception as error:  # noqa: BLE001
        check('宿主帧服务取到 PNG', False, str(error)[:80])

    failed = [name for name, ok in results if not ok]
    print()
    print(f'TOTAL {len(results)}  PASS {len(results) - len(failed)}  FAIL {len(failed)}')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
