"""On-device scheduler: selected config, native OAS due times and cancellable work."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
import time

SCHEDULER = {
    'running': False, 'stopping': False, 'tasks': [], 'config': 'oas1',
    'interval': 1800, 'current': '', 'round': 0, 'completed': 0,
    'rounds_today': 0, 'round_limit': 0, 'limit_reached': False,
    'task_index': 0, 'last_finished': 0, 'next_at': 0, 'error': '', 'log': [],
    'waiting_shizuku': False, 'waiting_reason': '',
    'guard_paused': False, 'guard_reason': '',
}
_LOCK = threading.RLock()
_THREAD = None
_STOP = None
_RUN_ID = None
_SHIZUKU_POLL_INTERVAL = 1.5


def _shizuku_is_ready() -> bool:
    try:
        from . import bridge
        fn = getattr(bridge, 'shizuku_available', None)
        if callable(fn):
            return bool(fn())
    except Exception:  # noqa: BLE001
        pass
    return True


def _should_check_shizuku_preflight() -> bool:
    try:
        from . import bridge
        if getattr(bridge, '_TEST_SHIZUKU_CHECK', False):
            return True
        return getattr(bridge, '_SHIZUKU', None) is not None
    except Exception:  # noqa: BLE001
        return False


def _is_shizuku_error(error_text: str) -> bool:
    text = str(error_text or '').lower()
    if not text:
        return False
    if any(token in text for token in (
        'shizuku unavailable',
        'shizuku permission',
        'shizuku 未授权',
        'shizuku 未启动',
        'shizuku 服务不可用',
    )):
        return True
    if any(token in text for token in (
        'background display start failed',
        'background capture stopped',
        'binder disconnected',
    )) and not _shizuku_is_ready():
        return True
    return False


def _wait_for_shizuku(stop_event: threading.Event, task_name: str = '') -> bool:
    """当 Shizuku 未启动/未授权时挂起等待，用户手动打开 Shizuku 后自动返回 True 继续拉起游戏。"""
    reason = 'Shizuku 未启动或未授权，请打开 Shizuku，就绪后将自动拉起游戏'
    with _LOCK:
        SCHEDULER['waiting_shizuku'] = True
        SCHEDULER['waiting_reason'] = reason
        SCHEDULER['current'] = ''
        SCHEDULER['error'] = reason
    _log(f'waiting for Shizuku before {task_name or "task"}')
    try:
        from . import device_guard
        device_guard._update_progress_notification(
            'OASX 等待 Shizuku', '请打开 Shizuku，就绪后将自动拉起游戏', True
        )
    except Exception:  # noqa: BLE001
        pass

    while not stop_event.is_set():
        if _shizuku_is_ready():
            with _LOCK:
                SCHEDULER['waiting_shizuku'] = False
                SCHEDULER['waiting_reason'] = ''
                if SCHEDULER.get('error') == reason:
                    SCHEDULER['error'] = ''
            _log('Shizuku is ready; resuming task automatically')
            return True
        stop_event.wait(_SHIZUKU_POLL_INTERVAL)

    with _LOCK:
        SCHEDULER['waiting_shizuku'] = False
        SCHEDULER['waiting_reason'] = ''
    return False


def clear_guard_pause_hint() -> None:
    with _LOCK:
        SCHEDULER['guard_paused'] = False
        SCHEDULER['guard_reason'] = ''


def _log(message: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {message}"
    with _LOCK:
        SCHEDULER['log'].append(line)
        del SCHEDULER['log'][:-60]
    print(f'[scheduler] {line}', flush=True)


def status() -> dict:
    with _LOCK:
        if not SCHEDULER['running']:
            # Read today's saved count after an app restart or a date change.
            try:
                _refresh_rounds(*_round_files())
            except (OSError, ValueError):
                pass
        return copy.deepcopy(SCHEDULER)


def configuration_tasks(config_name: str) -> list[dict]:
    from .task_runner import _make_config, prepare_workspace
    from module.config.config_model import ConfigModel
    prepare_workspace()
    config = _make_config(config_name)
    result = []
    for key, value in config.model.dict().items():
        if not isinstance(value, dict) or not isinstance(value.get('scheduler'), dict):
            continue
        settings = value['scheduler']
        name = ConfigModel.type(key)
        result.append({
            'name': name, 'enabled': bool(settings.get('enable')),
            'next_run': str(settings.get('next_run', '')),
            'priority': settings.get('priority', 0),
        })
    return result


def task_queue(config_name: str) -> dict:
    """Return real OAS scheduler execution order: running -> pending -> waiting."""
    from . import task_runner
    from .task_runner import _make_config, prepare_workspace
    prepare_workspace()
    config = _make_config(config_name)
    config.update_scheduler()

    try:
        from .i18n_zh import MESSAGES as i18n
    except Exception:  # noqa: BLE001
        i18n = {}

    with _LOCK:
        sched_running = bool(SCHEDULER.get('running')) and SCHEDULER.get('config') == config_name
        current_name = str(SCHEDULER.get('current') or '') if sched_running else ''
    if not current_name:
        t_stat = task_runner.status()
        if t_stat.get('running') and t_stat.get('config') == config_name:
            current_name = str(t_stat.get('task') or '')

    def _entry(func, state: str) -> dict:
        name = str(getattr(func, 'command', '') or '')
        next_run = getattr(func, 'next_run', '')
        priority = getattr(func, 'priority', 5)
        try:
            prio_val = int(priority)
        except (TypeError, ValueError):
            prio_val = 5
        return {
            'name': name,
            'title': str(i18n.get(name) or name),
            'priority': prio_val,
            'next_run': str(next_run) if next_run else '',
            'status': state,
            'enabled': True,
        }

    pending_list = list(getattr(config, 'pending_task', []) or [])
    waiting_list = list(getattr(config, 'waiting_task', []) or [])

    queue: list[dict] = []
    seen: set[str] = set()
    if current_name:
        matched = next((f for f in pending_list + waiting_list if getattr(f, 'command', '') == current_name), None)
        if matched is not None:
            queue.append(_entry(matched, 'running'))
        else:
            queue.append({
                'name': current_name,
                'title': str(i18n.get(current_name) or current_name),
                'priority': 0,
                'next_run': '',
                'status': 'running',
                'enabled': True,
            })
        seen.add(current_name)

    pending_count = 0
    for f in pending_list:
        name = str(getattr(f, 'command', '') or '')
        if not name or name in seen:
            continue
        queue.append(_entry(f, 'pending'))
        seen.add(name)
        pending_count += 1

    waiting_count = 0
    for f in waiting_list:
        name = str(getattr(f, 'command', '') or '')
        if not name or name in seen:
            continue
        queue.append(_entry(f, 'waiting'))
        seen.add(name)
        waiting_count += 1

    rule = ''
    try:
        rule = str(getattr(config.model.script.optimization, 'schedule_rule', '') or '')
    except Exception:  # noqa: BLE001
        pass

    return {
        'ok': True,
        'config': config_name,
        'running': current_name,
        'schedule_rule': rule,
        'pending_count': pending_count,
        'waiting_count': waiting_count,
        'queue': queue,
    }


def pin_task(config_name: str, task_name: str) -> dict:
    """Bump a task to the front of the pending queue by advancing next_run & priority."""
    from .task_runner import _make_config, prepare_workspace
    from module.config.utils import convert_to_underscore
    prepare_workspace()
    config = _make_config(config_name)
    config.update_scheduler()

    key = convert_to_underscore(task_name)
    model = getattr(config.model, key, None)
    settings = getattr(model, 'scheduler', None)
    if settings is None:
        return {'ok': False, 'error': f'未找到任务 {task_name}'}

    now = datetime.now().replace(microsecond=0)
    pending_list = list(getattr(config, 'pending_task', []) or [])
    waiting_list = list(getattr(config, 'waiting_task', []) or [])
    other_times = [
        f.next_run for f in pending_list + waiting_list
        if getattr(f, 'command', '') != task_name and isinstance(getattr(f, 'next_run', None), datetime)
    ]
    earliest = min([now] + other_times) - timedelta(seconds=60)
    non_restart_prios = [
        int(getattr(f, 'priority', 5)) for f in pending_list
        if getattr(f, 'command', '') not in (task_name, 'Restart')
    ]
    settings.enable = True
    settings.next_run = earliest.replace(microsecond=0)
    if non_restart_prios:
        min_p = min(non_restart_prios)
        try:
            cur_p = int(getattr(settings, 'priority', 5))
        except (TypeError, ValueError):
            cur_p = 5
        if cur_p > min_p:
            settings.priority = min_p
    config.save()
    return {'ok': True, 'pinned': task_name, **task_queue(config_name)}


def _native_next(config_name: str):
    from .task_runner import _make_config, prepare_workspace
    prepare_workspace()
    config = _make_config(config_name)
    config.update_scheduler()
    return config.get_next()


def _defer_if_due(config_name: str, name: str, seconds: int) -> None:
    """A task which did not schedule itself must not be restarted in a tight loop."""
    from .task_runner import _make_config
    from module.config.utils import convert_to_underscore
    config = _make_config(config_name)
    key = convert_to_underscore(name)
    model = getattr(config.model, key, None)
    settings = getattr(model, 'scheduler', None)
    if settings is not None and settings.next_run <= datetime.now():
        settings.next_run = datetime.now().replace(microsecond=0) + timedelta(seconds=seconds)
        config.save()


def _round_files():
    from .task_runner import prepare_workspace

    workspace = Path(prepare_workspace())
    return workspace.parent / 'safety_limits.json', workspace / 'scheduler_rounds.json'


def _read_round_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f'Invalid scheduler data in {path.name}')
    return data


def _refresh_rounds(preferences: Path, totals: Path) -> str:
    day = datetime.now().date().isoformat()
    limit = _read_round_file(preferences).get('maxRoundsPerDay', 0)
    counter = _read_round_file(totals)
    rounds = counter.get('rounds', 0) if counter.get('date') == day else 0
    if type(limit) is not int or limit < 0 or type(rounds) is not int or rounds < 0:
        raise ValueError('Invalid scheduler limit or daily counter')
    SCHEDULER.update(rounds_today=rounds, round_limit=limit,
                     limit_reached=limit > 0 and rounds >= limit)
    return day


def start(tasks=None, interval: int = 1800, config_name: str = 'oas1', force: bool = False) -> dict:
    global _THREAD, _STOP, _RUN_ID
    from . import task_runner
    if not task_runner._valid_config(config_name):
        return {'ok': False, 'error': 'invalid config name'}
    explicit = bool(tasks)
    if isinstance(tasks, str):
        tasks = [name.strip() for name in tasks.split(',') if name.strip()]
    try:
        selected = list(tasks) if explicit else [row['name'] for row in configuration_tasks(config_name) if row['enabled']]
        if not selected:
            return {'ok': False, 'error': '请先在当前配置启用至少一项任务'}
        interval = max(30, min(int(interval), 7 * 24 * 3600))
    except Exception as error:
        return {'ok': False, 'error': str(error)}

    guard_info = {'paused': False, 'code': 'ok', 'reason': ''}
    with _LOCK:
        if SCHEDULER['running'] or (_THREAD is not None and _THREAD.is_alive()):
            return {'ok': False, 'error': 'scheduler already running or stopping', 'status': status()}
        if task_runner.status()['running']:
            return {'ok': False, 'error': 'another task is running', 'status': status()}
        try:
            preferences, totals = _round_files()
            _refresh_rounds(preferences, totals)
        except (OSError, ValueError) as error:
            return {'ok': False, 'error': str(error), 'status': status()}
        if SCHEDULER['limit_reached']:
            return {'ok': False, 'error': 'daily scheduler round limit reached', 'status': status()}
        # 每次手动点击「开始托管」时，先收敛可能遗留的 paused 状态：
        # 清除上一次运行残留的手动暂停或已回落的过温滞后锁；只有此刻确实命中护航规则时才保持暂停并把原因带回前端。
        try:
            from . import device_guard
            guard_info = device_guard.prepare_for_start(task_runner.prepare_workspace(), force=force)
        except Exception:  # noqa: BLE001
            try:
                from . import bridge
                if callable(getattr(bridge, 'set_paused', None)):
                    bridge.set_paused(False)
            except Exception:  # noqa: BLE001
                pass
        stop_event = threading.Event()
        _STOP = stop_event
        _RUN_ID = None
        SCHEDULER.update({
            'running': True, 'stopping': False, 'tasks': selected, 'config': config_name,
            'interval': interval, 'current': '', 'round': 0, 'completed': 0,
            'task_index': 0, 'next_at': 0, 'error': '', 'log': [],
            'waiting_shizuku': False, 'waiting_reason': '',
            'guard_paused': bool(guard_info.get('paused')),
            'guard_reason': str(guard_info.get('reason') or ''),
        })

        def reserve_round() -> bool:
            with _LOCK:
                day = _refresh_rounds(preferences, totals)
                if stop_event.is_set() or SCHEDULER['limit_reached']:
                    return False
                count = SCHEDULER['rounds_today'] + 1
                pending = totals.with_suffix('.tmp')
                pending.write_text(json.dumps({'date': day, 'rounds': count}), encoding='utf-8')
                pending.replace(totals)
                SCHEDULER['rounds_today'] = count
                SCHEDULER['round'] += 1
                return True

        def reached_daily_limit() -> bool:
            with _LOCK:
                _refresh_rounds(preferences, totals)
                return SCHEDULER['limit_reached']

        def run_one(name: str) -> bool:
            global _RUN_ID
            while not stop_event.is_set():
                if _should_check_shizuku_preflight() and not _shizuku_is_ready():
                    if not _wait_for_shizuku(stop_event, name):
                        return False
                    continue
                with _LOCK:
                    SCHEDULER['current'] = name
                    SCHEDULER['next_at'] = 0
                if stop_event.is_set():
                    return False
                _log(f'run {name} ({config_name})')
                try:
                    from . import device_guard
                    device_guard.notify_task_progress(
                        name,
                        round_num=SCHEDULER['round'],
                        completed=SCHEDULER['completed'],
                        total=len(SCHEDULER['tasks']),
                    )
                except Exception:  # noqa: BLE001
                    pass
                result = task_runner.run_task(name, config_name, login=name != 'Restart')
                if not result.get('ok'):
                    raise RuntimeError(result.get('error', 'task not started'))
                run_id = result['run_id']
                with _LOCK:
                    _RUN_ID = run_id
                while True:
                    current = task_runner.status()
                    if current['run_id'] != run_id:
                        raise RuntimeError('active task instance changed unexpectedly')
                    if not current['running']:
                        break
                    if stop_event.is_set():
                        # Cancellation can wait for an in-flight screenshot or OCR call.
                        stopped = task_runner.stop_task(wait=True, timeout=5, run_id=run_id)
                        if not stopped['ok']:
                            raise RuntimeError(stopped['error'])
                        continue
                    stop_event.wait(.1)
                _log(f"{name} finished: ok={current.get('ok')} cancelled={current.get('cancelled')} error={current.get('error')}")
                with _LOCK:
                    _RUN_ID = None
                    SCHEDULER['last_finished'] = int(time.time())
                    SCHEDULER['current'] = ''
                    if current.get('ok'):
                        SCHEDULER['completed'] += 1
                # Shizuku 未启动/中途断开属于基础设施未就绪：绝不把任务推迟 30 分钟，也绝不因此终止调度器；
                # 直接原地等待用户打开 Shizuku，就绪后自动重跑该任务拉起游戏！
                if not stop_event.is_set() and not current.get('ok') and _is_shizuku_error(current.get('error', '')):
                    if not _wait_for_shizuku(stop_event, name):
                        return False
                    continue
                if not stop_event.is_set() and name == 'Restart' and current.get('phase') == 'failed':
                    # A subsequent task's automatic login would otherwise use the saved role.
                    raise RuntimeError(current.get('error') or 'Android Restart failed')
                if not stop_event.is_set() and not explicit:
                    _defer_if_due(config_name, name, interval if current.get('ok') else max(300, interval))
                return bool(current.get('ok'))
            return False

        def worker() -> None:
            try:
                try:
                    from . import device_guard
                    from .task_runner import prepare_workspace
                    device_guard.on_scheduler_started(prepare_workspace())
                except Exception:  # noqa: BLE001
                    pass
                while not stop_event.is_set():
                    if reached_daily_limit():
                        break
                    if explicit:
                        if _should_check_shizuku_preflight() and not _shizuku_is_ready():
                            if not _wait_for_shizuku(stop_event, selected[0] if selected else ''):
                                break
                            continue
                        if not reserve_round():
                            break
                        for index, name in enumerate(selected, 1):
                            if stop_event.is_set():
                                break
                            with _LOCK:
                                SCHEDULER['task_index'] = index
                            run_one(name)
                        if stop_event.is_set() or reached_daily_limit():
                            break
                        try:
                            from . import device_guard
                            from .task_runner import prepare_workspace
                            ws = prepare_workspace()
                            device_guard.on_daily_tasks_completed(ws)
                        except Exception as asset_err:  # noqa: BLE001
                            _log(f'daily tasks hook skipped: {asset_err}')
                        with _LOCK:
                            SCHEDULER['next_at'] = int(time.time()) + interval
                        stop_event.wait(interval)
                    else:
                        plan = configuration_tasks(config_name)
                        enabled = [row['name'] for row in plan if row['enabled']]
                        if not enabled:
                            _log('no tasks enabled; scheduler stopped')
                            break
                        with _LOCK:
                            SCHEDULER['tasks'] = enabled
                        next_task = _native_next(config_name)
                        delay = max(0.0, (next_task.next_run - datetime.now()).total_seconds())
                        if delay:
                            if SCHEDULER['completed'] > 0:
                                try:
                                    from . import device_guard
                                    from .task_runner import prepare_workspace
                                    ws = prepare_workspace()
                                    device_guard.on_daily_tasks_completed(ws)
                                except Exception as asset_err:  # noqa: BLE001
                                    _log(f'daily tasks hook skipped: {asset_err}')
                            with _LOCK:
                                SCHEDULER['next_at'] = int(time.time() + delay)
                            stop_event.wait(min(delay, 5))
                            continue
                        if _should_check_shizuku_preflight() and not _shizuku_is_ready():
                            if not _wait_for_shizuku(stop_event, next_task.command):
                                break
                            continue
                        if not reserve_round():
                            break
                        run_one(next_task.command)
            except Exception as error:
                with _LOCK:
                    SCHEDULER['error'] = f'{type(error).__name__}: {error}'
                _log(SCHEDULER['error'])
            finally:
                err_msg = SCHEDULER.get('error', '')
                with _LOCK:
                    SCHEDULER.update({
                        'running': False, 'stopping': False, 'current': '', 'next_at': 0,
                        'waiting_shizuku': False, 'waiting_reason': '',
                        'guard_paused': False, 'guard_reason': '',
                    })
                try:
                    from . import device_guard
                    from .task_runner import prepare_workspace
                    device_guard.on_scheduler_stopped(prepare_workspace(), error=err_msg)
                except Exception:  # noqa: BLE001
                    pass
                _log('scheduler stopped')

        _THREAD = threading.Thread(target=worker, name='oas-scheduler', daemon=True)
        _THREAD.start()
    return {
        'ok': True, 'started': True, 'tasks': selected, 'config': config_name, 'interval': interval,
        'guard_paused': bool(guard_info.get('paused')),
        'guard_code': str(guard_info.get('code') or 'ok'),
        'guard_reason': str(guard_info.get('reason') or ''),
    }


def stop(wait: bool = False) -> dict:
    from . import task_runner
    with _LOCK:
        thread, event, run_id = _THREAD, _STOP, _RUN_ID
        if event is not None:
            event.set()
        if thread is not None and thread.is_alive():
            SCHEDULER['stopping'] = True
    if run_id is not None:
        task_runner.stop_task(wait=wait, run_id=run_id)
    if wait and thread is not None and thread is not threading.current_thread():
        thread.join(timeout=6)
    snapshot = status()
    return {'ok': True, 'stopped': not snapshot['running'], 'status': snapshot}
