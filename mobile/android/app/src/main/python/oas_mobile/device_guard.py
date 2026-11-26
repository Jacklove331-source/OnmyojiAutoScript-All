import json
import os
import threading
import time
from typing import Any, Dict, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "thermal_enabled": True,
    "max_temp_c": 42.0,
    "cooldown_temp_c": 38.0,
    "battery_enabled": True,
    "min_battery_pct": 15,
    "resume_on_charge": True,
    "schedule_enabled": False,
    "start_time": "00:05",
    "stop_time": "02:30",
    "auto_mute_game": True,
    "notify_progress": True,
    "notify_alerts": True,
    "network_auto_resume": True,
    "shizuku_alert": True,
}

HOST_TELEMETRY: Dict[str, Any] = {
    "battery_pct": -1,
    "battery_temp_c": -1.0,
    "charging": False,
    "network_online": True,
    "network_type": "unknown",
}

_state_lock = threading.RLock()
_state: Dict[str, Any] = {
    "thread": None,
    "auto_paused": False,
    "auto_pause_code": "",
    "auto_pause_reason": "",
    "last_alert_code": None,
    "last_shizuku_alert_ts": 0.0,
    "auto_start_dates": set(),
    "daily_complete_dates": set(),
}


def _config_path(workspace: str) -> str:
    return os.path.join(str(workspace), "guard_config.json")


def _valid_hhmm(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split(":")
    if len(parts) != 2:
        return False
    try:
        h = int(parts[0])
        m = int(parts[1])
        return 0 <= h <= 23 and 0 <= m <= 59
    except ValueError:
        return False


def load_config(workspace: str) -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    path = _config_path(workspace)
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            cfg.update(saved)
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return cfg


def save_config(workspace: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_config(workspace)
    if isinstance(patch, dict):
        for key, value in patch.items():
            if key in DEFAULT_CONFIG:
                cfg[key] = value

    try:
        cfg["max_temp_c"] = min(55.0, max(35.0, float(cfg["max_temp_c"])))
    except (TypeError, ValueError):
        cfg["max_temp_c"] = DEFAULT_CONFIG["max_temp_c"]

    max_temp = float(cfg["max_temp_c"])
    try:
        cfg["cooldown_temp_c"] = min(
            max_temp - 1.0, max(30.0, float(cfg["cooldown_temp_c"]))
        )
    except (TypeError, ValueError):
        cfg["cooldown_temp_c"] = max_temp - 1.0

    try:
        cfg["min_battery_pct"] = min(60, max(5, int(cfg["min_battery_pct"])))
    except (TypeError, ValueError):
        cfg["min_battery_pct"] = DEFAULT_CONFIG["min_battery_pct"]

    for time_key in ("start_time", "stop_time"):
        if not _valid_hhmm(cfg.get(time_key)):
            cfg[time_key] = DEFAULT_CONFIG[time_key]

    os.makedirs(str(workspace), exist_ok=True)
    with open(_config_path(workspace), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    return {"ok": True, **get_status(workspace)}


def _hhmm_to_minutes(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def is_in_time_window(now_hhmm: str, start_hhmm: str, stop_hhmm: str) -> bool:
    try:
        now = _hhmm_to_minutes(now_hhmm)
        start = _hhmm_to_minutes(start_hhmm)
        stop = _hhmm_to_minutes(stop_hhmm)
    except (ValueError, AttributeError):
        return False

    if start == stop:
        return True
    if start < stop:
        return start <= now < stop
    return now >= start or now < stop


def evaluate_guard(
    cfg: Dict[str, Any],
    telemetry: Dict[str, Any],
    now_hhmm: str = "",
    was_overheat: bool = False,
) -> Dict[str, Any]:
    should_auto_start = False
    battery_temp = float(telemetry.get("battery_temp_c", -1.0))
    battery_pct = int(telemetry.get("battery_pct", -1))
    charging = bool(telemetry.get("charging", False))
    network_online = telemetry.get("network_online", True)

    if cfg.get("thermal_enabled", True) and battery_temp > 0:
        max_temp = float(cfg.get("max_temp_c", DEFAULT_CONFIG["max_temp_c"]))
        cooldown = float(cfg.get("cooldown_temp_c", DEFAULT_CONFIG["cooldown_temp_c"]))
        if battery_temp >= max_temp or (was_overheat and battery_temp > cooldown):
            return {
                "should_pause": True,
                "code": "overheat",
                "reason": f"电池温度 {battery_temp:.1f}℃ 过高，暂停降温",
                "should_auto_start": False,
            }

    if (
        cfg.get("battery_enabled", True)
        and 0 <= battery_pct <= int(cfg.get("min_battery_pct", DEFAULT_CONFIG["min_battery_pct"]))
        and not charging
    ):
        return {
            "should_pause": True,
            "code": "low_battery",
            "reason": f"电量仅剩 {battery_pct}%（未充电），已暂停保护",
            "should_auto_start": False,
        }

    if cfg.get("network_auto_resume", True) and network_online is False:
        return {
            "should_pause": True,
            "code": "offline",
            "reason": "网络已断开，等待恢复后自动续跑",
            "should_auto_start": False,
        }

    if cfg.get("schedule_enabled", False) and now_hhmm:
        start_t = str(cfg.get("start_time", DEFAULT_CONFIG["start_time"]))
        stop_t = str(cfg.get("stop_time", DEFAULT_CONFIG["stop_time"]))
        active = is_in_time_window(now_hhmm, start_t, stop_t)
        if not active:
            return {
                "should_pause": True,
                "code": "sleep_window",
                "reason": f"当前处于休眠时段（运行窗口 {start_t}~{stop_t}）",
                "should_auto_start": False,
            }
        should_auto_start = True

    return {
        "should_pause": False,
        "code": "ok",
        "reason": "",
        "should_auto_start": should_auto_start,
    }


def _get_device_guard_bridge() -> Optional[Any]:
    try:
        from java import jclass
        return jclass("com.oas.oasx.DeviceGuardBridge")
    except Exception:
        return None


def _call_device_guard(method: str, *args: Any, default: Any = None) -> Any:
    bridge = _get_device_guard_bridge()
    if bridge is None:
        return default
    try:
        fn = getattr(bridge, method, None)
        if fn is None:
            return default
        return fn(*args)
    except Exception:
        return default


def read_telemetry() -> Dict[str, Any]:
    bridge = _get_device_guard_bridge()
    if bridge is None:
        return dict(HOST_TELEMETRY)

    try:
        raw = bridge.getTelemetry()
        if isinstance(raw, dict):
            data = dict(raw)
        else:
            data = json.loads(str(raw))
        telemetry = dict(HOST_TELEMETRY)
        telemetry.update(data)
        return telemetry
    except Exception:
        return dict(HOST_TELEMETRY)


def set_media_muted(muted: bool) -> bool:
    result = _call_device_guard("setMediaMuted", bool(muted), default=False)
    return bool(result)


def _task_display_name(task_name: str) -> str:
    try:
        from oas_mobile.i18n_zh import MESSAGES

        if isinstance(MESSAGES, dict):
            return MESSAGES.get(task_name, task_name)
        return getattr(MESSAGES, task_name, task_name)
    except Exception:
        return task_name


def _update_progress_notification(title: str, text: str, paused: bool) -> None:
    _call_device_guard(
        "updateProgressNotification", title, text, bool(paused), default=None
    )


def notify_task_progress(
    task_name: str,
    round_num: int = 0,
    completed: int = 0,
    total: int = 0,
    paused: bool = False,
) -> None:
    display_name = _task_display_name(task_name)
    if paused:
        text = f"已暂停 - {display_name}"
    else:
        parts = []
        if round_num:
            parts.append(f"第{round_num}轮")
        if total:
            parts.append(f"{completed}/{total}")
        if parts:
            text = f"{display_name} " + " · ".join(parts)
        else:
            text = display_name

    _update_progress_notification("OASX 后台托管中", text, paused)


def notify_alert(title: str, body: str, alert_id: int = 2001) -> None:
    _call_device_guard(
        "sendAlertNotification", title, body, int(alert_id), default=None
    )


def on_scheduler_started(workspace: str) -> None:
    cfg = load_config(workspace)
    if cfg.get("auto_mute_game", True):
        set_media_muted(True)


def on_scheduler_stopped(workspace: str, error: str = "") -> None:
    set_media_muted(False)
    _update_progress_notification("OASX 后台托管中", "空闲", False)

    if error:
        cfg = load_config(workspace)
        if cfg.get("notify_alerts", True):
            notify_alert("托管异常", str(error), alert_id=2002)


def on_daily_tasks_completed(workspace: str) -> None:
    cfg = load_config(workspace)
    if not cfg.get("notify_alerts", True):
        return
    today = time.strftime("%Y-%m-%d")
    with _state_lock:
        if today in _state["daily_complete_dates"]:
            return
        _state["daily_complete_dates"].add(today)

    notify_alert("OASX 托管", "今日托管任务已全部完成", alert_id=2003)


def get_status(workspace: str) -> Dict[str, Any]:
    cfg = load_config(workspace)
    telemetry = read_telemetry()
    now_hhmm = time.strftime("%H:%M")
    with _state_lock:
        was_overheat = _state["auto_pause_code"] == "overheat"
        auto_paused = bool(_state["auto_paused"])
        auto_pause_code = _state["auto_pause_code"]
        auto_pause_reason = _state["auto_pause_reason"]

    guard = evaluate_guard(cfg, telemetry, now_hhmm=now_hhmm, was_overheat=was_overheat)

    return {
        "ok": True,
        "config": cfg,
        "telemetry": telemetry,
        "guard": guard,
        "auto_paused": auto_paused,
        "auto_pause_code": auto_pause_code,
        "auto_pause_reason": auto_pause_reason,
    }


def _get_python_bridge() -> Optional[Any]:
    try:
        from . import bridge as bridge_module

        return bridge_module
    except Exception:
        return None


def _set_paused(paused: bool) -> None:
    bridge = _get_python_bridge()
    if bridge is not None:
        fn = getattr(bridge, "set_paused", None)
        if callable(fn):
            try:
                fn(bool(paused))
                return
            except Exception:
                pass

    _call_device_guard("setPaused", bool(paused), default=None)


def _shizuku_ready() -> bool:
    bridge = _get_python_bridge()
    if bridge is not None:
        fn = getattr(bridge, "shizuku_available", None) or getattr(bridge, "shizuku_ready", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                pass

    result = _call_device_guard("shizukuReady", default=None)
    if result is None:
        return True
    return bool(result)


def _get_scheduler() -> Optional[Any]:
    try:
        from . import scheduler as scheduler_module

        return scheduler_module
    except Exception:
        return None


def _scheduler_is_running() -> bool:
    scheduler = _get_scheduler()
    if scheduler is None:
        return False

    status_fn = getattr(scheduler, "status", None)
    if callable(status_fn):
        try:
            snap = status_fn()
            if isinstance(snap, dict):
                return bool(snap.get("running"))
        except Exception:
            pass

    running_attr = getattr(scheduler, "running", None)
    if callable(running_attr):
        try:
            return bool(running_attr())
        except Exception:
            pass

    return bool(running_attr) if running_attr is not None else False


def _scheduler_start() -> bool:
    scheduler = _get_scheduler()
    if scheduler is None:
        return False

    start_fn = getattr(scheduler, "start", None)
    if callable(start_fn):
        try:
            start_fn(config_name="oas1")
            return True
        except Exception:
            return False
    return False


def _guard_tick(workspace: str) -> None:
    cfg = load_config(workspace)
    telemetry = read_telemetry()
    now_hhmm = time.strftime("%H:%M")

    with _state_lock:
        was_overheat = _state["auto_pause_code"] == "overheat"
        auto_paused = bool(_state["auto_paused"])

    guard = evaluate_guard(cfg, telemetry, now_hhmm=now_hhmm, was_overheat=was_overheat)
    scheduler_running = _scheduler_is_running()

    if guard["should_pause"] and scheduler_running:
        _set_paused(True)
        with _state_lock:
            _state["auto_paused"] = True
            _state["auto_pause_code"] = guard["code"]
            _state["auto_pause_reason"] = guard["reason"]
            last_alert_code = _state["last_alert_code"]

        if cfg.get("notify_alerts", True) and last_alert_code != guard["code"]:
            if guard["code"] == "overheat":
                notify_alert("高温保护", guard["reason"], alert_id=2101)
            elif guard["code"] == "low_battery":
                notify_alert("低电量保护", guard["reason"], alert_id=2102)
            elif guard["code"] == "offline":
                notify_alert("网络断开", guard["reason"], alert_id=2103)
            elif guard["code"] == "sleep_window":
                notify_alert("休眠时段", guard["reason"], alert_id=2104)

            with _state_lock:
                _state["last_alert_code"] = guard["code"]
    else:
        if not guard["should_pause"] and auto_paused:
            _set_paused(False)
            with _state_lock:
                _state["auto_paused"] = False
                _state["auto_pause_code"] = ""
                _state["auto_pause_reason"] = ""
                _state["last_alert_code"] = None

    if cfg.get("shizuku_alert", True) and scheduler_running:
        if not _shizuku_ready():
            now_ts = time.time()
            with _state_lock:
                last_shizuku_ts = _state["last_shizuku_alert_ts"]

            if now_ts - last_shizuku_ts >= 600:
                notify_alert(
                    "Shizuku 断开",
                    "Shizuku 服务不可用，部分自动化功能可能受限",
                    alert_id=2105,
                )
                with _state_lock:
                    _state["last_shizuku_alert_ts"] = now_ts

    if (
        cfg.get("schedule_enabled", False)
        and guard.get("should_auto_start")
        and not scheduler_running
    ):
        today = time.strftime("%Y-%m-%d")
        with _state_lock:
            already_started = today in _state["auto_start_dates"]

        if not already_started:
            _scheduler_start()
            with _state_lock:
                _state["auto_start_dates"].add(today)


def _guard_loop(workspace: str) -> None:
    while True:
        try:
            _guard_tick(workspace)
        except Exception:
            pass
        time.sleep(20)


def start_guard_loop(workspace: str) -> None:
    with _state_lock:
        thread = _state["thread"]
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return

        thread = threading.Thread(
            target=_guard_loop,
            args=(workspace,),
            name="device-guard-loop",
            daemon=True,
        )
        _state["thread"] = thread

    thread.start()
