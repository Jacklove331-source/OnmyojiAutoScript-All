# This Python file uses the following encoding: utf-8

"""跑完双 Agent 调度器的全部自测（供验收项 AC01 使用）。

每个测试都是独立脚本（仓库风格），这里用子进程跑并汇总。
用法：.\\toolkit\\python.exe dev_tools\\dual_agent\\tests\\run_all.py
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
PYTHON = Path(sys.executable)

TESTS = [
    'test_persistence.py',
    'test_budget.py',
    'test_safety.py',
    'test_protocol.py',
    'test_acceptance.py',
    'test_v2.py',
]


def main() -> int:
    failures = []
    for name in TESTS:
        path = HERE / name
        if not path.exists():
            print(f'[SKIP] {name} 不存在')
            failures.append(name)
            continue
        completed = subprocess.run(
            [str(PYTHON), str(path)],
            cwd=str(PROJECT),
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=900,
        )
        tail = [
            line for line in (completed.stdout or '').splitlines()
            if 'TOTAL' in line or 'FAILED' in line or 'PASSED' in line
        ]
        print(f'[{ "PASS" if completed.returncode == 0 else "FAIL" }] {name} :: '
              + ' | '.join(tail[-2:]))
        if completed.returncode != 0:
            failures.append(name)
            print((completed.stdout or '')[-1500:])
            print((completed.stderr or '')[-800:])
    print()
    total = len(TESTS)
    passed = total - len(failures)
    print(f'TOTAL {total}  PASS {passed}  FAIL {len(failures)}')
    if failures:
        print('DUAL AGENT TESTS: FAILED -> ' + ', '.join(failures))
        return 1
    print('DUAL AGENT TESTS: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
