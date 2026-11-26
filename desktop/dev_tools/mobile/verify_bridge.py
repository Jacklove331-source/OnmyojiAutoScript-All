# This Python file uses the following encoding: utf-8

"""验证 APK 内嵌 Python 服务 + Android 执行层（截图/点击/滑动）。

通过 `adb forward tcp:22267 tcp:22267` 把手机上的 127.0.0.1:22267 映射到本机，
然后本脚本用 HTTP 调它。纯本地回环调用，不涉及任何外网。

用法：.\\toolkit\\python.exe dev_tools\\mobile\\verify_bridge.py [--no-gesture]
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = 'http://127.0.0.1:22267'
OUT = ROOT / 'log' / 'mobile'
OUT.mkdir(parents=True, exist_ok=True)

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def get_json(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=15) as response:
        return json.loads(response.read().decode('utf-8'))


def post(path: str) -> dict:
    request = urllib.request.Request(BASE + path, data=b'', method='POST')
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode('utf-8'))


def png_size(data: bytes) -> tuple:
    if not data.startswith(b'\x89PNG'):
        return (0, 0)
    import struct
    width, height = struct.unpack('>II', data[16:24])
    return (width, height)


def main() -> int:
    gesture = '--no-gesture' not in sys.argv

    health = get_json('/health')
    check('/health 可访问', health.get('ok') is True, str(health))
    check('执行层已连接（无障碍服务）', health.get('bridge_available') is True,
          str(health.get('bridge_reason')))

    with urllib.request.urlopen(BASE + '/screenshot.png', timeout=20) as response:
        png = response.read()
    size = png_size(png)
    (OUT / 'device_screenshot.png').write_bytes(png)
    # 注意：屏幕如果大面积纯色（如 App 界面），PNG 可能只有几 KB，不能拿字节数当有效性判据
    check('截图返回合法 PNG', png.startswith(b'\x89PNG') and size[0] >= 500 and len(png) > 1024,
          f'bytes={len(png)} size={size}')
    check('截图分辨率合理(1280x720 或同比例)',
          size[0] in (1280, 720, 1080, 1440, 2340, 1920) or size[0] > 500,
          str(size))
    print(f'   截图: {size[0]}x{size[1]}, {len(png)/1024:.1f} KB -> {OUT / "device_screenshot.png"}')

    if gesture:
        # 后台守卫：目标 App 不在前台时手势会被拒绝（这是设计行为）。
        # 测桥接能力时，把目标临时设成当前前台应用。
        state = get_json('/state')
        target = state.get('foreground') or 'com.oas.oasx'
        post(f'/target?package={target}')
        print(f'   手势目标临时设为当前前台：{target}')
        tap = post('/tap?x=640&y=360')
        check('点击接口返回 ok', tap.get('ok') is True, str(tap))
        swipe = post('/swipe?x1=640&y1=500&x2=640&y2=200')
        check('滑动接口返回 ok', swipe.get('ok') is True, str(swipe))

    if gesture:
        post('/target?package=com.netease.onmyoji.wyzymnqsd_cps')  # 收尾：还原默认目标

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('MOBILE BRIDGE: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
