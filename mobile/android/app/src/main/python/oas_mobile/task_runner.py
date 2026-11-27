"""Run one real OAS task in the APK, with bounded and observable lifecycle."""
from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import traceback
import uuid

from .runtime import TaskCancelled, checkpoint, task_control

TASK_STATE = {
    'running': False, 'task': '', 'config': '', 'run_id': '', 'phase': 'idle',
    'started_at': 0, 'finished_at': 0, 'ok': None, 'error': '', 'log_tail': [],
    'device': '', 'cwd': '', 'stop_requested': False, 'cancelled': False,
}
_LOCK = threading.RLock()
_THREAD = None
_CANCEL = None
_FILE_LOGGER = logging.getLogger(__name__ + '.file')
_FILE_LOGGER.setLevel(logging.INFO)
_FILE_LOGGER.propagate = False


def _log(message: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {message}"
    with _LOCK:
        TASK_STATE['log_tail'].append(line)
        del TASK_STATE['log_tail'][:-60]
    print(line, flush=True)
    _FILE_LOGGER.info(message)


def _task_log_handler(workspace: str, config: str, task: str, run_id: str):
    folder = Path(workspace) / 'log' / run_id
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_{config}_{task}.txt"
    handler = logging.FileHandler(folder / filename, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s',
                                            datefmt='%Y-%m-%d %H:%M:%S'))
    return handler


def prepare_workspace() -> str:
    from java import jclass
    application = jclass('com.chaquo.python.Python').getPlatform().getApplication()
    oas_dir = os.path.join(str(application.getFilesDir().getAbsolutePath()), 'oas')
    for sub in ('config', 'log', 'screenshot'):
        os.makedirs(os.path.join(oas_dir, sub), exist_ok=True)
    if os.getcwd() != oas_dir:
        os.chdir(oas_dir)
    with _LOCK:
        TASK_STATE['cwd'] = oas_dir
    return oas_dir


def status() -> dict:
    with _LOCK:
        return copy.deepcopy(TASK_STATE)


def _make_config(name: str):
    from module.config.config import Config
    from module.config.utils import filepath_config
    path = filepath_config(name)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump({}, handle)
    return Config(name)


def _valid_config(name: str) -> bool:
    return (isinstance(name, str) and 0 < len(name) <= 80 and name not in ('.', '..')
            and not re.search(r'[\\/:*?"<>|\x00-\x1f]', name))


def task_end_result(task) -> tuple[bool, str]:
    """``TaskEnd`` 收尾时的 (ok, 错误文本)。

    桌面端用 ``TaskEnd`` 表示"脚本正常结束"，但业务失败也常这样收尾
    （如大神签到缺依赖）。任务用 ``BaseTask.mark_failure()`` 显式标记过失败时，
    运行器必须记录 ok=False 并带回原因，不能显示成"任务完成"。
    """
    reason = getattr(task, 'mobile_failure_reason', '') or ''
    return (not reason), str(reason)


def run_task(name: str, config_name: str = 'oas1', wait: bool = False,
             login: bool = True) -> dict:
    global _THREAD, _CANCEL
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        return {'ok': False, 'error': 'invalid task name'}
    if not _valid_config(config_name):
        return {'ok': False, 'error': 'invalid config name'}

    with _LOCK:
        prev_thread = _THREAD
        if not TASK_STATE['running'] and not TASK_STATE.get('assets_refreshing') and prev_thread is not None and prev_thread.is_alive():
            # 上一任务已标记结束但线程正在执行 finally 最后几行，短等其自然退出避免微秒级竞态误报
            pass
        else:
            prev_thread = None
    if prev_thread is not None and prev_thread is not threading.current_thread():
        prev_thread.join(timeout=2.0)

    with _LOCK:
        if TASK_STATE['running'] or (_THREAD is not None and _THREAD.is_alive()):
            return {'ok': False, 'error': 'another task is running', 'status': status()}
        if TASK_STATE.get('assets_refreshing'):
            return {'ok': False, 'error': '任务已结束，正在自动更新资产，请稍候', 'status': status()}
        run_id = uuid.uuid4().hex
        cancel = threading.Event()
        _CANCEL = cancel
        TASK_STATE.update({
            'running': True, 'task': name, 'config': config_name, 'run_id': run_id,
            'phase': 'starting', 'started_at': time.time(), 'finished_at': 0,
            'ok': None, 'error': '', 'log_tail': [], 'device': '',
            'stop_requested': False, 'cancelled': False,
        })

        def worker() -> None:
            ok, cancelled, error_text = False, False, ''
            file_handler = None
            saved_phase = {'value': 'starting'}

            def _on_pause(is_paused: bool) -> None:
                with _LOCK:
                    if TASK_STATE['run_id'] != run_id:
                        return
                    if is_paused:
                        cur = TASK_STATE.get('phase', 'starting')
                        if cur != 'paused':
                            saved_phase['value'] = cur
                        TASK_STATE['phase'] = 'paused'
                    elif TASK_STATE.get('phase') == 'paused':
                        TASK_STATE['phase'] = saved_phase['value']

            try:
                with task_control(cancel, on_pause=_on_pause):
                    checkpoint()
                    from module.logger import logger as business_logger

                    workspace = prepare_workspace()
                    try:
                        file_handler = _task_log_handler(workspace, config_name, name, run_id)
                        _FILE_LOGGER.addHandler(file_handler)
                        business_logger.addHandler(file_handler)
                    except OSError as error:
                        _log(f'could not save task log: {error}')
                    _log(f'workspace ready: {workspace}')
                    config = _make_config(config_name)
                    # This field is runtime state, not a user configuration change.
                    task_key = re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
                    config.model.running_task = task_key
                    _log(f'config loaded: {config_name}')
                    from module.device.device import Device
                    device = Device(config)
                    with _LOCK:
                        TASK_STATE['device'] = type(device).__name__
                    checkpoint()
                    # Restart verifies the selected role during its own cold start.
                    if login and name != 'Restart':
                        from .login import ensure_logged_in
                        with _LOCK:
                            TASK_STATE['phase'] = 'login'
                        device.app_start()
                        checkpoint()
                        confirmed = ensure_logged_in(device._ensure_display())
                        checkpoint()
                        _log(f'login result: {confirmed}')
                        if not confirmed:
                            raise RuntimeError('login not confirmed; business task was not started')
                        device.stuck_record_clear()
                    module_path = f'tasks.{name}.script_task'
                    module = __import__(module_path, fromlist=['ScriptTask'])
                    checkpoint()
                    task = module.ScriptTask(config, device)
                    with _LOCK:
                        TASK_STATE['phase'] = 'running'
                    _log(f'task instantiated: {module_path}')
                    from module.exception import TaskEnd
                    try:
                        result = task.run()
                        checkpoint()
                        ok = bool(result) if result is not None else True
                    except TaskEnd:
                        checkpoint()
                        ok, failure_reason = task_end_result(task)
                        if failure_reason:
                            error_text = failure_reason
                    _log(f'task finished: ok={ok}' + (f' error={error_text}' if error_text else ''))
            except (TaskCancelled, InterruptedError):
                cancelled = True
                _log('task stopped by user')
            except Exception as error:
                error_text = f'{type(error).__name__}: {error}'
                for line in traceback.format_exc().strip().splitlines()[-12:]:
                    _log(line)
            finally:
                # 先完成自动资产刷新，再置 running=False！
                # 否则 scheduler 轮询到 running=False 立刻拉起下一个任务，会撞上
                # _THREAD.is_alive() / assets_refreshing=True 抛出 RuntimeError 把整个托管杀掉。
                if not cancelled:
                    try:
                        from . import asset_monitor as _assets
                        with _LOCK:
                            if TASK_STATE['run_id'] == run_id:
                                TASK_STATE['assets_refreshing'] = True
                        _res = _assets.refresh_after_task(prepare_workspace(), config_name, name)
                        _reason = _res.get('reason') or 'updated'
                        _count = f"{_res.get('count', '')}/{_res.get('every', '')}" if _res.get('count') is not None else ''
                        _log(f"asset refresh: task={name} reason={_reason} {_count} "
                             f"updated={_res.get('snapshot_updated_at', '')}")
                    except Exception as asset_err:  # noqa: BLE001
                        _log(f'asset refresh skipped: {asset_err}')
                    finally:
                        with _LOCK:
                            TASK_STATE['assets_refreshing'] = False
                if file_handler is not None:
                    business_logger.removeHandler(file_handler)
                    _FILE_LOGGER.removeHandler(file_handler)
                    try:
                        file_handler.close()
                    except OSError as error:
                        _log(f'could not close task log: {error}')
                with _LOCK:
                    if TASK_STATE['run_id'] == run_id:
                        TASK_STATE.update({
                            'running': False, 'finished_at': time.time(), 'ok': ok,
                            'error': error_text, 'cancelled': cancelled,
                            'phase': 'stopped' if cancelled else ('completed' if ok else 'failed'),
                        })
                if not ok and not cancelled:
                    try:
                        from . import diag_uploader
                        diag_uploader.trigger_auto_upload(
                            prepare_workspace(),
                            trigger='task_failed',
                            task=name,
                            config_name=config_name,
                            note=error_text or f'{name} failed',
                        )
                    except Exception:  # noqa: BLE001
                        pass

        thread = threading.Thread(target=worker, name=f'oas-task-{name}', daemon=True)
        _THREAD = thread
        thread.start()
    if wait:
        thread.join(timeout=600)
    return {'ok': True, 'started': True, 'task': name, 'run_id': run_id, 'status': status()}


def stop_task(wait: bool = False, timeout: float = 5, run_id: str | None = None) -> dict:
    with _LOCK:
        if run_id is not None and run_id != TASK_STATE['run_id']:
            return {'ok': False, 'stopped': False, 'error': 'task instance changed', 'status': status()}
        thread = _THREAD
        if TASK_STATE['running'] and _CANCEL is not None:
            TASK_STATE.update({'stop_requested': True, 'phase': 'stopping'})
            _CANCEL.set()
    if wait and thread is not None and thread is not threading.current_thread():
        thread.join(timeout=max(0, min(float(timeout), 10)))
    snapshot = status()
    return {'ok': True, 'stopped': not snapshot['running'], 'status': snapshot}
