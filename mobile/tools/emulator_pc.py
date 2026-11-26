"""Open and control the dedicated OAS test instance in MuMu."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MUMU = Path(r'E:\MuMuPlayer\nx_main')
MANAGER = MUMU / 'MuMuManager.exe'
ADB = MUMU / 'adb.exe'
INSTANCE = '2'
INSTANCE_NAME = 'OAS安卓独立测试'
SERIAL = '127.0.0.1:16448'
HOST_PORT = 'tcp:23278'
APP_PORT = 'tcp:22267'
API = 'http://127.0.0.1:23278'
APP = 'com.oas.oasx/.MainActivity'
MOBILE_GAME = 'com.netease.onmyoji'
MUMU_GAME = 'com.netease.onmyoji.wyzymnqsd_cps'
TOOLS = Path(r'E:\OASX-Android\tools\scrcpy')
LOGS = Path(r'E:\OASX-Android\logs')


def run(executable, *args, timeout=15):
    result = subprocess.run(
        [str(executable), *args], capture_output=True, encoding='utf-8',
        errors='replace', timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or f'{executable.name} 执行失败。')
    return result.stdout.strip()


def player():
    info = json.loads(run(MANAGER, 'info', '-v', INSTANCE))
    if info.get('name') != INSTANCE_NAME or info.get('index') != INSTANCE or info.get('is_main'):
        raise RuntimeError(f'找不到专用 MuMu 实例 {INSTANCE_NAME}（编号 {INSTANCE}）。')
    if info.get('is_process_started') and info.get('adb_port'):
        if f"{info.get('adb_host_ip')}:{info['adb_port']}" != SERIAL:
            raise RuntimeError('专用实例的 ADB 地址与测试配置不符。')
    return info


def control(action):
    result = json.loads(run(MANAGER, 'control', '-v', INSTANCE, action, timeout=30))
    if result.get('errcode') != 0:
        raise RuntimeError(result.get('errmsg') or f'MuMu {action} 执行失败。')


def adb(*args, timeout=15):
    return run(ADB, '-s', SERIAL, *args, timeout=timeout)


def request(path, method='GET', timeout=5):
    with urlopen(Request(API + path, method=method), timeout=timeout) as response:
        return json.load(response)


def forwards():
    return [tuple(line.split()) for line in run(ADB, 'forward', '--list').splitlines()]


def connect():
    info = player()
    if not info.get('is_process_started'):
        control('launch')

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        info = player()
        if info.get('is_process_started') and info.get('adb_port'):
            try:
                run(ADB, 'connect', SERIAL, timeout=5)
                if adb('shell', 'getprop', 'sys.boot_completed', timeout=5) == '1':
                    break
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
        time.sleep(2)
    else:
        raise RuntimeError(f'等待 {INSTANCE_NAME} 启动超时。')

    route = (SERIAL, HOST_PORT, APP_PORT)
    if route not in forwards():
        adb('forward', '--no-rebind', HOST_PORT, APP_PORT)


def start_shizuku():
    processes = adb('shell', 'ps', '-A', '-o', 'NAME').split()
    if 'shizuku_server' in processes:
        return
    paths = adb('shell', 'pm', 'path', 'moe.shizuku.privileged.api')
    base = re.search(r'^package:(/data/app/[A-Za-z0-9_=/+.~-]+)/base\.apk$', paths, re.MULTILINE)
    if base is None:
        raise RuntimeError('专用 MuMu 实例中未找到 Shizuku。')
    adb('shell', base[1] + '/lib/x86_64/libshizuku.so')


def open_app(*, show=False):
    try:
        healthy = request('/health', timeout=2).get('ok') is True
    except (OSError, ValueError):
        healthy = False
    if show or not healthy:
        adb('shell', 'am', 'start', '-n', APP)
    if healthy:
        return

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            if request('/health', timeout=2).get('ok') is True:
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError('OAS 安卓服务未就绪，请查看专用 MuMu 实例中的 OAS。')


def game_display(package):
    if not request('/state').get('shizuku_ready'):
        raise RuntimeError('请在专用 MuMu 实例中给 OAS 授予 Shizuku 权限。')
    monitor = request('/monitor/status')
    if package is None:
        package = monitor.get('target_package') or MOBILE_GAME
        if not adb('shell', 'pm', 'path', package):
            package = MUMU_GAME
    if not adb('shell', 'pm', 'path', package):
        raise RuntimeError('专用 MuMu 实例中未找到指定的阴阳师客户端。')

    current = monitor.get('target_package')
    if package != current and current and (
        request('/scheduler/status').get('running') or request('/task/status').get('running')
    ):
        raise RuntimeError('OAS 正在运行任务，请停止后再切换游戏客户端。')
    request('/target?' + urlencode({'package': package}), 'POST')
    result = request('/background/start', 'POST', timeout=40)
    if not result.get('ok') or int(result['display']) <= 0:
        raise RuntimeError(result.get('error') or '启动后台游戏失败。')
    return int(result['display'])


def view(game, package=None):
    connect()
    start_shizuku()
    open_app(show=not game)
    display = game_display(package) if game else 0
    title = 'OAS 安卓 · 游戏' if game else 'OAS 安卓 · 控制台'
    LOGS.mkdir(exist_ok=True)
    log_path = LOGS / ('mumu-game-window.log' if game else 'mumu-panel-window.log')
    with log_path.open('ab') as log:
        process = subprocess.Popen(
            [str(TOOLS / 'scrcpy.exe'), '--serial', SERIAL, '--display-id', str(display),
             '--no-audio', '--window-title', title],
            env=dict(os.environ, ADB=str(ADB)), stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            print(f'{title}窗口已打开。关闭此窗口后 MuMu 仍会运行。')
            return
    raise RuntimeError(f'窗口未能启动，请查看 {log_path}。')


def stop():
    if not player().get('is_process_started'):
        print(f'{INSTANCE_NAME}已关闭。')
        return
    if (SERIAL, HOST_PORT, APP_PORT) in forwards():
        for path in ('/scheduler/stop', '/task/stop'):
            try:
                request(path, 'POST')
            except (OSError, ValueError):
                pass
        adb('forward', '--remove', HOST_PORT)
    control('shutdown')
    print(f'已请求关闭 {INSTANCE_NAME}。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('start', 'panel', 'game', 'status', 'stop'))
    parser.add_argument('--package', choices=(MOBILE_GAME, MUMU_GAME))
    options = parser.parse_args()
    try:
        if options.action == 'start':
            connect()
            start_shizuku()
            open_app()
            print(f'{INSTANCE_NAME}中的 OAS 已就绪。')
        elif options.action in ('panel', 'game'):
            view(options.action == 'game', options.package)
        elif options.action == 'status':
            if not player().get('is_process_started'):
                print(f'{INSTANCE_NAME}尚未启动。')
                return
            if (SERIAL, HOST_PORT, APP_PORT) not in forwards():
                raise RuntimeError('专用实例的 OAS 尚未连接，请先运行启动入口。')
            status = {part: request(path) for part, path in (
                ('health', '/health'), ('state', '/state'),
                ('task', '/task/status'), ('monitor', '/monitor/status'),
            )}
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            stop()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
