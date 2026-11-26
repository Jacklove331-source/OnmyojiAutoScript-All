# This Python file uses the following encoding: utf-8

"""错峰修复后的实盘盯盘：重启实例后跑它，确认「等待→关机→重启」循环消失。

用法：
    .\\toolkit\\python.exe dev_tools\\stagger_watch.py --minutes 10
    .\\toolkit\\python.exe dev_tools\\stagger_watch.py --minutes 30 --interval 60

判定（每个采样点打印累计计数）：
    closing  = 日志里 "emulator closed to save resources"   ← 修复后应保持 0
    waiting  = "waiting for execution token"
    restarts = "Scheduler: Start task `Restart`"
    tasks    = "Scheduler: Start task `X`"（X != Restart）
超过 10 分钟里 closing 仍增长 → 说明跑的还是旧代码（没重启）或修复被回退。
"""

import argparse
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    'closing': 'emulator closed to save resources',
    'waiting': 'waiting for execution token',
    'restarts': 'Scheduler: Start task `Restart`',
}


def log_path(config: str) -> Path:
    return ROOT / 'log' / f'{date.today().isoformat()}_{config}.txt'


def counts(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding='utf-8', errors='replace')
    result = {key: text.count(pattern) for key, pattern in PATTERNS.items()}
    starts = text.count('Scheduler: Start task `')
    result['tasks'] = starts - result['restarts']
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--minutes', type=float, default=10)
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--configs', default='oas1,oas3')
    args = parser.parse_args()

    configs = [item.strip() for item in args.configs.split(',') if item.strip()]
    baseline = {config: counts(log_path(config)) for config in configs}
    print('基线：')
    for config, value in baseline.items():
        print(f'  {config}: {value}')
    deadline = time.time() + args.minutes * 60
    verdict = 0
    while time.time() < deadline:
        time.sleep(args.interval)
        print(f'--- {time.strftime("%H:%M:%S")}')
        for config in configs:
            now = counts(log_path(config))
            base = baseline.get(config) or {}
            delta = {key: now.get(key, 0) - base.get(key, 0) for key in now}
            print(f'  {config}: now={now} delta={delta}')
            if delta.get('closing', 0) > 0:
                print(f'  [!] {config} 采样期间仍在「等令牌时关模拟器」→ 旧代码未重启')
                verdict = 1
    print()
    print('结论：' + ('FAIL（仍有等待时关机，需重启实例）' if verdict else
                    'PASS（采样期间没有因等待而关模拟器）'))
    return verdict


if __name__ == '__main__':
    sys.exit(main())
