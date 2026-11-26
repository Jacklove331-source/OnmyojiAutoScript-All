# This Python file uses the following encoding: utf-8
"""MuMu 模拟器实例状态查询。

ADB 掉线后需要回答两个问题：模拟器还在不在、正确的 adb 地址是什么。
`MuMuManager.exe info -v all` 是 MuMu 侧的权威来源（实例是否启动、
adb_host_ip/adb_port、player_state），比配置里的 serial 更可靠——实例重启
后端口会变，配置里的旧端口连不上。

该模块只做查询，不启动/关闭实例；所有调用失败都返回 None 而不是抛异常，
让调用方决定降级策略。结果有 5 秒缓存，避免每次截图都拉起一个进程。
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

if __package__ in (None, ''):  # 直接执行本文件时补上包搜索路径（诊断用）
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from module.logger import logger

MUMU_MANAGER_NAME = 'MuMuManager.exe'
DEFAULT_CACHE_TTL = 5.0

# 实例级字段（`info -v all` 里每个 index 的值）
STATE_FIELDS = (
    'index',
    'name',
    'is_process_started',
    'is_android_started',
    'player_state',
    'adb_host_ip',
    'adb_port',
    'pid',
    'main_wnd',
    'launch_err_msg',
)

_cache = {
    'manager': None,
    'time': 0.0,
    'data': None,
}


def _no_window_flags() -> int:
    if os.name != 'nt':
        return 0
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def find_manager(hints=()):
    """查找 MuMuManager.exe。

    Args:
        hints: 候选路径，可传 MuMuManager.exe / MuMuNxMain.exe / 安装根目录。

    Returns:
        Path | None
    """
    candidates = []
    for hint in hints:
        if not hint:
            continue
        text = str(hint).strip().strip('"')
        if not text:
            continue
        path = Path(text)
        if path.name.lower() == MUMU_MANAGER_NAME.lower():
            candidates.append(path)
        elif path.suffix.lower() == '.exe':
            # 配置里常见的是 MuMuNxMain.exe，管理器与它在同一目录
            candidates.append(path.with_name(MUMU_MANAGER_NAME))
        else:
            candidates.append(path / MUMU_MANAGER_NAME)
            candidates.append(path / 'nx_main' / MUMU_MANAGER_NAME)

    for env_name in ('OAS_MUMU_MANAGER', 'MUMU_MANAGER'):
        env = os.environ.get(env_name)
        if env:
            candidates.append(Path(env))

    which = shutil.which(MUMU_MANAGER_NAME) or shutil.which('MuMuManager')
    if which:
        candidates.append(Path(which))

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def query_all(manager=None, force=False, timeout=15.0, cache_ttl=DEFAULT_CACHE_TTL,
              hints=()):
    """读取全部实例信息。

    Args:
        manager (Path | None): MuMuManager.exe 路径，None 时用缓存/自动查找。
        force (bool): 忽略缓存。
        timeout (float): 子进程超时。
        cache_ttl (float): 缓存有效期（秒）。
        hints: 自动查找 MuMuManager 时的候选路径（如配置里的模拟器 exe）。

    Returns:
        dict[str, dict] | None: 形如 {'1': {...}}，失败返回 None。
    """
    import time

    if manager is None:
        manager = _cache.get('manager')
    if manager is None:
        manager = find_manager(hints)

    now = time.monotonic()
    cached = _cache.get('data')
    if (
        not force
        and cached is not None
        and _cache.get('manager') == manager
        and now - _cache.get('time', 0.0) <= cache_ttl
    ):
        return cached

    if manager is None:
        logger.warning('MuMu state: MuMuManager.exe not found')
        return None

    cmd = [str(manager), 'info', '-v', 'all']
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=_no_window_flags(),
            encoding='utf-8',
            errors='replace',
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning(f'MuMu state: query failed: {error!r}')
        return None

    text = (completed.stdout or '').strip()
    if not text:
        stderr = (completed.stderr or '').strip()
        logger.warning(f'MuMu state: empty output, stderr={stderr!r}')
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        logger.warning(f'MuMu state: invalid JSON ({error}); head={text[:120]!r}')
        return None
    if not isinstance(data, dict):
        logger.warning(f'MuMu state: unexpected payload type {type(data).__name__}')
        return None

    _cache.update({'manager': manager, 'time': now, 'data': data})
    return data


def instances(manager=None, force=False, hints=()):
    """返回实例列表（按 index 排序），失败返回 []。"""
    data = query_all(manager=manager, force=force, hints=hints)
    if not data:
        return []
    result = []
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        item = dict(value)
        item.setdefault('index', str(key))
        result.append(item)
    result.sort(key=lambda entry: str(entry.get('index', '')))
    return result


def clear_cache():
    _cache.update({'manager': None, 'time': 0.0, 'data': None})


def serial_port(serial):
    """从 serial 取端口：'127.0.0.1:16416' -> 16416，取不到返回 None。"""
    try:
        text = str(serial or '').strip()
        if ':' not in text:
            return None
        return int(text.rsplit(':', 1)[1])
    except (TypeError, ValueError):
        return None


def instance_endpoint(instance):
    """把实例信息转成 adb 连接串；缺端口返回 None。"""
    if not instance:
        return None
    port = instance.get('adb_port')
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not 0 < port <= 65535:
        return None
    host = str(instance.get('adb_host_ip') or '127.0.0.1').strip() or '127.0.0.1'
    return f'{host}:{port}'


def _is_started(instance):
    return bool(
        instance.get('is_android_started')
        or instance.get('is_process_started')
    )


def instance_for_serial(serial, manager=None, force=False, hints=()):
    """按 serial 端口匹配实例；端口对不上时退化为唯一运行中的实例。

    防御性处理：MuMu 的**未启动**实例也可能带一个 `adb_port`（重启后端口号
    会沿用）。只按端口取第一个匹配，可能拿到没启动的实例，进而误判
    「模拟器没起、重连无意义」。所以端口匹配优先挑已启动的实例。
    """
    port = serial_port(serial)
    items = instances(manager=manager, force=force, hints=hints)
    if not items:
        return None

    if port is not None:
        matched = []
        for item in items:
            try:
                if int(item.get('adb_port', -1)) == port:
                    matched.append(item)
            except (TypeError, ValueError):
                continue
        if matched:
            started = [item for item in matched if _is_started(item)]
            return started[0] if started else matched[0]

    started = [item for item in items if _is_started(item)]
    if len(started) == 1:
        return started[0]
    if len(items) == 1:
        return items[0]
    return None


def instance_index(instance):
    try:
        return int(instance.get('index', -1))
    except (TypeError, ValueError):
        return -1


def instance_for_index(index, hints=(), timeout=3.0):
    """Read one explicitly configured instance without falling back to a different guest."""
    manager = find_manager(hints)
    if manager is None:
        return None
    try:
        completed = subprocess.run(
            [str(manager), 'info', '--vmindex', str(index)],
            capture_output=True, timeout=timeout, creationflags=_no_window_flags(),
            encoding='utf-8-sig', errors='replace',
        )
        data = json.loads(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if isinstance(data, dict) and instance_index(data) == index:
        return data
    return None


def summary(instance):
    """给日志用的紧凑摘要。"""
    if not instance:
        return 'mumu=<none>'
    return (
        f"mumu#{instance.get('index')} "
        f"state={instance.get('player_state')} "
        f"android_started={instance.get('is_android_started')} "
        f"adb={instance_endpoint(instance)}"
    )


if __name__ == '__main__':
    for item in instances(force=True):
        print(summary(item))
