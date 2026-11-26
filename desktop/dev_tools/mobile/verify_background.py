# This Python file uses the following encoding: utf-8

"""验证「后台运行 + 不影响前台使用」的行为。

场景（在模拟器/真机上跑，宿主通过 adb forward 访问手机内 Python 服务）：
1. 服务常驻：关掉 App 界面（切到桌面/别的 App）后进程仍在、/health 仍通；
2. 前台判定：切桌面时 /state 显示 target_foreground=false、can_inject=false；
3. 手势守卫：此时 /tap 被拒绝（返回 paused=true），不会乱点用户正在用的界面；
4. 回到目标 App：can_inject 恢复 true，/tap 成功；
5. 用户主动暂停：/pause 后即使目标在前台也拒绝注入，/resume 后恢复。

用法：
    .\\toolkit\\python.exe dev_tools\\mobile\\verify_background.py [--target <package>]
默认 target 用 `com.android.settings`（用系统设置页模拟「被自动化的目标 App」，
避免依赖游戏是否装在这台设备上）。
"""

import json
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:22267'
ADB = r'E:\OASX-Android\sdk\platform-tools\adb.exe'
SERIAL = '127.0.0.1:16416'
PKG_OURS = 'com.oas.oasx'

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as response:
        return json.loads(response.read().decode('utf-8'))


def post(path):
    request = urllib.request.Request(BASE + path, data=b'', method='POST')
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode('utf-8'))


def adb(*args):
    import subprocess
    return subprocess.run([ADB, '-s', SERIAL, *args], capture_output=True, text=True,
                          encoding='utf-8', errors='replace').stdout


def home():
    adb('shell', 'input', 'keyevent', '3')       # KEYCODE_HOME
    time.sleep(2.0)


def open_app(package, activity=None):
    if activity:
        adb('shell', 'am', 'start', '-n', f'{package}/{activity}')
    else:
        adb('shell', 'monkey', '-p', package, '-c', 'android.intent.category.LAUNCHER', '1')
    time.sleep(2.5)


def main() -> int:
    target = 'com.android.settings'
    if '--target' in sys.argv:
        target = sys.argv[sys.argv.index('--target') + 1]

    print('== 1. 服务常驻与目标设置')
    health = get('/health')
    check('/health 可访问', health.get('ok') is True, str(health))
    check('后台服务在运行', health.get('background_service') is True, str(health))
    set_target = post(f'/target?package={target}')
    check(f'设置目标 App 为 {target}', set_target.get('ok') is True, str(set_target))

    print('== 2. 切到桌面（离开目标 App）')
    home()
    state = get('/state')
    print('   state =', json.dumps(state, ensure_ascii=False))
    check('前台不再是目标 App', state.get('target_foreground') is False, str(state))
    check('此时禁止注入手势', state.get('can_inject') is False, str(state))
    tap = post('/tap?x=640&y=360')
    check('切后台时 /tap 被拒绝', tap.get('ok') is False and tap.get('paused') is True, str(tap))
    check('服务进程仍存活（/health 仍通）', get('/health').get('ok') is True)

    print('== 3. 打开目标 App（回到前台）')
    open_app(target)
    state = get('/state')
    print('   state =', json.dumps(state, ensure_ascii=False))
    check('目标 App 回到前台', state.get('target_foreground') is True, str(state))
    check('允许注入手势', state.get('can_inject') is True, str(state))
    tap = post('/tap?x=640&y=360')
    check('前台时 /tap 成功', tap.get('ok') is True, str(tap))

    print('== 4. 用户主动暂停/恢复')
    post('/pause')
    blocked = post('/tap?x=640&y=360')
    check('暂停后 /tap 被拒绝', blocked.get('ok') is False and blocked.get('paused') is True,
          str(blocked))
    post('/resume')
    resumed = post('/tap?x=640&y=360')
    check('恢复后 /tap 成功', resumed.get('ok') is True, str(resumed))

    print('== 5. 回桌面并把目标设回默认（收尾）')
    home()
    post('/target?package=com.netease.onmyoji.wyzymnqsd_cps')

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('MOBILE BACKGROUND: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
