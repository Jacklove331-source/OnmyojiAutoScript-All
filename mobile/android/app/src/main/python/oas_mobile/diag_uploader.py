"""OASX Mobile 个人专享诊断日志与现场快照上传器（纯 Python 标准库）。

设计原则：
- 公开版默认无内置凭据（configured=False），完全静默关闭、不产生任何网络请求；
- 个人专用版通过 APK 内置 assets/diag_private.json（已被 .gitignore 排除，绝不进源码库）提供私有仓库凭据；
- 任何返回值、日志、诊断包内容均做密钥脱敏，token 永不外泄；
- 自动上报在后台守护线程异步执行并带冷却节流，绝不阻塞任务或 UI。
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone, timedelta
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

__all__ = [
    "get_status",
    "save_config",
    "upload_report",
    "trigger_auto_upload",
    "capture_current_frame",
]

_BJ_TZ = timezone(timedelta(hours=8))
_USER_AGENT = "OASX-Diag/1.0"
_ASSET_NAME = "diag_private.json"
_CONFIG_FILENAME = "diag_config.json"
_HISTORY_FILENAME = "diag_history.json"
_MAX_HISTORY = 12
_AUTO_COOLDOWN_SECONDS = 180.0
_MAX_LOG_BYTES = 256 * 1024

_SENSITIVE_KEY_TOKENS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "cookie",
    "webhook",
    "authorization",
    "private_key",
    "push_key",
    "sckey",
    "sendkey",
    "access_key",
)

_LOCK = threading.RLock()
_STATE = {
    "uploading": False,
    "last_error": "",
    "last_upload": None,
}
_LAST_AUTO_AT: dict[tuple[str, str], float] = {}


def _now_bj() -> datetime:
    return datetime.now(_BJ_TZ)


def _mask_secret(text: object, secret: str = "") -> str:
    out = "" if text is None else str(text)
    if secret:
        out = out.replace(secret, "****")
        try:
            quoted = urllib.parse.quote(secret, safe="")
            if quoted and quoted != secret:
                out = out.replace(quoted, "****")
        except Exception:  # noqa: BLE001
            pass
    return out


def _sanitize_obj(value):
    """递归脱敏配置对象中的密钥/密码/推送令牌字段。"""
    if isinstance(value, dict):
        cleaned = {}
        for k, v in value.items():
            key_lower = str(k).lower()
            if any(tok in key_lower for tok in _SENSITIVE_KEY_TOKENS):
                if isinstance(v, str) and v.strip():
                    cleaned[k] = "***"
                elif isinstance(v, dict):
                    cleaned[k] = _sanitize_obj(v)
                else:
                    cleaned[k] = v
            else:
                cleaned[k] = _sanitize_obj(v)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_obj(item) for item in value]
    return value


def _safe_slug(text: str, fallback: str = "general", max_len: int = 32) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(text or "").strip()).strip("_")
    return (cleaned[:max_len] or fallback)


def _read_bundled_asset_config() -> dict:
    """从 Android APK assets/diag_private.json 读取个人版内置通道配置。"""
    try:
        from java import jclass  # type: ignore

        app = jclass("com.chaquo.python.Python").getPlatform().getApplication()
        stream = app.getAssets().open(_ASSET_NAME)
        try:
            buf = bytearray()
            while True:
                b = stream.read()
                if b == -1:
                    break
                buf.append(b & 0xFF)
        finally:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        data = json.loads(bytes(buf).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _workspace_path(workspace: str) -> Path:
    raw = str(workspace or "").strip() or os.getcwd()
    return Path(raw)


def _load_effective_config(workspace: str) -> dict:
    """合并 APK 内置私有配置与本地用户开关，绝不向外部回显 token。"""
    bundled = _read_bundled_asset_config()
    local_path = _workspace_path(workspace) / _CONFIG_FILENAME
    local_cfg: dict = {}
    try:
        if local_path.exists():
            raw = json.loads(local_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                local_cfg = raw
    except Exception:  # noqa: BLE001
        local_cfg = {}

    repo = str(local_cfg.get("repo") or bundled.get("repo") or "").strip()
    branch = str(local_cfg.get("branch") or bundled.get("branch") or "main").strip() or "main"
    base_url = (
        str(local_cfg.get("base_url") or bundled.get("base_url") or "https://gitcode.com")
        .strip()
        .rstrip("/")
        or "https://gitcode.com"
    )
    token = str(local_cfg.get("token") or bundled.get("token") or "").strip()
    configured = bool(repo and "/" in repo and token)

    default_enabled = bool(bundled.get("enabled", configured)) if configured else False
    default_auto = bool(bundled.get("auto_upload", configured)) if configured else False
    enabled = bool(local_cfg.get("enabled")) if "enabled" in local_cfg else default_enabled
    auto_upload = bool(local_cfg.get("auto_upload")) if "auto_upload" in local_cfg else default_auto

    return {
        "configured": configured,
        "bundled": bool(bundled.get("repo") and bundled.get("token")),
        "enabled": bool(enabled and configured),
        "auto_upload": bool(auto_upload and enabled and configured),
        "repo": repo,
        "branch": branch,
        "base_url": base_url,
        "token": token,
        "label": str(local_cfg.get("label") or bundled.get("label") or "个人专享诊断通道"),
    }


def _load_history(workspace: str) -> list[dict]:
    path = _workspace_path(workspace) / _HISTORY_FILENAME
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)][:_MAX_HISTORY]
    except Exception:  # noqa: BLE001
        pass
    return []


def _append_history(workspace: str, entry: dict) -> None:
    root = _workspace_path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    path = root / _HISTORY_FILENAME
    items = _load_history(workspace)
    items.insert(0, entry)
    del items[_MAX_HISTORY:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def get_status(workspace: str) -> dict:
    with _LOCK:
        cfg = _load_effective_config(workspace)
        history = _load_history(workspace)
        return {
            "ok": True,
            "configured": cfg["configured"],
            "bundled": cfg["bundled"],
            "enabled": cfg["enabled"],
            "auto_upload": cfg["auto_upload"],
            "repo": cfg["repo"] if cfg["configured"] else "",
            "branch": cfg["branch"] if cfg["configured"] else "",
            "label": cfg["label"],
            "uploading": bool(_STATE["uploading"]),
            "last_error": str(_STATE["last_error"] or ""),
            "last_upload": _STATE["last_upload"] or (history[0] if history else None),
            "history": history,
        }


def save_config(workspace: str, patch: dict) -> dict:
    if not isinstance(patch, dict):
        return {"ok": False, "error": "配置格式无效"}
    with _LOCK:
        root = _workspace_path(workspace)
        root.mkdir(parents=True, exist_ok=True)
        path = root / _CONFIG_FILENAME
        current: dict = {}
        try:
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    current = loaded
        except Exception:  # noqa: BLE001
            current = {}

        for key in ("enabled", "auto_upload"):
            if key in patch:
                current[key] = bool(patch[key])
        for key in ("repo", "branch", "base_url", "token", "label"):
            if key in patch and patch[key] is not None:
                val = str(patch[key]).strip()
                if key == "token" and not val:
                    continue
                current[key] = val

        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return get_status(workspace)


def capture_current_frame() -> bytes:
    """尽量无侵入地抓一帧后台虚拟屏画面（不建新屏、不弹窗）。"""
    try:
        from . import bridge

        if callable(getattr(bridge, "vdstream_running", None)) and bridge.vdstream_running():
            frame = b""
            if callable(getattr(bridge, "vdstream_capture", None)):
                frame = bridge.vdstream_capture(68) or b""
            if not frame and callable(getattr(bridge, "vdstream_jpeg", None)):
                frame = bridge.vdstream_jpeg() or b""
            if frame:
                return bytes(frame)
    except Exception:  # noqa: BLE001
        pass
    return b""


def _collect_recent_log_text(workspace: str, max_lines: int = 400) -> str:
    parts: list[str] = []
    try:
        from . import task_runner

        t_stat = task_runner.status()
        tail = t_stat.get("log_tail") or []
        if tail:
            parts.append("=== task_runner.log_tail ===")
            parts.extend(str(line) for line in tail)
    except Exception:  # noqa: BLE001
        pass

    try:
        log_root = _workspace_path(workspace) / "log"
        if log_root.exists():
            candidates = []
            for p in log_root.rglob("*"):
                if p.is_file() and p.suffix.lower() in (".txt", ".log"):
                    try:
                        candidates.append((p.stat().st_mtime, p))
                    except OSError:
                        continue
            candidates.sort(key=lambda item: item[0], reverse=True)
            if candidates:
                latest_path = candidates[0][1]
                raw = latest_path.read_bytes()
                if len(raw) > _MAX_LOG_BYTES:
                    raw = raw[-_MAX_LOG_BYTES:]
                lines = raw.decode("utf-8", errors="replace").splitlines()[-max_lines:]
                parts.append(f"\n=== latest file: {latest_path.relative_to(log_root).as_posix()} ===")
                parts.extend(lines)
    except Exception:  # noqa: BLE001
        pass

    return "\n".join(parts) + ("\n" if parts else "")


def _collect_android_device_info() -> dict:
    info: dict = {}
    try:
        from java import jclass  # type: ignore

        build = jclass("android.os.Build")
        version = jclass("android.os.Build$VERSION")
        info = {
            "brand": str(build.BRAND),
            "model": str(build.MODEL),
            "device": str(build.DEVICE),
            "android_release": str(version.RELEASE),
            "sdk_int": int(version.SDK_INT),
        }
    except Exception:  # noqa: BLE001
        pass
    return info


def _build_bundle(
    workspace: str,
    *,
    trigger: str,
    task: str,
    config_name: str,
    note: str,
    secret: str,
    include_screenshot: bool = True,
    screenshot_bytes: bytes | None = None,
) -> tuple[bytes, dict]:
    now = _now_bj()
    sched_status = {}
    sched_log: list[str] = []
    idle_policy = {}
    queue_info = {}
    try:
        from . import scheduler

        sched_status = scheduler.status()
        sched_log = [str(x) for x in (sched_status.get("log") or [])]
        cfg_for_policy = config_name or str(sched_status.get("config") or "oas1")
        en, min_w = scheduler._idle_close_policy(cfg_for_policy)
        idle_policy = {"enabled": en, "min_wait_seconds": min_w, "config": cfg_for_policy}
        try:
            queue_info = scheduler.task_queue(cfg_for_policy)
        except Exception as exc:  # noqa: BLE001
            queue_info = {"error": str(exc)}
    except Exception:  # noqa: BLE001
        pass

    task_status = {}
    try:
        from . import task_runner

        task_status = task_runner.status()
    except Exception:  # noqa: BLE001
        pass

    active_cfg = config_name or str(sched_status.get("config") or task_status.get("config") or "oas1")

    apk_build = ""
    ota_info = {}
    guard_info = {}
    bridge_info = {}
    try:
        from . import server as _server

        apk_build = str(_server._apk_build() or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import ota_updater

        raw_ota = ota_updater.get_status(workspace)
        ota_info = {
            "active": raw_ota.get("active"),
            "installed_version": raw_ota.get("installed_version"),
            "installed_at": raw_ota.get("installed_at"),
            "source": raw_ota.get("source"),
        }
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import device_guard

        guard_info = device_guard.get_status(workspace)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import bridge

        pkg = bridge.target_package() if callable(getattr(bridge, "target_package", None)) else ""
        bridge_info = {
            "target_package": pkg,
            "shizuku_available": bridge.shizuku_available() if callable(getattr(bridge, "shizuku_available", None)) else False,
            "shizuku_status": bridge.shizuku_status() if callable(getattr(bridge, "shizuku_status", None)) else "",
            "game_running": bridge.game_running(pkg) if callable(getattr(bridge, "game_running", None)) else False,
            "vdstream_running": bridge.vdstream_running() if callable(getattr(bridge, "vdstream_running", None)) else False,
            "vdstream_info": bridge.vdstream_info() if callable(getattr(bridge, "vdstream_info", None)) else {},
            "paused": bridge.paused() if callable(getattr(bridge, "paused", None)) else False,
        }
    except Exception:  # noqa: BLE001
        pass

    config_snapshot = {}
    try:
        cfg_file = _workspace_path(workspace) / "config" / f"{active_cfg}.json"
        if cfg_file.exists():
            raw_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            config_snapshot = _sanitize_obj(raw_cfg)
    except Exception as exc:  # noqa: BLE001
        config_snapshot = {"error": str(exc)}

    shot = b""
    if include_screenshot:
        shot = bytes(screenshot_bytes) if screenshot_bytes else capture_current_frame()
    shot_name = ""
    if shot:
        if shot[:2] == b"\xff\xd8":
            shot_name = "screen.jpg"
        elif shot[:4] == b"\x89PNG":
            shot_name = "screen.png"
        else:
            shot_name = "screen.bin"

    summary = _sanitize_obj({
        "uploaded_at": now.strftime("%Y-%m-%d %H:%M:%S %z"),
        "trigger": trigger,
        "task": task or str(task_status.get("task") or sched_status.get("current") or ""),
        "config": active_cfg,
        "note": _mask_secret(note, secret),
        "apk_build": apk_build,
        "ota": ota_info,
        "device": _collect_android_device_info(),
        "bridge": bridge_info,
        "idle_close_policy": idle_policy,
        "scheduler": {k: v for k, v in sched_status.items() if k != "log"},
        "task_status": {k: v for k, v in task_status.items() if k != "log_tail"},
        "guard": guard_info,
        "has_screenshot": bool(shot),
        "screenshot_file": shot_name,
    })

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(
            "summary.json",
            _mask_secret(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", secret),
        )
        zf.writestr(
            "scheduler_log.txt",
            _mask_secret("\n".join(sched_log) + ("\n" if sched_log else ""), secret),
        )
        zf.writestr(
            "task_log.txt",
            _mask_secret(_collect_recent_log_text(workspace), secret),
        )
        zf.writestr(
            "config_snapshot.json",
            _mask_secret(
                json.dumps(
                    {"config": config_snapshot, "queue": queue_info},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                secret,
            ),
        )
        if shot and shot_name:
            zf.writestr(shot_name, shot)

    return buf.getvalue(), summary


def _upload_bytes_to_gitcode(
    cfg: dict,
    remote_path: str,
    payload: bytes,
    message: str,
    timeout: int = 35,
) -> dict:
    repo = cfg["repo"]
    owner, name = repo.split("/", 1)
    branch = cfg["branch"]
    token = cfg["token"]
    quoted_path = urllib.parse.quote(remote_path.lstrip("/"), safe="/")
    url = f"https://api.gitcode.com/api/v5/repos/{owner}/{name}/contents/{quoted_path}"
    body = json.dumps({
        "content": base64.b64encode(payload).decode("ascii"),
        "message": message,
        "branch": branch,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "PRIVATE-TOKEN": token,
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:260]
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(_mask_secret(f"HTTP {exc.code}: {detail or exc.reason}", token)) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(_mask_secret(f"网络错误: {getattr(exc, 'reason', exc)}", token)) from None


def upload_report(
    workspace: str,
    *,
    trigger: str = "manual",
    task: str = "",
    config_name: str = "",
    note: str = "",
    include_screenshot: bool = True,
    screenshot_bytes: bytes | None = None,
) -> dict:
    """打包当前现场（日志+配置+状态+截图）并上传到个人私有仓库。"""
    cfg = _load_effective_config(workspace)
    if not cfg["configured"]:
        return {"ok": False, "error": "未配置个人诊断上传通道"}
    if not cfg["enabled"] and trigger != "manual":
        return {"ok": False, "error": "诊断上传已关闭"}

    with _LOCK:
        _STATE["uploading"] = True
        _STATE["last_error"] = ""

    token = cfg["token"]
    try:
        zip_bytes, summary = _build_bundle(
            workspace,
            trigger=trigger,
            task=task,
            config_name=config_name,
            note=note,
            secret=token,
            include_screenshot=include_screenshot,
            screenshot_bytes=screenshot_bytes,
        )
        now = _now_bj()
        day_str = now.strftime("%Y%m%d")
        ts_str = now.strftime("%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
        trig_slug = _safe_slug(trigger, "manual", 20)
        task_slug = _safe_slug(summary.get("task") or task, "idle", 28)
        remote_path = f"reports/{day_str}/{ts_str}_{trig_slug}_{task_slug}.zip"
        commit_msg = f"diag({trig_slug}): {task_slug} {now.strftime('%Y-%m-%d %H:%M:%S')}"

        _upload_bytes_to_gitcode(cfg, remote_path, zip_bytes, commit_msg)

        entry = {
            "ok": True,
            "path": remote_path,
            "size_bytes": len(zip_bytes),
            "has_screenshot": bool(summary.get("has_screenshot")),
            "trigger": trigger,
            "task": summary.get("task") or "",
            "note": summary.get("note") or "",
            "uploaded_at": summary.get("uploaded_at") or now.strftime("%Y-%m-%d %H:%M:%S %z"),
        }
        with _LOCK:
            _STATE["last_upload"] = entry
            _STATE["last_error"] = ""
        try:
            _append_history(workspace, entry)
        except Exception:  # noqa: BLE001
            pass
        return entry
    except Exception as exc:  # noqa: BLE001
        err = _mask_secret(str(exc), token)
        with _LOCK:
            _STATE["last_error"] = err
        return {"ok": False, "error": err}
    finally:
        with _LOCK:
            _STATE["uploading"] = False


def trigger_auto_upload(
    workspace: str,
    *,
    trigger: str,
    task: str = "",
    config_name: str = "",
    note: str = "",
    include_screenshot: bool = True,
) -> bool:
    """异常/掉线时后台异步自动上传（带冷却节流，绝不阻塞主流程）。"""
    try:
        cfg = _load_effective_config(workspace)
        if not (cfg["configured"] and cfg["enabled"] and cfg["auto_upload"]):
            return False
        key = (str(trigger or "auto"), str(task or ""))
        now = time.monotonic()
        with _LOCK:
            prev = _LAST_AUTO_AT.get(key, 0.0)
            if prev and (now - prev) < _AUTO_COOLDOWN_SECONDS:
                return False
            _LAST_AUTO_AT[key] = now

        # 在当前线程快速抢拍一帧（避免后台线程晚几秒才拍导致现场画面变掉）
        shot = capture_current_frame() if include_screenshot else b""

        def _bg() -> None:
            try:
                upload_report(
                    workspace,
                    trigger=trigger,
                    task=task,
                    config_name=config_name,
                    note=note,
                    include_screenshot=include_screenshot,
                    screenshot_bytes=shot,
                )
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(
            target=_bg,
            name=f"oas-diag-{_safe_slug(trigger, 'auto', 12)}",
            daemon=True,
        ).start()
        return True
    except Exception:  # noqa: BLE001
        return False
