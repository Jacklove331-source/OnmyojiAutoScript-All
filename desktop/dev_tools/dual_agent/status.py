# This Python file uses the following encoding: utf-8

"""查看双 Agent 调度器状态（不调用模型、不修改状态）。

用法：
    .\\toolkit\\python.exe dev_tools\\dual_agent\\status.py
    .\\toolkit\\python.exe dev_tools\\dual_agent\\status.py --tasks 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from config import load_config  # noqa: E402
from state import State  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', type=int, default=12)
    args = parser.parse_args()

    config = load_config()
    state = State(config.state_dir, config.project_root)
    since = str(state.get_meta('session_start', '') or '')
    print('== 调度器状态')
    print(f"  status      : {state.get_meta('status', 'unknown')}"
          + (f"  原因: {state.get_meta('pause_reason')}"
             if str(state.get_meta('status', '')).startswith('paused') else ''))
    resume_after = float(state.get_meta('resume_after', 0) or 0)
    if resume_after > time.time():
        print(f"  自动续跑于  : {datetime.fromtimestamp(resume_after):%Y-%m-%d %H:%M:%S}")
    print(f"  v2 会话     : {state.get_meta('v2_started_at', '-')}（预算/指标从此刻算）")
    print(f"  completed_at: {state.get_meta('completed_at', '-')}")
    print(f"  恢复次数    : {state.get_meta('resume_events', 0)}")
    print(f"  当前阶段    : {state.get_meta('current_phase', '-')}")
    lock = state.lock_info('executor')
    print(f"  执行锁      : {lock.get('owner')} / {lock.get('task_key')}" if lock else
          '  执行锁      : 空闲')
    job = state.get_meta('chess_job')
    print(f"  实机评估    : pid={job.get('pid')} sha={job.get('sha')} log={job.get('log')}"
          if job else '  实机评估    : 空闲')
    if (config.state_dir / 'PAUSE').exists():
        print('  !! PAUSE 文件存在：调度器会在下一轮安全暂停')

    budget = config.budget
    caps_a = config.provider_caps('anthropic')
    caps_d = config.provider_caps('deepseek')
    day = datetime.fromtimestamp(time.time() - 86400).strftime('%Y-%m-%d %H:%M:%S')
    day = max(day, since)
    a_all = state.run_totals(since=since, provider='anthropic')
    a_day = state.run_totals(since=day, provider='anthropic')
    d_all = state.run_totals(since=since, provider='deepseek')
    d_day = state.run_totals(since=day, provider='deepseek')
    print('== 预算（硬上限）')
    print(f"  Claude   : {a_all['calls']} 次 ${a_all['cost_usd']:.3f} / ${caps_a.get('max_usd_total')}"
          f"；24h ${a_day['cost_usd']:.3f} / ${caps_a.get('max_usd_24h')}")
    print(f"  DeepSeek : {d_all['calls']} 次 ¥{d_all['cost_cny']:.3f} / ¥{caps_d.get('max_cny_total')}"
          f"；24h ¥{d_day['cost_cny']:.3f} / ¥{caps_d.get('max_cny_24h')}")
    total = state.run_totals(since=since)
    print(f"  合计     : {total['calls']} 次 ¥{total['cost_cny']:.3f} / ¥{budget.get('max_cny_total')}"
          f"  tokens in={total['tokens_input']} cache_read={total['tokens_cached']} "
          f"cache_write={total['tokens_cache_write']} out={total['tokens_output']}")
    print(f"  任务分布 : {json.dumps(state.task_counts(), ensure_ascii=False)}")

    acceptance = state.acceptance_rows()
    if acceptance:
        print('== 验收')
        for row in acceptance:
            mark = 'PASS' if row['ok'] else 'FAIL'
            print(f"  [{mark}] {row['ac_id']:<6} {row['title'][:34]} :: "
                  f"{str(row['detail'])[:110]}")

    rows = state.task_cost_table(args.tasks)
    if rows:
        print('== 最近任务（调用/费用/token/耗时/返工）')
        for row in rows:
            print(f"  {row['task_key'][:26]:<26} {row['phase']:<9} {row['verdict'] or '-':<17} "
                  f"calls={row['calls']} A¥{row['a_cny']:.2f} B¥{row['b_cny']:.2f} "
                  f"tok={int(row['tin'] / 1000)}k/{int(row['tout'] / 1000)}k "
                  f"{int(row['secs'] / 60)}min rework={row['reworks']}")

    heartbeat = config.state_dir / 'heartbeat.json'
    if heartbeat.exists():
        beat = json.loads(heartbeat.read_text(encoding='utf-8'))
        age = time.time() - float(beat.get('epoch') or 0)
        alive = '活着' if age <= 120 else f'{age / 60:.1f} 分钟没更新'
        print(f"== 心跳: {beat.get('ts')} status={beat.get('status')} "
              f"pid={beat.get('pid')} ({alive})"
              + (f" phase={beat.get('phase')}" if beat.get('phase') else ''))
    state.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
