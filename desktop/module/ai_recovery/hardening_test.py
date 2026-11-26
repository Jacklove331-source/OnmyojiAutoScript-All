"""Offline behavioral regressions for recovery, learning, isolation and request limits.

Run: toolkit\\python.exe -X utf8 -B -m module.ai_recovery.hardening_test
No real model calls or emulator input are made.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import requests

from module.ai_recovery import client as client_module, orchestrator
from module.ai_recovery.actions import ActionExecutor, ActionRecord, ExecResult
from module.ai_recovery.budget import BudgetGuard, Usage
from module.ai_recovery.client import AiUnavailable, AnalyzeResult, DeepSeekClient
from module.ai_recovery.detector import ProgressMonitor
from module.ai_recovery.integration import AiRecoveryMixin, TaskAdapter
from module.ai_recovery.kb import KnowledgeBase
from module.ai_recovery.reporting import health_report, pending_repairs
from module.ai_recovery.runtime import RecoveryLease, RecoveryStateStore
from module.ai_recovery.schema import SchemaError, parse_diagnosis
from module.ai_recovery.selftest import (FakeAdapter, FakeClient, base_config,
                                       diagnosis, make_screen, noisy_screen, run_orchestrator)
from module.ai_recovery.templates import TemplateLibrary
from module.ai_recovery.types import Action, Anomaly, Diagnosis, Outcome
from module.ai_recovery.verification import verify_recovery


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp_context = tempfile.TemporaryDirectory(prefix='oas_ai_regression_')
        self.tmp = Path(self.tmp_context.name)
        self.environment = patch.dict(os.environ, {'OAS_AI_LOG_DIR': str(self.tmp / 'logs')})
        self.environment.start()
        orchestrator._STATE.clear()
        client_module._BREAKER.clear()

    def tearDown(self):
        self.environment.stop()
        self.tmp_context.cleanup()

    def recovery(self, adapter=None, client=None, **config):
        cfg = base_config(**config)
        adapter = adapter if adapter is not None else FakeAdapter()
        client = client if client is not None else FakeClient(diagnosis())
        recovery, kb = run_orchestrator(cfg, adapter, client, self.tmp)
        return cfg, adapter, client, recovery, kb

    def test_unchanged_known_page_wait_is_not_recovered_or_learned(self):
        cfg, adapter, client, recovery, kb = self.recovery(
            FakeAdapter(page='page_main'),
            FakeClient(diagnosis(actions=[Action(type='wait', wait_ms=200)])))
        result = recovery.handle(Anomaly(type='device_stuck', task='Dummy', page='page_main'))
        self.assertEqual(result.outcome, Outcome.STOPPED)
        self.assertEqual(client.calls, 1)
        self.assertFalse(result.verified)
        self.assertFalse(any(e.success for e in kb.entries.values()))

    def test_expected_page_must_match(self):
        adapter = FakeAdapter(page='page_main')
        executed = ExecResult(True, [ActionRecord('click', True)])
        result = verify_recovery(base_config(), adapter, Anomaly('popup'), executed,
                                 page_before='unknown', expected_page='page_chess')
        self.assertFalse(result.verified)
        self.assertEqual(result.reason, 'expected_page_mismatch')

    def test_page_transition_after_recovery_is_progress_not_failure(self):
        # 2026-11 复盘（log/error/1795092647822 EvoZone）：恢复动作之后页面本就在动
        # （battle_prepare → battle → reward），旧实现把过渡态判成 page_not_stable 属误判。
        adapter = FakeAdapter()
        pages = iter(['page_main', 'page_reward'])
        adapter.refresh_page = lambda: next(pages)
        proof = verify_recovery(base_config(), adapter, Anomaly('popup'),
                                 ExecResult(True, [ActionRecord('click', True)]))
        self.assertTrue(proof.verified)
        self.assertEqual(proof.reason, 'page_advancing')
        self.assertGreaterEqual(len(proof.frames), 2)

    def test_all_frames_unrecognized_is_still_failure(self):
        # 放宽只针对「有证据的误判」：所有帧都读不出已知页时仍然是失败
        adapter = FakeAdapter(page='unknown')
        adapter.refresh_page = lambda: ''
        proof = verify_recovery(base_config(), adapter, Anomaly('popup'),
                                 ExecResult(True, [ActionRecord('click', True)]))
        self.assertFalse(proof.verified)
        self.assertEqual(proof.reason, 'page_not_freshly_recognized')
        self.assertTrue(proof.frames)

    def test_task_can_prove_same_page_progress(self):
        adapter = FakeAdapter(page='page_main')
        adapter.verify_task_progress = lambda anomaly, plan: True
        proof = verify_recovery(base_config(), adapter, Anomaly('device_stuck', page='page_main'),
                                 ExecResult(True, [ActionRecord('wait', True)]))
        self.assertTrue(proof.verified)

    def test_failed_refresh_does_not_use_old_navigator_page(self):
        task = SimpleNamespace(config=None, navigator=SimpleNamespace(
            current_page=SimpleNamespace(key='page_main')), get_current_page=lambda **kwargs: None)
        self.assertEqual(TaskAdapter(task).refresh_page(), '')

    def test_ocr_actual_target_obeys_forbidden_zone(self):
        adapter = FakeAdapter()
        with patch('module.ai_recovery.actions._find_text_anchor', return_value=(1216, 36)), \
                patch.object(ActionExecutor, '_danger_text', return_value=''):
            result = ActionExecutor(base_config(), adapter).execute([
                Action(type='click', x=.7, y=.3, text='关闭')])
        self.assertFalse(result.ok)
        self.assertFalse(adapter.clicks)

    def test_crop_transforms_click_and_template_region(self):
        cfg, adapter, client, recovery, kb = self.recovery()
        plan = diagnosis(actions=[Action('click', x=.5, y=.5)], template={
            'needed': True, 'name': 'test', 'region': {'x': .1, 'y': .2, 'w': .3, 'h': .4}})
        mapped = recovery._map_plan(plan, (400, 100, 600, 400), (1280, 720))
        self.assertAlmostEqual(mapped.actions[0].x, 700 / 1280)
        self.assertAlmostEqual(mapped.actions[0].y, 300 / 720)
        self.assertAlmostEqual(mapped.template_update['region']['x'], 460 / 1280)
        self.assertAlmostEqual(mapped.template_update['region']['h'], 160 / 720)
        self.assertEqual(plan.actions[0].x, .5)

    def test_nonfinite_confidence_and_coordinates_rejected(self):
        for value in [float('nan'), float('inf'), -float('inf')]:
            payload = {'page': 'main', 'page_known': True, 'confidence': value,
                       'cause': 'test', 'risk': 'low', 'actions': []}
            with self.assertRaises(SchemaError):
                parse_diagnosis(payload)
        adapter = FakeAdapter()
        result = ActionExecutor(base_config(), adapter).execute([Action('click', x=float('nan'), y=.5)])
        self.assertFalse(result.ok)
        self.assertFalse(adapter.clicks)

    def test_observe_mode_does_not_reconnect_device(self):
        adapter = FakeAdapter(local_ok=True)
        adapter.local_recovery = unittest.mock.Mock(return_value=True)
        cfg, adapter, client, recovery, kb = self.recovery(adapter, mode='observe')
        self.assertEqual(recovery.handle(Anomaly('adb_offline')).outcome, Outcome.OBSERVED)
        adapter.local_recovery.assert_not_called()

    def test_replan_uses_updated_failure_context_and_can_recover(self):
        class RetryAdapter(FakeAdapter):
            def click(self, x, y):
                if x < 500:
                    self.clicks.append((x, y))
                else:
                    super().click(x, y)

        class RetryClient(FakeClient):
            def analyze(self, jpeg, context):
                self.diagnosis = diagnosis(actions=[Action('click', x=.3 if not self.calls else .6, y=.5)])
                return super().analyze(jpeg, context)

        cfg, adapter, client, recovery, kb = self.recovery(RetryAdapter(), RetryClient())
        result = recovery.handle(Anomaly('unknown_page', task='Dummy'))
        self.assertEqual(result.outcome, Outcome.RECOVERED)
        self.assertEqual(client.calls, 2)
        self.assertTrue(client.contexts[1]['attempted_recoveries'])
        self.assertEqual([x for x, y in adapter.clicks], [384, 768])

    def test_replan_does_not_exceed_total_action_limit(self):
        cfg, adapter, client, recovery, kb = self.recovery(
            FakeAdapter(noop_click=True), FakeClient(diagnosis(actions=[Action('click', x=.5, y=.5)])),
            safety={'max_actions': 1, 'replan_attempts': 2})
        result = recovery.handle(Anomaly('unknown_page', task='Dummy'))
        self.assertEqual(result.outcome, Outcome.STOPPED)
        self.assertEqual(len(adapter.clicks), 1)
        self.assertEqual(client.calls, 1)

    def test_failed_kb_replay_recaptures_new_pixels(self):
        class UnknownAfterClick(FakeAdapter):
            def click(self, x, y):
                super().click(x, y)
                self._page = 'unknown'

        class InspectClient(FakeClient):
            def analyze(self, jpeg, context):
                self.mean = float(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).mean())
                return super().analyze(jpeg, context)

        adapter = UnknownAfterClick(image=np.full((720, 1280, 3), 20, np.uint8))
        cfg, adapter, client, recovery, kb = self.recovery(adapter, InspectClient(diagnosis(actions=[])))
        from module.ai_recovery.imageutil import dhash
        anomaly = Anomaly('unknown_page', task='Dummy')
        kb.record(anomaly, diagnosis(), [{'type': 'click', 'x': .5, 'y': .5}], True,
                  dhash(adapter.image), (1280, 720), 'test-1.0')
        recovery.handle(anomaly)
        self.assertAlmostEqual(client.mean, float(adapter.image.mean()), delta=2)

    def test_knowledge_is_account_scoped_and_legacy_is_not_auto_replayed(self):
        from module.ai_recovery.imageutil import dhash
        cfg = base_config()
        kb = KnowledgeBase(cfg, config_name='oas1', path=self.tmp / 'shared.jsonl')
        anomaly = Anomaly('unknown_page', task='Dummy')
        image = make_screen()
        entry = kb.record(anomaly, diagnosis(), [], True, dhash(image), (1280, 720))
        other = KnowledgeBase(cfg, config_name='oas3', path=kb.path)
        self.assertIsNone(other.lookup(anomaly, dhash(image), (1280, 720)))
        entry.verification_version = 0
        self.assertIsNone(kb.lookup(anomaly, dhash(image), (1280, 720)))

    def test_api_failure_has_evidence_and_pending_repair(self):
        cfg, adapter, client, recovery, kb = self.recovery(client=FakeClient(error=AiUnavailable('offline')))
        result = recovery.handle(Anomaly('template_miss', task='Dummy'))
        folder = Path(result.evidence_dir)
        self.assertTrue((folder / 'result.json').exists())
        self.assertTrue((folder / 'repair_request.json').exists())
        # 证据是 UTF-8 写的（ensure_ascii=False），zh-CN Windows 默认 GBK 读不了，显式指定
        self.assertEqual(json.loads((folder / 'result.json').read_text(encoding='utf-8'))['outcome'], 'unavailable')

    def test_device_lease_is_exclusive_and_released_after_exception(self):
        with self.assertRaises(RuntimeError):
            with RecoveryLease(self.tmp, 'device') as first:
                self.assertTrue(first.acquired)
                with RecoveryLease(self.tmp, 'device') as second:
                    self.assertFalse(second.acquired)
                raise RuntimeError('test')
        with RecoveryLease(self.tmp, 'device') as third:
            self.assertTrue(third.acquired)

    def test_failed_requests_consume_persistent_call_limit(self):
        cfg = base_config(budget={'per_day_calls': 2})
        first = BudgetGuard(cfg, path=self.tmp / 'budget.json')
        second = BudgetGuard(cfg, path=first.path)
        self.assertTrue(first.reserve()[0])
        self.assertTrue(second.reserve()[0])
        third = BudgetGuard(cfg, path=first.path)
        self.assertFalse(third.reserve()[0])
        self.assertEqual(third.state.calls_today, 2)

    def test_budget_commit_does_not_count_success_twice(self):
        guard = BudgetGuard(base_config(), path=self.tmp / 'budget.json')
        self.assertTrue(guard.reserve()[0])
        guard.commit(Usage(prompt_tokens=10, completion_tokens=2, cost_cny=.0002))
        self.assertEqual(guard.state.calls_today, 1)
        self.assertAlmostEqual(guard.state.cost_today, .0002)

    def test_corrupt_budget_fails_closed(self):
        path = self.tmp / 'budget.json'
        path.write_text('not JSON')
        self.assertFalse(BudgetGuard(base_config(), path=path).reserve()[0])
        self.assertEqual(path.read_text(), 'not JSON')

    def test_client_retries_cannot_bypass_request_budget(self):
        cfg = base_config(budget={'per_incident_calls': 2}, api={'max_retries': 4})
        client = DeepSeekClient(cfg)
        client.request_guard = BudgetGuard(cfg, path=self.tmp / 'budget.json')
        error = requests.HTTPError('503', response=SimpleNamespace(status_code=503))
        with patch.object(client, '_post', side_effect=error) as post, \
                patch('module.ai_recovery.client.time.sleep', return_value=None):
            with self.assertRaises(AiUnavailable):
                client.analyze(b'fake-jpeg', {})
        self.assertEqual(post.call_count, 2)
        self.assertEqual(client.request_guard.state.calls_today, 2)

    def test_recognition_template_without_verified_action_is_not_executable(self):
        cfg = base_config()
        library = TemplateLibrary(cfg, config_name='test', root=self.tmp / 'tpl')
        image = make_screen()
        region = {'x': 90 / 1280, 'y': 80 / 720, 'w': 160 / 1280, 'h': 60 / 720}
        candidate = library.create_candidate(image, region, 'button', 'Dummy')
        report = library.validate(candidate, [image], [noisy_screen()])
        self.assertTrue(report.passed)
        library.promote(candidate, report)
        self.assertIsNotNone(library.match_active(image))
        self.assertIsNone(library.match_active(image, task='Dummy', for_execution=True))

    def test_executable_template_scopes_and_failure_retirement(self):
        cfg = base_config()
        library = TemplateLibrary(cfg, config_name='test', root=self.tmp / 'tpl')
        image = make_screen()
        candidate = library.create_candidate(image, {'x': 90/1280, 'y': 80/720,
            'w': 160/1280, 'h': 60/720}, 'button', 'Dummy')
        report = library.validate(candidate, [image], [noisy_screen()])
        library.promote(candidate, report, game_version='v1', replay={'verified': True,
            'source_page': '', 'expected_page': 'page_main', 'action': {'type': 'click'}, 'offset': [.5, .5]})
        self.assertIsNotNone(library.match_active(image, task='Dummy', game_version='v1', for_execution=True))
        self.assertIsNone(library.match_active(image, task='Other', game_version='v1', for_execution=True))
        self.assertIsNone(library.match_active(image, task='Dummy', game_version='v2', for_execution=True))
        library.mark_replay('Dummy', 'button', False)
        library.mark_replay('Dummy', 'button', False)
        self.assertIsNone(library.match_active(image, task='Dummy', game_version='v1', for_execution=True))

    def test_flat_template_and_confusing_full_negative_rejected(self):
        library = TemplateLibrary(base_config(), root=self.tmp / 'tpl')
        blank = np.zeros((720, 1280, 3), np.uint8)
        candidate = library.create_candidate(blank, {'x': .1, 'y': .1, 'w': .2, 'h': .2}, 'flat', 'Dummy')
        self.assertFalse(library.validate(candidate, [blank]).passed)
        image = make_screen()
        candidate = library.create_candidate(image, {'x': 90/1280, 'y': 80/720,
            'w': 160/1280, 'h': 60/720}, 'button', 'Dummy')
        negative = noisy_screen()
        negative[400:460, 800:960] = image[80:140, 90:250]
        self.assertFalse(library.validate(candidate, [image], [negative]).passed)

    def test_click_monitor_receives_events(self):
        cfg = base_config()

        class Task(AiRecoveryMixin):
            def get_task_name(self):
                return 'Dummy'

        task = Task()
        task._ai_cfg = cfg
        task._ai_monitor = ProgressMonitor(cfg, 'Dummy')
        task.navigator = SimpleNamespace(current_page=SimpleNamespace(key='page_main'))
        task.config = SimpleNamespace(config_name='test')
        task.ai_note_click(100, 200, 'button')
        self.assertEqual(len(task._ai_monitor.clicks), 1)
        self.assertEqual(task._ai_monitor.clicks[0].page, 'page_main')

    def test_health_report_and_repairs_are_local_and_account_scoped(self):
        from module.ai_recovery.evidence import EvidenceStore
        from module.ai_recovery.repair_queue import enqueue
        from module.ai_recovery.types import RecoveryResult
        cfg = base_config()
        store = EvidenceStore(cfg.config_name)
        anomaly = Anomaly('template_miss', task='Dummy')
        result = RecoveryResult(Outcome.STOPPED, anomaly.incident_id, evidence_dir=str(self.tmp))
        enqueue(store.root, cfg.config_name, anomaly, result)
        enqueue(store.root, cfg.config_name, anomaly, result)
        self.assertEqual(len(pending_repairs(cfg.config_name)), 1)
        self.assertEqual(pending_repairs(cfg.config_name)[0]['occurrences'], 2)
        self.assertFalse(pending_repairs('other'))
        self.assertEqual(health_report(cfg)['pending_repairs'], 1)

    def test_stale_ai_plan_is_replaced_before_any_click(self):
        adapter = FakeAdapter()

        class SlowClient(FakeClient):
            def analyze(self, jpeg, context):
                if not self.calls:
                    adapter.image[:] = 245
                self.diagnosis = diagnosis(actions=[Action('click', x=.5, y=.5)])
                return super().analyze(jpeg, context)

        cfg, adapter, client, recovery, kb = self.recovery(adapter, SlowClient())
        result = recovery.handle(Anomaly('unknown_page', task='Dummy'))
        self.assertEqual(result.outcome, Outcome.RECOVERED)
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(adapter.clicks), 1)
        self.assertIn('plan_frame_stale', client.contexts[1]['attempted_recoveries'][0])

    def test_each_account_keeps_its_active_template(self):
        cfg = base_config()
        first = TemplateLibrary(cfg, config_name='oas1', root=self.tmp / 'tpl')
        second = TemplateLibrary(cfg, config_name='oas3', root=first.root)
        image = make_screen()
        region = {'x': 90/1280, 'y': 80/720, 'w': 160/1280, 'h': 60/720}
        candidate = first.create_candidate(image, region, 'button', 'Dummy')
        report = first.validate(candidate, [image], [noisy_screen()])
        first.promote(candidate, report)
        second.promote(candidate, report)
        self.assertEqual(first.list_active(refresh=True)[0]['config_name'], 'oas1')
        self.assertEqual(second.list_active(refresh=True)[0]['config_name'], 'oas3')

    def test_repeated_identical_buttons_are_not_clicked_arbitrarily(self):
        library = TemplateLibrary(base_config(), config_name='test', root=self.tmp / 'tpl')
        image = make_screen()
        candidate = library.create_candidate(image, {'x': 90/1280, 'y': 80/720,
            'w': 160/1280, 'h': 60/720}, 'button', 'Dummy')
        report = library.validate(candidate, [image], [noisy_screen()])
        library.promote(candidate, report, replay={'verified': True, 'action': {'type': 'click'},
            'source_page': '', 'expected_page': 'page_main', 'offset': [.5, .5]})
        image[400:460, 800:960] = image[80:140, 90:250]
        self.assertIsNone(library.match_active(image, task='Dummy', for_execution=True))

    def test_verified_learned_button_runs_through_action_and_page_verification(self):
        cfg = base_config(templates={'dir': str(self.tmp / 'tpl')})
        cfg.config_name = 'test'
        library = TemplateLibrary(cfg, config_name='test')
        image = make_screen()
        candidate = library.create_candidate(image, {'x': 90/1280, 'y': 80/720,
            'w': 160/1280, 'h': 60/720}, 'button', 'Dummy')
        report = library.validate(candidate, [image], [noisy_screen()])
        library.promote(candidate, report, replay={'verified': True, 'action': {'type': 'click'},
            'source_page': '', 'expected_page': 'page_main', 'offset': [.5, .5]})

        class Task(AiRecoveryMixin):
            def __init__(self):
                self.fake = FakeAdapter(image=image)
                self.device = SimpleNamespace(image=image.copy())
                self.config = SimpleNamespace(config_name='test')
                self.navigator = SimpleNamespace(current_page=None)
                self.device.screenshot = lambda: setattr(self.device, 'image', self.fake.screenshot())
                self.device.click = lambda x, y, **kwargs: self.fake.click(x, y)

            def get_task_name(self):
                return 'Dummy'

            def ai_config(self, **kwargs):
                return cfg

            def get_current_page(self, **kwargs):
                return None if self.fake._page == 'unknown' else SimpleNamespace(key=self.fake._page)

        task = Task()
        self.assertTrue(task.ai_try_learned_template())
        self.assertEqual(task.fake.clicks, [(170, 110)])
        self.assertFalse(task._ai_busy)

    def test_cooldown_survives_recovery_instance_restart(self):
        cfg, adapter, client, recovery, kb = self.recovery(
            client=FakeClient(diagnosis(actions=[])), detector={'cooldown_s': 180})
        recovery.handle(Anomaly('unknown_page', task='Dummy'))
        orchestrator._STATE.clear()
        restarted = orchestrator.AiRecovery(cfg, adapter, client=client, kb=kb,
            evidence=recovery.evidence, templates=recovery.templates)
        result = restarted.handle(Anomaly('unknown_page', task='Dummy'))
        self.assertEqual(result.outcome, Outcome.SKIPPED)
        self.assertEqual(client.calls, 1)

    def test_attempt_window_expires_instead_of_blocking_task_forever(self):
        store = RecoveryStateStore(self.tmp, 'test')
        store.save('Dummy|stuck', {'last_attempt': 1000, 'incidents': 3, 'attempted': ['failed']})
        self.assertEqual(store.load('Dummy|stuck', now=1100, window_s=3600)['incidents'], 3)
        self.assertEqual(store.load('Dummy|stuck', now=5000, window_s=3600)['incidents'], 0)

    def test_later_verified_success_resolves_pending_repair(self):
        from module.ai_recovery.repair_queue import enqueue
        from module.ai_recovery.types import RecoveryResult
        anomaly = Anomaly('template_miss', task='Dummy')
        root = Path(os.environ['OAS_AI_LOG_DIR'])
        enqueue(root, 'test', anomaly, RecoveryResult(Outcome.STOPPED, anomaly.incident_id))
        self.assertEqual(len(pending_repairs('test')), 1)
        enqueue(root, 'test', anomaly, RecoveryResult(Outcome.RECOVERED, anomaly.incident_id, verified=True))
        self.assertFalse(pending_repairs('test'))

    # ---------- 8 项升级新增覆盖：视觉推进 / ROI 帧新鲜度 / OCR 上下文 ----------

    def test_unknown_page_visual_progress_accepts_stable_change(self):
        before = np.full((720, 1280, 3), 60, np.uint8)
        after = np.full((720, 1280, 3), 180, np.uint8)
        adapter = FakeAdapter(image=after)
        adapter.refresh_page = lambda: ''
        frames = iter([after.copy(), after.copy(), after.copy()])
        adapter.screenshot = lambda: next(frames)
        proof = verify_recovery(base_config(), adapter, Anomaly('unknown_page', task='Restart', page=''),
                                ExecResult(True, [ActionRecord('click', True)]),
                                page_before='', before_image=before)
        self.assertTrue(proof.verified)
        self.assertEqual(proof.reason, 'unknown_page_visual_progress')
        self.assertEqual(proof.page_after, 'unknown')

    def test_unknown_page_visual_progress_rejects_unchanged_or_wait(self):
        before = np.full((720, 1280, 3), 60, np.uint8)
        adapter = FakeAdapter(image=before)
        adapter.refresh_page = lambda: ''
        adapter.screenshot = lambda: before.copy()
        proof = verify_recovery(base_config(), adapter, Anomaly('unknown_page', task='Restart', page=''),
                                ExecResult(True, [ActionRecord('click', True)]),
                                page_before='', before_image=before)
        self.assertFalse(proof.verified)
        self.assertEqual(proof.reason, 'page_not_freshly_recognized')
        # 画面变了但全是等待动作：不算真实推进
        after = np.full((720, 1280, 3), 180, np.uint8)
        wait_adapter = FakeAdapter(image=after)
        wait_adapter.refresh_page = lambda: ''
        wait_adapter.screenshot = lambda: after.copy()
        proof = verify_recovery(base_config(), wait_adapter, Anomaly('unknown_page', task='Restart', page=''),
                                ExecResult(True, [ActionRecord('wait', True)]),
                                page_before='', before_image=before)
        self.assertFalse(proof.verified)
        self.assertEqual(proof.reason, 'page_not_freshly_recognized')

    def test_unknown_page_visual_progress_accepts_dialog_category(self):
        before = np.full((720, 1280, 3), 60, np.uint8)
        after = np.full((720, 1280, 3), 180, np.uint8)
        adapter = FakeAdapter(image=after)
        adapter.refresh_page = lambda: ''
        adapter.screenshot = lambda: after.copy()
        diag = Diagnosis(page='', page_known=False, confidence=0.8, cause='test',
                         actions=[], page_category='dialog')
        proof = verify_recovery(base_config(), adapter, Anomaly('unknown_page', task='Dummy', page=''),
                                ExecResult(True, [ActionRecord('click', True)]),
                                diagnosis=diag, page_before='', before_image=before)
        self.assertTrue(proof.verified)
        self.assertEqual(proof.reason, 'unknown_page_visual_progress')

    def test_plan_stale_uses_click_target_region(self):
        cfg, adapter, client, recovery, kb = self.recovery()
        before = np.full((720, 1280, 3), 40, np.uint8)
        before[300:420, 550:730] = 200  # (640,360) 附近的按钮区域
        actions = [Action('click', x=.5, y=.5)]
        # 顶部大面积变化但按钮区域未动：方案不失效（旧全屏对比会误判为 stale）
        latest = before.copy()
        latest[0:300, :] = 255
        self.assertFalse(recovery._is_plan_frame_stale(before, latest, actions, 0.18))
        # 按钮区域本身变了：失效
        latest2 = before.copy()
        latest2[300:420, 550:730] = 255
        self.assertTrue(recovery._is_plan_frame_stale(before, latest2, actions, 0.18))
        # 纯等待 / 无坐标点击退回全屏对比
        self.assertTrue(recovery._is_plan_frame_stale(before, latest, [Action('wait', wait_ms=100)], 0.18))
        self.assertTrue(recovery._is_plan_frame_stale(before, latest, [Action('click', y=.5)], 0.18))

    def test_ocr_hints_flow_into_context_only_when_enabled(self):
        cfg = base_config(api={'include_ocr_hints': True})
        recovery, _ = run_orchestrator(cfg, FakeAdapter(), FakeClient(diagnosis()), self.tmp)
        with patch('module.ai_recovery.orchestrator._ocr_boxes', return_value=[
                ((100, 200, 160, 230), '开始战斗'), ((300, 400, 380, 430), '确认')]):
            hints = recovery._extract_ocr_hints(make_screen())
        self.assertEqual(hints, [
            {'t': '开始战斗', 'x': round(130/1280, 3), 'y': round(215/720, 3)},
            {'t': '确认', 'x': round(340/1280, 3), 'y': round(415/720, 3)}])
        # 纯色图 / 关闭开关时绝不调 OCR 引擎
        with patch('module.ai_recovery.orchestrator._ocr_boxes',
                   side_effect=AssertionError('must not call')) as boxes:
            self.assertEqual(recovery._extract_ocr_hints(np.zeros((720, 1280, 3), np.uint8)), [])
            disabled = run_orchestrator(base_config(), FakeAdapter(), FakeClient(diagnosis()), self.tmp)[0]
            self.assertEqual(disabled._extract_ocr_hints(make_screen()), [])
        boxes.assert_not_called()

    def test_ocr_hints_reach_model_context_and_prompt(self):
        class InspectClient(FakeClient):
            def analyze(self, jpeg, context):
                self.seen_ocr = context.get('ocr_elements')
                return super().analyze(jpeg, context)

        client = InspectClient(diagnosis(actions=[]))
        recovery, _ = run_orchestrator(base_config(api={'include_ocr_hints': True}),
                                       FakeAdapter(), client, self.tmp)
        with patch('module.ai_recovery.orchestrator._ocr_boxes',
                   return_value=[((100, 200, 160, 230), '确认')]):
            recovery.handle(Anomaly('unknown_page', task='Dummy'))
        self.assertEqual(client.seen_ocr, [{'t': '确认', 'x': round(130/1280, 3), 'y': round(215/720, 3)}])
        from module.ai_recovery.prompt import build_user_text
        text = build_user_text({'task': 'Dummy', 'ocr_elements': [{'t': 'x' * 30, 'x': 0.5, 'y': 0.25}]})
        self.assertIn('ocr_elements', text)
        self.assertIn('x' * 20, text)
        self.assertNotIn('x' * 21, text)
        self.assertNotIn('ocr_elements', build_user_text({'task': 'Dummy'}))

    # ---------- 8 项升级新增覆盖：跨账号知识库 / compact / 单任务预算 ----------

    def test_shared_knowledge_cross_account_and_compact(self):
        from module.ai_recovery.imageutil import dhash
        cfg = base_config(knowledge={'share_across_accounts': True, 'max_age_days': 1})
        path = self.tmp / 'shared.jsonl'
        first = KnowledgeBase(cfg, config_name='oas1', path=path)
        other = KnowledgeBase(cfg, config_name='oas3', path=path)
        anomaly = Anomaly('unknown_page', task='Dummy')
        image = make_screen()
        entry = first.record(anomaly, diagnosis(), [], True, dhash(image), (1280, 720))
        hit = other.lookup(anomaly, dhash(image), (1280, 720))
        self.assertIsNotNone(hit)
        self.assertEqual(hit.id, entry.id)
        # 代码异常方案永远账号隔离
        code_anomaly = Anomaly('script_error', task='Dummy')
        first.record(code_anomaly, diagnosis(), [], True, dhash(image), (1280, 720))
        self.assertIsNone(other.lookup(code_anomaly, dhash(image), (1280, 720)))
        # 从未成功的方案不跨账号
        failed = Anomaly('popup', task='Dummy')
        first.record(failed, diagnosis(), [], False, dhash(image), (1280, 720))
        self.assertIsNone(other.lookup(failed, dhash(image), (1280, 720)))
        # 汉明距离相同优先本账号条目
        own = other.record(anomaly, diagnosis(), [], True, dhash(image), (1280, 720))
        self.assertEqual(other.lookup(anomaly, dhash(image), (1280, 720)).id, own.id)
        # compact 清除超期且从未成功的废条目
        dead = first.record(Anomaly('popup', task='Dummy', page='old'), diagnosis(), [], False,
                            dhash(image), (1280, 720))
        dead.created_at = 1.0
        dead.last_used = 1.0
        self.assertEqual(first.compact(now=1.0 + 86400 * 30), 1)
        reloaded = KnowledgeBase(cfg, config_name='oas1', path=path)
        self.assertNotIn(dead.id, reloaded.entries)
        self.assertIn(entry.id, reloaded.entries)

    def test_per_task_hour_calls_limits_only_that_task(self):
        cfg = base_config(budget={'per_task_hour_calls': 2, 'per_incident_calls': 10,
                                  'per_hour_calls': 100, 'per_day_calls': 1000})
        path = self.tmp / 'budget_task.json'
        guard = BudgetGuard(cfg, path=path, now=1_000_000.0)
        guard.start_incident(task='Dummy')
        self.assertTrue(guard.reserve(task='Dummy')[0])
        self.assertTrue(guard.reserve(task='Dummy')[0])
        allowed, reason = guard.reserve(task='Dummy')
        self.assertFalse(allowed)
        self.assertEqual(reason, 'per_task_hour_calls=2')
        self.assertTrue(guard.reserve(task='Other')[0])  # 其他任务不受影响
        # 计数持久化：新实例读到同一上限
        restarted = BudgetGuard(cfg, path=path, now=1_000_000.0)
        self.assertFalse(restarted.reserve(task='Dummy')[0])
        # 整点小时滚动后清零
        next_hour = BudgetGuard(cfg, path=path, now=1_000_000.0 + 3600.0)
        self.assertTrue(next_hour.reserve(task='Dummy')[0])
        # 0 = 不限制
        unlimited = base_config(budget={'per_task_hour_calls': 0, 'per_incident_calls': 10,
                                        'per_hour_calls': 100, 'per_day_calls': 1000})
        guard2 = BudgetGuard(unlimited, path=self.tmp / 'budget_task2.json', now=1_000_000.0)
        guard2.start_incident(task='Dummy')
        for _ in range(5):
            self.assertTrue(guard2.reserve(task='Dummy')[0])

    def test_orchestrator_per_task_budget_skips_with_advice(self):
        cfg, adapter, client, recovery, kb = self.recovery(
            client=FakeClient(diagnosis(actions=[])),
            budget={'per_task_hour_calls': 1, 'per_incident_calls': 10,
                    'per_hour_calls': 100, 'per_day_calls': 1000})
        first = recovery.handle(Anomaly('unknown_page', task='Dummy'))
        self.assertEqual(first.outcome, Outcome.STOPPED)  # 第一次正常调用（无动作）
        self.assertEqual(client.calls, 1)
        adapter.image = make_screen(99)  # 换帧避免截图去重，确保走到预算分支
        second = recovery.handle(Anomaly('unknown_page', task='Dummy'))
        self.assertEqual(second.outcome, Outcome.SKIPPED)
        self.assertTrue(second.soft_fail)
        self.assertIn('per_task_hour_calls', second.message)
        self.assertEqual(second.retry_after_s, 3600.0)
        self.assertEqual(client.calls, 1)  # 第二次没有调用模型

    def test_task_error_picks_business_frame_over_toolkit(self):
        import sys
        import traceback
        from module.ai_recovery import config as ai_config
        from module.ai_recovery import integration
        fake_root = self.tmp / 'repo'
        (fake_root / 'tasks/Dummy').mkdir(parents=True)
        toolkit = fake_root / 'toolkit/Lib/site-packages'
        toolkit.mkdir(parents=True)
        (toolkit / 'ai_fake_toolkit_mod.py').write_text(
            'def trigger():\n    raise ValueError("boom from toolkit")\n', encoding='utf-8')
        business = fake_root / 'tasks/Dummy/script_task.py'
        business.write_text(
            'from ai_fake_toolkit_mod import trigger\n'
            '\n'
            'def run():\n'
            '    return trigger()\n', encoding='utf-8')
        sys.path.insert(0, str(toolkit))
        try:
            namespace = {}
            exec(compile(business.read_text(encoding='utf-8'), str(business), 'exec'), namespace)
            captured = None
            try:
                namespace['run']()
            except ValueError as exc:
                captured = exc
            self.assertIsNotNone(captured)
        finally:
            sys.path.remove(str(toolkit))
        frames = traceback.extract_tb(captured.__traceback__)
        self.assertEqual(Path(frames[-1].filename), toolkit / 'ai_fake_toolkit_mod.py')

        class Task(AiRecoveryMixin):
            def get_task_name(self):
                return 'Dummy'

        task = Task()
        task.config = SimpleNamespace(config_name='test')
        task._ai_cfg = base_config()
        task._ai_cfg_at = time.time()
        with patch.object(ai_config, 'ROOT', fake_root), \
                patch.object(integration, 'build_anomaly', wraps=integration.build_anomaly) as builder:
            result = task.ai_handle_task_error(captured)
        self.assertIsNotNone(result)
        evidence = builder.call_args.kwargs['evidence']
        self.assertEqual(evidence['source_path'], 'tasks/Dummy/script_task.py')
        self.assertTrue(evidence['source'].startswith('script_task.py'))
        self.assertNotIn('toolkit', evidence['source_path'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
