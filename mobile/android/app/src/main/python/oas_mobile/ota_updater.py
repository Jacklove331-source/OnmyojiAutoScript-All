"""oas_mobile.ota_updater — OAS Mobile 数据包 OTA 更新器（纯 Python 3.10 标准库）。

特性：
- 支持 GitHub / GitCode 公开仓库免令牌直连及私有仓库令牌认证，token 永不进入日志或返回值。
- 覆盖层（overlay）热加载：``<workspace>/ota_overlay`` 置于 ``sys.path[0]``。
- 原子式切换：``ota_overlay`` <-> ``ota_overlay_prev``，支持一键回滚。
- 严格 ZIP 校验（SHA256 + zip-slip 防护 + 顶层目录白名单）。

该模块同时适用于 Chaquopy(Android) 与宿主机 pytest 环境。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

__all__ = [
    "DEFAULT_REPO",
    "DEFAULT_BRANCH",
    "DEFAULT_BASE_URL",
    "MANIFEST_PATH",
    "DEFAULT_PATCH_FILE",
    "OTAError",
    "OTAFetchError",
    "OTAUpdateError",
    "load_config",
    "save_config",
    "get_status",
    "activate_overlay",
    "check_update",
    "apply_update",
    "rollback_update",
    "get_apk_download_status",
    "download_apk_async",
]

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

DEFAULT_REPO = "Jacklove331-source/OnmyojiAutoScript-All"
DEFAULT_BRANCH = "main"
DEFAULT_BASE_URL = "https://github.com"
MANIFEST_PATH = "datapack/manifest.json"
DEFAULT_PATCH_FILE = "datapack/oas-patch.zip"

USER_AGENT = "OASX-Mobile/1.0"

CONFIG_FILENAME = "ota_config.json"
OVERLAY_DIRNAME = "ota_overlay"
PREV_DIRNAME = "ota_overlay_prev"
APPLIED_MANIFEST_NAME = "applied_manifest.json"

#: 覆盖层中允许出现的顶层目录（zip-slip / 越权写入防护白名单）
ALLOWED_TOP_DIRS = ("tasks", "module", "oas_mobile")

#: reload_modules=True 时需要清理缓存的顶层包前缀
_RELOAD_PREFIXES = ("tasks", "module")

#: 下载体积上限（字节），防御异常服务端
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
#: APK 下载体积上限（字节）
MAX_APK_BYTES = 350 * 1024 * 1024
#: 解压后总体积上限（字节），防御 zip 炸弹
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024

#: 全局可重入锁：所有公开函数均在同把锁内执行
_LOCK = threading.RLock()

#: 每个 workspace 的内存态（last_check_at / remote_manifest）
_STATE: dict = {}

#: APK 后台下载状态
_APK_STATE: dict = {
    "downloading": False,
    "percent": 0,
    "downloaded_bytes": 0,
    "total_bytes": 0,
    "ready": False,
    "apk_path": "",
    "version": "",
    "error": "",
}


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #


class OTAError(Exception):
    """OTA 基础异常。"""


class OTAFetchError(OTAError):
    """远程下载相关异常（消息已脱敏）。"""

    def __init__(self, message: str, status: int | None = None, allow_fallback: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.allow_fallback = bool(allow_fallback)


class OTAUpdateError(OTAError):
    """本地更新流程异常。"""


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #


def _utc_now() -> str:
    """返回 ISO-8601 UTC 时间戳（秒级）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mask_token(value: object, token: str = "") -> str:
    """从任意错误串中抹除 token，并做长度裁剪。"""
    text = "" if value is None else str(value)
    secret = "" if token is None else str(token)
    if secret:
        try:
            text = text.replace(secret, "****")
        except Exception:  # pragma: no cover - 极端防御
            pass
        try:
            quoted = urllib.parse.quote(secret, safe="")
        except Exception:  # pragma: no cover
            quoted = ""
        if quoted and quoted != secret:
            try:
                text = text.replace(quoted, "****")
            except Exception:  # pragma: no cover
                pass
    if len(text) > 600:
        text = text[:600] + "…"
    return text


def _json_safe(value: object):
    """尽量把对象规整成可 JSON 序列化的结构。"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return None


def _workspace_path(workspace: str) -> Path:
    raw = str(workspace or "").strip()
    if not raw:
        raw = os.getcwd()
    try:
        return Path(raw).expanduser()
    except Exception:
        return Path(os.path.abspath(raw or "."))


def _ensure_dir(path) -> Path:
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return target


def _config_path(workspace: str) -> Path:
    return _workspace_path(workspace) / CONFIG_FILENAME


def _overlay_dir(workspace: str) -> Path:
    return _workspace_path(workspace) / OVERLAY_DIRNAME


def _prev_dir(workspace: str) -> Path:
    return _workspace_path(workspace) / PREV_DIRNAME


def _state_key(workspace: str) -> str:
    path = _workspace_path(workspace)
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _resolve(path) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(os.path.abspath(str(path)))


def _is_within(child, parent) -> bool:
    """判断 child 是否位于 parent 内部（含相等）。"""
    try:
        child_s = os.path.normcase(str(child))
        parent_s = os.path.normcase(str(parent))
    except Exception:
        return False
    if child_s == parent_s:
        return True
    return child_s.startswith(parent_s + os.sep)


def _validate_repo(repo: object) -> str | None:
    """校验 ``owner/name`` 形式；合法返回 None，否则返回错误消息。"""
    if not isinstance(repo, str):
        return "仓库格式无效：需要 owner/name"
    text = repo.strip()
    if not text:
        return "仓库不能为空"
    if ".." in text:
        return "仓库格式无效：需要 owner/name"
    parts = text.split("/")
    if len(parts) != 2:
        return "仓库格式无效：需要 owner/name"
    for part in parts:
        if not part or part != part.strip():
            return "仓库格式无效：需要 owner/name"
        if ":" in part or "\\" in part:
            return "仓库格式无效：需要 owner/name"
        if any(ch.isspace() for ch in part):
            return "仓库格式无效：需要 owner/name"
    return None


def _write_private_text(path: Path, text: str) -> None:
    """以 0o600 权限原子写入文本（best-effort）。"""
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp" + str(os.getpid()))
    data = text.encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                raise OSError("写入配置失败")
            offset += written
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        os.chmod(str(tmp), 0o600)
    except OSError:
        pass
    os.replace(str(tmp), str(target))
    try:
        os.chmod(str(target), 0o600)
    except OSError:
        pass


def _read_applied_manifest(overlay) -> dict:
    """读取 ``<overlay>/applied_manifest.json``；失败返回空 dict。"""
    path = Path(overlay) / APPLIED_MANIFEST_NAME
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _installed_version(workspace: str) -> str:
    applied = _read_applied_manifest(_overlay_dir(workspace))
    return str(applied.get("version") or "")


def _record_check(workspace: str, manifest: object) -> None:
    """记录最近一次检查结果到内存（非持久化，避免写入 token 相关日志）。"""
    entry = _STATE.setdefault(_state_key(workspace), {})
    entry["last_check_at"] = _utc_now()
    entry["remote_manifest"] = _json_safe(manifest) if isinstance(manifest, dict) else None


# --------------------------------------------------------------------------- #
# GitCode 私有文件下载
# --------------------------------------------------------------------------- #


def _is_gitcode_base(base_url: str) -> bool:
    """base_url 是否为标准 GitCode 站点（决定是否优先走 API v5）。"""
    try:
        parts = urllib.parse.urlsplit(str(base_url or ""))
    except Exception:
        return False
    host = (parts.netloc or "").lower()
    return "gitcode.com" in host


def _is_github_base(base_url: str) -> bool:
    """base_url 是否为标准 GitHub 站点（优先走 raw.githubusercontent.com）。"""
    try:
        parts = urllib.parse.urlsplit(str(base_url or ""))
    except Exception:
        return False
    host = (parts.netloc or "").lower()
    return host in ("github.com", "www.github.com", "raw.githubusercontent.com")


class _SafeCrossHostRedirect(urllib.request.HTTPRedirectHandler):
    """跨域名重定向时自动剥离认证头，避免令牌泄露到第三方对象存储/CDN。"""

    def redirect_request(self, req_obj, fp, code, msg, hdrs, newurl):
        new_req = super().redirect_request(req_obj, fp, code, msg, hdrs, newurl)
        if new_req is not None:
            old_host = (urllib.parse.urlsplit(req_obj.full_url).netloc or "").lower()
            new_host = (urllib.parse.urlsplit(newurl).netloc or "").lower()
            if old_host != new_host:
                for hk in list(new_req.headers.keys()):
                    if hk.lower() in ("authorization", "private-token"):
                        new_req.remove_header(hk)
        return new_req


def _http_get(url: str, headers: dict, timeout: int = 30) -> bytes:
    """执行一次 GET 请求，异常统一转换为 :class:`OTAFetchError`（消息不含 token）。"""
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_SafeCrossHostRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        raise OTAFetchError(
            "HTTP %d 下载失败：%s" % (code, url),
            status=code,
            allow_fallback=code in (401, 403, 404),
        ) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise OTAFetchError("网络错误：%s" % (reason,)) from None
    except (OSError, ValueError) as exc:
        raise OTAFetchError("请求失败：%s" % (exc,)) from None
    if not data:
        raise OTAFetchError("下载内容为空")
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise OTAFetchError("下载内容过大")
    return bytes(data)


def _download_bytes(cfg: dict, file_path: str, timeout: int = 30) -> bytes:
    """从 GitHub 公开库、GitCode 仓库或直链 URL 下载文件原始字节。

    - 若 ``file_path`` 本身为 ``http(s)://`` 直链（例如 GitHub Release 附件链接），直接下载；
    - 若 ``base_url`` 为 GitHub，优先走 ``https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}``，
      失败时回退到 ``https://github.com/{owner}/{repo}/raw/{branch}/{path}``；
    - 若 ``base_url`` 为 GitCode，优先走 API v5，失败回退 raw 直链；
    - 公开库无需配置 token 即可直接访问。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    repo = str(cfg.get("repo") or DEFAULT_REPO).strip() or DEFAULT_REPO
    branch = str(cfg.get("branch") or DEFAULT_BRANCH).strip() or DEFAULT_BRANCH
    base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL
    token = str(cfg.get("token") or "")

    raw_target = str(file_path or "").strip()
    if raw_target.startswith("http://") or raw_target.startswith("https://"):
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        url_host = (urllib.parse.urlsplit(raw_target).netloc or "").lower()
        base_host = (urllib.parse.urlsplit(base_url).netloc or "").lower()
        if token and (url_host == base_host or "gitcode.com" in url_host):
            if _is_gitcode_base(base_url) or "gitcode.com" in url_host:
                headers["PRIVATE-TOKEN"] = token
            headers["Authorization"] = "Bearer " + token
        try:
            return _http_get(raw_target, headers, timeout=max(timeout, 60))
        except OTAFetchError as exc:
            raise OTAFetchError(_mask_token(str(exc), token)) from None

    rel = raw_target.replace("\\", "/").strip()
    while rel.startswith("/"):
        rel = rel[1:]
    if not rel:
        raise OTAFetchError("文件路径为空")
    if "/" not in repo:
        raise OTAFetchError("仓库配置无效：需要 owner/repo 形式")
    owner, name = repo.split("/", 1)
    if not owner or not name:
        raise OTAFetchError("仓库配置无效：需要 owner/repo 形式")

    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if token:
        if _is_gitcode_base(base_url):
            headers["PRIVATE-TOKEN"] = token
        headers["Authorization"] = "Bearer " + token

    quoted_path = urllib.parse.quote(rel, safe="/")
    quoted_branch = urllib.parse.quote(branch, safe="")
    errors: list[str] = []

    if _is_github_base(base_url):
        gh_urls = [
            "https://raw.githubusercontent.com/%s/%s/%s/%s" % (
                owner, name, quoted_branch, quoted_path
            ),
            "https://github.com/%s/%s/raw/%s/%s" % (
                owner, name, quoted_branch, quoted_path
            ),
        ]
        for u in gh_urls:
            try:
                return _http_get(u, headers, timeout)
            except OTAFetchError as exc:
                errors.append(str(exc))
        raise OTAFetchError(_mask_token("；".join(errors) or "下载失败", token))

    if _is_gitcode_base(base_url):
        gc_urls = [
            "https://api.gitcode.com/api/v5/repos/%s/%s/raw/%s?ref=%s" % (
                owner,
                name,
                quoted_path,
                quoted_branch,
            ),
            "%s/api/v5/repos/%s/%s/raw/%s?ref=%s" % (
                base_url,
                owner,
                name,
                quoted_path,
                quoted_branch,
            ),
        ]
        seen = set()
        for api_url in gc_urls:
            if api_url in seen:
                continue
            seen.add(api_url)
            try:
                return _http_get(api_url, headers, timeout)
            except OTAFetchError as exc:
                errors.append(str(exc))
                if not exc.allow_fallback:
                    raise OTAFetchError(_mask_token("；".join(errors), token)) from None

    raw_url = "%s/%s/%s/raw/%s/%s" % (base_url, owner, name, quoted_branch, quoted_path)
    try:
        return _http_get(raw_url, headers, timeout)
    except OTAFetchError as exc:
        errors.append(str(exc))

    raise OTAFetchError(_mask_token("；".join(errors) or "下载失败", token))


# --------------------------------------------------------------------------- #
# 配置读写
# --------------------------------------------------------------------------- #


def load_config(workspace: str) -> dict:
    """读取并合并配置（内部使用；返回值可能包含明文 token，切勿直接外泄）。"""
    with _LOCK:
        cfg = {
            "repo": DEFAULT_REPO,
            "branch": DEFAULT_BRANCH,
            "base_url": DEFAULT_BASE_URL,
            "token": "",
        }
        data: object = {}
        try:
            path = _config_path(workspace)
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}

        if isinstance(data, dict):
            repo = data.get("repo")
            if isinstance(repo, str):
                repo = repo.strip()
                if repo and _validate_repo(repo) is None:
                    cfg["repo"] = repo

            branch = data.get("branch")
            if isinstance(branch, str) and branch.strip():
                cfg["branch"] = branch.strip()

            base_url = data.get("base_url")
            if isinstance(base_url, str) and base_url.strip():
                cfg["base_url"] = base_url.strip().rstrip("/")

            token = data.get("token")
            if isinstance(token, str) and token.strip():
                cfg["token"] = token.strip()

        return cfg


def save_config(workspace: str, patch: dict) -> dict:
    """更新配置并返回脱敏后的状态。"""
    with _LOCK:
        if not isinstance(patch, dict):
            return {"ok": False, "error": "配置无效：需要字典"}

        cfg = load_config(workspace)
        changed = False

        if patch.get("repo") is not None:
            repo = patch.get("repo")
            if not isinstance(repo, str):
                return {"ok": False, "error": "仓库格式无效：需要 owner/name"}
            repo = repo.strip()
            error = _validate_repo(repo)
            if error:
                return {"ok": False, "error": error}
            cfg["repo"] = repo
            changed = True

        if patch.get("branch") is not None:
            branch = patch.get("branch")
            if not isinstance(branch, str) or not branch.strip():
                return {"ok": False, "error": "分支不能为空"}
            branch = branch.strip()
            if branch.startswith("/") or ".." in branch or any(ch.isspace() for ch in branch):
                return {"ok": False, "error": "分支格式无效"}
            cfg["branch"] = branch
            changed = True

        if patch.get("base_url") is not None:
            base_url = patch.get("base_url")
            if not isinstance(base_url, str):
                return {"ok": False, "error": "服务地址无效"}
            base_url = base_url.strip().rstrip("/")
            if not (base_url.startswith("http://") or base_url.startswith("https://")):
                return {"ok": False, "error": "服务地址必须以 http:// 或 https:// 开头"}
            cfg["base_url"] = base_url
            changed = True

        clear_token = patch.get("clear_token")
        if clear_token is True or clear_token in ("1", "true", "True", 1):
            cfg["token"] = ""
            changed = True
        else:
            token = patch.get("token")
            if isinstance(token, str) and token.strip():
                cfg["token"] = token.strip()
                changed = True

        if changed:
            try:
                _write_private_text(
                    _config_path(workspace),
                    json.dumps(
                        {
                            "repo": cfg.get("repo") or DEFAULT_REPO,
                            "branch": cfg.get("branch") or DEFAULT_BRANCH,
                            "base_url": cfg.get("base_url") or DEFAULT_BASE_URL,
                            "token": cfg.get("token") or "",
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                )
            except OSError as exc:
                return {"ok": False, "error": _mask_token("保存配置失败：%s" % (exc,), cfg.get("token") or "")}

        status = get_status(workspace)
        return {"ok": True, **status}


# --------------------------------------------------------------------------- #
# 覆盖层激活 / 模块热加载
# --------------------------------------------------------------------------- #


def _purge_task_modules() -> list:
    """清理 tasks / module 及其子模块缓存，使下次 import 立即生效。"""
    removed: list[str] = []
    for name in list(sys.modules.keys()):
        if not isinstance(name, str):
            continue
        if name.startswith("oas_mobile"):
            continue
        matched = False
        for prefix in _RELOAD_PREFIXES:
            if name == prefix or name.startswith(prefix + "."):
                matched = True
                break
        if not matched:
            continue
        try:
            del sys.modules[name]
        except KeyError:
            pass
        else:
            removed.append(name)
    # Pydantic v1 keeps a global validator registry (_FUNCS) keyed by qualname;
    # clearing it prevents duplicate-validator ConfigError when tasks.* are re-imported
    # after a live OTA hot-update in the same Python process.
    for mod_name in (
        "_pydantic_v1.class_validators",
        "pydantic.v1.class_validators",
        "pydantic.class_validators",
    ):
        cv = sys.modules.get(mod_name)
        funcs = getattr(cv, "_FUNCS", None)
        if isinstance(funcs, set):
            funcs.clear()
    # 热更新后同时刷新词库、登录状态机与资产采集模块，免重启立刻生效
    for mod_key in ("oas_mobile.i18n_zh", "oas_mobile.login", "oas_mobile.asset_monitor"):
        sys.modules.pop(mod_key, None)
    srv = sys.modules.get("oas_mobile.server")
    i18n_cache = getattr(srv, "_I18N_CACHE", None)
    if isinstance(i18n_cache, dict):
        i18n_cache.clear()
    return removed


def activate_overlay(workspace: str, reload_modules: bool = False) -> dict:
    """把覆盖层目录挂载到 ``sys.path[0]``（或在不活跃时移除）。"""
    with _LOCK:
        overlay = _overlay_dir(workspace)
        applied_path = overlay / APPLIED_MANIFEST_NAME
        active = overlay.is_dir() and applied_path.is_file()
        applied = _read_applied_manifest(overlay) if active else {}

        entry = str(overlay)
        try:
            norm = os.path.normpath(entry)
            sys.path[:] = [
                item for item in sys.path if os.path.normpath(str(item)) != norm
            ]
        except Exception:  # pragma: no cover - sys.path 异常对象防御
            pass

        if active:
            sys.path.insert(0, entry)

        # 同时把 <overlay>/oas_mobile 挂入已导入的 oas_mobile.__path__ 首位，
        # 让 oas_mobile.login / oas_mobile.i18n_zh 等子模块也能真正走热更新覆盖层。
        pkg = sys.modules.get("oas_mobile")
        pkg_path = getattr(pkg, "__path__", None)
        if isinstance(pkg_path, list):
            sub_entry = str(overlay / "oas_mobile")
            sub_norm = os.path.normpath(sub_entry)
            pkg_path[:] = [p for p in pkg_path if os.path.normpath(str(p)) != sub_norm]
            if active and (overlay / "oas_mobile").is_dir():
                pkg_path.insert(0, sub_entry)

        if reload_modules:
            _purge_task_modules()

        return {
            "ok": True,
            "active": bool(active),
            "version": str(applied.get("version") or "") if active else "",
        }


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #


def get_status(workspace: str) -> dict:
    """返回 JSON 安全的状态字典（永不包含明文 token）。"""
    with _LOCK:
        try:
            cfg = load_config(workspace)
        except Exception:
            cfg = {
                "repo": DEFAULT_REPO,
                "branch": DEFAULT_BRANCH,
                "base_url": DEFAULT_BASE_URL,
                "token": "",
            }

        token = str(cfg.get("token") or "")
        overlay = _overlay_dir(workspace)
        applied_path = overlay / APPLIED_MANIFEST_NAME
        active = overlay.is_dir() and applied_path.is_file()
        applied = _read_applied_manifest(overlay) if active else {}

        state = _STATE.get(_state_key(workspace)) or {}
        remote = state.get("remote_manifest")
        remote_manifest = _json_safe(remote) if isinstance(remote, dict) else None
        apk_info = _build_apk_update_summary(remote_manifest)

        return {
            "ok": True,
            "repo": cfg.get("repo") or DEFAULT_REPO,
            "branch": cfg.get("branch") or DEFAULT_BRANCH,
            "base_url": cfg.get("base_url") or DEFAULT_BASE_URL,
            "has_token": bool(token),
            "token_hint": ("****" + token[-4:]) if len(token) >= 4 else "",
            "active": bool(active),
            "installed_version": str(applied.get("version") or ""),
            "installed_at": str(applied.get("installed_at") or ""),
            "installed_note": str(applied.get("note") or ""),
            "can_rollback": _prev_dir(workspace).is_dir(),
            "last_check_at": str(state.get("last_check_at") or ""),
            "remote_manifest": remote_manifest,
            "current_build": _current_build_number(),
            "apk_update": apk_info,
            "apk_download": dict(_APK_STATE),
        }


# --------------------------------------------------------------------------- #
# 检查更新
# --------------------------------------------------------------------------- #


def check_update(workspace: str) -> dict:
    """下载远端 manifest 并与本地已安装版本比对。"""
    with _LOCK:
        cfg = load_config(workspace)
        token = str(cfg.get("token") or "")

        try:
            payload = _download_bytes(cfg, MANIFEST_PATH)
        except OTAError as exc:
            _record_check(workspace, None)
            return {"ok": False, "error": _mask_token(str(exc), token)}
        except Exception as exc:  # pragma: no cover - 兜底
            _record_check(workspace, None)
            return {"ok": False, "error": _mask_token("检查更新失败：%s" % (exc,), token)}

        try:
            manifest = json.loads(payload.decode("utf-8-sig"))
        except Exception:
            _record_check(workspace, None)
            return {"ok": False, "error": "清单解析失败：不是合法的 JSON"}

        if not isinstance(manifest, dict):
            _record_check(workspace, None)
            return {"ok": False, "error": "清单格式无效：应为 JSON 对象"}

        version = manifest.get("version")
        if not isinstance(version, str) or not version.strip():
            return {"ok": False, "error": "清单缺少 version 字段"}

        sha256 = manifest.get("sha256")
        if not isinstance(sha256, str) or not sha256.strip():
            return {"ok": False, "error": "清单缺少 sha256 字段"}

        file_path = manifest.get("file")
        if file_path is not None and (not isinstance(file_path, str) or not file_path.strip()):
            return {"ok": False, "error": "清单 file 字段无效"}

        _record_check(workspace, manifest)

        installed_version = _installed_version(workspace)
        return {
            "ok": True,
            "has_update": version.strip() != installed_version,
            "installed_version": installed_version,
            "remote": manifest,
        }


# --------------------------------------------------------------------------- #
# ZIP 解压（严格防护）
# --------------------------------------------------------------------------- #


def _safe_member(name: object):
    """把 zip 内文件名规整为安全的相对路径；非法返回 None。"""
    if not isinstance(name, str) or not name:
        return None
    raw = name.replace("\\", "/")
    if raw.endswith("/"):
        return None
    if raw.startswith("/"):
        return None
    if "\x00" in raw:
        return None

    parts: list[str] = []
    for segment in raw.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            return None
        if ":" in segment:
            return None
        if segment != segment.strip():
            return None
        parts.append(segment)

    if not parts:
        return None
    if parts[0] not in ALLOWED_TOP_DIRS:
        return None
    return Path(*parts)


def _extract_zip(zip_path, staging) -> int:
    """把 zip 安全解压到 staging，返回成功写出的文件数量。"""
    base = Path(staging)
    base_resolved = _resolve(base)

    try:
        archive = zipfile.ZipFile(str(zip_path))
    except zipfile.BadZipFile:
        raise OTAUpdateError("更新包损坏：不是有效的 ZIP 文件") from None
    except OSError as exc:
        raise OTAUpdateError("无法读取更新包：%s" % (exc,)) from None

    count = 0
    total = 0
    with archive:
        for info in archive.infolist():
            try:
                if info.is_dir():
                    continue
            except Exception:
                continue
            rel = _safe_member(info.filename)
            if rel is None:
                continue

            target = base.joinpath(*rel.parts)
            if not _is_within(_resolve(target), base_resolved):
                continue

            try:
                total += int(info.file_size or 0)
            except Exception:
                total += 0
            if total > MAX_UNCOMPRESSED_BYTES:
                raise OTAUpdateError("更新包解压后体积过大")

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 64 * 1024)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise OTAUpdateError("写入文件失败：%s" % (exc,)) from None
            count += 1

    return count


def _swap_overlay(workspace: str, staging) -> None:
    """原子式切换：overlay -> prev，staging -> overlay（失败时尽量回退）。"""
    root = _workspace_path(workspace)
    overlay = root / OVERLAY_DIRNAME
    prev = root / PREV_DIRNAME
    staging_path = Path(staging)

    if prev.exists():
        shutil.rmtree(prev, ignore_errors=True)
        if prev.exists():
            raise OTAUpdateError("无法清理旧备份目录")

    moved_prev = False
    if overlay.exists():
        shutil.move(str(overlay), str(prev))
        moved_prev = True

    try:
        shutil.move(str(staging_path), str(overlay))
    except Exception:
        try:
            if overlay.exists():
                shutil.rmtree(overlay, ignore_errors=True)
            if moved_prev and prev.exists() and not overlay.exists():
                shutil.move(str(prev), str(overlay))
        except Exception:  # pragma: no cover - 二次失败兜底
            pass
        raise


# --------------------------------------------------------------------------- #
# 应用更新
# --------------------------------------------------------------------------- #


def _apply_update_locked(workspace: str, zip_bytes, manifest, cfg: dict) -> dict:
    if manifest is None:
        result = check_update(workspace)
        if not result.get("ok"):
            return result
        manifest = result.get("remote") or {}

    if not isinstance(manifest, dict):
        return {"ok": False, "error": "清单无效"}

    version = str(manifest.get("version") or "").strip()
    expected_sha = str(manifest.get("sha256") or "").strip().lower()
    file_path = str(manifest.get("file") or DEFAULT_PATCH_FILE).strip() or DEFAULT_PATCH_FILE

    if not expected_sha:
        return {"ok": False, "error": "清单缺少 sha256 字段"}

    payload = zip_bytes
    if payload is None:
        payload = _download_bytes(cfg, file_path)
    if isinstance(payload, (bytearray, memoryview)):
        try:
            payload = bytes(payload)
        except Exception:
            return {"ok": False, "error": "更新包数据无效"}
    if not isinstance(payload, bytes):
        return {"ok": False, "error": "更新包数据无效"}
    if not payload:
        return {"ok": False, "error": "更新包为空"}

    digest = hashlib.sha256(payload).hexdigest().lower()
    if digest != expected_sha:
        return {"ok": False, "error": "校验失败：SHA256 不匹配"}

    root = _ensure_dir(_workspace_path(workspace))
    stamp = "%d_%d" % (int(time.time() * 1000), os.getpid())
    staging = root / ("ota_staging_" + stamp)
    tmp_zip = root / ("ota_patch_" + stamp + ".zip")
    file_count = 0
    installed_at = _utc_now()

    try:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        with open(tmp_zip, "wb") as handle:
            handle.write(payload)

        file_count = _extract_zip(tmp_zip, staging)
        if file_count <= 0:
            return {
                "ok": False,
                "error": "压缩包内容无效：未找到允许的目录（tasks/module/oas_mobile）",
            }

        applied = {
            "version": version,
            "installed_at": installed_at,
            "note": str(manifest.get("note") or manifest.get("notes") or ""),
            "sha256": expected_sha,
            "file": file_path,
            "file_count": file_count,
            "source": str(manifest.get("source") or ""),
        }
        (staging / APPLIED_MANIFEST_NAME).write_text(
            json.dumps(applied, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        _swap_overlay(workspace, staging)
    finally:
        try:
            if tmp_zip.exists():
                tmp_zip.unlink()
        except OSError:
            pass
        try:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        except OSError:
            pass

    activate_overlay(workspace, reload_modules=True)

    return {
        "ok": True,
        "installed_version": version,
        "file_count": file_count,
        "status": get_status(workspace),
    }


def apply_update(workspace: str, zip_bytes: bytes | None = None, manifest: dict | None = None) -> dict:
    """下载 / 校验 / 解压 / 切换覆盖层，并立即热加载。"""
    with _LOCK:
        cfg = load_config(workspace)
        token = str(cfg.get("token") or "")
        try:
            return _apply_update_locked(workspace, zip_bytes, manifest, cfg)
        except OTAError as exc:
            return {"ok": False, "error": _mask_token(str(exc), token)}
        except Exception as exc:  # pragma: no cover - 兜底
            return {"ok": False, "error": _mask_token("更新失败：%s" % (exc,), token)}


# --------------------------------------------------------------------------- #
# 回滚
# --------------------------------------------------------------------------- #


def rollback_update(workspace: str) -> dict:
    """回滚到上一个覆盖层；若仅有当前覆盖层则整体移除，恢复 APK 内置版本。"""
    with _LOCK:
        root = _ensure_dir(_workspace_path(workspace))
        overlay = root / OVERLAY_DIRNAME
        prev = root / PREV_DIRNAME

        rolled_back = False
        try:
            if prev.exists():
                if overlay.exists():
                    shutil.rmtree(overlay, ignore_errors=True)
                shutil.move(str(prev), str(overlay))
                rolled_back = True
            elif overlay.exists():
                shutil.rmtree(overlay, ignore_errors=True)
                rolled_back = True
        except OSError as exc:
            token = str(load_config(workspace).get("token") or "")
            return {"ok": False, "error": _mask_token("回滚失败：%s" % (exc,), token)}

        activate_overlay(workspace, reload_modules=True)
        return {
            "ok": True,
            "rolled_back": rolled_back,
            "status": get_status(workspace),
        }


# --------------------------------------------------------------------------- #
# Release APK 自更新
# --------------------------------------------------------------------------- #


def _current_build_number() -> int:
    try:
        from .server import _apk_build
        raw = str(_apk_build() or "")
        if "+" in raw:
            return int(raw.rsplit("+", 1)[1])
    except Exception:
        pass
    return 0


def _build_apk_update_summary(remote_manifest: object) -> dict | None:
    if not isinstance(remote_manifest, dict):
        return None
    apk = remote_manifest.get("apk")
    if not isinstance(apk, dict):
        return None
    download_url = str(apk.get("download_url") or "").strip()
    if not (download_url.startswith("http://") or download_url.startswith("https://")):
        return None
    try:
        remote_build = int(apk.get("build_number") or apk.get("build") or 0)
    except (TypeError, ValueError):
        remote_build = 0
    current_build = _current_build_number()
    version_name = str(apk.get("version_name") or apk.get("version") or "")
    version_label = f"{version_name}+{remote_build}" if version_name and remote_build else (version_name or str(remote_build))
    return {
        "has_update": remote_build > current_build if remote_build > 0 else False,
        "current_build": current_build,
        "build_number": remote_build,
        "version": version_label,
        "file_name": str(apk.get("file_name") or f"oasx-mobile-{version_label}.apk"),
        "size_bytes": int(apk.get("size_bytes") or 0),
        "sha256": str(apk.get("sha256") or "").strip().lower(),
        "download_url": download_url,
        "note": str(apk.get("note") or ""),
    }


def _apk_cache_dir(workspace: str) -> Path:
    try:
        from java import jclass
        raw = str(jclass("com.oas.oasx.DeviceGuardBridge").apkCacheDir() or "").strip()
        if raw:
            p = Path(raw)
            p.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass
    p = _workspace_path(workspace) / "apk_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_apk_download_status(workspace: str) -> dict:
    with _LOCK:
        state = _STATE.get(_state_key(workspace)) or {}
        apk_info = _build_apk_update_summary(state.get("remote_manifest"))
        return {
            "ok": True,
            "apk_update": apk_info,
            **dict(_APK_STATE),
        }


def download_apk_async(workspace: str) -> dict:
    with _LOCK:
        if _APK_STATE.get("downloading"):
            return {"ok": True, "already_running": True, **dict(_APK_STATE)}
        state = _STATE.get(_state_key(workspace)) or {}
        remote = state.get("remote_manifest")

    if not isinstance(remote, dict) or not isinstance(remote.get("apk"), dict):
        checked = check_update(workspace)
        if not checked.get("ok"):
            return {"ok": False, "error": str(checked.get("error") or "无法读取远端清单")}
        remote = checked.get("remote")

    apk_info = _build_apk_update_summary(remote)
    if not apk_info:
        return {"ok": False, "error": "远端清单中暂无新版 APK 发布记录"}

    cfg = load_config(workspace)
    token = str(cfg.get("token") or "")
    base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL)
    url = apk_info["download_url"]
    expected_sha = apk_info["sha256"]
    expected_size = int(apk_info["size_bytes"] or 0)
    safe_name = os.path.basename(apk_info["file_name"]) or "oasx-update.apk"
    if not safe_name.lower().endswith(".apk"):
        safe_name += ".apk"

    with _LOCK:
        _APK_STATE.update({
            "downloading": True,
            "percent": 0,
            "downloaded_bytes": 0,
            "total_bytes": expected_size,
            "ready": False,
            "apk_path": "",
            "version": apk_info["version"],
            "error": "",
        })

    def _worker() -> None:
        tmp_path = None
        try:
            cache_dir = _apk_cache_dir(workspace)
            target_path = cache_dir / safe_name
            tmp_path = cache_dir / (safe_name + ".part")

            headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
            url_host = (urllib.parse.urlsplit(url).netloc or "").lower()
            base_host = (urllib.parse.urlsplit(base_url).netloc or "").lower()
            if token and (url_host == base_host or "gitcode.com" in url_host):
                headers["PRIVATE-TOKEN"] = token
                headers["Authorization"] = "Bearer " + token

            class _SafeApkRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req_obj, fp, code, msg, hdrs, newurl):
                    new_req = super().redirect_request(req_obj, fp, code, msg, hdrs, newurl)
                    if new_req is not None:
                        for hk in list(new_req.headers.keys()):
                            if hk.lower() in ("authorization", "private-token"):
                                new_req.remove_header(hk)
                    return new_req

            opener = urllib.request.build_opener(_SafeApkRedirect())
            req = urllib.request.Request(url, headers=headers, method="GET")
            hasher = hashlib.sha256()
            downloaded = 0
            with opener.open(req, timeout=180) as resp:
                hdr_len = int(resp.headers.get("Content-Length") or 0)
                total = hdr_len or expected_size
                with _LOCK:
                    _APK_STATE["total_bytes"] = total
                with open(tmp_path, "wb") as out:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > MAX_APK_BYTES:
                            raise OTAFetchError("APK 文件超出体积上限")
                        out.write(chunk)
                        hasher.update(chunk)
                        pct = int(min(99, downloaded * 100 // total)) if total > 0 else 0
                        with _LOCK:
                            _APK_STATE["downloaded_bytes"] = downloaded
                            _APK_STATE["percent"] = pct

            if downloaded <= 0:
                raise OTAFetchError("下载到的 APK 为空")
            actual_sha = hasher.hexdigest().lower()
            if expected_sha and actual_sha != expected_sha:
                raise OTAUpdateError("APK SHA256 校验失败，已中止安装")

            os.replace(str(tmp_path), str(target_path))
            with _LOCK:
                _APK_STATE.update({
                    "downloading": False,
                    "percent": 100,
                    "downloaded_bytes": downloaded,
                    "total_bytes": downloaded,
                    "ready": True,
                    "apk_path": str(target_path),
                    "error": "",
                })
        except Exception as exc:
            if tmp_path is not None:
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
            with _LOCK:
                _APK_STATE.update({
                    "downloading": False,
                    "ready": False,
                    "error": _mask_token(str(exc), token),
                })

    threading.Thread(target=_worker, name="oas-apk-dl", daemon=True).start()
    return {"ok": True, "started": True, **dict(_APK_STATE)}
