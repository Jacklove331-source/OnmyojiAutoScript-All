# This Python file uses the following encoding: utf-8

"""调度器 v2 机制测试（不调用真实模型）：

供应商计费、分供应商预算/24h 窗口、执行锁、DSH 交付后补审核、
机器验证否决 PASS、疑难升级只发生一次、知识缓存、旧 evidence 兼容、
审核模型分级、Claude 辅助任务路径白名单、环境故障暂停。
"""

import json
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parents[1]))

import cli_agent  # noqa: E402
from config import load_config  # noqa: E402
from scheduler import (BudgetExceeded, EnvironmentPause, ExecutorBusy,  # noqa: E402
                       Scheduler, EXECUTOR_OWNER)
from state import State, now_text  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


class R:
    def __init__(self, ok=True, text='', provider='deepseek', model='m', cost_cny=0.0,
                 cost_usd=0.0, error='', error_kind='', usage=None):
        self.ok = ok
        self.text = text
        self.provider = provider
        self.model = model
        self.purpose = ''
        self.cost_cny = cost_cny
        self.cost_usd = cost_usd
        self.error = error
        self.error_kind = error_kind
        self.usage = usage or {}
        self.duration_s = 1.0
        self.denials = []


class FakeAgent:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def run(self, prompt, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if self.replies else R(text='{}')
        return reply


def make(folder, budget=None):
    config = load_config()
    if budget:
        for key, value in budget.items():
            if key == 'providers':
                config.raw['budget'].setdefault('providers', {}).update(value)
            else:
                config.raw['budget'][key] = value
    state = State(Path(folder), config.project_root)
    state.set_meta('session_start', now_text())
    state.set_meta('session_started_epoch', time.time())
    state.set_meta('v2_started_at', now_text())
    scheduler = Scheduler(config, state, dry_run=True)
    return scheduler, state, config


def test_cost_accounting():
    config = load_config()

    class Dummy(cli_agent.CliAgent):
        def __init__(self, cfg):
            self.config = cfg
            self.prices = cfg.price_per_mtok()

    agent = Dummy(config)
    cny, usd = agent.cost_of('agent_a', {'total_cost_usd': 0.1},
                             {'input_tokens': 10, 'output_tokens': 10})
    check('Claude 费用用 CLI 报告的美元价 × 汇率',
          abs(usd - 0.1) < 1e-9 and abs(cny - 0.1 * config.usd_to_cny()) < 1e-6,
          f'{cny} {usd}')
    cny2, _ = agent.cost_of('agent_b', {}, {'input_tokens': 1_000_000,
                                           'cache_creation_input_tokens': 0,
                                           'cache_read_input_tokens': 0,
                                           'output_tokens': 0})
    check('DeepSeek 费用按 ¥/百万 token', abs(cny2 - 4.0) < 1e-6, str(cny2))
    check('角色分属不同供应商',
          config.provider_for('agent_a') == 'anthropic'
          and config.provider_for('agent_b') == 'deepseek')
    check('A 用 claude-anthropic.cmd、B 用 claude.CMD',
          config.cli_for('agent_a').lower().endswith('claude-anthropic.cmd')
          and config.cli_for('agent_b').lower().endswith('claude.cmd'))
    check('错误分类：代理 403 → env',
          cli_agent.classify_error('API Error: 403 Request not allowed') == 'env')
    check('错误分类：未登录 → fatal',
          cli_agent.classify_error('Not logged in') == 'fatal')


def test_provider_caps():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state, _ = make(folder, {'providers': {
            'anthropic': {'max_usd_total': 100.0, 'max_usd_24h': 1.0}}})
        state.record_run('', 'agent_a', now_text(),
                         R(provider='anthropic', cost_usd=1.5, cost_cny=10.0))
        try:
            scheduler.check_budget()
            check('Claude 24h 上限触发', False, '没有异常')
        except BudgetExceeded as error:
            check('Claude 24h 上限触发 daily（可自动续跑）', error.kind == 'daily', error.kind)
        status = scheduler.loop(max_iterations=2)
        check('loop 返回 paused_daily', status == 'paused_daily', status)
        check('写入 resume_after', float(state.get_meta('resume_after', 0)) > time.time())
        state.close()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state, _ = make(folder, {'providers': {
            'deepseek': {'max_cny_total': 2.0, 'max_cny_24h': 100.0}}})
        state.record_run('', 'agent_b', now_text(), R(provider='deepseek', cost_cny=2.5))
        status = scheduler.loop(max_iterations=2)
        check('DeepSeek 总额到顶 → paused_budget（终态）', status == 'paused_budget', status)
        state.close()


def test_executor_lock():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state, _ = make(folder)
        ok, _ = state.acquire_lock('executor', 'dsh-deepseek', 3600, 'T-a')
        check('DSH 抢到执行锁', ok)
        ok2, info = state.acquire_lock('executor', EXECUTOR_OWNER, 3600, 'T-b')
        check('调度器拿不到被占用的锁', not ok2 and info.get('owner') == 'dsh-deepseek')
        task = state.add_task({'id': 'T-b', 'title': 'b'})
        try:
            scheduler.run_task(task)
            check('锁被占用时 run_task 抛 ExecutorBusy', False)
        except ExecutorBusy:
            check('锁被占用时 run_task 抛 ExecutorBusy', True)
        state.release_lock('executor', 'dsh-deepseek')
        ok3, _ = state.acquire_lock('executor', EXECUTOR_OWNER, -1, 'T-b')
        ok4, _ = state.acquire_lock('executor', 'dsh-deepseek', 3600, 'T-c')
        check('过期锁可以被抢', ok3 and ok4)
        state.close()


def _patch(scheduler_cls, verify_rows):
    import safety as safety_module
    saved = (safety_module.git_checkpoint, scheduler_cls.verify_task)
    safety_module.git_checkpoint = lambda *a, **k: {'committed': False, 'sha': 'abc', 'tag': ''}
    scheduler_cls.verify_task = lambda self_obj, t, root=None: verify_rows
    return saved


def _unpatch(scheduler_cls, saved):
    import safety as safety_module
    safety_module.git_checkpoint, scheduler_cls.verify_task = saved


def test_verifying_resume_and_machine_veto():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state, _ = make(folder)
        scheduler.dry_run = False
        fake = FakeAgent([R(text='{"verdict":"PASS","notes":"ok"}', provider='anthropic',
                            model='claude-sonnet-5-5')])
        scheduler.agent = fake
        task = state.add_task({'id': 'T-v', 'title': 'v', 'verify': ['AC05S']})
        state.update_task('T-v', phase='verifying',
                          agent_b_summary=json.dumps({'ok': True, 'summary': 'dsh did it'}))
        saved = _patch(Scheduler, [{'check': 'AC05S', 'ok': False, 'detail': 'x'}])
        try:
            scheduler.run_task(state.get_task('T-v'))
        finally:
            _unpatch(Scheduler, saved)
        after = state.get_task('T-v')
        check('verifying 任务只补审核、不重新执行（只调用 1 次模型）', len(fake.calls) == 1,
              str(len(fake.calls)))
        check('机器验证失败时 Claude 的 PASS 被否决', after.phase != 'accepted', after.phase)
        reviews = state.reviews_for('T-v')
        check('审核记录写入 reviews（含供应商）',
              reviews and reviews[-1]['provider'] == 'anthropic', str(reviews))
        check('执行锁在任务结束后释放', not state.lock_info('executor'))
        state.close()


def test_rework_then_pass_and_knowledge():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state, _ = make(folder)
        scheduler.dry_run = False
        fake = FakeAgent([
            R(text='{"summary":"v1"}'),
            R(text='{"verdict":"REWORK","notes":"补测试"}', provider='anthropic'),
            R(text='{"summary":"v2"}'),
            R(text='{"verdict":"PASS","notes":"ok","knowledge":"过渡位限购有效"}',
              provider='anthropic', model='claude-opus-5-5'),
        ])
        scheduler.agent = fake
        state.add_task({'id': 'T-rw', 'title': 'rw', 'verify': ['AC05S'], 'max_rework': 1})
        saved = _patch(Scheduler, [{'check': 'AC05S', 'ok': True, 'detail': 'ok'}])
        try:
            scheduler.run_task(state.get_task('T-rw'))
            first = state.get_task('T-rw')
            scheduler.run_task(first)
        finally:
            _unpatch(Scheduler, saved)
        after = state.get_task('T-rw')
        check('第一次 REWORK 后回到 pending', first.phase == 'pending', first.phase)
        check('返工后第二次审核通过', after.phase == 'accepted', after.phase)
        check('返工反馈带进执行提示词',
              '补测试' in json.dumps(first.evidence, ensure_ascii=False))
        check('第二轮审核升级到 review_hard（attempts≥2）',
              fake.calls[3].get('purpose') == 'review_hard', str(fake.calls[3]))
        import acceptance
        cycle = acceptance.check_rework_cycle(scheduler.config, state)
        check('AC02R 识别到返工闭环', cycle.ok, cycle.detail)
        knowledge = state.knowledge()
        check('PASS 结论进入知识缓存', knowledge and '过渡位' in knowledge[-1]['note'])
        state.close()


def test_escalation_once():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state, _ = make(folder)
        scheduler.dry_run = False
        escalation = {'diagnosis': 'x.py:10 条件反了', 'decision': 'retry',
                      'tasks': [{'title': '按方案修', 'goal': 'g', 'guidance': ['改 x.py:10'],
                                 'verify': ['AC08', 'AC99']}]}
        fake = FakeAgent([
            R(text='{"summary":"v1"}'),
            R(text='{"verdict":"REWORK","notes":"不对"}', provider='anthropic'),
            R(text=json.dumps(escalation, ensure_ascii=False), provider='anthropic'),
        ])
        scheduler.agent = fake
        state.add_task({'id': 'T-esc', 'title': 'esc', 'verify': ['AC05S'], 'max_rework': 0})
        saved = _patch(Scheduler, [{'check': 'AC05S', 'ok': True, 'detail': 'ok'}])
        try:
            scheduler.run_task(state.get_task('T-esc'))
        finally:
            _unpatch(Scheduler, saved)
        child = state.get_task('T-esc-E1')
        check('返工用尽 → Claude 疑难升级生成新任务', child is not None)
        check('新任务带 guidance 与 escalated_from',
              child and child.card.get('guidance') and child.card.get('escalated_from') == 'T-esc')
        check('非法 verify 项被过滤', child and child.card.get('verify') == ['AC08'],
              str(child.card.get('verify') if child else None))
        check('升级调用用 escalate 用途', fake.calls[2].get('purpose') == 'escalate')
        again = scheduler.escalate(child, {'verdict': 'REWORK'}, [])
        check('升级出来的任务不会再次升级', again == [])
        check('execute 提示词包含疑难方案', '改 x.py:10' in scheduler.execute_prompt(child))
        state.close()


def test_legacy_evidence_list():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config = load_config()
        state = State(Path(folder), config.project_root)
        state.add_task({'id': 'T-old', 'title': 'old'})
        state.update_task('T-old', evidence=json.dumps([{'check': 'x', 'ok': True}]))
        task = state.get_task('T-old')
        check('v1 list 形式的 evidence 不再导致 .get 崩溃',
              isinstance(task.evidence, dict) and 'verify_rows' in task.evidence)
        state.close()


def test_env_pause():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state, config = make(folder)
        scheduler.dry_run = False
        config.raw['limits']['env_failures_before_pause'] = 2
        scheduler.agent = FakeAgent([
            R(ok=False, error='Connection error', error_kind='env', provider='anthropic'),
            R(ok=False, error='Connection error', error_kind='env', provider='anthropic'),
        ])
        scheduler.call_agent('agent_a', 'p', purpose='plan')
        try:
            scheduler.call_agent('agent_a', 'p', purpose='plan')
            check('连续网络故障触发 EnvironmentPause', False)
        except EnvironmentPause:
            check('连续网络故障触发 EnvironmentPause', True)
        state.close()


def test_pause_file():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state, _ = make(folder)
        (Path(folder) / 'PAUSE').write_text('x', encoding='utf-8')
        status = scheduler.loop(max_iterations=2)
        check('PAUSE 文件 → paused_manual', status == 'paused_manual', status)
        state.close()


def test_cards_and_owner():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state, _ = make(folder)
        added = scheduler._add_cards([
            {'id': 'T-c1', 'title': 'research', 'owner': 'claude', 'verify': ['AC10R']},
            {'id': 'T-c2', 'title': 'code', 'owner': 'whoever'},
        ], source='plan')
        check('owner 归一化（claude/deepseek）',
              [task.card['owner'] for task in added] == ['claude', 'deepseek'])
        check('默认 verify = AC05S', added[1].card['verify'] == ['AC05S'])
        dup = scheduler._add_cards([{'id': 'T-c1', 'title': 'dup'}], source='plan')
        check('重复 id 自动改名而不是静默丢弃', dup and dup[0].task_key != 'T-c1')
        state.close()


def test_card_tests_paths():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        scheduler, state, config = make(folder)
        task = state.add_task({'id': 'T-t', 'title': 't', 'tests': [
            'dev_tools/dual_agent/scheduler.py', '../evil.py', 'dev_tools/zz_not_exist_x.py']})
        rows = scheduler.card_test_rows(task, config.project_root)
        check('任务卡测试：调度器自身/越界/不存在 一律判失败',
              len(rows) == 3 and not any(row['ok'] for row in rows), str(rows))
        state.close()


def test_git_lock_hook():
    import git_lock_hook as hook
    main_root = str(hook.MAIN_ROOT)
    held = {'owner': 'scheduler', 'task_key': 'T-x', 'expires': time.time() + 600}
    check('钩子：调度器持锁时拦下手工提交',
          not hook.decide('docs(chess): x', held, main_root)[0])
    check('钩子：autosave / 调度器 / merge 提交放行',
          all(hook.decide(msg, held, main_root)[0] for msg in
              ('[autosave] 2026', 'chore(agent): after T-x', 'merge: y')))
    check('钩子：DSH 自己持锁时放行',
          hook.decide('fix: x', dict(held, owner='dsh-deepseek'), main_root)[0])
    check('钩子：锁过期放行',
          hook.decide('fix: x', dict(held, expires=time.time() - 1), main_root)[0])
    check('钩子：worktree 里的提交不受影响',
          hook.decide('fix: x', held, 'D:/3D/YYS2/oas-wt-claude-aux-not-main')[0])


def main():
    for case in (test_git_lock_hook, test_card_tests_paths, test_cost_accounting, test_provider_caps,
                 test_executor_lock,
                 test_verifying_resume_and_machine_veto, test_rework_then_pass_and_knowledge,
                 test_escalation_once, test_legacy_evidence_list, test_env_pause,
                 test_pause_file, test_cards_and_owner):
        print(f'--- {case.__name__}')
        case()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('DUAL AGENT V2: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
