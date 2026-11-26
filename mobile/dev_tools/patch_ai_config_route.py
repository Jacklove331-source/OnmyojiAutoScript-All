"""加 /ai/config：GET 读自愈配置，POST 写 Key/模式/模型，让"自愈"在 App 内可配置。"""

import io
import re
import subprocess
import sys

PATH = r"D:\3D\YYS2\oasx_mobile\android\app\src\main\python\oas_mobile\server.py"

HELPERS = '''def _ai_config_payload() -> dict:
    """\u8bfb\u81ea\u6108\uff08AI \u56de\u590d\uff09\u914d\u7f6e\uff0c\u6c38\u4e0d\u8fd4\u56de Key \u672c\u8eab\u3002"""
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
    """\u5199\u5165 api_key / mode / model / enabled\uff1bKey \u53ea\u5199\u8fdb\u5bc6\u94a5\u6587\u4ef6\uff0c\u4e0d\u56de\u663e\u3002"""
    import json
    import os

    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": "bad json: %r" % (error,)}

    try:
        from pathlib import Path

        from module.ai_recovery.config import config_path, load_config

        cfg = load_config(force=True)
        changed = []

        api_key = str(body.get("api_key") or "").strip()
        if api_key:
            key_file = Path(cfg.api.key_file)
            if not key_file.is_absolute():
                key_file = Path(config_path()).parent.parent / key_file
            key_file.parent.mkdir(parents=True, exist_ok=True)
            with open(key_file, "w", encoding="utf-8") as handle:
                handle.write(api_key + "\\n")
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
            text = re.sub(r"(?m)^(\\s+)model:.*$", r"\\1model: %s" % model, text, count=1)
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


'''

GET_ROUTE = '''        if route.path == "/ai/config":
            self._send_json(200, _ai_config_payload())
            return
'''

POST_ROUTE = '''            if route.path == "/ai/config":
                self._send_json(200, _ai_config_apply(body))
                return
'''

text = io.open(PATH, "rb").read().decode("utf-8-sig")
if '"/ai/config"' in text:
    raise SystemExit("already patched")

anchor_get = '        if route.path == "/ai/health":\n'
if anchor_get not in text:
    raise SystemExit("ai/health anchor missing")
text = text.replace(anchor_get, GET_ROUTE + anchor_get, 1)

anchor_post = '            if route.path == "/pause":\n'
if anchor_post not in text:
    raise SystemExit("pause anchor missing")
text = text.replace(anchor_post, POST_ROUTE + anchor_post, 1)

anchor_helper = "def _health_payload() -> dict:\n"
if anchor_helper not in text:
    raise SystemExit("health helper anchor missing")
text = text.replace(anchor_helper, HELPERS + anchor_helper, 1)

io.open(PATH, "w", encoding="utf-8", newline="\n").write(text)
p = subprocess.run([sys.executable, "-m", "py_compile", PATH],
                   capture_output=True, text=True, errors="replace")
io.open(r"D:\3D\YYS2\oasx_mobile\dist\_patch_log23.txt", "w", encoding="utf-8").write(
    "rc=%d\n%s" % (p.returncode, p.stderr[-400:]))
