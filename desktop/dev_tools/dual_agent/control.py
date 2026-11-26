# This Python file uses the following encoding: utf-8

"""人工介入入口：安全暂停 / 恢复 / 启动看门狗。

    .\\toolkit\\python.exe dev_tools\\dual_agent\\control.py pause --by-user   # 下一轮安全暂停（仅用户/Claude）
    .\\toolkit\\python.exe dev_tools\\dual_agent\\control.py resume   # 去掉 PAUSE、清暂停状态并拉起看门狗
    .\\toolkit\\python.exe dev_tools\\dual_agent\\control.py start    # 只拉起看门狗（已在跑则跳过）
    .\\toolkit\\python.exe dev_tools\\dual_agent\\control.py add cards.json  # Claude 手工下达任务卡

总额预算到顶（paused_budget）后要继续：先改 config.json 的上限，再 resume。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from config import load_config  # noqa: E402
from state import State  # noqa: E402


def watchdog_running() -> bool:
    try:
        completed = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*dual_agent*watchdog.py*' }).Count"],
            capture_output=True, text=True, timeout=60,
        )
        return (completed.stdout or '0').strip() not in ('', '0')
    except (OSError, subprocess.SubprocessError):
        return False


def start_watchdog(config) -> str:
    if watchdog_running():
        return 'watchdog 已在运行'
    python = str(config.python)
    # 用字符串命令行：list 形式会把 "" 转义成 \"\"，cmd 的 start 会把它当成程序名。
    # `cmd /c start "" /b` 是本机实测能脱离调用方会话、长期存活的启动方式。
    command = f'cmd /c start "" /b "{python}" "{BASE / "watchdog.py"}"'
    subprocess.Popen(
        command, cwd=str(config.project_root), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    return 'watchdog 已启动'


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    action = (sys.argv[1:] or ['help'])[0]
    config = load_config()
    pause = config.state_dir / 'PAUSE'
    if action == 'pause':
        # 暂停整套无人值守系统是用户 / Claude 总指挥的权限。
        # DeepSeek 会话想独占主工作区或模拟器时应拿执行锁（deepseek_b.py next），
        # 调度器看到锁会自动等待——不需要、也不应该暂停调度器（否则用户睡觉时整夜停摆）。
        by = sys.argv[2] if len(sys.argv) > 2 else ''
        if by not in ('--by-user', '--by-claude'):
            print('拒绝：暂停需要注明 --by-user 或 --by-claude。\n'
                  'DeepSeek 会话请不要暂停调度器：要独占主工作区/模拟器就运行\n'
                  '  .\\toolkit\\python.exe dev_tools\\dual_agent\\deepseek_b.py next\n'
                  '拿执行锁，调度器会自动等你 record → review 后再继续。')
            return 2
        pause.write_text(f'paused {by}\n', encoding='utf-8')
        print(f'已写入 {pause}：调度器会在当前步骤结束后安全暂停')
        return 0
    if action in ('resume', 'start'):
        if action == 'resume':
            if pause.exists():
                pause.unlink()
            state = State(config.state_dir, config.project_root)
            status = str(state.get_meta('status', '') or '')
            if status.startswith('paused') or status == 'blocked':
                state.set_meta('status', 'resume_requested')
                state.set_meta('resume_after', 0)
            state.close()
        print(start_watchdog(config))
        return 0
    if action == 'add' and len(sys.argv) > 2:
        # Claude 总指挥手工下达任务卡：control.py add cards.json（{"tasks":[...]} 或 [...]）
        import json

        from scheduler import Scheduler

        payload = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
        cards = payload.get('tasks') if isinstance(payload, dict) else payload
        state = State(config.state_dir, config.project_root)
        scheduler = Scheduler(config, state, dry_run=True)
        added = scheduler._add_cards(cards or [], source='claude-manual')
        print(json.dumps([task.task_key for task in added], ensure_ascii=False))
        state.close()
        return 0
    print(__doc__)
    return 1


if __name__ == '__main__':
    sys.exit(main())
