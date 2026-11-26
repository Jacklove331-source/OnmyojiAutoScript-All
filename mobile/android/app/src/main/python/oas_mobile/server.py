"""OAS Mobile 本地服务：给 Flutter(OASX) 界面用的最小 HTTP 接口。

设计取舍：
- 优先用 aiohttp（异步、后续要挂 WebSocket 给 OASX 推送日志）；
- 若 aiohttp 未打进 APK（阶段 2a 为了先验证「Python + Kotlin 桥」而没装依赖），
  自动退回 Python 标准库 `http.server`，接口保持一致，保证阶段二能立刻在真机上验证。

接口：
    GET  /health          {ok, version, bridge_available, bridge_reason, server}
    GET  /screenshot.png  当前屏幕 PNG
    POST /tap?x=&y=       点击
    POST /swipe?x1=&y1=&x2=&y2=
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, bridge

logger = logging.getLogger("oas_mobile.server")

DEFAULT_PORT = 22267  # 与 OAS 桌面 WebUI 相同端口，方便 OASX 复用默认地址

# 当前使用的虚拟屏 id（-1 表示未创建）。后台挂机时游戏跑在这块屏上，主屏留给用户。
VIRTUAL_DISPLAY_ID = -1

# 默认被自动化 App（阴阳师）
DEFAULT_TARGET_PACKAGE = "com.netease.onmyoji"


def _ensure_display(package: str = DEFAULT_TARGET_PACKAGE) -> int:
    """确保虚拟屏可用。

    overlay 虚拟屏在部分模拟器/ROM 上会被系统悄悄销毁（实测 MuMu：游戏搬到副屏后一段时间就没了），
    所以每次截图/注入前都要确认它还活着；不在了就重建并把任务再搬过去。
    """
    global VIRTUAL_DISPLAY_ID
    stream_display = bridge.vdstream_display()
    if bridge.vdstream_running() and stream_display >= 0:
        VIRTUAL_DISPLAY_ID = stream_display
        return stream_display
    if VIRTUAL_DISPLAY_ID >= 0 and bridge.display_alive(VIRTUAL_DISPLAY_ID):
        return VIRTUAL_DISPLAY_ID
    display = bridge.display_ensure_app(package)
    if display >= 0:
        VIRTUAL_DISPLAY_ID = display
    return display


def _set_config_value(script_name: str, task_name: str, group_name: str,
                      argument: str, raw_value, value_type: str = "") -> bool:
    """走 OAS 官方入口写配置值（校验 + 正确序列化）。"""
    try:
        from .task_runner import prepare_workspace
        prepare_workspace()
        from module.config.config import Config

        value = raw_value
        if value_type == "integer":
            value = int(float(value))
        elif value_type == "number":
            value = float(value)
        elif value_type == "boolean":
            value = str(value).lower() in ("true", "1", "yes")

        config = Config(script_name)
        # 写前备份：写坏能回滚（曾经把枚举写坏导致该任务参数页整页变空）
        import os as _os
        import shutil as _shutil

        cfg_path = _os.path.join(_workspace(), "config", script_name + ".json")
        bak_path = cfg_path + ".bak"
        try:
            _shutil.copyfile(cfg_path, bak_path)
        except OSError:
            bak_path = ""
        ok = bool(config.model.script_set_arg(task_name, group_name, argument, value))
        if ok:
            # script_set_arg 只看返回值不够：枚举/类型不对时文件坏了、下次读才炸
            try:
                Config(script_name).model.script_task(task_name)
            except Exception as error:  # noqa: BLE001
                logger.error('[mobile] config invalid after write: %r', error)
                ok = False
        if not ok and bak_path:
            try:
                _shutil.copyfile(bak_path, cfg_path)
                logger.info('[mobile] config rolled back from %s', bak_path)
            except Exception as error:  # noqa: BLE001
                logger.error('[mobile] rollback failed: %r', error)
        logger.info(f'[mobile] config set {script_name}/{task_name}/{group_name}/{argument}={value!r} -> {ok}')
        return ok
    except Exception as error:  # noqa: BLE001
        logger.exception(f'[mobile] config set failed: {error!r}')
        return False


def _task_args(script_name: str, task_name: str) -> dict:
    """`Config(script).model.script_task(task)` → OASX 需要的参数树。"""
    from module.config.config import Config

    config = Config(script_name)
    model = getattr(config, "model", None)
    if model is None or not hasattr(model, "script_task"):
        return {}
    return model.script_task(task_name)


def _workspace() -> str:
    """应用私有工作目录（config/ log/ 所在）；桌面环境退回 cwd。"""
    try:
        import os
        from java import jclass  # type: ignore

        application = jclass("com.chaquo.python.Python").getPlatform().getApplication()
        base = application.getFilesDir().getAbsolutePath()
        return os.path.join(base, "oas")
    except Exception:  # noqa: BLE001
        import os
        return os.getcwd()


def _config_list() -> list:
    """列出工作目录 config/*.json（OASX 的配置下拉）。"""
    try:
        import glob
        import os
        cfg_dir = os.path.join(_workspace(), "config")
        os.makedirs(cfg_dir, exist_ok=True)
        pattern = os.path.join(cfg_dir, "*.json")
        names = sorted(os.path.splitext(os.path.basename(path))[0]
                       for path in glob.glob(pattern))
        if not names:
            # 没有任何配置时落一份最小的 oas1，保证 OASX 下拉可用
            import json
            default_path = os.path.join(cfg_dir, "oas1.json")
            if not os.path.exists(default_path):
                with open(default_path, "w", encoding="utf-8") as handle:
                    json.dump({}, handle)
            names = ["oas1"]
        return names
    except Exception:  # noqa: BLE001
        return []


def _next_config_name() -> str:
    existing = set(_config_list())
    for index in range(1, 100):
        name = f"oas{index}"
        if name not in existing:
            return name
    return "oas_new"


ANDROID_UNSUPPORTED_TASKS = {
    # 上游 AutoCheckinBigGod 的工作方式是「Windows 侧 adb.exe + frida.exe 通过 USB/TCP attach
    # 到装了官方网易大神 App 的手机」：_get_frida_exe() 找 python_dir/Scripts/frida.exe、
    # _NO_WINDOW 用 win32 常量、_push_frida_server() 往 /data/local/tmp 推 frida-server 并 su 启动。
    # Chaquopy 容器里没有 adb 客户端、没有 frida.exe，push/su 需要 root，且依赖已登录的官方大神 App，
    # 所以这一项在 Android 端是架构性不可用（不是参数问题），菜单里标出来、不进定时计划。
    "AutoCheckinBigGod": "Android 端不支持：上游依赖桌面端 adb+frida 与官方网易大神 App",
}


def _script_menu() -> dict:
    """List scheduled task configurations and any other packaged entry points."""
    import os
    from importlib.util import find_spec

    names = set()
    try:
        from module.config.config_model import ConfigModel

        for key, field in ConfigModel.__fields__.items():
            if "scheduler" in getattr(field.type_, "__fields__", {}):
                names.add(ConfigModel.type(key))
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not read configured task names: %s", type(error).__name__)

    try:
        import tasks as tasks_package

        base = os.path.dirname(getattr(tasks_package, "__file__", "") or "")
        if not base:
            base = next(iter(getattr(tasks_package, "__path__", ())), "")
        directories = os.listdir(base) if base else []
    except (ImportError, OSError):
        directories = []
    for name in directories:
        if name in names or name.startswith("__") or "." in name:
            continue
        try:
            if find_spec(f"tasks.{name}.script_task") is not None:
                names.add(name)
        except (ImportError, AttributeError, ValueError):
            continue
    sorted_names = sorted(names)
    mapping = _i18n_map("zh_CN")
    titles = {n: mapping[n] for n in sorted_names if n in mapping}
    return {
        "Tasks": sorted_names,
        "titles": titles,
        "unsupported": dict(ANDROID_UNSUPPORTED_TASKS),
    }


_I18N_CACHE = {}


def _i18n_map(lang: str = "zh_CN") -> dict:
    cache = _I18N_CACHE.get(lang)
    if cache is not None:
        return cache
    mapping = {}
    if lang == "zh_CN":
        # 优先用构建期生成的 .py（XML 在 APK 里读不到，只剩 .py 会被编进包）
        try:
            from .i18n_zh import MESSAGES

            mapping.update(MESSAGES)
        except Exception:  # noqa: BLE001
            pass
    try:
        import os as _os
        import xml.etree.ElementTree as _et

        path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                             "..", "module", "config", "i18n", lang + ".xml")
        for msg in _et.parse(path).getroot().findall("message"):
            src = (msg.findtext("source") or "").strip()
            dst = (msg.findtext("translation") or "").strip()
            if src and dst:
                mapping[src] = dst
    except Exception:  # noqa: BLE001
        pass
    _I18N_CACHE[lang] = mapping
    return mapping


def _enum_table() -> dict:
    try:
        from .enum_values import ENUM_VALUES

        return ENUM_VALUES
    except Exception:  # noqa: BLE001
        try:
            from oas_mobile.enum_values import ENUM_VALUES

            return ENUM_VALUES
        except Exception:  # noqa: BLE001
            return {}


def _title_of(mapping: dict, name: str) -> str:
    """从 i18n 里找字段中文标题：裸名 -> Title Case -> "Title Case Type"。"""
    if not name:
        return ""
    title_case = " ".join(word.capitalize() for word in name.split("_"))
    for key in (name, title_case, title_case + " Type"):
        found = mapping.get(key)
        if found:
            return found
    return ""


_FIELD_ENUMS = {}


def _field_enums() -> dict:
    """构建期导出的枚举表（运行时不能反射 pydantic，只能查表）。"""
    if _FIELD_ENUMS:
        return _FIELD_ENUMS
    try:
        from .enum_map import FIELD_ENUMS

        _FIELD_ENUMS.update(FIELD_ENUMS)
    except Exception:  # noqa: BLE001
        pass
    return _FIELD_ENUMS


def _attach_labels(payload: dict, script_name: str = "", task_name: str = "") -> dict:
    """给参数树补中文标题、说明和枚举候选（只加字段，不改原键）。"""
    if not isinstance(payload, dict):
        return payload
    mapping = _i18n_map("zh_CN")
    enums = _field_enums()
    for group_name, fields in payload.items():
        if not isinstance(fields, list):
            continue
        for field in fields:
            if not isinstance(field, dict):
                continue
            name = field.get("name") or ""
            if not name:
                continue
            title = _title_of(mapping, name)
            if title:
                field["title_zh"] = title
                field.setdefault("title_en", field.get("title"))
                field["title"] = title  # 前端读 title，直接给中文
            help_text = mapping.get(name + "_help") or ""
            if help_text:
                field["help_text"] = help_text
            if isinstance(field.get("value"), list):
                continue
            candidates = enums.get((group_name, name)) or enums.get(name) or []
            if not candidates:
                continue
            field["type"] = "enum"
            field["option_values"] = list(candidates)
            field["option_labels"] = list(candidates)
            field["options"] = [{"value": v, "label": v} for v in candidates]
    return payload


def _log_entries():
    from pathlib import Path

    root = (Path(_workspace()) / "log").resolve()
    entries = []
    for candidate in root.rglob("*"):
        if candidate.suffix.lower() not in (".txt", ".log"):
            continue
        try:
            resolved = candidate.resolve(strict=True)
            if root not in resolved.parents or not resolved.is_file():
                continue
            stat = resolved.stat()
            path = "log/" + candidate.relative_to(root).as_posix()
        except (OSError, ValueError, RuntimeError):
            continue
        entries.append((path, resolved, stat))
    return sorted(entries, key=lambda item: (item[2].st_mtime, item[0]), reverse=True)


def _log_files() -> dict:
    return {
        "ok": True,
        "files": [
            {"path": path, "size": stat.st_size, "mtime": int(stat.st_mtime)}
            for path, _, stat in _log_entries()[:100]
        ],
    }


def _log_tail(lines: int = 60, path: str = "") -> dict:
    from collections import deque

    entries = _log_entries()
    entry = next((item for item in entries if not path or item[0] == path), None)
    if entry is None:
        return ({"ok": False, "error": "找不到这份日志", "lines": []} if path
                else {"ok": True, "file": None, "lines": []})
    name, file, stat = entry
    try:
        with file.open("r", encoding="utf-8", errors="replace") as handle:
            content = deque(handle, maxlen=max(1, min(lines, 1000)))
    except OSError:
        return {"ok": False, "error": "日志读取失败", "lines": []}
    return {
        "ok": True,
        "file": file.name,
        "path": name,
        "mtime": int(stat.st_mtime),
        "lines": [line.rstrip("\n") for line in content],
    }


def _save_frame(data) -> None:
    """把刚抓到的 PNG 存进 screenshot/，最新的保留 20 张。"""
    import os
    import time

    try:
        frame_dir = os.path.join(_workspace(), "screenshot")
        os.makedirs(frame_dir, exist_ok=True)
        name = "frame-%d.png" % int(time.time() * 1000)
        with open(os.path.join(frame_dir, name), "wb") as handle:
            handle.write(data)
        files = sorted(
            (os.path.join(frame_dir, item) for item in os.listdir(frame_dir) if item.endswith(".png")),
            key=os.path.getmtime,
            reverse=True,
        )
        for old in files[20:]:
            try:
                os.remove(old)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass


def _latest_frame() -> dict:
    """返回最近一帧 PNG（base64）及其时间；没有就如实告诉前端。"""
    import base64
    import glob
    import os

    try:
        frame_dir = os.path.join(_workspace(), "screenshot")
        files = sorted(glob.glob(os.path.join(frame_dir, "*.png")),
                       key=os.path.getmtime, reverse=True)
        if not files:
            frame_dir = os.path.join(_workspace(), "frames")
            files = sorted(glob.glob(os.path.join(frame_dir, "*.png")),
                           key=os.path.getmtime, reverse=True)
        if not files:
            # 没历史帧就现抓一张（虚拟屏路径在模拟器上不可用，但 capture_png 可用）
            try:
                data = bridge.capture_png()
            except Exception:  # noqa: BLE001
                data = None
            if not data or len(data) < 12000:
                # 720x1280 的真实画面压缩后远超 12KB；小于该值基本是全黑帧
                return {"ok": True, "has_frame": False,
                        "reason": "抓到的帧是全黑（模拟器无障碍截屏限制）",
                        "image": None}
            _save_frame(data)
            return {
                "ok": True,
                "has_frame": True,
                "mtime": 0,
                "image": base64.b64encode(data).decode("ascii"),
            }
        newest = ""
        raw = b""
        for candidate in files:
            data = open(candidate, "rb").read()
            if len(data) >= 12000:  # 太小的基本是全黑帧，跳过
                newest, raw = candidate, data
                break
        if not newest:
            # 历史帧全是黑的：不能就此说没帧，得现抓一张再判
            try:
                fresh = bridge.capture_png()
            except Exception:  # noqa: BLE001
                fresh = None
            if fresh and len(fresh) >= 12000:
                _save_frame(fresh)
                return {"ok": True, "has_frame": True, "mtime": 0,
                        "image": base64.b64encode(fresh).decode("ascii")}
            return {"ok": True, "has_frame": False,
                    "reason": "抓到的帧仍是全黑（模拟器无障碍截屏限制）",
                    "image": None}
        data = base64.b64encode(raw).decode("ascii")
        return {
            "ok": True,
            "has_frame": True,
            "mtime": int(os.path.getmtime(newest)),
            "image": data,
        }
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": repr(error), "has_frame": False}


def _ai_config_payload() -> dict:
    """读自愈（AI 回复）配置，永不返回 Key 本身。"""
    try:
        from module.ai_recovery.config import config_path, load_config

        cfg = load_config(force=True)
        return {
            "ok": True,
            "enabled": bool(cfg.enabled),
            "mode": cfg.mode,
            "model": cfg.api.model,
            "base_url": cfg.api.base_url,
            "has_key": bool(cfg.resolve_api_key()),
            "config_file": str(config_path()),
        }
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": repr(error)}


def _ai_config_apply(raw: bytes) -> dict:
    """写入 api_key / mode / model / enabled；Key 只写进密钥文件，不回显。"""
    import json
    import os
    import re

    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": "bad json: %r" % (error,)}

    try:
        from pathlib import Path

        from module.ai_recovery.config import DEFAULT_KEY_FILE, config_path, load_config

        cfg = load_config(force=True)
        changed = []

        api_key = str(body.get("api_key") or "").strip()
        if api_key:
            key_file = Path(cfg.api.key_file)
            if not key_file.is_absolute():
                key_file = DEFAULT_KEY_FILE
            key_file.parent.mkdir(parents=True, exist_ok=True)
            with open(key_file, "w", encoding="utf-8") as handle:
                handle.write(api_key + "\n")
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
            changed.append("api_key")

        target = Path(config_path())
        if not target.exists():
            from module.ai_recovery.config import write_default_config

            write_default_config(target)
        text = target.read_text(encoding="utf-8")

        mode = str(body.get("mode") or "").strip()
        if mode in ("observe", "auto"):
            text = re.sub(r"(?m)^mode:.*$", "mode: %s" % mode, text)
            changed.append("mode")
        model = str(body.get("model") or "").strip()
        if model:
            text = re.sub(r"(?m)^(\s+)model:.*$", r"\1model: %s" % model, text, count=1)
            changed.append("model")
        if "enabled" in body:
            flag = "true" if body.get("enabled") else "false"
            text = re.sub(r"(?m)^enabled:.*$", "enabled: %s" % flag, text)
            changed.append("enabled")

        target.write_text(text, encoding="utf-8")
        fresh = load_config(force=True)
        return {
            "ok": True,
            "changed": changed,
            "enabled": bool(fresh.enabled),
            "mode": fresh.mode,
            "model": fresh.api.model,
            "has_key": bool(fresh.resolve_api_key()),
        }
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": repr(error)}


def _apk_build() -> str:
    """Use the installed package metadata so client status tracks the actual APK."""
    try:
        from java import jclass

        application = jclass("com.chaquo.python.Python").getPlatform().getApplication()
        details = application.getPackageManager().getPackageInfo(application.getPackageName(), 0)
        sdk = jclass("android.os.Build$VERSION").SDK_INT
        code = details.getLongVersionCode() if sdk >= 28 else details.versionCode
        return f"{details.versionName}+{code}"
    except Exception:  # Android package metadata is unavailable in host tests.
        return __version__


def _health_payload() -> dict:
    accessibility_ready = bridge.available()
    shizuku_ready = bridge.shizuku_available()
    available = shizuku_ready or accessibility_ready
    return {
        "ok": True,
        "build": _apk_build(),
        "version": __version__,
        "bridge_available": available,
        "shizuku_ready": shizuku_ready,
        "accessibility_ready": accessibility_ready,
        "bridge_reason": None if available else bridge.unavailable_reason(),
        "background_service": bridge.service_running(),
        "paused": bridge.paused(),
    }


def _state_payload() -> dict:
    """后台运行状态：前台是哪个 App、目标游戏是否在前台、是否被用户暂停。"""
    foreground = bridge.foreground_package()
    info = bridge.vdstream_info()
    display = int(info.get('display_id', -1)) if info.get('running') else -1
    return {
        "ok": True,
        "foreground": foreground,
        "target_foreground": bridge.is_target_foreground(),
        "background_service": bridge.service_running(),
        "paused": bridge.paused(),
        "can_inject": display >= 0 and info.get("target_on_display") is True and not bridge.paused(),
        # 后台挂机（虚拟屏）能力
        "shizuku": bridge.shizuku_status(),
        "shizuku_ready": bridge.shizuku_available(),
        "virtual_display": display,
    }


def _injection_blocked() -> dict | None:
    """后台运行的核心守卫：目标游戏不在前台/用户暂停时，禁止注入手势。"""
    if bridge.paused():
        return {"ok": False, "paused": True, "reason": "paused by user"}
    if not bridge.is_target_foreground():
        return {"ok": False, "paused": True,
                "reason": f"target app not foreground (foreground={bridge.foreground_package()!r})"}
    return None


# ---------------------------------------------------------------- stdlib 实现


class _Handler(BaseHTTPRequestHandler):
    server_version = "OASMobile/0.1"

    def log_message(self, fmt: str, *args) -> None:  # 打到 logcat，方便 adb logcat 过滤
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json")

    def _handle_websocket(self, name: str) -> None:
        """`/ws/<config>`：握手后进入连接循环，推送状态/日志并响应 start/stop/get_state。"""
        from . import ws as ws_mod

        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_error(400, "missing Sec-WebSocket-Key")
            return
        self.wfile.write(ws_mod.handshake_response(key))
        self.wfile.flush()
        sock = self.connection

        def on_message(config_name: str, text: str):
            from . import scheduler as sched
            from . import task_runner

            if text == "__tick__":
                state = "running" if task_runner.status().get("running") else "idle"
                lines = [json.dumps({"state": state})]
                for entry in (task_runner.status().get("log_tail") or [])[-3:]:
                    lines.append(entry)
                return lines
            if text == "get_state":
                state = "running" if task_runner.status().get("running") else "idle"
                return [json.dumps({"state": state})]
            if text == "start":
                task_runner.run_task("WantedQuests", config_name, login=True)
                return [json.dumps({"state": "running"}), "[ws] WantedQuests started"]
            if text == "stop":
                sched.stop()
                return [json.dumps({"state": "idle"}), "[ws] stop requested"]
            return []

        ws_mod.serve_connection(sock, name, on_message)

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(route.query).items()}

        if route.path == "/task/audit":
            import importlib
            results = []
            for name in _script_menu().get("Tasks", []):
                try:
                    task_module = importlib.import_module(f"tasks.{name}.script_task")
                    task_class = getattr(task_module, "ScriptTask")
                    results.append({"task": name, "ok": callable(getattr(task_class, "run", None))})
                except Exception as error:
                    results.append({"task": name, "ok": False, "error": f"{type(error).__name__}: {error}"})
            self._send_json(200, {"ok": bool(results) and all(row["ok"] for row in results), "tasks": results})
            return
        if route.path == "/config_tasks":
            from .scheduler import configuration_tasks
            from .task_runner import _valid_config
            config_name = query.get("config", "oas1")
            if not _valid_config(config_name):
                self._send_json(400, {"ok": False, "error": "invalid config"})
                return
            try:
                self._send_json(200, {"ok": True, "config": config_name, "tasks": configuration_tasks(config_name)})
            except Exception as error:
                self._send_json(500, {"ok": False, "error": str(error)})
            return
        if route.path.startswith("/ws/"):
            self._handle_websocket(route.path[len("/ws/"):])
            return
        if route.path == "/log/files":
            self._send_json(200, _log_files())
            return
        if route.path == "/log/tail":
            try:
                lines = int(query.get("lines") or 60)
            except ValueError:
                self._send_json(400, {"ok": False, "error": "invalid line count", "lines": []})
                return
            self._send_json(200, _log_tail(lines, query.get("path", "")))
            return
        if route.path == "/i18n":
            translations = _i18n_map()
            keys = query.get("keys", "").split(",")
            self._send_json(200, {"ok": True, "map": {
                key: translations[key] for key in keys if key in translations
            }})
            return
        if route.path == "/frame/latest":
            payload = _latest_frame()
            encoded = payload.get("image")
            if encoded:
                import base64 as _b64

                self._send(200, _b64.b64decode(encoded), "image/png")
                return
            self._send_json(200, payload)
            return
        if route.path == "/debug/threads":
            import sys as _sys
            import threading as _threading
            import traceback as _tb

            names = {t.ident: t.name for t in _threading.enumerate()}
            parts = []
            for tid, frame in _sys._current_frames().items():  # noqa: SLF001
                parts.append("=== %s (%s) ===" % (tid, names.get(tid, "?")))
                parts.extend(_tb.format_stack(frame))
            self._send_json(200, {"ok": True, "threads": "".join(parts)})
            return
        if route.path == "/health":
            payload = _health_payload()
            payload["server"] = "stdlib"
            self._send_json(200, payload)
            return
        if route.path == "/ai/config":
            self._send_json(200, _ai_config_payload())
            return
        if route.path == "/ai/health":
            try:
                from module.ai_recovery.config import load_config
                cfg = load_config(force=True)
                self._send_json(200, {
                    "ok": True,
                    "enabled": bool(cfg.enabled),
                    "mode": cfg.mode,
                    "model": cfg.api.model,
                    "has_key": bool(cfg.resolve_api_key()),
                })
            except Exception as error:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": str(error)})
            return
        if route.path == "/ai/test":
            try:
                import cv2
                import numpy as np
                from dataclasses import asdict
                from module.ai_recovery.config import load_config
                from module.ai_recovery.client import DeepSeekClient
                png = bridge.capture_png()
                image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
                ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if not ok:
                    raise RuntimeError("jpeg encode failed")
                cfg = load_config(force=True)
                result = DeepSeekClient(cfg).analyze(
                    buf.tobytes(),
                    {"task": "ai_test", "page": "unknown",
                     "anomaly": "manual healthcheck",
                     "anomaly_detail": "verify DeepSeek vision on current screen"},
                )
                self._send_json(200, {
                    "ok": True,
                    "model": cfg.api.model,
                    "latency_s": round(result.latency_s, 2),
                    "usage": asdict(result.usage),
                    "diagnosis": result.diagnosis.to_dict(),
                })
            except Exception as error:  # noqa: BLE001
                self._send_json(503, {"ok": False, "error": str(error)})
            return
        if route.path == "/monitor/start":
            ok = bridge.vdstream_start(int(query.get("width", 1280)),
                                       int(query.get("height", 720)),
                                       int(query.get("display", -1)))
            self._send_json(200, {"ok": ok, "status": bridge.vdstream_status()})
            return
        if route.path == "/monitor/stop":
            # 停流的同时把游戏搬回主屏：模拟器窗口只渲染 display 0，
            # 游戏留在虚拟屏上时用户会以为「关不掉、点标签也回不去」（实测 2026-11-22）。
            # 顺序很重要：**先搬到主屏，再停流**。反过来的话虚拟屏被销毁会连带把游戏 Activity 干掉，
            # 用户看到的就是「游戏直接消失」而不是「回到桌面可见」。
            try:
                bridge.display_move_task(bridge.target_package(), 0)
            except Exception:  # noqa: BLE001
                pass
            _stopped = bridge.vdstream_stop()
            # 停完后把前台让给桌面：停流后画面上只剩 OASX 自己，用户隔着模拟器窗口会以为
            # "切不回桌面"（实测按一次 Home 立刻回 lawnchair）。默认回桌面，?home=0 可关掉。
            if query.get("home", "1").strip().lower() not in ("0", "false", "no"):
                try:
                    bridge.send_home()
                except Exception:  # noqa: BLE001
                    pass
            self._send_json(200, {"ok": _stopped})
            return
        if route.path == "/monitor/status":
            self._send_json(200, {"ok": True, "status": bridge.vdstream_status(), **bridge.vdstream_info()})
            return
        if route.path == "/monitor/frame":
            if callable(getattr(bridge, "vdstream_monitor_jpeg", None)):
                data = bridge.vdstream_monitor_jpeg()
            else:
                data = bridge.vdstream_jpeg()
            if not data:
                self._send_json(503, {"ok": False, "error": "no frame", "status": bridge.vdstream_status()})
                return
            info = bridge.vdstream_info()
            self.send_response(200)
            self.send_header("X-OAS-Frame-Id", str(info.get("frame_id", 0)))
            self.send_header("X-OAS-Captured-At", str(info.get("captured_at_ms", 0)))
            self.send_header("X-OAS-Display-Id", str(info.get("display_id", -1)))
            self.send_header("X-OAS-Width", str(info.get("width", 0)))
            self.send_header("X-OAS-Height", str(info.get("height", 0)))
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if route.path == "/vd/probe":
            payload = {"ok": False, "code": None, "output": "", "error": ""}
            shizuku = getattr(bridge, "_SHIZUKU", None)
            if shizuku is None:
                payload["error"] = "shizuku object missing"
            else:
                def _pick(obj, name, default=None):
                    try:
                        return getattr(obj, name)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        return obj.callAttr(name)
                    except Exception:  # noqa: BLE001
                        return default

                try:
                    result = shizuku.exec("id", 5000)
                    payload["ok"] = True
                    payload["repr"] = repr(result)[:300]
                    payload["code"] = _pick(result, "code", _pick(result, "getCode"))
                    payload["output"] = str(_pick(result, "output", _pick(result, "getOutput", "")))[:400]
                except Exception as error:  # noqa: BLE001
                    payload["error"] = f"{type(error).__name__}: {error}"
            self._send_json(200, payload)
            return
        if route.path == "/vd/screenshot":
            # 任何异常都要回 JSON；不能让请求线程死在这里（之前直接断连、前端只能看到空）。
            try:
                hidden_id = -1
                try:
                    if bridge.hidden_ready():
                        hidden_id = bridge.hidden_display()
                        requested = int(query.get("display", -1))
                        if requested in (-1, hidden_id):
                            data = bridge.hidden_screenshot()
                            if data:
                                _save_frame(data)
                                self._send(200, data, "image/png")
                                return
                except Exception as error:  # noqa: BLE001
                    logger.warning(f'[mobile] hidden screenshot failed: {error!r}')

                global VIRTUAL_DISPLAY_ID
                if VIRTUAL_DISPLAY_ID < 0:
                    created = bridge.display_create(1280, 720)
                    if created >= 0:
                        VIRTUAL_DISPLAY_ID = created
                display = int(query.get("display", VIRTUAL_DISPLAY_ID))
                if display < 0:
                    self._send_json(503, {"ok": False, "error": "no display available"})
                    return
                data = bridge.display_screenshot(display)
                if not data:
                    self._send_json(503, {"ok": False, "error": f"empty frame (display={display})"})
                    return
                _save_frame(data)
                self._send(200, data, "image/png")
            except Exception as error:  # noqa: BLE001
                logger.warning(f'[mobile] vd screenshot failed: {error!r}')
                try:
                    data = bridge.capture_png()
                except Exception:  # noqa: BLE001
                    data = None
                if data:
                    _save_frame(data)
                    self._send(200, data, "image/png")
                else:
                    self._send_json(503, {"ok": False, "error": repr(error)})
            return
        if route.path == "/assets":
            from .matcher import list_assets
            self._send_json(200, {"ok": True, "assets": list_assets()})
            return
        if route.path == "/match":
            from .matcher import match
            asset = query.get("asset", "")
            display = int(query.get("display", _ensure_display()))
            try:
                frame = bridge.display_screenshot(display)
            except Exception as error:  # noqa: BLE001
                self._send_json(503, {"ok": False, "error": str(error)})
                return
            self._send_json(200, match(asset, frame))
            return
        if route.path == "/match_self":
            # 自检：从当前虚拟屏帧里裁一块再匹配回去，命中位置必须等于裁剪原点
            # （证明「截图 → 解码 → 裁剪 → 模板匹配 → 坐标」整条链路在设备上正确）
            import cv2
            import numpy as np

            display = int(query.get("display", _ensure_display()))
            try:
                x = int(query.get("x", 100))
                y = int(query.get("y", 100))
                w = int(query.get("w", 120))
                h = int(query.get("h", 80))
            except ValueError:
                self._send_json(400, {"ok": False, "error": "bad crop args"})
                return
            frame_png = bridge.display_screenshot(display)
            frame = cv2.imdecode(np.frombuffer(frame_png, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                self._send_json(503, {"ok": False, "error": "empty frame"})
                return
            patch = frame[y:y + h, x:x + w]
            if patch.size == 0:
                self._send_json(400, {"ok": False, "error": "crop out of frame"})
                return
            scores = []
            for scale in (0.9, 1.0, 1.1):
                scaled = patch if abs(scale - 1.0) < 1e-6 else cv2.resize(patch, None, fx=scale, fy=scale)
                if scaled.shape[0] > frame.shape[0] or scaled.shape[1] > frame.shape[1]:
                    continue
                res = cv2.matchTemplate(frame, scaled, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                scores.append((float(max_val), (int(max_loc[0]), int(max_loc[1])), scale))
            best = max(scores)
            ok = best[0] > 0.98 and abs(best[1][0] - x) <= 2 and abs(best[1][1] - y) <= 2
            self._send_json(200, {"ok": ok, "score": round(best[0], 4),
                                  "found": list(best[1]), "crop": [x, y, w, h],
                                  "scale": best[2]})
            return
        if route.path == "/debug_paths":
            import os as _os
            info = {"cwd": _os.getcwd(), "workspace": _workspace(),
                    "workspace_exists": _os.path.isdir(_workspace()),
                    "workspace_entries": [], "tasks_dir": None, "tasks_entries": []}
            try:
                info["workspace_entries"] = sorted(_os.listdir(_workspace()))[:20]
                cfg_dir = _os.path.join(_workspace(), "config")
                info["config_files"] = sorted(_os.listdir(cfg_dir))[:20] if _os.path.isdir(cfg_dir) else []
            except Exception as error:  # noqa: BLE001
                info["workspace_error"] = repr(error)
            try:
                import tasks as _tasks
                base = _os.path.dirname(getattr(_tasks, "__file__", "") or "")
                if not base:
                    paths = getattr(_tasks, "__path__", None)
                    if paths:
                        base = list(paths)[0]
                info["tasks_dir"] = base
                info["tasks_entries"] = sorted(_os.listdir(base))[:20]
            except Exception as error:  # noqa: BLE001
                info["tasks_error"] = repr(error)
            self._send_json(200, info)
            return
        if route.path == "/home/additional_translate":
            self._send_json(200, {"zh-CN": {}, "en-US": {}})
            return
        if route.path == "/debug_args":
            import traceback as _tb
            script_name = query.get("script", "oas1")
            task_name = query.get("task", "WantedQuests")
            info = {"script": script_name, "task": task_name}
            try:
                from .task_runner import prepare_workspace
                prepare_workspace()
                from module.config.config import Config
                cfg = Config(script_name)
                info["config_ok"] = True
                model = getattr(cfg, "model", None)
                info["model_type"] = type(model).__name__ if model is not None else None
                info["has_script_task"] = hasattr(model, "script_task")
                if hasattr(model, "script_task"):
                    result = model.script_task(task_name)
                    info["result_type"] = type(result).__name__
                    info["result_keys"] = list(result.keys())[:8] if hasattr(result, "keys") else None
                info["model_attrs"] = [a for a in dir(model) if not a.startswith("_")][:25]
                try:
                    from module.config.utils import filepath_config as _fpc, read_file as _rf
                    _path = _fpc(script_name)
                    _data = _rf(_path) or {}
                    info["config_path"] = str(_path)
                    info["config_top_keys"] = list(_data.keys())[:8]
                    _task = _data.get(task_name) or {}
                    info["task_keys"] = list(_task.keys())[:8] if hasattr(_task, "keys") else None
                    _group = (_task or {}).get("wanted_quests_config") if hasattr(_task, "get") else None
                    info["coop_value_in_file"] = (_group or {}).get("cooperation_only") if hasattr(_group, "get") else None
                except Exception as _probe_error:  # noqa: BLE001
                    info["config_error"] = repr(_probe_error)
            except Exception as error:  # noqa: BLE001
                info["error"] = repr(error)
                info["trace"] = _tb.format_exc().strip().splitlines()[-6:]
            self._send_json(200, info)
            return
        if route.path == "/debug_cfg":
            import os as _os
            script_name = query.get("script", "oas1")
            try:
                from .task_runner import prepare_workspace
                prepare_workspace()
                from module.config.utils import read_file, filepath_config
                info = {"cwd": _os.getcwd(), "ws": _workspace()}
                rel = read_file(filepath_config(script_name)) or {}
                abs_path = _os.path.join(_workspace(), "config", script_name + ".json")
                abs_data = read_file(abs_path) or {}
                info["rel_path"] = filepath_config(script_name)
                info["abs_path"] = abs_path
                info["rel_top"] = list(rel.keys())[:6]
                info["abs_top"] = list(abs_data.keys())[:6]
                for label, data in (("rel", rel), ("abs", abs_data)):
                    for task_key in ("WantedQuests", "wanted_quests"):
                        section = data.get(task_key) if hasattr(data, "get") else None
                        if isinstance(section, dict):
                            group = section.get("wanted_quests_config")
                            if isinstance(group, dict):
                                info[f"{label}:{task_key}:coop"] = group.get("cooperation_only")
                self._send_json(200, info)
            except Exception as error:  # noqa: BLE001
                self._send_json(200, {"error": repr(error)})
            return
        if route.path == "/hidden":
            action = query.get("action", "status")
            try:
                if action == "ensure":
                    payload = {"ok": bridge.hidden_ensure(), "status": bridge.hidden_status()}
                elif action == "create":
                    display = bridge.hidden_create(int(query.get("width", 1280)),
                                                   int(query.get("height", 720)))
                    payload = {"ok": display >= 0, "display": display,
                               "status": bridge.hidden_status()}
                elif action == "release":
                    payload = {"ok": bridge.hidden_release()}
                elif action == "prefer":
                    bridge.hidden_set_preferred(query.get("value", "1") not in ("0", "false"))
                    payload = {"ok": True, "preferred": bridge.hidden_preferred()}
                else:
                    payload = {"ok": bridge.hidden_ready(),
                               "display": bridge.hidden_display(),
                               "status": bridge.hidden_status()}
            except Exception as error:  # noqa: BLE001
                payload = {"ok": False, "error": repr(error)}
            self._send_json(200, payload)
            return
        if route.path == "/test":
            self._send(200, b"success", "text/plain")
            return
        if route.path == "/config_list":
            self._send_json(200, _config_list())
            return
        if route.path == "/config_new_name":
            self._send_json(200, _next_config_name())
            return
        if route.path == "/home/home_menu":
            self._send_json(200, {"Home": ["Scheduler", "Update"]})
            return
        if route.path == "/script_menu":
            self._send_json(200, _script_menu())
            return
        if route.path.endswith("/args") and route.path.count("/") == 3:
            # OASX: GET /<script>/<task>/args —— 用 OAS 自己的 Config 模型生成参数表
            from .task_runner import _valid_config
            parts = route.path.strip("/").split("/")
            script_name, task_name = unquote(parts[0]), unquote(parts[1])
            if not _valid_config(script_name):
                self._send_json(400, {"ok": False, "error": "invalid config"})
                return
            try:
                from .task_runner import prepare_workspace
                prepare_workspace()
                payload = _task_args(script_name, task_name)
                payload = _attach_labels(payload, script_name, task_name)
            except Exception as error:  # noqa: BLE001
                logger.exception(f'[mobile] task args failed: {error!r}')
                # 不要用空对象冒充"没有参数"：把错误照实回给前端好定位
                payload = {"ok": False, "error": repr(error),
                           "hint": "该任务的配置节可能写坏了，看 logcat python.stderr"}
            self._send_json(200, payload)
            return
        if route.path == "/vd/frame":
            try:
                data = bridge.vdhost_screenshot()
            except Exception as error:  # noqa: BLE001
                logger.warning(f'[mobile] vdhost frame failed: {error!r}')
                data = b""
            if not data:
                self._send_json(503, {"ok": False, "error": "no frame from vdhost"})
                return
            self._send(200, data, "image/png")
            return
        if route.path == "/scheduler/status":
            from .scheduler import status as scheduler_status
            self._send_json(200, scheduler_status())
            return
        if route.path == "/scheduler/queue":
            from .scheduler import task_queue
            from .task_runner import _valid_config
            config_name = query.get("config", "oas1")
            if not _valid_config(config_name):
                self._send_json(400, {"ok": False, "error": "invalid config"})
                return
            try:
                self._send_json(200, task_queue(config_name))
            except Exception as error:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": str(error)})
            return
        if route.path == "/task/status":
            from .task_runner import status as task_status
            self._send_json(200, task_status())
            return
        if route.path == "/core_import":
            try:
                from .core_import import run as run_core_import
                payload = run_core_import()
                self._send(200, payload.encode("utf-8"), "application/json")
            except Exception as error:  # noqa: BLE001
                self._send_json(500, {"ok": False, "error": str(error)})
            return
        if route.path == "/ocr_selftest":
            import numpy as np
            import cv2

            display = int(query.get("display", _ensure_display()))
            try:
                png = bridge.display_screenshot(display)
                frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                from ppocronnx.predict_system import TextSystem
                system = TextSystem()
                results = system.detect_and_ocr(frame)
                payload = {
                    "ok": True,
                    "display": display,
                    "count": len(results),
                    "texts": [{"text": r.ocr_text, "score": round(float(r.score), 3),
                               "box": [float(v) for v in np.asarray(r.box).reshape(-1)[:4]]}
                              for r in results[:20]],
                }
            except Exception as error:  # noqa: BLE001
                payload = {"ok": False, "error": repr(error)}
            self._send_json(200, payload)
            return
        if route.path == "/ocr_roi":
            import numpy as np
            import cv2

            display = int(query.get("display", _ensure_display()))
            x = int(query.get("x", 0)); y = int(query.get("y", 0))
            w = int(query.get("w", 200)); h = int(query.get("h", 80))
            try:
                png = bridge.display_screenshot(display)
                frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                roi = frame[y:y + h, x:x + w]
                from ppocronnx.predict_system import TextSystem, _recognize
                system = TextSystem()
                variants = {}
                for index, variant in enumerate(system._preprocess_variants(roi)):
                    res = _recognize(np.ascontiguousarray(variant))
                    variants[f"v{index}"] = [{"text": r.ocr_text, "score": round(float(r.score), 3)}
                                             for r in res[:4]]
                text, score = system.ocr_single_line(roi)
                payload = {"ok": True, "roi": [x, y, w, h],
                           "single_line": {"text": text, "score": round(float(score), 3)},
                           "variants": variants}
            except Exception as error:  # noqa: BLE001
                payload = {"ok": False, "error": repr(error)}
            self._send_json(200, payload)
            return
        if route.path == "/detector_selftest":
            from .selftest import run_detector
            self._send_json(200, run_detector())
            return
        if route.path == "/selftest":
            try:
                from .selftest import run as run_selftest
                payload = run_selftest()
            except Exception as error:  # noqa: BLE001
                payload = f'{{"ok": false, "error": {str(error)!r}}}'
            self._send(200, payload.encode("utf-8"), "application/json")
            return
        if route.path == "/state":
            self._send_json(200, _state_payload())
            return
        if route.path == "/assets/snapshot":
            from . import asset_monitor
            cfg = query.get("config", "oas1")
            self._send_json(200, asset_monitor.load_snapshot(_workspace(), cfg))
            return
        if route.path == "/ota/status":
            from . import ota_updater
            self._send_json(200, ota_updater.get_status(_workspace()))
            return
        if route.path == "/ota/apk_status":
            from . import ota_updater
            self._send_json(200, ota_updater.get_apk_download_status(_workspace()))
            return
        if route.path == "/guard/status":
            from . import device_guard
            self._send_json(200, device_guard.get_status(_workspace()))
            return
        if route.path == "/screenshot.png":
            try:
                data = bridge.capture_png()
            except Exception as error:  # noqa: BLE001
                self._send_json(503, {"ok": False, "error": str(error)})
                return
            self._send(200, data, "image/png")
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_PUT(self) -> None:  # noqa: N802
        """两种 PUT：中文词库（忽略）与任务参数值（写进 config json）。"""
        route = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(route.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        body = b""
        if length:
            try:
                body = self.rfile.read(length)
            except OSError:
                body = b""

        parts = route.path.strip("/").split("/")
        # /<script>/<task>/<group>/<argument>/value
        if len(parts) == 5 and parts[4] == "value":
            from .task_runner import _valid_config
            script_name, task_name, group_name, argument = (unquote(p) for p in parts[:4])
            if not _valid_config(script_name):
                self._send_json(400, {"ok": False, "error": "invalid config"})
                return
            raw_value = query.get("value")
            if raw_value is None and body:
                raw_value = body.decode("utf-8", "replace").strip().strip('"')
            ok = _set_config_value(script_name, task_name, group_name, argument,
                                   raw_value, query.get("types", ""))
            self._send(200, b"true" if ok else b"false", "text/plain")
            return
        self._send(200, b"true", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        global VIRTUAL_DISPLAY_ID
        route = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(route.query).items()}
        blocked = _injection_blocked() if route.path in ("/tap", "/swipe") else None
        try:
            if route.path == "/background/start":
                package = bridge.target_package()
                ok = bridge.vdstream_start(1280, 720)
                display = bridge.display_ensure_app(package) if ok else -1
                if ok and display < 0 and hasattr(bridge, "ensure_game_started"):
                    bridge.ensure_game_started(package)
                    display = bridge.display_ensure_app(package)
                self._send_json(200, {"ok": display >= 0, "display": display,
                                     "error": "" if display >= 0 else "无法启动后台游戏，请检查游戏首次启动界面和 Shizuku 授权",
                                     **bridge.vdstream_info()})
                return
            if route.path == "/background/foreground":
                package = bridge.target_package()
                moved = bool(bridge.display_move_task(package, 0))
                self._send_json(200, {"ok": moved, "display": 0, "package": package})
                return
            if route.path == "/monitor/start":
                ok = bridge.vdstream_start(int(query.get("width", 1280)),
                                           int(query.get("height", 720)),
                                           int(query.get("display", -1)))
                self._send_json(200, {"ok": ok, "status": bridge.vdstream_status()})
                return
            if route.path == "/monitor/stop":
                # 停流的同时把游戏搬回主屏（模拟器窗口只渲染 display 0）。
                try:
                    bridge.display_move_task(bridge.target_package(), 0)
                except Exception:  # noqa: BLE001
                    pass
                _stopped = bridge.vdstream_stop()
                self._send_json(200, {"ok": _stopped})
                return
            if route.path == "/ai/config":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                except OSError:
                    raw = b"{}"
                self._send_json(200, _ai_config_apply(raw))
                return
            if route.path == "/assets/refresh":
                from . import asset_monitor
                cfg = query.get("config", "oas1")
                force = query.get("force", "1") not in ("0", "false", "False")
                self._send_json(200, asset_monitor.refresh_async(_workspace(), cfg, force=force))
                return
            if route.path == "/ota/config":
                from . import ota_updater
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    patch = json.loads(raw.decode("utf-8", "replace")) if raw else {}
                except Exception:  # noqa: BLE001
                    patch = {}
                self._send_json(200, ota_updater.save_config(_workspace(), patch if isinstance(patch, dict) else {}))
                return
            if route.path == "/ota/check":
                from . import ota_updater
                self._send_json(200, ota_updater.check_update(_workspace()))
                return
            if route.path == "/ota/apply":
                from . import ota_updater
                self._send_json(200, ota_updater.apply_update(_workspace()))
                return
            if route.path == "/ota/rollback":
                from . import ota_updater
                self._send_json(200, ota_updater.rollback_update(_workspace()))
                return
            if route.path == "/ota/download_apk":
                from . import ota_updater
                self._send_json(200, ota_updater.download_apk_async(_workspace()))
                return
            if route.path == "/guard/config":
                from . import device_guard
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    patch = json.loads(raw.decode("utf-8", "replace")) if raw else {}
                except Exception:  # noqa: BLE001
                    patch = {}
                self._send_json(200, device_guard.save_config(_workspace(), patch if isinstance(patch, dict) else {}))
                return
            if route.path == "/pause":
                accepted = bridge.set_paused(True)
                self._send_json(200, {"ok": accepted, "paused": bridge.paused()})
                return
            if route.path == "/resume":
                accepted = bridge.set_paused(False)
                self._send_json(200, {"ok": accepted, "paused": bridge.paused()})
                return
            if route.path in ("/tap", "/swipe") and blocked:
                self._send_json(200, blocked)
                return
            if route.path == "/target":
                package = query.get("package", "").strip()
                if not package:
                    self._send_json(400, {"ok": False, "error": "package required"})
                    return
                bridge.set_target_package(package)
                self._send_json(200, {"ok": True, "target": package})
                return
            if route.path == "/scheduler/start":
                from .scheduler import start as scheduler_start
                tasks = query.get("tasks")
                try:
                    interval = int(query.get("interval", 1800))
                except ValueError:
                    interval = 1800
                config_name = query.get("config", "oas1")
                result = scheduler_start(tasks, interval, config_name)
                self._send_json(200, result)
                return
            if route.path == "/scheduler/stop":
                from .scheduler import stop as scheduler_stop
                self._send_json(200, scheduler_stop())
                return
            if route.path == "/scheduler/pin":
                from .scheduler import pin_task
                from .task_runner import _valid_config
                config_name = query.get("config", "oas1")
                task_name = query.get("task", "").strip()
                if not _valid_config(config_name):
                    self._send_json(400, {"ok": False, "error": "invalid config"})
                    return
                if not task_name:
                    self._send_json(400, {"ok": False, "error": "task required"})
                    return
                self._send_json(200, pin_task(config_name, task_name))
                return
            if route.path == "/task/stop":
                from .task_runner import stop_task
                self._send_json(200, stop_task(run_id=query.get("run_id") or None))
                return
            if route.path == "/task/run":
                from .task_runner import run_task
                name = query.get("name", "")
                config_name = query.get("config", "oas1")
                if not name:
                    self._send_json(400, {"ok": False, "error": "name required"})
                    return
                do_login = query.get("login", "1") not in ("0", "false", "False")
                result = run_task(name, config_name, login=do_login)
                self._send_json(200, result)
                return
            if route.path == "/vd/create":
                width = int(query.get("width", 1280))
                height = int(query.get("height", 720))
                display = bridge.display_create(width, height)
                if display >= 0:
                    VIRTUAL_DISPLAY_ID = display
                self._send_json(200, {"ok": display >= 0, "display": display})
                return
            if route.path == "/vd/host":
                action = query.get("action", "status")
                try:
                    if action == "start":
                        display = bridge.vdhost_start(int(query.get("width", 1280)),
                                                      int(query.get("height", 720)))
                        payload = {"ok": display >= 0, "display": display,
                                   "status": bridge.vdhost_status()}
                    elif action == "stop":
                        payload = {"ok": bridge.vdhost_stop()}
                    else:
                        payload = {"ok": bridge.vdhost_ready(),
                                   "display": bridge.vdhost_display(),
                                   "status": bridge.vdhost_status()}
                except Exception as error:  # noqa: BLE001
                    payload = {"ok": False, "error": repr(error)}
                self._send_json(200, payload)
                return
            if route.path == "/vd/launch":
                display = int(query.get("display", VIRTUAL_DISPLAY_ID))
                package = query.get("package", "")
                ok = bridge.display_launch(package, display)
                self._send_json(200, {"ok": ok, "display": display, "package": package})
                return
            if route.path == "/vd/ensure":
                package = query.get("package", DEFAULT_TARGET_PACKAGE)
                width = int(query.get("width", 1280))
                height = int(query.get("height", 720))
                display = bridge.display_ensure_app(package, width, height)
                if display >= 0:
                    VIRTUAL_DISPLAY_ID = display
                self._send_json(200, {"ok": display >= 0, "display": display,
                                      "package": package})
                return
            if route.path == "/vd/move":
                display = int(query.get("display", VIRTUAL_DISPLAY_ID))
                package = query.get("package", DEFAULT_TARGET_PACKAGE)
                ok = bridge.display_move_task(package, display)
                self._send_json(200, {"ok": ok, "display": display, "package": package})
                return
            if route.path == "/vd/tap":
                raw_display = query.get("display")
                if raw_display is not None and raw_display != "":
                    display = int(raw_display)
                elif bridge.vdstream_running() and bridge.vdstream_display() >= 0:
                    display = bridge.vdstream_display()
                else:
                    display = _ensure_display()
                ok = bridge.display_tap(display, int(query["x"]), int(query["y"]))
                self._send_json(200, {"ok": ok, "display": display})
                return
            if route.path == "/vd/swipe":
                raw_display = query.get("display")
                if raw_display is not None and raw_display != "":
                    display = int(raw_display)
                elif bridge.vdstream_running() and bridge.vdstream_display() >= 0:
                    display = bridge.vdstream_display()
                else:
                    display = _ensure_display()
                duration = max(80, min(int(query.get("duration", 260)), 1500))
                ok = bridge.display_swipe(display, int(query["x1"]), int(query["y1"]),
                                          int(query["x2"]), int(query["y2"]), duration)
                self._send_json(200, {"ok": ok, "display": display})
                return
            if route.path == "/vd/keyevent":
                raw_display = query.get("display")
                if raw_display is not None and raw_display != "":
                    display = int(raw_display)
                elif bridge.vdstream_running() and bridge.vdstream_display() >= 0:
                    display = bridge.vdstream_display()
                else:
                    display = _ensure_display()
                code = int(query.get("code", 4))
                ok = bridge.display_keyevent(display, code)
                self._send_json(200, {"ok": ok, "display": display, "code": code})
                return
            if route.path == "/vd/release":
                ok = bridge.display_release()
                self._send_json(200, {"ok": ok})
                return
            if route.path == "/shizuku/request":
                ok = bridge.shizuku_request_permission()
                self._send_json(200, {"ok": ok, "status": bridge.shizuku_status()})
                return
            if route.path == "/tap":
                ok = bridge.tap(int(query["x"]), int(query["y"]))
                self._send_json(200, {"ok": ok})
                return
            if route.path == "/swipe":
                ok = bridge.swipe(int(query["x1"]), int(query["y1"]),
                                  int(query["x2"]), int(query["y2"]))
                self._send_json(200, {"ok": ok})
                return
        except KeyError as error:
            self._send_json(400, {"ok": False, "error": f"missing param: {error}"})
            return
        except Exception as error:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(error)})
            return
        self._send_json(404, {"ok": False, "error": "not found"})


_SERVER_LOCK = threading.RLock()
_HTTPD = None
_SERVER_THREAD = None


# ---------------------------------------------------------------- aiohttp 实现


def _serve_aiohttp(port: int) -> None:
    import asyncio

    from aiohttp import web

    async def health(request: web.Request) -> web.Response:
        payload = _health_payload()
        payload["server"] = "aiohttp"
        return web.json_response(payload)

    async def screenshot(request: web.Request) -> web.Response:
        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(None, bridge.capture_png)
        except Exception as error:  # noqa: BLE001
            return web.json_response({"ok": False, "error": str(error)}, status=503)
        return web.Response(body=data, content_type="image/png")

    async def tap(request: web.Request) -> web.Response:
        x = int(request.query["x"])
        y = int(request.query["y"])
        loop = asyncio.get_running_loop()
        return web.json_response({"ok": await loop.run_in_executor(None, bridge.tap, x, y)})

    async def swipe(request: web.Request) -> web.Response:
        args = (int(request.query["x1"]), int(request.query["y1"]),
                int(request.query["x2"]), int(request.query["y2"]))
        loop = asyncio.get_running_loop()
        return web.json_response(
            {"ok": await loop.run_in_executor(None, bridge.swipe, *args)})

    async def state(request: web.Request) -> web.Response:
        return web.json_response(_state_payload())

    async def target(request: web.Request) -> web.Response:
        package = request.query.get("package", "").strip()
        if not package:
            return web.json_response({"ok": False, "error": "package required"}, status=400)
        bridge.set_target_package(package)
        return web.json_response({"ok": True, "target": package})

    async def pause(request: web.Request) -> web.Response:
        bridge.set_paused(True)
        return web.json_response({"ok": True, "paused": True})

    async def resume(request: web.Request) -> web.Response:
        bridge.set_paused(False)
        return web.json_response({"ok": True, "paused": False})

    async def guarded_tap(request: web.Request) -> web.Response:
        blocked = _injection_blocked()
        if blocked:
            return web.json_response(blocked)
        return await tap(request)

    async def guarded_swipe(request: web.Request) -> web.Response:
        blocked = _injection_blocked()
        if blocked:
            return web.json_response(blocked)
        return await swipe(request)

    app = web.Application()
    app.add_routes([web.get("/health", health), web.get("/state", state),
                    web.get("/screenshot.png", screenshot),
                    web.post("/tap", guarded_tap), web.post("/swipe", guarded_swipe),
                    web.post("/pause", pause), web.post("/resume", resume),
                    web.post("/target", target)])

    async def run() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        logger.info("aiohttp server listening on 127.0.0.1:%s", port)
        while True:
            await asyncio.sleep(3600)

    asyncio.run(run())


def start(port: int = DEFAULT_PORT) -> str:
    """Bind before reporting success; repeated Activity creation reuses the service."""
    global _HTTPD, _SERVER_THREAD
    logging.basicConfig(level=logging.INFO)
    try:
        from . import ota_updater
        ota_updater.activate_overlay(_workspace(), reload_modules=False)
    except Exception as error:  # noqa: BLE001
        logger.warning("ota overlay activation skipped: %r", error)
    try:
        from . import device_guard
        device_guard.start_guard_loop(_workspace())
    except Exception as error:  # noqa: BLE001
        logger.warning("device guard startup skipped: %r", error)
    with _SERVER_LOCK:
        if _HTTPD is not None and _SERVER_THREAD is not None and _SERVER_THREAD.is_alive():
            return f"already running on 127.0.0.1:{_HTTPD.server_address[1]}"
        httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), _Handler)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, name="oas-mobile-http", daemon=True)
        _HTTPD, _SERVER_THREAD = httpd, thread
        thread.start()
        return f"started(stdlib) on 127.0.0.1:{httpd.server_address[1]}"


def stop() -> str:
    global _HTTPD, _SERVER_THREAD
    from .scheduler import stop as stop_scheduler
    from .task_runner import stop_task
    stop_scheduler(wait=True)
    stopped = stop_task(wait=True)
    try:
        if hasattr(bridge, "display_move_task") and hasattr(bridge, "target_package"):
            bridge.display_move_task(bridge.target_package(), 0)
    except Exception:  # noqa: BLE001
        pass
    bridge.vdstream_stop()
    with _SERVER_LOCK:
        httpd, thread = _HTTPD, _SERVER_THREAD
        _HTTPD, _SERVER_THREAD = None, None
    if httpd is not None:
        httpd.shutdown()
        httpd.server_close()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=2)
    return "stopped" if stopped["stopped"] else "stopping task"


def ping() -> str:
    """最小自检：Kotlin 侧可用它确认「Python 已跑起来」。"""
    return f"pong v{__version__} bridge={'ok' if bridge.available() else 'unavailable'}"
