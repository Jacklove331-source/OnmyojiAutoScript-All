# This Python file uses the following encoding: utf-8

"""DSH 里的 DeepSeek 会话作为「执行工程师」接入调度器（Claude 负责规划与审核）。

与无人值守调度器共用同一份 state.db 与**执行锁**：
- `next` 抢执行锁（owner=dsh-deepseek）并打 git 检查点，调度器看到锁就不会再派活给自己的
  Agent B，避免两个执行者同时改主工作区、审核 diff 混在一起；
- `record` 记录执行结果（任务进入 verifying，锁保留到审核结束）；
- `review` 机器验证 + **Claude** 审核（v2：走真 Claude），然后释放锁；
- `release` 主动释放锁（放弃任务时用）；锁有 TTL（默认 3h），会话中断也不会永久卡死调度器。

子命令：
    next / record --key K ... / review --key K / release / stats
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import safety  # noqa: E402
from config import load_config  # noqa: E402
from scheduler import Scheduler  # noqa: E402
from state import State, now_text  # noqa: E402

OWNER = 'dsh-deepseek'


def get_scheduler():
    config = load_config()
    state = State(config.state_dir, config.project_root)
    return Scheduler(config, state, dry_run=False), state, config


def _out(payload) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode('utf-8') + b'\n')


def _ttl(config) -> float:
    return float(config.limit('executor_lock_ttl_sec', 10800))


def cmd_next(args) -> int:
    scheduler, state, config = get_scheduler()
    lock = state.lock_info('executor')
    if lock and lock.get('owner') != OWNER:
        _out({'task': None, 'reason': f"执行锁被 {lock.get('owner')} 占用（任务 "
              f"{lock.get('task_key')}，到期 {lock.get('expires')}）；请做只读分析或稍后再试"})
        state.close()
        return 2
    if lock and lock.get('owner') == OWNER and lock.get('task_key'):
        held = state.get_task(lock['task_key'])
        if held is not None and held.is_open:
            task = held
        else:
            task = None
    else:
        task = None
    if task is None:
        task = next((item for item in state.open_tasks()
                     if str(item.card.get('owner', 'deepseek')) != 'claude'), None)
    if task is None:
        print('// 队列为空，请 Claude 规划新任务 ...', file=sys.stderr)
        planned = [item for item in scheduler.plan_tasks()
                   if str(item.card.get('owner', 'deepseek')) != 'claude']
        task = planned[0] if planned else None
    if task is None:
        _out({'task': None, 'reason': 'Claude 未能给出可执行任务'})
        state.close()
        return 1
    ok, info = state.acquire_lock('executor', OWNER, _ttl(config), task.task_key)
    if not ok:
        _out({'task': None, 'reason': f"抢锁失败：{info.get('owner')}"})
        state.close()
        return 2
    evidence = dict(task.evidence or {})
    if not evidence.get('base_sha') or task.phase == 'pending':
        checkpoint = safety.git_checkpoint(
            config, state, f'chore(agent): checkpoint before {task.task_key} (dsh)')
        if checkpoint.get('sha'):
            evidence['base_sha'] = checkpoint['sha']
            state.update_task(task.task_key,
                              evidence=json.dumps(evidence, ensure_ascii=False)[:6000])
    state.event('dsh_claimed', task_key=task.task_key)
    card = dict(task.card)
    card['_state'] = {'task_key': task.task_key, 'phase': task.phase,
                      'rework_left': task.rework_left, 'attempts': task.attempts,
                      'base_sha': evidence.get('base_sha', '')}
    card['_feedback'] = str(evidence.get('feedback') or '')[:1500]
    _out(card)
    state.close()
    return 0


def cmd_record(args) -> int:
    _scheduler, state, config = get_scheduler()
    task = state.get_task(args.key)
    if task is None:
        print(f'找不到任务 {args.key}')
        state.close()
        return 1
    lock = state.lock_info('executor')
    if lock and lock.get('owner') != OWNER:
        print(f"执行锁被 {lock.get('owner')} 占用，不能记录（避免与调度器的执行混在一起）")
        state.close()
        return 2
    state.acquire_lock('executor', OWNER, _ttl(config), task.task_key)
    summary = {
        'ok': True,
        'partial': False,
        'summary': args.summary,
        'files_changed': [item for item in (args.files or '').split(',') if item],
        'tests_run': [item for item in (args.tests or '').split(';') if item],
        'notes': args.notes or '',
        'denials': [],
        'error': '',
        'executor': 'deepseek (DSH session)',
        'recorded_at': now_text(),
    }
    state.update_task(
        task.task_key, phase='verifying',
        agent_b_summary=json.dumps(summary, ensure_ascii=False)[:6000],
        attempts=task.attempts + 1,
    )
    state.event('deepseek_recorded', task_key=task.task_key,
                summary=args.summary[:300])
    print(f'recorded {task.task_key} -> verifying（执行 review 交给 Claude 审核）')
    state.close()
    return 0


def cmd_review(args) -> int:
    scheduler, state, _config = get_scheduler()
    task = state.get_task(args.key)
    if task is None:
        print(f'找不到任务 {args.key}')
        state.close()
        return 1
    # 审核期间也持锁：否则调度器会把同一个 verifying 任务再审一遍（重复花 Claude 的钱）
    ok, info = state.acquire_lock('executor', OWNER, _ttl(_config), task.task_key)
    if not ok:
        print(f"执行锁被 {info.get('owner')} 占用（{info.get('task_key')}），稍后再 review")
        state.close()
        return 2
    try:
        scheduler.finalize_task(task, scheduler._stored_summary(task))
        last = (state.reviews_for(task.task_key) or [{}])[-1]
        safety.git_checkpoint(
            _config, state,
            f'chore(agent): after {task.task_key} '
            f'({last.get("verdict", "?")} by {last.get("model") or "?"}, dsh)')
    finally:
        lock = state.lock_info('executor')
        if lock and lock.get('owner') == OWNER:
            state.release_lock('executor', OWNER)
    after = state.get_task(task.task_key)
    note_row = state.conn.execute(
        'SELECT review_note FROM tasks WHERE task_key = ?', (task.task_key,)
    ).fetchone()
    _out({
        'task_key': after.task_key,
        'phase': after.phase,
        'verdict': (state.reviews_for(task.task_key) or [{}])[-1].get('verdict'),
        'reviewer': (state.reviews_for(task.task_key) or [{}])[-1].get('model'),
        'review_note': str(note_row['review_note'] or '')[:800] if note_row else '',
    })
    state.close()
    return 0


def cmd_release(args) -> int:
    _scheduler, state, _config = get_scheduler()
    released = state.release_lock('executor', OWNER)
    print('released' if released else 'no lock held by dsh-deepseek')
    state.close()
    return 0


def cmd_stats(args) -> int:
    scheduler, state, _config = get_scheduler()
    _out({
        'status': state.get_meta('status'),
        'budget': scheduler.budget_snapshot(),
        'tasks': state.task_counts(),
        'executor_lock': state.lock_info('executor'),
        'acceptance_fail': [row['ac_id'] for row in state.acceptance_rows()
                            if not row['ok']],
    })
    state.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='DeepSeek 执行工程师调度 CLI（v2）')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('next')
    record = sub.add_parser('record')
    record.add_argument('--key', required=True)
    record.add_argument('--summary', required=True)
    record.add_argument('--files', default='')
    record.add_argument('--tests', default='')
    record.add_argument('--notes', default='')
    review = sub.add_parser('review')
    review.add_argument('--key', required=True)
    sub.add_parser('release')
    sub.add_parser('stats')
    args = parser.parse_args()
    return {
        'next': cmd_next,
        'record': cmd_record,
        'review': cmd_review,
        'release': cmd_release,
        'stats': cmd_stats,
    }[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
