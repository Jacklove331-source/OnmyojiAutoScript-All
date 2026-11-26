# This Python file uses the following encoding: utf-8

"""git commit-msg 钩子：调度器的 DeepSeek 正在执行任务时，拦下「没拿执行锁」的手工提交。

为什么需要：DSH 里的 DeepSeek 会话看不到调度器，会在调度器执行同一文件时提交改动
（2026-10-28 10:48 实测：调度器在改 chess_decision_audit.py，DSH 会话同时提交了同一文件）。
被拒的提交信息会直接告诉对方协议，比黑板更可靠。

放行规则（任何异常都放行，绝不因钩子本身的问题挡住提交）：
- 执行锁空闲 / 过期，或锁的 owner 不是 scheduler；
- 提交信息以 `[autosave]`、`chore(agent):`、`merge` 开头（调度器、自动保存、合并）；
- 不是主仓库工作区（worktree 里的提交不受影响）。

安装（.git/hooks/commit-msg）：
    #!/bin/sh
    PY=/d/3D/YYS2/OnmyojiAutoScript-easy-install-1/toolkit/python.exe
    HOOK=/d/3D/YYS2/OnmyojiAutoScript-easy-install-1/dev_tools/dual_agent/git_lock_hook.py
    [ -f "$HOOK" ] && [ -f "$PY" ] || exit 0
    exec "$PY" "$HOOK" "$1"
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
MAIN_ROOT = BASE.parents[1]
ALLOWED_PREFIXES = ('[autosave]', 'chore(agent):', 'merge', 'Merge')


def decide(message: str, lock: dict, toplevel: str) -> tuple[bool, str]:
    if Path(toplevel).resolve() != MAIN_ROOT.resolve():
        return True, 'not main worktree'
    if not lock or float(lock.get('expires', 0)) <= time.time():
        return True, 'lock free'
    if lock.get('owner') != 'scheduler':
        return True, f"lock owner {lock.get('owner')}"
    if message.lstrip().startswith(ALLOWED_PREFIXES):
        return True, 'allowed prefix'
    return False, (
        '⛔ 调度器 v2 的 DeepSeek 执行者正在改主工作区（任务 '
        f"{lock.get('task_key')}，执行锁到期 "
        f"{time.strftime('%H:%M', time.localtime(float(lock.get('expires', 0))))}）。\n"
        '   DSH 里的 DeepSeek 会话：改主工作区前先运行\n'
        '     .\\toolkit\\python.exe dev_tools\\dual_agent\\deepseek_b.py next\n'
        '   拿到执行锁再改；拿不到就先做只读分析。协议见 AI_COORD.md「调度器 v2」一节。\n'
        '   （你的改动仍在工作区里，没有丢失。）'
    )


def main() -> int:
    try:
        message = Path(sys.argv[1]).read_text(encoding='utf-8', errors='replace') \
            if len(sys.argv) > 1 else ''
        toplevel = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                                  capture_output=True, text=True, timeout=20).stdout.strip()
        db = BASE / 'state' / 'state.db'
        lock = {}
        if db.exists():
            conn = sqlite3.connect(str(db), timeout=5)
            row = conn.execute("SELECT value FROM meta WHERE key='lock:executor'").fetchone()
            conn.close()
            if row and row[0]:
                lock = json.loads(row[0]) or {}
        ok, reason = decide(message, lock, toplevel or str(MAIN_ROOT))
        if not ok:
            sys.stderr.buffer.write((reason + '\n').encode('utf-8'))
            return 1
        return 0
    except Exception:  # noqa: BLE001 - 钩子故障一律放行
        return 0


if __name__ == '__main__':
    sys.exit(main())
