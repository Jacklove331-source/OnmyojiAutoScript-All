# This file uses the following encoding: utf-8
"""小工具：桌面版多开 / 虚拟定位 / 设备探活。

只调用 MuMuManager 与 adb 两条外部命令，不做内存注入、不发游戏协议。
所有函数返回 dict，失败也返回 dict（带 ok=False + reason），不抛异常给上层 UI。
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Optional

MUMU_MANAGER = r"E:\MuMuPlayer\nx_main\MuMuManager.exe"
ADB = r"E:\MuMuPlayer\nx_main\adb.exe"
GAME_PKG = "com.netease.onmyoji"


def _dec(raw: bytes) -> str:
    # MuMuManager 在中文 Windows 控制台上按 GBK 输出，adb 多为 UTF-8
    for enc in ("utf-8", "gbk", "mbcs"):
        try:
            return raw.decode(enc).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace").strip()


def _run(cmd: List[str], timeout: int = 30) -> Dict[str, Any]:
    """跑外部命令并回收输出。

    输出必须落文件而不是走管道：MuMuManager 在 stdout 是管道时会挂住不返回
    （实测 capture_output=True 连续两次 60s 超时、输出 0 字节；重定向到文件立刻返回）。
    """
    import tempfile
    fd1, out_path = tempfile.mkstemp(suffix=".out")
    fd2, err_path = tempfile.mkstemp(suffix=".err")
    os.close(fd1)
    os.close(fd2)
    try:
        with open(out_path, "wb") as fo, open(err_path, "wb") as fe:
            p = subprocess.run(cmd, stdout=fo, stderr=fe, stdin=subprocess.DEVNULL,
                               timeout=timeout)
        with open(out_path, "rb") as fo, open(err_path, "rb") as fe:
            out, err = _dec(fo.read()), _dec(fe.read())
        return {"ok": p.returncode == 0, "code": p.returncode, "out": out, "err": err}
    except FileNotFoundError:
        return {"ok": False, "code": -1, "out": "", "err": "not found: %s" % cmd[0]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -2, "out": "", "err": "timeout"}
    finally:
        for path in (out_path, err_path):
            try:
                os.remove(path)
            except OSError:
                pass


# ------------------------------------------------------------ 多开
def parse_instances(payload: str) -> List[Dict[str, Any]]:
    """把 MuMuManager info -v all 的 JSON 解析成实例列表。"""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    rows = []
    for key, item in data.items():
        if not isinstance(item, dict):
            continue
        rows.append({
            "index": str(item.get("index", key)),
            "name": item.get("name", ""),
            "running": bool(item.get("is_android_started")) or bool(item.get("is_process_started")),
            "adb_port": item.get("adb_port"),
            "player_state": item.get("player_state"),
        })
    rows.sort(key=lambda r: r["index"])
    return rows


def list_instances(slow: bool = False) -> Dict[str, Any]:
    if not slow:
        return list_instances_fast()
    # 慢路径：MuMuManager info -v all 实测 120s，只在需要 player_state 时用
    res = _run([MUMU_MANAGER, "info", "-v", "all"], timeout=180)
    if not res["ok"] and not res["out"]:
        return {"ok": False, "reason": res["err"] or "MuMuManager 无输出", "instances": []}
    rows = parse_instances(res["out"])
    return {"ok": bool(rows), "instances": rows, "count": len(rows)}


def control_instance(index: Any, action: str = "launch") -> Dict[str, Any]:
    """action: launch | shutdown | restart"""
    if action not in ("launch", "shutdown", "restart"):
        return {"ok": False, "reason": "unsupported action: %s" % action}
    res = _run([MUMU_MANAGER, "control", "-v", str(index), action], timeout=120)
    try:
        info = json.loads(res["out"] or "{}")
    except json.JSONDecodeError:
        info = {}
    ok = info.get("errcode", 1) == 0
    return {"ok": ok, "index": str(index), "action": action,
            "errcode": info.get("errcode"), "errmsg": info.get("errmsg") or res["err"]}


# ------------------------------------------------------------ 快速枚举
VMS_DIR = r"E:\MuMuPlayer\vms"


def list_instances_fast(vms_dir: str = VMS_DIR) -> Dict[str, Any]:
    """直接读 vms/<实例>/configs/vm_config.json，绕开 MuMuManager。

    MuMuManager info -v all 实测要 120s 才返回（会去探每个实例的服务），
    不适合放进 UI 请求路径；这里只读配置 + adb 在线状态。
    """
    rows = []
    if not os.path.isdir(vms_dir):
        return {"ok": False, "reason": "vms 目录不存在: %s" % vms_dir, "instances": []}
    devices = {d["serial"]: d["state"] for d in adb_devices()["devices"]}
    for name in sorted(os.listdir(vms_dir)):
        cfg = os.path.join(vms_dir, name, "configs", "vm_config.json")
        if not os.path.isfile(cfg):
            continue
        try:
            with open(cfg, encoding="utf-8") as fh:
                data = json.load(fh)
            vm = data.get("vm") or {}
            port = (((vm.get("nat") or {}).get("port_forward") or {}).get("adb") or {}).get("host_port")
            if isinstance(port, dict):
                port = port.get("host_port")
            port = int(port) if port else None
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        serial = "127.0.0.1:%s" % port if port else ""
        idx = name.rsplit("-", 1)[-1]
        rows.append({
            "index": idx,
            "name": name,
            "adb_port": int(port) if port else None,
            "serial": serial,
            "adb_state": devices.get(serial, "offline" if serial else ""),
            "online": devices.get(serial) == "device",
            "root": str(vm.get("root", "")).lower() == "true",
        })
    rows.sort(key=lambda r: int(r["index"]) if r["index"].isdigit() else 99)
    return {"ok": bool(rows), "instances": rows, "count": len(rows)}


# ------------------------------------------------------------ 设备 / 游戏
def adb_devices() -> Dict[str, Any]:
    res = _run([ADB, "devices"], timeout=30)
    rows = []
    for line in res["out"].splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            rows.append({"serial": parts[0], "state": parts[1]})
    return {"ok": res["ok"], "devices": rows}


def adb_connect(serial: str) -> Dict[str, Any]:
    res = _run([ADB, "connect", serial], timeout=30)
    return {"ok": "connected" in res["out"], "serial": serial, "out": res["out"] or res["err"]}


def game_state(serial: str) -> Dict[str, Any]:
    res = _run([ADB, "-s", serial, "shell",
                "dumpsys window | grep mCurrentFocus"], timeout=20)
    focus = [l.strip() for l in res["out"].splitlines() if "mCurrentFocus" in l]
    running = any(GAME_PKG in f for f in focus)
    return {"ok": True, "serial": serial, "running": running, "focus": focus[-1] if focus else ""}


def launch_game(serial: str, pkg: str = GAME_PKG) -> Dict[str, Any]:
    res = _run([ADB, "-s", serial, "shell", "monkey", "-p", pkg,
                "-c", "android.intent.category.LAUNCHER", "1"], timeout=60)
    return {"ok": res["ok"], "pkg": pkg, "out": (res["out"] or res["err"])[-200:]}


def stop_game(serial: str, pkg: str = GAME_PKG) -> Dict[str, Any]:
    res = _run([ADB, "-s", serial, "shell", "am", "force-stop", pkg], timeout=30)
    return {"ok": res["ok"], "pkg": pkg}


def capture(serial: str, out_path: str) -> Dict[str, Any]:
    """截图落盘（用 exec-out，避免 PS 5.1 的文本重定向损坏二进制）。"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    try:
        with open(out_path, "wb") as fh:
            p = subprocess.run([ADB, "-s", serial, "exec-out", "screencap", "-p"],
                               stdout=fh, timeout=60)
        size = os.path.getsize(out_path)
        return {"ok": p.returncode == 0 and size > 1024, "path": out_path, "bytes": size}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "path": out_path, "reason": str(exc)}


# ------------------------------------------------------------ 虚拟定位
# 常见「模拟定位」App 的包名 → 它们的广播 action（装了就自动用）
MOCK_LOCATION_APPS = {
    "com.lexa.fakegps": "com.lexa.fakegps.SET",
    "ru.gavrikov.mocklocations": "ru.gavrikov.mocklocations.SET_LOCATION",
    "com.blogspot.newapphorizons.fakegps": "com.blogspot.newapphorizons.fakegps.SET_LOCATION",
}


def detect_mock_app(serial: str) -> Dict[str, Any]:
    """看设备上有没有装可用的模拟定位 App，返回其包名与广播 action。"""
    res = _run([ADB, "-s", serial, "shell", "pm", "list", "packages"], timeout=30)
    if not res["ok"]:
        return {"ok": False, "reason": "无法读取设备已安装的 App，请检查 ADB 连接"}
    pkgs = {line.split(":", 1)[1].strip() for line in res["out"].splitlines()
            if line.startswith("package:")}
    for pkg, action in MOCK_LOCATION_APPS.items():
        if pkg in pkgs:
            return {"ok": True, "package": pkg, "action": action}
    return {"ok": False, "reason": "设备上没有受支持的模拟定位 App",
            "installed": sorted(p for p in pkgs if "mock" in p or "fake" in p)[:5]}


def set_location(serial: str, lat: float, lng: float,
                 action: str = "") -> Dict[str, Any]:
    """向用户已配置的模拟定位 App 发送坐标。"""
    pkg = next((pkg for pkg, receiver in MOCK_LOCATION_APPS.items() if receiver == action), None)
    if not pkg:
        return {"ok": False, "lat": lat, "lng": lng, "steps": [],
                "reason": "缺少受支持的定位广播 action"}

    b = _run([ADB, "-s", serial, "shell", "am", "broadcast", "-a", action,
              "-p", pkg, "--ef", "latitude", str(lat), "--ef", "longitude", str(lng)], timeout=30)
    sent = b["ok"] and "Broadcast completed" in b["out"]
    return {"ok": sent, "lat": lat, "lng": lng, "applied": None,
            "steps": [{"step": "broadcast", "ok": sent, "detail": (b["out"] or b["err"])[-200:]}],
            "reason": "广播已发送，请在设备上确认坐标" if sent else "定位广播发送失败"}
