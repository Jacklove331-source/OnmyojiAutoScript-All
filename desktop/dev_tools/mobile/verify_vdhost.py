"""一键「重建→安装→重启 App→起无头屏→验证」脚本，省得每次手敲一长串。

用法：.\\toolkit\\python.exe dev_tools\\mobile\\verify_vdhost.py [--observe 20]
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


def adb(*args: str, timeout: int = 120) -> str:
    result = subprocess.run([ADB, '-s', SERIAL, *args], capture_output=True, text=True,
                            timeout=timeout, encoding='utf-8', errors='replace')
    return (result.stdout or '') + (result.stderr or '')


def post(path: str, timeout: int = 120) -> dict:
    request = urllib.request.Request(BASE + path, data=b'', method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def get(path: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--observe', type=int, default=20)
    args = parser.parse_args()

    results = []

    def check(name, ok, detail=''):
        results.append((name, bool(ok)))
        print(f'[{"PASS" if ok else "FAIL"}] {name}' + (f'  ({detail})' if detail else ''))

    adb('forward', 'tcp:22267', 'tcp:22267')
    check('服务在线（/health）', bool(get('/health').get('ok')))

    started = post('/vd/host?action=start&width=1280&height=720')
    display = int(started.get('display') or -1)
    check('无头屏创建成功', display >= 0, str(started))

    if display >= 0:
        # 把游戏搬到该屏并检查主屏
        adb('shell', 'am force-stop com.netease.onmyoji.wyzymnqsd_cps')
        time.sleep(1)
        activity = adb('shell',
                       'cmd package resolve-activity --brief -c android.intent.category.LAUNCHER '
                       'com.netease.onmyoji.wyzymnqsd_cps').strip().splitlines()[-1].strip()
        adb('shell', f'am start -n {activity}')
        time.sleep(6)
        adb('shell', f'am display move-stack $(dumpsys activity activities | '
                     f'grep -B1 "A=10052:com.netease.onmyoji" | head -1 | '
                     f'sed "s/.*#\\([0-9]*\\).*/\\1/") {display}')
        time.sleep(3)
        tasks = adb('shell', f'dumpsys activity activities | sed -n "/Display #{display}/,/Display #0/p" | '
                             f'grep -c "com.netease.onmyoji"')
        check('游戏任务已在该屏', tasks.strip() not in ('', '0'), tasks.strip())
        focus = adb('shell', 'dumpsys window | grep mCurrentFocus | head -1')
        check('主屏前台不是游戏', 'onmyoji' not in focus, focus.strip()[:60])
        shot = adb('shell', f'screencap -p -d {display} > /sdcard/vdhost.png; '
                            f'ls -l /sdcard/vdhost.png')
        check('该屏可截图', 'vdhost.png' in shot, shot.strip()[:60])

    stopped = post('/vd/host?action=stop')
    check('可停止宿主', stopped.get('ok') is True, str(stopped))

    failed = [name for name, ok in results if not ok]
    print()
    print(f'TOTAL {len(results)}  PASS {len(results) - len(failed)}  FAIL {len(failed)}')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
