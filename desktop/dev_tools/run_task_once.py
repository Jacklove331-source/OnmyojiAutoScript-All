# This file uses the following encoding: utf-8

"""单次运行一个任务（集成测试用，绕过调度器）。

用法：``python dev_tools/run_task_once.py oas3 Restart``
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script import Script  # noqa: E402


def main() -> int:
    config_name = sys.argv[1] if len(sys.argv) > 1 else 'oas3'
    task = sys.argv[2] if len(sys.argv) > 2 else 'Restart'
    script = Script(config_name)
    result = script.run(task)
    print(f'RESULT {task} -> {result!r}')
    return 0 if result else 1


if __name__ == '__main__':
    sys.exit(main())
