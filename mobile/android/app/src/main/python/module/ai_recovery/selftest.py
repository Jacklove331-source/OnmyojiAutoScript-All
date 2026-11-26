# This Python file uses the following encoding: utf-8

"""AI 自愈模块离线自检（无设备、无网络）。

运行：
    .\\toolkit\\python.exe -m module.ai_recovery.selftest
    .\\toolkit\\python.exe dev_tools\\ai_recovery_selftest.py

覆盖：结构校验、检测器、预算、知识库、模板候选/验证/晋级/回滚/正式资源替换、
安全动作白名单与边界、编排器五种结局、观察/自动模式、去重与冷却、BaseTask 接入。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from module.ai_recovery.actions import ActionExecutor
from module.ai_recovery.budget import BudgetGuard, Usage
from module.ai_recovery.client import AiUnavailable, AnalyzeResult
from module.ai_recovery.config import AiRecoveryConfig
from module.ai_recovery.detector import ProgressMonitor
from module.ai_recovery.evidence import EvidenceStore
from module.ai_recovery.imageutil import change_ratio, dhash, encode_jpeg
from module.ai_recovery.kb import KnowledgeBase
from module.ai_recovery.orchestrator import AiRecovery
from module.ai_recovery.schema import SchemaError, parse_diagnosis, validate, DIAGNOSIS_SCHEMA
from module.ai_recovery.templates import TemplateLibrary
from module.ai_recovery.types import Action, Anomaly, Diagnosis, Outcome


class Checker:
    def __init__(self):
        self.total = 0
        self.passed = 0
        self.failures: list[str] = []

    def check(self, name: str, condition, detail: str = '') -> None:
        self.total += 1
        if condition:
            self.passed += 1
            print(f'  PASS  {name}')
        else:
            self.failures.append(f'{name} {detail}')
            print(f'  FAIL  {name} {detail}')

    def eq(self, name: str, actual, expected) -> None:
        self.check(name, actual == expected, f'actual={actual!r} expected={expected!r}')

    def summary(self) -> bool:
        print('')
        print(f'AI RECOVERY SELFTEST: {"PASSED" if not self.failures else "FAILED"}')
        print(f'TOTAL {self.total} PASS {self.passed} FAIL {len(self.failures)}')
        for failure in self.failures:
            print(f'  - {failure}')
        return not self.failures


def noisy_screen(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)


def make_screen(seed: int = 0, pattern: bool = True) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = np.full((720, 1280, 3), 40, dtype=np.uint8)
    if pattern:
        image[80:140, 90:250] = (230, 230, 230)
        image[90:130, 100:240] = (30, 30, 30)
        image[600:660, 1000:1200] = (200, 60, 60)
    image[0:720, 600:700] = rng.integers(0, 255, (720, 100, 3), dtype=np.uint8)
    return image


def base_config(**over) -> AiRecoveryConfig:
    data = {
        'enabled': True,
        'mode': 'auto',
        'budget': {'per_incident_calls': 2, 'per_hour_calls': 10, 'per_day_calls': 30,
                   'per_day_cny': 5.0},
        'detector': {'cooldown_s': 0, 'max_incidents_per_key': 5, 'stale_frame_s': 10,
                     'unknown_page_s': 5, 'repeated_click_n': 3},
        'safety': {'max_actions': 4, 'max_click_per_point': 2, 'verify_wait_s': 0.0,
                   'verify_retries': 1, 'verify_change_ratio': 0.01,
                   'forbidden_keywords': ['充值', '购买', '退出游戏'], 'edge_margin_px': 5},
        'knowledge': {'enabled': True, 'min_success_rate': 0.8, 'max_age_days': 30},
        'templates': {'enabled': True, 'auto_apply_to_source': False},
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    cfg = AiRecoveryConfig.from_dict(data, config_name='test')
    cfg.api_key = 'test-key'
    return cfg


class FakeAdapter:
    def __init__(self, image=None, page='unknown', local_ok=False, noop_click=False):
        self.image = make_screen(1) if image is None else image.copy()
        self._page = page
        self.local_ok = local_ok
        self.noop_click = noop_click
        self.clicks: list[tuple[int, int]] = []
        self.task_name = 'Dummy'
        self.screen_size = (1280, 720)
        self.game_version = 'test-1.0'
        self.back_calls = 0

    def screenshot(self):
        return self.image.copy()

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.noop_click:
            return
        shade = 30 + (len(self.clicks) * 70) % 200
        self.image[80:560, 80:700] = (shade, 40, 40)
        self._page = 'page_main'

    def back(self):
        self.back_calls += 1
        self._page = 'page_main'

    def sleep(self, seconds):
        return None

    def close_popup_local(self):
        return False

    def goto_page(self, key):
        if key == 'page_main':
            self._page = 'page_main'
            return True
        return False

    def current_page(self):
        return self._page

    def is_main_page(self):
        return self._page == 'page_main'

    def known_pages(self):
        return ['page_main', 'page_chess', 'page_reward']

    def recent_actions(self):
        return ['click@(1,1)']

    def state_machine(self):
        return 'dummy'

    def notify(self, text):
        return None

    def local_recovery(self, kind):
        return self.local_ok


class FakeClient:
    def __init__(self, diagnosis=None, error=None):
        self.diagnosis = diagnosis
        self.error = error
        self.calls = 0
        self.contexts = []

    def analyze(self, image, context):
        self.calls += 1
        self.contexts.append(context)
        if self.error:
            raise self.error
        return AnalyzeResult(diagnosis=self.diagnosis,
                             usage=Usage(prompt_tokens=100, completion_tokens=50,
                                         cost_cny=0.0005, model='deepseek-flash'),
                             text='{}', latency_s=0.1)


def diagnosis(actions=None, confidence=0.9, risk='low', needs_human=False, template=None,
              page='page_main') -> Diagnosis:
    return Diagnosis(page=page, page_known=True, confidence=confidence, cause='test',
                     actions=actions or [], expected_change='page_main', risk=risk,
                     needs_human=needs_human, template_update=template or {}, raw={})


def test_schema(c: Checker):
    print('\n[schema]')
    good = {
        'page': '庭院', 'page_known': True, 'page_category': 'main', 'confidence': 0.92,
        'cause': '停在庭院', 'risk': 'low',
        'actions': [{'type': 'click', 'x': 0.5, 'y': 0.5, 'text': '确定', 'reason': '关闭'}],
        'expected_change': '进入庭院', 'needs_human': False,
        'template_update': {'needed': True, 'name': 'btn_ok',
                            'region': {'x': 0.1, 'y': 0.1, 'w': 0.1, 'h': 0.06}},
    }
    c.eq('schema valid', validate(good, DIAGNOSIS_SCHEMA), [])
    parsed = parse_diagnosis(good)
    c.eq('schema page', parsed.page, '庭院')
    c.eq('schema actions', len(parsed.actions), 1)

    for name, mutate in [
        ('missing cause', lambda d: d.pop('cause')),
        ('bad risk', lambda d: d.__setitem__('risk', 'extreme')),
        ('bad action type', lambda d: d['actions'][0].__setitem__('type', 'swipe')),
        ('coord out of range', lambda d: d['actions'][0].__setitem__('x', 1.5)),
        ('click without coords', lambda d: d['actions'][0].pop('x')),
        ('confidence too high', lambda d: d.__setitem__('confidence', 2.0)),
    ]:
        payload = json.loads(json.dumps(good))
        mutate(payload)
        try:
            parse_diagnosis(payload)
            c.check(name, False, 'should raise')
        except SchemaError:
            c.check(name, True)

    c.check('max actions clamp', len(parse_diagnosis({**good, 'actions': good['actions'] * 4}).actions) == 4)


def test_detector(c: Checker):
    print('\n[detector]')
    cfg = base_config(detector={'stale_frame_s': 10, 'unknown_page_s': 5, 'repeated_click_n': 3,
                                'cooldown_s': 60, 'repeated_click_window_s': 30})
    now = [0.0]
    monitor = ProgressMonitor(cfg, 'Dummy', clock=lambda: now[0])
    image = make_screen(3)

    monitor.observe(image, 'page_main')
    now[0] = 12
    monitor.observe(image, 'page_main')
    c.check('still frame alone does not trigger', monitor.check() is None)

    for index in range(3):
        now[0] = 20 + index
        monitor.observe(image, 'page_main')
        monitor.note_click(640, 360, 'button')
    now[0] = 24
    monitor.observe(image, 'page_main')
    anomaly = monitor.check()
    c.check('repeated click no effect', anomaly is not None and anomaly.type in ('click_no_effect', 'no_progress'),
            str(anomaly.to_dict() if anomaly else None))
    c.check('cooldown suppresses repeat', monitor.check() is None)

    # 动画/战斗帧持续变化不应误判
    now[0] = 0
    animated = ProgressMonitor(base_config(), 'Dummy', clock=lambda: now[0])
    for index in range(20):
        now[0] = index * 2
        animated.observe(make_screen(seed=index), 'battle')
    c.check('animated battle frames no anomaly', animated.check() is None)

    # 未知页面
    now[0] = 0
    unknown = ProgressMonitor(cfg, 'Dummy', clock=lambda: now[0])
    unknown.observe(make_screen(9), '')
    now[0] = 6
    unknown.observe(make_screen(9), '')
    anomaly = unknown.check()
    c.check('unknown page detected', anomaly is not None and anomaly.type == 'unknown_page')

    # 显式信号
    monitor2 = ProgressMonitor(cfg, 'Dummy', clock=lambda: now[0])
    monitor2.observe(make_screen(4), 'page_main')
    monitor2.note_signal('template_miss', 'I_CHECK_MAIN')
    anomaly = monitor2.check()
    c.check('template miss signal', anomaly is not None and anomaly.type == 'template_miss')


def test_imageutil(c: Checker):
    print('\n[imageutil]')
    a = make_screen(5)
    b = a.copy()
    c.eq('dhash stable', dhash(a), dhash(b))
    c.check('dhash differs', dhash(a) != dhash(noisy_screen(6)))
    c.check('change ratio zero', change_ratio(a, b) < 1e-6)
    b[100:300, 100:300] = 255
    c.check('change ratio detects', change_ratio(a, b) > 0.01)
    jpeg = encode_jpeg(a, max_side=640, quality=60)
    c.check('jpeg encoded', 1000 < len(jpeg) < 400000, str(len(jpeg)))


def test_budget(c: Checker, tmp: Path):
    print('\n[budget]')
    cfg = base_config(budget={'per_incident_calls': 1, 'per_hour_calls': 2, 'per_day_calls': 5,
                              'per_day_cny': 0.001})
    guard = BudgetGuard(cfg, config_name='test', path=tmp / 'budget.json', now=1_000_000.0)
    guard.start_incident()
    c.check('reserve ok', guard.reserve()[0])
    guard.commit(Usage(prompt_tokens=10, completion_tokens=10, cost_cny=0.0006))
    c.check('per incident cap', not guard.reserve()[0])
    guard.start_incident()
    c.check('reserve after incident reset', guard.reserve()[0])
    guard.commit(Usage(prompt_tokens=10, completion_tokens=10, cost_cny=0.0006))
    c.check('daily cost cap', not guard.reserve()[0])
    c.check('state persisted', (tmp / 'budget.json').exists())


def test_kb(c: Checker, tmp: Path):
    print('\n[kb]')
    cfg = base_config()
    kb = KnowledgeBase(cfg, config_name='test', path=tmp / 'knowledge.jsonl')
    anomaly = Anomaly(type='unknown_page', task='Dummy', page='')
    image = make_screen(7)
    diag = diagnosis(actions=[Action(type='click', x=0.5, y=0.5)])
    entry = kb.record(anomaly, diag, [{'type': 'click', 'x': 0.5, 'y': 0.5}], True,
                      dhash(image), (1280, 720), 'v1')
    c.eq('kb success count', entry.success, 1)
    found = kb.lookup(anomaly, dhash(image), (1280, 720), 'v1')
    c.check('kb lookup', found is not None and found.id == entry.id)
    other = Anomaly(type='unknown_page', task='Dummy', page='')
    c.check('kb env mismatch rejected', kb.lookup(other, dhash(image), (1920, 1080), 'v1') is None)
    c.check('kb different hash rejected', kb.lookup(other, dhash(noisy_screen(8)), (1280, 720), 'v1') is None)
    kb.mark_reuse(entry, False)
    c.check('kb failure lowers rate', kb.lookup(other, dhash(image), (1280, 720), 'v1') is None)
    reopened = KnowledgeBase(cfg, config_name='test', path=tmp / 'knowledge.jsonl')
    c.check('kb persisted', any(item.id == entry.id for item in reopened.entries.values()))
    kb.mark_seen('abc')
    c.check('dedupe seen', kb.was_seen('abc'))


def test_templates(c: Checker, tmp: Path):
    print('\n[template]')
    cfg = base_config(templates={'dir': str(tmp / 'templates'), 'min_match_score': 0.85,
                                 'min_negative_margin': 0.08, 'max_false_positive': 0.05})
    lib = TemplateLibrary(cfg, config_name='test', root=tmp / 'templates')
    image = make_screen(11)
    region = {'x': 90 / 1280, 'y': 80 / 720, 'w': 160 / 1280, 'h': 60 / 720}
    candidate = lib.create_candidate(image, region, 'Btn_Test', 'Dummy', source='test')
    c.check('candidate file', candidate.path.exists())
    negative = make_screen(12)
    negative[80:140, 90:250] = (10, 200, 10)
    negative[90:130, 100:240] = (240, 10, 10)
    report = lib.validate(candidate, [image], [negative])
    c.check('template validation pass', report.passed, report.reason + ' ' + str(report.detail))
    bad = lib.validate(candidate, [np.full((720, 1280, 3), 90, dtype=np.uint8)], [negative])
    c.check('template rejects missing positive', not bad.passed)
    manifest = lib.promote(candidate, report, game_version='v1')
    c.eq('promote version', manifest['active_version'], 1)
    c.check('active path', Path(lib.active_path('Dummy', 'Btn_Test')).exists())

    region2 = {'x': 1000 / 1280, 'y': 600 / 720, 'w': 200 / 1280, 'h': 60 / 720}
    candidate2 = lib.create_candidate(image, region2, 'Btn_Test', 'Dummy')
    report2 = lib.validate(candidate2, [image], [negative])
    if report2.passed:
        lib.promote(candidate2, report2)
        c.eq('rollback to previous', lib.rollback('Dummy', 'Btn_Test'), 1)

    source = tmp / 'source_template.png'
    from module.ai_recovery.imageutil import save_png
    save_png(source, make_screen(99))
    original_bytes = source.read_bytes()
    applied = lib.apply_to_source('Dummy', 'Btn_Test', source)
    c.check('apply replaced source', source.read_bytes() != original_bytes)
    c.check('backup recorded', Path(applied['backup']).exists())
    lib.restore_source(applied['backup'], source)
    c.check('restore source', source.read_bytes() == original_bytes)

    c.check('template list', len(lib.list_all()) >= 1)
    hit = lib.match_active(image)
    c.check('learned template matches live frame', hit is not None and hit['score'] >= 0.9,
            str(hit))
    miss = lib.match_active(noisy_screen(21))
    c.check('learned template rejects other frame', miss is None or miss['score'] < 0.86, str(miss))


def test_actions(c: Checker):
    print('\n[actions]')
    cfg = base_config()
    adapter = FakeAdapter()
    executor = ActionExecutor(cfg, adapter)
    result = executor.execute([Action(type='click', x=0.5, y=0.5)])
    c.check('click succeeds', result.ok and len(adapter.clicks) == 1, result.message)

    adapter = FakeAdapter()
    result = executor = ActionExecutor(cfg, adapter).execute([Action(type='click', x=0.99, y=0.99)])
    c.check('forbidden zone blocked', not result.ok and not adapter.clicks)

    adapter = FakeAdapter()
    result = ActionExecutor(cfg, adapter).execute([Action(type='click', x=0.5, y=0.5, text='充值')])
    c.check('forbidden text blocked', not result.ok and not adapter.clicks)

    adapter = FakeAdapter()
    result = ActionExecutor(cfg, adapter).execute([Action(type='click', x=0.001, y=0.001)])
    c.check('edge blocked', not result.ok and not adapter.clicks)

    adapter = FakeAdapter()
    result = ActionExecutor(cfg, adapter).execute([Action(type='swipe')])
    c.check('action whitelist', not result.ok)

    adapter = FakeAdapter(noop_click=True)
    result = ActionExecutor(cfg, adapter).execute([Action(type='click', x=0.5, y=0.5)])
    c.check('unverified click stops', not result.ok)

    adapter = FakeAdapter()
    result = ActionExecutor(cfg, adapter).execute([Action(type='click', x=0.5, y=0.5)] * 3)
    c.check('max clicks per point', not result.ok and len(adapter.clicks) <= 2)

    adapter = FakeAdapter(page='page_main')
    result = ActionExecutor(cfg, adapter).execute([Action(type='back')])
    c.check('back blocked on main page', not result.ok and adapter.back_calls == 0)

    adapter = FakeAdapter()
    result = ActionExecutor(cfg, adapter).execute([Action(type='wait', wait_ms=200)])
    c.check('wait does not require visual change', result.ok, result.message)

    adapter = FakeAdapter()
    result = ActionExecutor(cfg, adapter).execute([Action(type='goto_page', page_key='page_main')])
    c.check('goto page allowed', result.ok)

    adapter = FakeAdapter()
    result = ActionExecutor(cfg, adapter).execute([Action(type='goto_page', page_key='page_evil')])
    c.check('goto unknown page blocked', not result.ok)


def run_orchestrator(cfg, adapter, client, tmp: Path) -> tuple:
    kb = KnowledgeBase(cfg, config_name='test', path=tmp / f'kb_{id(adapter)}.jsonl')
    templates = TemplateLibrary(cfg, config_name='test', root=tmp / 'tpl')
    evidence = EvidenceStore('test', root=tmp / 'evidence')
    recovery = AiRecovery(cfg, adapter, client=client, kb=kb, templates=templates, evidence=evidence)
    return recovery, kb


def test_orchestrator(c: Checker, tmp: Path):
    print('\n[orchestrator]')
    from module.ai_recovery import orchestrator
    orchestrator._STATE.clear()

    # 关闭
    cfg = base_config(enabled=False)
    adapter = FakeAdapter()
    client = FakeClient(diagnosis())
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='unknown_page', task='Dummy'))
    c.check('disabled -> skipped', result.outcome == Outcome.SKIPPED and client.calls == 0)

    # 观察模式
    cfg = base_config(mode='observe')
    adapter = FakeAdapter()
    client = FakeClient(diagnosis(actions=[Action(type='click', x=0.5, y=0.5)]))
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='unknown_page', task='Dummy'))
    c.check('observe -> no click', result.outcome == Outcome.OBSERVED and not adapter.clicks)
    c.check('observe calls api', client.calls == 1)

    # 自动模式成功
    orchestrator._STATE.clear()
    cfg = base_config()
    adapter = FakeAdapter()
    client = FakeClient(diagnosis(actions=[Action(type='click', x=0.5, y=0.5)]))
    recovery, kb = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='unknown_page', task='Dummy'))
    c.check('auto -> recovered', result.outcome == Outcome.RECOVERED and len(adapter.clicks) == 1,
            result.message)
    c.check('auto records kb', len(kb.entries) == 1)
    recorded = list(kb.entries.values())[0]
    c.check('kb stores replayable plan',
            bool(recorded.actions) and recorded.actions[0].get('type') == 'click'
            and recorded.actions[0].get('x') == 0.5, str(recorded.actions))

    # 低置信度
    orchestrator._STATE.clear()
    cfg = base_config()
    adapter = FakeAdapter()
    client = FakeClient(diagnosis(actions=[Action(type='click', x=0.5, y=0.5)], confidence=0.3))
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='unknown_page', task='Dummy'))
    c.check('low confidence -> needs human', result.outcome == Outcome.NEEDS_HUMAN and not adapter.clicks)

    # 高风险
    orchestrator._STATE.clear()
    cfg = base_config()
    adapter = FakeAdapter()
    client = FakeClient(diagnosis(actions=[Action(type='click', x=0.5, y=0.5)], risk='high'))
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='unknown_page', task='Dummy'))
    c.check('high risk -> needs human', result.outcome == Outcome.NEEDS_HUMAN and not adapter.clicks)

    # API 不可用
    orchestrator._STATE.clear()
    cfg = base_config()
    adapter = FakeAdapter()
    client = FakeClient(error=AiUnavailable('offline'))
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='unknown_page', task='Dummy'))
    c.check('api unavailable -> unavailable', result.outcome == Outcome.UNAVAILABLE)

    # 设备类异常先本地
    orchestrator._STATE.clear()
    cfg = base_config()
    adapter = FakeAdapter(local_ok=True)
    client = FakeClient(diagnosis())
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='adb_offline', task='Dummy'))
    c.check('local first recovers', result.outcome == Outcome.RECOVERED and client.calls == 0)

    # 预算为 0
    orchestrator._STATE.clear()
    cfg = base_config(budget={'per_incident_calls': 0})
    adapter = FakeAdapter()
    client = FakeClient(diagnosis())
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='unknown_page', task='Dummy'))
    c.check('budget zero -> skipped', result.outcome == Outcome.SKIPPED and client.calls == 0)

    # 知识库复用
    orchestrator._STATE.clear()
    cfg = base_config()
    adapter = FakeAdapter()
    client = FakeClient(error=RuntimeError('should not call'))
    recovery, kb = run_orchestrator(cfg, adapter, client, tmp)
    anomaly = Anomaly(type='unknown_page', task='Dummy', page='')
    kb.record(anomaly, diagnosis(), [{'type': 'click', 'x': 0.5, 'y': 0.5}], True,
              dhash(adapter.image), (1280, 720), 'test-1.0')
    result = recovery.handle(anomaly)
    c.check('kb replay without api', result.outcome == Outcome.RECOVERED and client.calls == 0)

    # 截图去重：第一次成功后再遇到完全相同的截图
    orchestrator._STATE.clear()
    cfg = base_config(detector={'cooldown_s': 0, 'max_incidents_per_key': 5,
                                'stale_frame_s': 10, 'unknown_page_s': 5})
    adapter = FakeAdapter(noop_click=True)
    client = FakeClient(diagnosis(actions=[Action(type='click', x=0.5, y=0.5)]))
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    first = recovery.handle(Anomaly(type='unknown_page', task='Dummy', page='unknown'))
    second = recovery.handle(Anomaly(type='unknown_page', task='Dummy', page='unknown'))
    c.check('duplicate screenshot skipped', second.outcome == Outcome.SKIPPED, second.message)
    c.check('duplicate no extra api', client.calls == 1)

    # 验证失败 -> stopped
    orchestrator._STATE.clear()
    cfg = base_config()
    adapter = FakeAdapter(noop_click=True)
    client = FakeClient(diagnosis(actions=[Action(type='click', x=0.5, y=0.5)]))
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='no_progress', task='Dummy'))
    c.check('unverified -> stopped', result.outcome == Outcome.STOPPED)

    # 目标区域裁剪（省 token）：带 focus 的模板异常只发局部图
    orchestrator._STATE.clear()
    cfg = base_config(mode='observe')
    adapter = FakeAdapter()
    client = FakeClient(diagnosis(actions=[]))
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='template_miss', task='Dummy',
                                     evidence={'focus': {'x': 0.1, 'y': 0.1, 'w': 0.2, 'h': 0.1}}))
    c.check('target-region crop sent', client.contexts[-1].get('view') == 'target_crop',
            str(client.contexts[-1].get('view')))

    # 模板候选（观察模式，生成但不晋级）
    orchestrator._STATE.clear()
    cfg = base_config(mode='observe', templates={'dir': str(tmp / 'tpl2')})
    adapter = FakeAdapter()
    template = {'needed': True, 'name': 'BtnObserve',
                'region': {'x': 90 / 1280, 'y': 80 / 720, 'w': 160 / 1280, 'h': 60 / 720}}
    client = FakeClient(diagnosis(actions=[], template=template))
    recovery, _ = run_orchestrator(cfg, adapter, client, tmp)
    result = recovery.handle(Anomaly(type='template_miss', task='Dummy'))
    c.check('observe saved candidate', any('template' in str(item) for item in result.actions_done),
            str(result.actions_done))
    manifest = tmp / 'tpl2' / 'Dummy' / 'BtnObserve' / 'manifest.json'
    c.check('observe did not promote', not manifest.exists())


def test_integration(c: Checker, tmp: Path):
    print('\n[integration]')
    from module.ai_recovery.integration import AiRecoveryMixin

    config_path = tmp / 'ai.yaml'
    config_path.write_text('enabled: false\nmode: observe\n', encoding='utf-8')
    previous = os.environ.get('OAS_AI_CONFIG')
    os.environ['OAS_AI_CONFIG'] = str(config_path)
    try:
        class DummyTask(AiRecoveryMixin):
            def __init__(self):
                self.device = SimpleNamespace(image=make_screen(2))
                self.config = SimpleNamespace(config_name='test')

            def get_task_name(self):
                return 'DummyTask'

        task = DummyTask()
        task.ai_tick()
        c.check('disabled tick no-op', task._ai_last_result is None)
        c.check('disabled config', task.ai_config().enabled is False)
        c.check('unknown page recovery disabled', task.ai_recover_unknown_page() is False)
    finally:
        if previous is None:
            os.environ.pop('OAS_AI_CONFIG', None)
        else:
            os.environ['OAS_AI_CONFIG'] = previous


def main() -> int:
    print('OAS AI RECOVERY SELFTEST')
    tmp = Path(tempfile.mkdtemp(prefix='oas_ai_selftest_'))
    old_log = os.environ.get('OAS_AI_LOG_DIR')
    os.environ['OAS_AI_LOG_DIR'] = str(tmp / 'logs')
    checker = Checker()
    try:
        test_schema(checker)
        test_detector(checker)
        test_imageutil(checker)
        test_budget(checker, tmp)
        test_kb(checker, tmp)
        test_templates(checker, tmp)
        test_actions(checker)
        test_orchestrator(checker, tmp)
        test_integration(checker, tmp)
    finally:
        if old_log is None:
            os.environ.pop('OAS_AI_LOG_DIR', None)
        else:
            os.environ['OAS_AI_LOG_DIR'] = old_log
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if checker.summary() else 1


if __name__ == '__main__':
    sys.exit(main())

