# This Python file uses the following encoding: utf-8

"""最终验收并收尾：重跑全部机器验收，通过则写 FINAL_REPORT 并把状态置 completed。

为什么单独做入口：调度器主循环会在没有任务时继续找活干，而「收尾」是一次性动作。
这里只做：全量验收 → 全过则 finish()（写报告 + 状态 completed），不过则打印缺口并退出 1。

用法：
    .\\toolkit\\python.exe dev_tools\\dual_agent\\finalize.py            # 验收+收尾
    .\\toolkit\\python.exe dev_tools\\dual_agent\\finalize.py --check    # 只验收不改状态
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import acceptance  # noqa: E402
from config import load_config  # noqa: E402
from scheduler import Scheduler  # noqa: E402
from state import State  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='只验收，不改状态')
    args = parser.parse_args()

    config = load_config()
    state = State(config.state_dir, config.project_root)
    ok, missing = acceptance.final_gate(config, state)
    rows = state.acceptance_rows()
    print(f'机器验收: {sum(1 for row in rows if row["ok"])}/{len(rows)} 通过')
    for row in rows:
        print(f'  [{"PASS" if row["ok"] else "FAIL"}] {row["ac_id"]} '
              f'{row["title"]} :: {str(row["detail"])[:160]}')
    if not ok:
        print(f'\n未通过: {missing}')
        state.close()
        return 1
    print('\n全部强制验收通过')
    if args.check:
        state.close()
        return 0
    scheduler = Scheduler(config, state, dry_run=True)
    scheduler.finish()
    print('状态 -> completed，FINAL_REPORT.md 已生成')
    state.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
