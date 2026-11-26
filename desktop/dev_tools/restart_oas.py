# This Python file uses the following encoding: utf-8

"""重启 OAS 实例（GUI 管理下的 oas1/oas3）——分步、可干跑、失败可回退。

背景：实例由 `gui.py` 的 ProcessManager 以 multiprocessing 子进程方式拉起，
QML 里的 `create_all()` 已被注释掉，所以**重启 GUI 不会自动拉起实例**，
必须在 GUI 里逐个点启动。本脚本只做「安全停机 + 重启 GUI」，然后提示手动启动。

用法：
    .\\toolkit\\python.exe dev_tools\\restart_oas.py            # 干跑：只列目标进程
    .\\toolkit\\python.exe dev_tools\\restart_oas.py --execute   # 真的停机并重启 GUI
    .\\toolkit\\python.exe dev_tools\\restart_oas.py --execute --no-relaunch   # 只停机
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def powershell(command: str) -> str:
    completed = subprocess.run(
        ['powershell', '-NoProfile', '-Command', command],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    return (completed.stdout or '') + (completed.stderr or '')


def list_python_tree() -> list[dict]:
    """列出 python.exe：pid / parent / 是否 GUI（有 python 子进程即视为 GUI）。"""
    raw = powershell(
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object ProcessId,ParentProcessId,CreationDate | "
        "ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId,$_.ParentProcessId,$_.CreationDate }")
    rows = []
    for line in raw.splitlines():
        parts = line.strip().split('|')
        if len(parts) != 3 or not parts[0].isdigit():
            continue
        rows.append({'pid': int(parts[0]), 'ppid': int(parts[1]), 'started': parts[2]})
    parents = {row['ppid'] for row in rows}
    for row in rows:
        row['is_gui'] = row['pid'] in parents
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true', help='真的停机（默认干跑）')
    parser.add_argument('--no-relaunch', action='store_true', help='不停后不重启 GUI')
    parser.add_argument('--elevate', action='store_true',
                        help='通过 UAC 提权跑 dev_tools/restart_oas_elevated.ps1（会弹 UAC 确认）')
    args = parser.parse_args()

    rows = list_python_tree()
    if not rows:
        print('没有找到 python.exe 进程（实例可能没在跑）')
        return 0
    guis = [row for row in rows if row['is_gui']]
    children = [row for row in rows if not row['is_gui']]
    print('GUI 进程（有 python 子进程）:', [row['pid'] for row in guis] or '无')
    print('实例子进程:', [row['pid'] for row in children] or '无')
    if not args.execute and not args.elevate:
        print('\n干跑结束。加 --execute 才会真的停机并重启 GUI。')
        print('停机后需要在 GUI 里对 oas1 / oas3 各点一次「启动」'
              '（QML 的 create_all 被注释掉，不会自动拉起）。')
        return 0

    # 先确认自己有没有权限：GUI 常以管理员身份启动，非提权 shell 停不掉它。
    # 停不掉就**不要**继续重启，否则会多出一个 GUI 抢端口/重复管理实例（实测过一次）。
    identity = powershell(
        '$id=[Security.Principal.WindowsIdentity]::GetCurrent();'
        '$p=New-Object Security.Principal.WindowsPrincipal($id);'
        'if ($p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))'
        '{"yes"} else {"no"}').strip()
    if args.elevate:
        ps1 = ROOT / 'dev_tools' / 'restart_oas_elevated.ps1'
        if not ps1.exists():
            print(f'找不到提权脚本: {ps1}')
            return 4
        log_file = ROOT / 'log' / 'restart_elevated.log'
        if log_file.exists():
            log_file.unlink()
        print('通过 UAC 提权执行停机+重启（请在弹窗里点「是」）...')
        subprocess.Popen(['powershell', '-NoProfile', '-Command',
                          f"Start-Process powershell -Verb RunAs -ArgumentList "
                          f"'-NoProfile','-ExecutionPolicy','Bypass','-File','{ps1}'"],
                         cwd=str(ROOT))
        for _ in range(24):
            time.sleep(5)
            if log_file.exists():
                content = log_file.read_text(encoding='utf-8', errors='replace')
                if 'done' in content:
                    print(content)
                    print('\n下一步：在 GUI 里对 oas1、oas3 各点一次「启动」；')
                    print('然后跑 .\\toolkit\\python.exe dev_tools\\stagger_watch.py --minutes 10')
                    return 0
        print('UAC 提权未完成（可能被取消）。日志：')
        if log_file.exists():
            print(log_file.read_text(encoding='utf-8', errors='replace'))
        return 5

    if identity.lower() != 'yes':
        print('当前 shell 不是管理员：GUI 通常以管理员运行，停不掉。')
        print('两种做法：')
        print('  1) 右键用「管理员身份」开 PowerShell，再跑本脚本 --execute；')
        print('  2) 直接在 GUI 里对 oas1 / oas3 各点一次「重启」（等价且最简单）。')
        return 2

    failed = []
    for row in guis:
        print(f"停止 GUI 进程树 pid={row['pid']}")
        output = powershell(f'Stop-Process -Id {row["pid"]} -Force -ErrorAction Continue')
        time.sleep(1)
        still = powershell(
            f'if (Get-Process -Id {row["pid"]} -ErrorAction SilentlyContinue) {{"alive"}} else {{"dead"}}'
        ).strip()
        if still != 'dead':
            failed.append(row['pid'])
            print(f"  停止失败: {output.strip() or '进程仍在'}")
    if failed:
        print(f'有进程停不掉 {failed}，已中止重启（不启动第二个 GUI，避免抢端口）。')
        return 3
    if args.no_relaunch:
        print('已停机（未重启 GUI）')
        return 0

    time.sleep(3)
    python = ROOT / 'toolkit' / 'python.exe'
    if not python.exists():
        print(f'找不到解释器: {python}')
        return 1
    print('重启 GUI ...')
    subprocess.Popen([str(python), 'gui.py'], cwd=str(ROOT),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(8)
    rows = list_python_tree()
    print('重启后进程:', [(row['pid'], 'GUI' if row['is_gui'] else 'child') for row in rows])
    print('\n下一步（必须手动）：在 GUI 里对 oas1、oas3 各点一次「启动」；')
    print('然后运行：.\\toolkit\\python.exe dev_tools\\stagger_watch.py --minutes 10')
    return 0


if __name__ == '__main__':
    sys.exit(main())
