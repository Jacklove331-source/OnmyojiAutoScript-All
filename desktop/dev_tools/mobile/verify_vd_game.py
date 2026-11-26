# This Python file uses the following encoding: utf-8

"""验证「真·后台挂机」全链路（走 App 内 Shizuku 虚拟屏，不碰 adb 注入）。

前置：
1. 模拟器/真机已启动 Shizuku，并在 OASX Mobile 里点过「始终允许」；
2. 宿主已 `adb forward tcp:22267 tcp:22267`。

检查项：
1. Shizuku 可用（backend=shizuku）；
2. 建虚拟屏拿到 displayId；
3. 在虚拟屏上启动目标 App（默认阴阳师）；
4. 从虚拟屏截到真实画面（PNG、非全黑）；
5. 往虚拟屏注入点击，画面发生变化（证明点击落到被自动化 App 上）；
6. 主屏焦点没有变成被自动化 App（用户仍可正常用手机）。

用法：
    .\\toolkit\\python.exe dev_tools\\mobile\\verify_vd_game.py [--package <pkg>] [--keep]
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'log' / 'mobile'
OUT.mkdir(parents=True, exist_ok=True)
BASE = 'http://127.0.0.1:22267'
ADB = r'E:\OASX-Android\sdk\platform-tools\adb.exe'
SERIAL = '127.0.0.1:16416'


def post(path: str, timeout: int = 180) -> dict:
    request = urllib.request.Request(BASE + path, data=b'', method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def get_json(path: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def fetch_png(display: int, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(f'{BASE}/vd/screenshot?display={display}', timeout=timeout) as response:
        return response.read()


def adb(*args: str) -> str:
    return subprocess.run([ADB, '-s', SERIAL, *args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace').stdout


def not_black(png: bytes) -> tuple:
    """粗判是否全黑：统计 PNG 解码后字节的离散度（不依赖 PIL）。"""
    if len(png) < 5000:
        return (False, 0)
    distinct = len(set(png[1000:8000]))
    return (distinct > 32, distinct)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', default='com.netease.onmyoji.wyzymnqsd_cps')
    parser.add_argument('--keep', action='store_true', help='结束时保留虚拟屏（默认保留）')
    args = parser.parse_args()

    results = []

    def check(name, ok, detail=''):
        results.append((name, bool(ok)))
        print(f'[{"PASS" if ok else "FAIL"}] {name}' + (f'  ({detail})' if detail and not ok else ''))

    state = get_json('/state')
    check('Shizuku 后端可用', state.get('shizuku_ready') is True, state.get('shizuku', ''))
    print(f'   shizuku: {state.get("shizuku")}')

    # 一键：建屏 → 启动 App → 把任务搬到虚拟屏（游戏会自己跑回主屏，必须搬）
    ensured = post(f'/vd/ensure?package={args.package}', timeout=240)
    display = ensured.get('display', -1)
    check('虚拟屏 + App 迁移就绪', ensured.get('ok') is True and display >= 0, str(ensured))
    if display < 0:
        return 1
    print(f'   displayId = {display}')

    # 游戏冷启动要等：轮询截图直到画面有内容（最多 60s）
    png = b''
    ok_pixels, distinct = False, 0
    for _ in range(12):
        time.sleep(5)
        try:
            png = fetch_png(display)
        except Exception:
            continue
        ok_pixels, distinct = not_black(png)
        if ok_pixels:
            break
    (OUT / 'vd_game_before.png').write_bytes(png)
    check('虚拟屏截到真实画面（非全黑）', ok_pixels, f'bytes={len(png)} distinct={distinct}')

    before_hash = hashlib.sha256(png).hexdigest()
    tapped = post(f'/vd/tap?display={display}&x=640&y=600')
    check('虚拟屏注入点击返回 ok', tapped.get('ok') is True, str(tapped))
    time.sleep(4)
    png2 = fetch_png(display)
    (OUT / 'vd_game_after.png').write_bytes(png2)
    after_hash = hashlib.sha256(png2).hexdigest()
    check('点击后画面发生变化（点击真的落到游戏上）', before_hash != after_hash,
          'hash unchanged')

    # 主屏与虚拟屏的焦点要分开看：`dumpsys window displays` 会按 display 分块打印
    windows = adb('shell', "dumpsys window displays | grep -E 'Display: mDisplayId|mCurrentFocus='")
    main_block = ''
    capture = False
    for line in windows.splitlines():
        if 'Display: mDisplayId=0' in line:
            capture = True
            continue
        if 'Display: mDisplayId=' in line:
            capture = False
        if capture and 'mCurrentFocus=' in line:
            main_block = line.strip()
            break
    check('主屏焦点没有被自动化 App 占据（用户仍可正常用手机）',
          args.package not in main_block, main_block[:140])
    check('虚拟屏上确实是被自动化的 App', args.package in windows,
          windows[:160].replace(chr(10), ' | '))
    print(f'   主屏焦点: {main_block[:100] or "(null)"}')

    if not args.keep:
        post('/vd/release')

    failed = [name for name, ok in results if not ok]
    print()
    print(f'TOTAL {len(results)}  PASS {len(results) - len(failed)}  FAIL {len(failed)}')
    print(f'截图：{OUT / "vd_game_before.png"} / {OUT / "vd_game_after.png"}')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
