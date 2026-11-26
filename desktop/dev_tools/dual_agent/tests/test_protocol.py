# This Python file uses the following encoding: utf-8

"""协议解析与费用估算测试：JSON 抽取、usage 计费、审核兜底。"""

import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parents[1]))

from cli_agent import estimate_cost, extract_json_block  # noqa: E402
from config import load_config  # noqa: E402
from scheduler import Scheduler  # noqa: E402
from state import State, now_text  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def test_extract_json():
    check('纯 JSON',
          extract_json_block('{"a":1}') == {'a': 1})
    check('前后有说明文字',
          extract_json_block('说明\n{"verdict":"PASS","notes":"ok"}\n结束')
          == {'verdict': 'PASS', 'notes': 'ok'})
    check('嵌套对象',
          extract_json_block('x {"a":{"b":[1,2]}} y') == {'a': {'b': [1, 2]}})
    check('字符串里的花括号不干扰',
          extract_json_block('{"a":"}{"}') == {'a': '}{'})
    check('没有 JSON 返回 None',
          extract_json_block('只有文字') is None)


def test_estimate_cost():
    prices = {'input': 4.0, 'cache_read': 1.0, 'output': 16.0}
    usage = {'input_tokens': 1_000_000, 'cache_read_input_tokens': 0,
             'output_tokens': 0}
    check('输入 1M tokens = ¥4', abs(estimate_cost(usage, prices) - 4.0) < 1e-6,
          str(estimate_cost(usage, prices)))
    usage2 = {'input_tokens': 0, 'cache_read_input_tokens': 1_000_000,
              'output_tokens': 0}
    check('缓存读 1M tokens = ¥1', abs(estimate_cost(usage2, prices) - 1.0) < 1e-6)
    usage3 = {'input_tokens': 0, 'cache_read_input_tokens': 0,
              'output_tokens': 1_000_000}
    check('输出 1M tokens = ¥16', abs(estimate_cost(usage3, prices) - 16.0) < 1e-6)


def test_review_fallback_on_agent_error():
    """审核调用失败时按机器验证结果兜底：有失败项就 REWORK。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config = load_config()
        state = State(Path(folder), config.project_root)
        state.set_meta('session_start', now_text())
        state.set_meta('session_started_epoch', __import__('time').time())
        scheduler = Scheduler(config, state, dry_run=True)

        class FakeAgent:
            def run(self, *args, **kwargs):
                class R:
                    ok = False
                    text = ''
                    usage = {}
                    cost_cny = 0.0
                    error = 'api_error'
                    duration_s = 1.0
                    denials = []
                return R()

        scheduler.agent = FakeAgent()
        scheduler.dry_run = False
        task = state.add_task({'id': 'T-r', 'title': 'r', 'verify': ['AC05S']})
        verdict = scheduler.review_task(
            task, [{'check': 'AC05S', 'ok': False, 'detail': 'x'}],
            {'ok': True, 'summary': 's'},
        )
        check('审核失败 + 验证失败 → REWORK', verdict['verdict'] == 'REWORK',
              str(verdict))
        verdict2 = scheduler.review_task(
            task, [{'check': 'AC05S', 'ok': True, 'detail': 'x'}],
            {'ok': True, 'summary': 's'},
        )
        check('审核失败 + 验证通过 → PASS', verdict2['verdict'] == 'PASS',
              str(verdict2))
        state.close()


def test_task_flow_reworks_then_blocks():
    """返工次数用尽后任务必须变 blocked，避免无限循环烧预算。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config = load_config()
        state = State(Path(folder), config.project_root)
        state.set_meta('session_start', now_text())
        state.set_meta('session_started_epoch', __import__('time').time())
        scheduler = Scheduler(config, state, dry_run=True)
        task = state.add_task({
            'id': 'T-loop', 'title': 'loop', 'verify': ['AC05S'],
            'max_rework': 1,
        })

        class FakeAgent:
            def run(self, *args, **kwargs):
                class R:
                    ok = True
                    text = '{"verdict":"REWORK","notes":"再改"}'
                    usage = {}
                    cost_cny = 0.0
                    error = ''
                    duration_s = 1.0
                    denials = []
                return R()

        scheduler.agent = FakeAgent()
        scheduler.dry_run = False

        def fake_verify(task_obj):
            return [{'check': 'AC05S', 'ok': False, 'detail': 'fail'}]

        def fake_checkpoint(*args, **kwargs):
            return {'committed': False, 'sha': '', 'tag': ''}

        def fake_execute(task_obj, feedback=''):
            return {'ok': True, 'summary': 'x', 'files_changed': [],
                    'tests_run': [], 'notes': '', 'denials': []}

        import safety as safety_module
        original_checkpoint = safety_module.git_checkpoint
        original_verify = Scheduler.verify_task
        original_execute = Scheduler.execute_task
        safety_module.git_checkpoint = fake_checkpoint
        Scheduler.verify_task = lambda self_obj, t: fake_verify(t)
        Scheduler.execute_task = lambda self_obj, t, feedback='': fake_execute(t, feedback)
        try:
            scheduler.run_task(task)
            after_first = state.get_task('T-loop')
            scheduler.run_task(after_first)
            after_second = state.get_task('T-loop')
        finally:
            safety_module.git_checkpoint = original_checkpoint
            Scheduler.verify_task = original_verify
            Scheduler.execute_task = original_execute
        check('第一次返工后任务回到 pending',
              after_first.phase == 'pending', after_first.phase)
        check('返工用尽后任务变 blocked',
              after_second.phase == 'blocked', after_second.phase)
        state.close()


def main():
    for case in (test_extract_json, test_estimate_cost,
                 test_review_fallback_on_agent_error,
                 test_task_flow_reworks_then_blocks):
        print(f'--- {case.__name__}')
        case()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('DUAL AGENT PROTOCOL: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
