"""用平安志自己的读取任务刷新快照（"它怎么解决就怎么解决"）。

原理：平安志（Tauri）开着 CDP，可调它自己的 IPC 命令：
  list_mumu_devices      → 看设备/游戏是否在跑
  start_mumu_read_task   → 就是它界面上的「数据读取→模拟器→开始读取」
任务跑完会把新的 current-data-*-snapshot-*.json 落到 %APPDATA%\\com.yys.analysis。

前置条件（缺一条就会失败，失败原因都在 %APPDATA%\\com.yys.analysis\\logs\\mumu-read-error-*.json）：
  1) 平安志进程在跑（默认用 E:\\平安志\\yys-analysis-cdp.exe，带 CDP 补丁的那个）；
  2) 游戏在局内（庭院等）——登录页/加载中会报 `Player data not available.`。

用法：
  toolkit\\python.exe tasks\\Workbench\\paz_refresh.py                 # 触发一次并等新快照
  toolkit\\python.exe tasks\\Workbench\\paz_refresh.py --timeout 120   # 加长等待
  toolkit\\python.exe tasks\\Workbench\\paz_refresh.py --dry           # 只看状态不触发
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tasks" / "Workbench"))

import paz_asset_reader as PZ  # noqa: E402

PAZ_EXE = Path(r"E:\平安志\yys-analysis-cdp.exe")
CDP_HELPER = Path(r"D:\3D\YYS2\_tools\paz_cdp.ps1")
LOGS = PZ.PAZ_DIR / "logs"


def _ps() -> str:
    """本机没有 pwsh 时退回 Windows PowerShell（实测 pwsh 不在 PATH 上）。"""
    import shutil

    for cand in ("pwsh", "powershell"):
        if shutil.which(cand):
            return cand
    fallback = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    return str(fallback) if fallback.exists() else "powershell"


def _cdp(cmd: str, args: str = "{}", timeout: int = 40) -> Dict[str, Any]:
    """调 paz_cdp.ps1，返回解析后的 dict（失败时 {"_raw": stdout}）。"""
    if not CDP_HELPER.exists():
        return {"error": f"缺少 CDP 助手 {CDP_HELPER}"}
    try:
        proc = subprocess.run(
            [_ps(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(CDP_HELPER),
             "-Cmd", cmd, "-Args", args],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    out = (proc.stdout or "").strip()
    body = out.split("\n", 1)[1] if out.lower().startswith("page=") and "\n" in out else out
    body = body.strip()
    if body.startswith("IPC_ERROR"):
        return {"error": "ipc_error", "detail": body[:600]}
    try:
        return json.loads(body)
    except Exception:
        return {"_raw": body[:800]}


def paz_running() -> bool:
    try:
        r = subprocess.run([_ps(), "-NoProfile", "-Command",
                            "(Get-Process | Where-Object { $_.ProcessName -like 'yys-analysis*' } | Measure-Object).Count"],
                           capture_output=True, text=True, timeout=25)
        return int((r.stdout or "0").strip() or 0) > 0
    except Exception:
        return False


def ensure_paz() -> Dict[str, Any]:
    if paz_running():
        return {"ok": True, "started": False}
    if not PAZ_EXE.exists():
        return {"ok": False, "error": f"平安志没在跑且找不到 {PAZ_EXE}"}
    try:
        subprocess.Popen([str(PAZ_EXE)], cwd=str(PAZ_EXE.parent))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"启动平安志失败: {exc}"}
    for _ in range(20):
        time.sleep(1.5)
        if _cdp("list_mumu_devices", timeout=20).get("endpoint"):
            return {"ok": True, "started": True}
    return {"ok": False, "error": "平安志启动后 CDP 仍不可用"}


_ADB_CANDIDATES = [
    Path(r"E:\MuMuPlayer\nx_main\adb.exe"),
    Path(r"E:\MuMuPlayer\nx_device\12.0\shell\adb.exe"),
]


def live_serials() -> list:
    """本机 adb 当前在线设备（只看 state=device 的）。"""
    adb = next((p for p in _ADB_CANDIDATES if p.exists()), None)
    if adb is None:
        return []
    try:
        out = subprocess.run([str(adb), "devices"], capture_output=True, text=True, timeout=15).stdout
    except Exception:  # noqa: BLE001
        return []
    serials = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def latest_snapshot():
    snap = PZ._latest("current-data-*-snapshot-*.json")
    if snap is None:
        return None, None
    try:
        d = json.loads(snap.read_text(encoding="utf-8"))
        return snap, d.get("capturedAt")
    except Exception:
        return snap, None


def last_error() -> Optional[str]:
    if not LOGS.exists():
        return None
    errs = sorted(LOGS.glob("mumu-read-error-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not errs:
        return None
    try:
        d = json.loads(errs[0].read_text(encoding="utf-8"))
        return f"{errs[0].name}: {d.get('message')}"
    except Exception:
        return errs[0].name


def refresh(timeout: int = 60, dry: bool = False,
            endpoint: Optional[str] = None, expect_role: Optional[str] = None) -> Dict[str, Any]:
    """触发平安志读取任务并等新快照。返回 {ok, before, after, error}。

    endpoint/expect_role：按角色挑设备与快照（oas1=127.0.0.1:16385、oas3=127.0.0.1:16416），
    `start_mumu_read_task` 的入参是 `{request: {endpoint}}`（实测传错层级会被静默忽略）。
    """
    before_snap, _grp = PZ.snapshot_for_role(expect_role)
    before_cap = None
    if before_snap is not None:
        try:
            before_cap = json.loads(before_snap.read_text(encoding="utf-8")).get("capturedAt")
        except Exception:
            before_cap = None
    res: Dict[str, Any] = {"ok": False, "before_captured": before_cap,
                           "before_file": before_snap.name if before_snap else None,
                           "endpoint": endpoint, "expect_role": expect_role}
    ens = ensure_paz()
    if not ens.get("ok"):
        res["error"] = ens.get("error")
        return res
    res["paz_started"] = ens.get("started")

    dev = _cdp("list_mumu_devices")
    res["device"] = dev
    if dry:
        res.update({"ok": True, "dry": True})
        return res

    live = live_serials()
    res["live_serials"] = live
    if endpoint and live and endpoint not in live:
        # 配置里记的是历史端口（如 16385）：平安志会直接报
        # 「所选模拟器不在本机 ADB 设备列表中」，整轮刷新报废。这里自动退到在线设备。
        res["endpoint_requested"] = endpoint
        endpoint = live[0]
        res["endpoint_fallback"] = endpoint
        res["endpoint"] = endpoint
    elif not endpoint and live:
        endpoint = live[0]
        res["endpoint"] = endpoint

    payload = json.dumps({"request": {"endpoint": endpoint}}) if endpoint else "{}"
    started = _cdp("start_mumu_read_task", payload)
    res["task"] = started
    if started.get("error") or not started.get("taskId"):
        res["error"] = f"触发失败: {started}"
        return res

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        snap, _g = PZ.snapshot_for_role(expect_role)
        cap = None
        if snap is not None:
            try:
                cap = json.loads(snap.read_text(encoding="utf-8")).get("capturedAt")
            except Exception:
                cap = None
        if snap is not None and cap != before_cap:
            res.update({"ok": True, "after_captured": cap, "after_file": snap.name,
                        "waited_sec": round(timeout - (deadline - time.time()), 1)})
            return res
    res["error"] = f"{timeout}s 内没有新快照；最近失败：{last_error()}"
    return res


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--endpoint", default="", help="目标设备（如 127.0.0.1:16416）")
    ap.add_argument("--expect-role", default="", help="按角色挑快照（如 半生緣）")
    ap.add_argument("--ensure-only", action="store_true",
                    help="只保证平安志在跑（随 OAS 栈启动时用），不触发采集")
    args = ap.parse_args(argv)
    if args.ensure_only:
        res = ensure_paz()
        res["device"] = _cdp("list_mumu_devices") if res.get("ok") else None
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res.get("ok") else 3
    res = refresh(timeout=args.timeout, dry=args.dry,
                  endpoint=args.endpoint or None, expect_role=args.expect_role or None)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
