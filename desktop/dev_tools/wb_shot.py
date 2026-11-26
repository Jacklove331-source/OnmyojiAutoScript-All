# This file uses the following encoding: utf-8
"""工作台页面真截图工具（离线单页，无构建步骤）。

做四件事：
  1. 起一个只挂 /workbench 路由的临时服务（默认 23334，不碰 22267）；
  2. 用 Edge/Chrome 的 DevTools 协议打开页面，**等接口数据渲染完**再截图；
  3. 输出 log/workbench/ui/desktop.png（1440x2400）、mobile.png（720x2400），
     以及 soul_detail.png / roster_detail.png（详情页）与可选 desktop_full.png（整页）；
  4. 退出时关掉临时服务和浏览器。

用法（仓库根目录）：
    .\\toolkit\\python.exe dev_tools\\wb_shot.py
    .\\toolkit\\python.exe dev_tools\\wb_shot.py --port 23340 --keep-full

依赖：本机 Edge（自动探测）与 websockets（tools venv 已带）；只读本地档案，
不重启 OAS 服务、不动设备。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "log" / "workbench" / "ui"

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def find_browser(explicit: str = "") -> str:
    if explicit:
        if not Path(explicit).is_file():
            raise SystemExit("找不到浏览器: %s" % explicit)
        return explicit
    for path in EDGE_CANDIDATES:
        if Path(path).is_file():
            return path
    raise SystemExit("没有找到 Edge/Chrome，请用 --edge 显式指定可执行文件")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pick_port(preferred: int) -> int:
    if port_free(preferred):
        return preferred
    for port in range(preferred + 1, preferred + 60):
        if port_free(port):
            return port
    raise SystemExit("没有空闲端口可用")


# ------------------------------------------------------------------ 临时服务
def start_server(port: int):
    sys.path.insert(0, str(ROOT))
    from fastapi import FastAPI  # 延迟导入，先确认依赖存在
    import uvicorn
    from module.server.workbench_router import app as workbench

    app = FastAPI()
    app.include_router(workbench)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def wait_ready(port: int, timeout: float = 60.0) -> dict:
    url = "http://127.0.0.1:%d/workbench/overview" % port
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # 起服务阶段轮询，最终失败再报
            last = exc
            time.sleep(0.4)
    raise SystemExit("临时服务没起来: %s" % last)


def fetch(port: int, path: str) -> dict:
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ------------------------------------------------------------------ DevTools 协议
class Cdp:
    def __init__(self, ws):
        self.ws = ws
        self.seq = 0

    def call(self, method: str, params: dict | None = None):
        from websockets.exceptions import ConnectionClosed

        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            try:
                message = json.loads(self.ws.recv(timeout=90))
            except ConnectionClosed as exc:
                raise RuntimeError("浏览器连接断了: %s" % exc) from exc
            if message.get("id") != self.seq:
                continue  # 忽略事件
            if "error" in message:
                raise RuntimeError("%s 失败: %s" % (method, message["error"]))
            return message.get("result", {})

    def js(self, expression: str):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        return result.get("result", {}).get("value")


READY_JS = """(() => {
  const loading = document.querySelectorAll('.state.loading').length;
  const pending = document.querySelectorAll('[data-state="loading"]').length;
  const kpi = document.querySelectorAll('.kpi').length;
  const errors = document.querySelectorAll('.state.error').length;
  return {loading, pending, kpi, errors, ready: document.readyState};
})()"""


def launch_browser(browser: str, profile_dir: Path, debug_port: int, width: int, height: int):
    args = [
        browser, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--hide-scrollbars", "--disable-extensions",
        "--disable-background-networking", "--no-proxy-server",
        "--remote-debugging-port=%d" % debug_port,
        "--user-data-dir=%s" % profile_dir,
        "--window-size=%d,%d" % (width, height),
        "about:blank",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % debug_port, timeout=3) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
            pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                return proc, pages[0]["webSocketDebuggerUrl"]
        except Exception:  # 浏览器还在启动
            pass
        time.sleep(0.3)
    proc.kill()
    raise SystemExit("浏览器没起来（DevTools 端口 %d 无响应）" % debug_port)


def wait_page_ready(cdp: Cdp, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    stable = 0
    last = None
    while time.time() < deadline:
        try:
            state = cdp.js(READY_JS)
        except RuntimeError:
            time.sleep(0.3)
            continue
        if isinstance(state, dict):
            last = state
            if state.get("loading") == 0 and state.get("pending") == 0 and state.get("kpi", 0) > 0:
                stable += 1
                if stable >= 3:
                    return
            else:
                stable = 0
        time.sleep(0.35)
    raise SystemExit("页面等不到加载完成：%s" % last)


def wait_js(cdp: Cdp, expression: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if cdp.js(expression):
                return
        except RuntimeError:
            pass
        time.sleep(0.3)
    raise SystemExit("等不到页面元素: %s" % expression)


def capture(cdp: Cdp, url: str, out: Path, width: int, height: int,
            full: bool = False, section: str = "", wait_for: str = "",
            extra_wait: float = 0.6) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": width < 800,
    })
    cdp.call("Page.navigate", {"url": url})
    wait_page_ready(cdp)
    if wait_for:
        wait_js(cdp, wait_for)
    time.sleep(extra_wait)
    if section:
        offset = cdp.js(
            "(() => { const el = document.getElementById(%s);"
            " return el ? Math.max(0, el.getBoundingClientRect().top + window.scrollY - 78) : 0; })()"
            % json.dumps(section)
        )
        clip = {"x": 0, "y": float(offset or 0), "width": width, "height": height, "scale": 1}
        beyond = True
    elif full:
        page_height = cdp.js(
            "Math.max(document.documentElement.scrollHeight, document.body.scrollHeight, %d)" % height
        )
        clip = {"x": 0, "y": 0, "width": width, "height": float(page_height or height), "scale": 1}
        beyond = True
    else:
        clip = {"x": 0, "y": 0, "width": width, "height": height, "scale": 1}
        beyond = False
    shot = cdp.call("Page.captureScreenshot", {
        "format": "png", "captureBeyondViewport": beyond, "clip": clip,
    })
    out.write_bytes(base64.b64decode(shot["data"]))
    if not out.is_file() or out.stat().st_size == 0:
        raise SystemExit("截图没写出来: %s" % out)
    return out


def kill_tree(proc) -> None:
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def kill_profile(profile_dir: Path) -> None:
    """Edge 会另起浏览器进程、父进程先退出；按 user-data-dir 收掉整组进程。"""
    if sys.platform != "win32":
        return
    script = (
        "$pat = '*' + [char]95 + 'edge' + [char]95 + 'profile*';"
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" |"
        " Where-Object { $_.CommandLine -like $pat } |"
        " ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def first_soul_id(port: int) -> str:
    data = fetch(port, "/workbench/todos?status=open&limit=1")
    items = data.get("items") or []
    return str(items[0].get("soul_id") or "") if items else ""


def first_roster_name(port: int) -> str:
    data = fetch(port, "/workbench/roster")
    rows = data.get("rows") or []
    return str(rows[0].get("name") or "") if rows else ""


# ------------------------------------------------------------------ 主流程
def main() -> int:
    from websockets.sync.client import connect

    parser = argparse.ArgumentParser(description="工作台页面真截图")
    parser.add_argument("--port", type=int, default=23334, help="临时服务端口")
    parser.add_argument("--debug-port", type=int, default=9223, help="浏览器 DevTools 端口")
    parser.add_argument("--edge", default="")
    parser.add_argument("--keep-full", action="store_true", help="额外输出 desktop_full.png 整页截图")
    args = parser.parse_args()

    browser = find_browser(args.edge)
    port = pick_port(args.port)
    debug_port = pick_port(args.debug_port)
    server = start_server(port)
    browser_proc = None
    ws = None
    profile_dir = None
    try:
        overview = wait_ready(port)
        profile = (overview.get("profile") or {}).get("name") or "未建立"
        print("临时服务 http://127.0.0.1:%d/workbench/ui （档案：%s）" % (port, profile))

        profile_dir = ROOT / "log" / "workbench" / ("_edge_profile_%d" % os.getpid())
        profile_dir.mkdir(parents=True, exist_ok=True)
        browser_proc, ws_url = launch_browser(browser, profile_dir, debug_port, 1440, 2400)
        ws = connect(ws_url, max_size=80 * 1024 * 1024, open_timeout=30)
        cdp = Cdp(ws)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")

        base = "http://127.0.0.1:%d/workbench/ui" % port
        made = []
        made.append(capture(cdp, base, OUT_DIR / "desktop.png", 1440, 2400))
        made.append(capture(cdp, base, OUT_DIR / "mobile.png", 720, 2400))

        soul = first_soul_id(port)
        if soul:
            made.append(capture(
                cdp, base + "?soul=" + urllib.parse.quote(soul) + "#soul-detail",
                OUT_DIR / "soul_detail.png", 1440, 1200,
                section="soul-detail", wait_for="!!document.querySelector('#soulDetailBox .detail-head')"))
        name = first_roster_name(port)
        if name:
            made.append(capture(
                cdp, base + "?roster=" + urllib.parse.quote(name) + "#roster-detail",
                OUT_DIR / "roster_detail.png", 1440, 1100,
                section="roster-detail", wait_for="!!document.querySelector('#rosterDetailBox .detail-head')"))
        if args.keep_full:
            made.append(capture(cdp, base, OUT_DIR / "desktop_full.png", 1440, 2400, full=True))

        print("截图完成：")
        for path in made:
            print("  %s  %d bytes" % (path, path.stat().st_size))
        return 0
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        kill_tree(browser_proc)
        if profile_dir is not None:
            kill_profile(profile_dir)
        server.should_exit = True
        time.sleep(0.3)


if __name__ == "__main__":
    raise SystemExit(main())
