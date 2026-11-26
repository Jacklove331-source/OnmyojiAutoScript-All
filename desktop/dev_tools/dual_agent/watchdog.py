# This Python file uses the following encoding: utf-8

"""调度器守护进程（v2）：崩溃自动重启；按状态决定「立刻续跑 / 等到某时刻 / 退出」。

- completed / paused_budget（总额到顶）/ paused_manual（PAUSE 文件或人工中断）→ 退出（终态）；
- paused_daily（24h 窗口到顶）/ paused_env（代理/网络故障）/ blocked（暂无可执行任务）
  → 读 meta.resume_after，睡到那个时刻再重启调度器；
- paused_limit / 崩溃 → 短暂等待后重启。

为什么用 Python 而不是计划任务：本机 `Register-ScheduledTask` 返回 0x80070005（无权限）。
登录后续跑靠启动文件夹里的 autostart.cmd。

用法（后台）：
    cmd /c start "" /b "<repo>\\toolkit\\python.exe" dev_tools\\dual_agent\\watchdog.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from config import load_config  # noqa: E402

TERMINAL = {'completed', 'paused_budget', 'paused_manual'}
WAITING = {'paused_daily', 'paused_env', 'blocked'}


def now_text() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def read_meta(db_path: Path, key: str):
    import sqlite3

    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
        row = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
        conn.close()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return row[0]
    except Exception:  # noqa: BLE001 - 守护进程不能因为读库失败而退出
        return None


def read_status(db_path: Path) -> str:
    value = read_meta(db_path, 'status')
    return str(value or '')


def scheduler_running(base: Path) -> bool:
    """避免同时跑两个调度器（会互相抢同一份 state.db）。"""
    try:
        completed = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*dual_agent*scheduler.py*' } | "
             "Select-Object -First 1 -ExpandProperty ProcessId"],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=60,
        )
        return bool((completed.stdout or '').strip())
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--hours', type=float, default=8.0,
                        help='单次调度器进程的运行时长上限（到时重启续跑）')
    parser.add_argument('--max-restarts', type=int, default=500)
    parser.add_argument('--delay', type=int, default=30)
    args = parser.parse_args()

    config = load_config()
    db = config.state_dir / 'state.db'
    pause_file = config.state_dir / 'PAUSE'
    log_dir = BASE / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    watch_log = log_dir / f"watch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    def write(text: str) -> None:
        line = f'[{now_text()}] {text}'
        with open(watch_log, 'a', encoding='utf-8') as stream:
            stream.write(line + '\n')

    scheduler = BASE / 'scheduler.py'
    restarts = 0
    write(f'watchdog start hours={args.hours} max_restarts={args.max_restarts}')
    while restarts <= args.max_restarts:
        if pause_file.exists():
            write('PAUSE 文件存在 → watchdog 退出（删除文件后重新启动即可续跑）')
            break
        status = read_status(db)
        if status in TERMINAL and status != 'paused_manual':
            write(f'terminal state ({status}) -> watchdog stop')
            break
        resume_after = float(read_meta(db, 'resume_after') or 0)
        if status in WAITING and resume_after > time.time():
            wait = min(resume_after - time.time(), 900)
            write(f'status={status}，等待 {wait / 60:.1f} 分钟后重试')
            time.sleep(max(5, wait))
            continue
        if scheduler_running(BASE):
            write('另一个 scheduler 已在运行，等待 60s')
            time.sleep(60)
            continue
        run_log = log_dir / f"scheduler_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        write(f'launch scheduler -> {run_log.name}')
        with open(run_log, 'a', encoding='utf-8', errors='replace') as stream:
            code = subprocess.call(
                [sys.executable, str(scheduler), '--hours', str(args.hours)],
                cwd=str(BASE), stdout=stream, stderr=subprocess.STDOUT,
                env={**__import__('os').environ, 'PYTHONIOENCODING': 'utf-8'},
            )
        status = read_status(db)
        write(f'scheduler exit code={code} status={status}')
        if status in TERMINAL:
            write(f'terminal state ({status}) -> watchdog stop')
            break
        restarts += 1
        if restarts > args.max_restarts:
            write('restart budget exhausted')
            break
        if status not in WAITING:
            write(f'restart #{restarts} in {args.delay}s')
            time.sleep(args.delay)
    write('watchdog end')
    return 0


if __name__ == '__main__':
    sys.exit(main())
