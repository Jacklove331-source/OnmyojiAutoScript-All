# This Python file uses the following encoding: utf-8

"""持久化与恢复测试：进程被杀死后要从断点继续，而不是从头开始。"""

import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from state import State, now_text  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def test_task_survives_restart():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        state_dir = Path(folder)
        state = State(state_dir, BASE)
        card = {'id': 'T-a', 'title': '示例任务', 'goal': 'g', 'verify': ['AC05S']}
        state.add_task(card)
        state.update_task('T-a', phase='verifying', attempts=2,
                          evidence=json.dumps({'feedback': 'x'}))
        state.set_meta('status', 'running')
        state.bump_meta('resume_events', 1)
        # 模拟进程被杀：不调用 close()
        del state

        reopened = State(state_dir, BASE)
        task = reopened.get_task('T-a')
        check('任务在重启后仍在', task is not None)
        check('阶段被保留', task is not None and task.phase == 'verifying',
              task.phase if task else '-')
        check('尝试次数被保留', task is not None and task.attempts == 2)
        check('证据被保留',
              task is not None and task.evidence.get('feedback') == 'x')
        check('状态标记被保留', reopened.get_meta('status') == 'running')
        check('resume_events 可累加', reopened.get_meta('resume_events') == 1)
        reopened.close()


def test_open_tasks_and_next_task():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        state = State(Path(folder), BASE)
        state.add_task({'id': 'T-1', 'title': 'a'})
        state.add_task({'id': 'T-2', 'title': 'b'})
        state.update_task('T-1', phase='accepted')
        check('open_tasks 只返回未完成任务',
              [task.task_key for task in state.open_tasks()] == ['T-2'])
        check('next_task 取到未完成任务',
              state.next_task().task_key == 'T-2')
        state.update_task('T-2', phase='review')
        check('review 阶段仍会被取出',
              state.next_task().task_key == 'T-2')
        state.update_task('T-2', phase='blocked')
        check('全部完成后 next_task 为 None', state.next_task() is None)
        state.close()


def test_runs_and_budget_accounting():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        state = State(Path(folder), BASE)
        state.add_task({'id': 'T-cost', 'title': 'cost'})

        class Result:
            ok = True
            error = ''
            usage = {'input_tokens': 1000, 'cache_read_input_tokens': 500,
                     'output_tokens': 200}
            cost_cny = 0.25

        state.record_run('T-cost', 'agent_b', now_text(), Result())
        totals = state.run_totals()
        check('调用次数累加', totals['calls'] == 1, str(totals))
        check('费用累加', abs(totals['cost_cny'] - 0.25) < 1e-9, str(totals))
        check('任务成本累加',
              abs(state.get_task('T-cost').cost_cny - 0.25) < 1e-9)
        check('meta 里的全局费用同步',
              abs(float(state.get_meta('cost_cny_total')) - 0.25) < 1e-9)
        state.close()


def test_events_and_heartbeat():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        state = State(Path(folder), BASE)
        state.event('unit_test', note='ok')
        lines = (Path(folder) / 'events.jsonl').read_text(
            encoding='utf-8').strip().splitlines()
        record = json.loads(lines[-1])
        check('事件流写入 jsonl', record['kind'] == 'unit_test'
              and record['note'] == 'ok')
        state.heartbeat('running')
        beat = json.loads(
            (Path(folder) / 'heartbeat.json').read_text(encoding='utf-8')
        )
        check('心跳文件含状态与预算',
              beat['status'] == 'running' and 'budget' in beat)
        state.close()


def test_acceptance_persist():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        state = State(Path(folder), BASE)
        state.record_acceptance('AC05', '回归', True, 'ok')
        state.record_acceptance('AC05', '回归', False, 'later fail')
        rows = state.acceptance_rows()
        check('验收记录按 id 覆盖更新',
              len(rows) == 1 and rows[0]['ok'] == 0, str(rows))
        state.close()


def main():
    for case in (test_task_survives_restart, test_open_tasks_and_next_task,
                 test_runs_and_budget_accounting, test_events_and_heartbeat,
                 test_acceptance_persist):
        print(f'--- {case.__name__}')
        case()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('DUAL AGENT PERSISTENCE: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
