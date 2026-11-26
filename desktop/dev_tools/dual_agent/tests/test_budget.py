# This Python file uses the following encoding: utf-8

"""预算与安全停止测试：费用/次数/时长任一超标都必须停，且不调用模型。"""

import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parents[1]))  # 项目根

from config import load_config  # noqa: E402
from scheduler import BudgetExceeded, Scheduler  # noqa: E402
from state import State, now_text  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def make_scheduler(folder, overrides=None):
    config = load_config()
    if overrides:
        config.raw['budget'].update(overrides)
    state = State(Path(folder), config.project_root)
    state.set_meta('session_start', now_text())
    state.set_meta('session_started_epoch', __import__('time').time())
    scheduler = Scheduler(config, state, dry_run=True)
    return scheduler, state


def test_cost_cap():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state = make_scheduler(folder, {'max_cny_total': 0.5})
        class Result:
            ok = True
            error = ''
            usage = {'input_tokens': 0, 'cache_read_input_tokens': 0,
                     'output_tokens': 0}
            cost_cny = 0.5

        state.record_run('', 'agent_b', 'x', Result())
        try:
            scheduler.check_budget()
            ok = False
            detail = '没有抛异常'
        except BudgetExceeded as error:
            ok = True
            detail = str(error)
        check('费用超限触发 BudgetExceeded', ok, detail)

        result = scheduler.loop(max_iterations=3)
        check('循环在超预算时返回 paused_budget',
              result == 'paused_budget', result)
        check('状态被记为 paused_budget',
              state.get_meta('status') == 'paused_budget')
        state.close()


def test_call_cap():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state = make_scheduler(folder, {'max_calls': 1})
        class Result2:
            ok = True
            error = ''
            usage = {'input_tokens': 0, 'cache_read_input_tokens': 0,
                     'output_tokens': 0}
            cost_cny = 0.0

        state.record_run('', 'agent_a', 'x', Result2())
        try:
            scheduler.check_budget()
            ok = False
        except BudgetExceeded:
            ok = True
        check('调用次数超限触发 BudgetExceeded', ok)
        state.close()


def test_hours_cap():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state = make_scheduler(folder, {'max_hours': 0.0})
        try:
            scheduler.check_budget()
            ok = False
        except BudgetExceeded:
            ok = True
        check('时长超限触发 BudgetExceeded', ok)
        state.close()


def test_dry_run_does_not_call_models():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state = make_scheduler(folder, {'max_cny_total': 5.0})
        check('dry-run 下不创建 CLI agent', scheduler.agent is None)
        response = scheduler.call_agent('agent_a', 'prompt')
        check('dry-run 调用返回成功且无费用',
              response['ok'] and response['cost'] == 0)
        check('dry-run 不写入 runs 表', state.run_totals()['calls'] == 0)
        state.close()


def test_execute_requires_success_flag():
    """B 调用失败时必须判 REWORK，不能因为「机器验证通过」就放行。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state = make_scheduler(folder, {'max_cny_total': 5.0})
        task = state.add_task({'id': 'T-x', 'title': 'x', 'verify': ['AC05S']})
        verdict = scheduler.review_task(
            task, [{'check': 'compileall', 'ok': True}],
            {'ok': False, 'error': 'api_error'},
        )
        check('B 失败 → REWORK', verdict['verdict'] == 'REWORK', str(verdict))
        state.close()


def main():
    for case in (test_cost_cap, test_call_cap, test_hours_cap,
                 test_dry_run_does_not_call_models,
                 test_execute_requires_success_flag):
        print(f'--- {case.__name__}')
        case()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('DUAL AGENT BUDGET: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
