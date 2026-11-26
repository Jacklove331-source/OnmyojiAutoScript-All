"""Durable learning regressions. No real model calls or game inputs."""
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.ai_recovery.actions import ActionRecord, ExecResult
from module.ai_recovery.evidence import EvidenceStore
from module.ai_recovery.imageutil import dhash
from module.ai_recovery.integration import AiRecoveryMixin, TaskAdapter
from module.ai_recovery.kb import KnowledgeBase
from module.ai_recovery.learning import LearningMemory, error_fingerprint, plan_signature
from module.ai_recovery.orchestrator import AiRecovery
from module.ai_recovery.prompt import build_user_text
from module.ai_recovery.selftest import FakeAdapter, FakeClient, base_config, diagnosis, make_screen
from module.ai_recovery.templates import TemplateLibrary
from module.ai_recovery.types import Action, Anomaly, Outcome, RecoveryResult
from module.ai_recovery.verification import verify_recovery


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='oas_learning_test_')
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {'OAS_AI_LOG_DIR':str(self.root/'evidence')})
        self.env.start()
        self.now = 1000.0
        self.cfg = base_config(learning={'task_defer_s':60,'base_backoff_s':60,'max_backoff_s':600})
        self.cfg.config_name = 'account_a'
        self.memory = self.reopen()

    def tearDown(self):
        self.env.stop()
        self.temporary.cleanup()

    def reopen(self, cfg=None):
        return LearningMemory(cfg or self.cfg,self.root/'evidence',clock=lambda:self.now)

    def anomaly(self, **kwargs):
        fields = dict(type='script_error',task='Dummy',page='unknown',detail='button recognition failed')
        fields.update(kwargs)
        return Anomaly(**fields)

    def plan(self):
        plan = diagnosis(actions=[Action('click',x=.5,y=.5)])
        plan.expected_page = 'page_main'
        return plan

    def fail(self):
        anomaly = self.anomaly()
        self.memory.begin(anomaly)
        self.memory.finish(anomaly,RecoveryResult(Outcome.STOPPED,anomaly.incident_id,message='still stuck'))
        return anomaly

    def recover(self):
        anomaly = self.anomaly()
        self.memory.begin(anomaly)
        self.memory.finish(anomaly,RecoveryResult(Outcome.RECOVERED,anomaly.incident_id,
                                                 diagnosis=self.plan(),verified=True))
        return anomaly

    def test_different_causes_have_different_fingerprints(self):
        self.assertNotEqual(error_fingerprint(self.anomaly(detail='mode mismatch')),
                            error_fingerprint(self.anomaly(detail='fatigue OCR failed')))

    def test_volatile_timestamp_and_pid_do_not_fragment_memory(self):
        a = self.anomaly(detail='failed pid=1234 at 2026-11-13 19:00:01')
        b = self.anomaly(detail='failed pid=5678 at 2026-11-14 10:22:31')
        self.assertEqual(error_fingerprint(a),error_fingerprint(b))

    def test_semantic_error_numbers_are_not_erased(self):
        self.assertNotEqual(error_fingerprint(self.anomaly(detail='HTTP 403')),
                            error_fingerprint(self.anomaly(detail='HTTP 429')))

    def test_code_revision_changes_invalidate_old_case(self):
        self.assertNotEqual(error_fingerprint(self.anomaly(evidence={'source_revision':'old'})),
                            error_fingerprint(self.anomaly(evidence={'source_revision':'new'})))

    def test_failure_history_survives_worker_restart(self):
        self.fail()
        self.assertEqual(self.reopen().stats()['recent_errors'][0]['consecutive_failures'],1)

    def test_accounts_do_not_share_failures(self):
        self.fail()
        other = base_config()
        other.config_name = 'account_b'
        self.assertEqual(self.reopen(other).stats()['cases'],0)

    def test_repeated_failure_opens_persistent_case_breaker(self):
        for _ in range(3):
            self.fail()
        check = self.reopen().begin(self.anomaly())
        self.assertFalse(check['allowed'])
        self.assertEqual(check['retry_after_s'],60)

    def test_backoff_grows_after_an_unsuccessful_probe(self):
        for _ in range(3):
            self.fail()
        self.now += 61
        self.assertTrue(self.memory.begin(self.anomaly())['allowed'])
        self.fail()
        self.assertEqual(self.memory.begin(self.anomaly())['retry_after_s'],120)

    def test_page_recovery_is_provisional_until_task_completes(self):
        self.recover()
        self.assertEqual(self.memory.stats()['confirmed_solutions'],0)
        self.memory.task_finished('Dummy',success=True)
        self.assertEqual(self.reopen().stats()['confirmed_solutions'],1)

    def test_recurrence_after_recovery_penalizes_previous_plan(self):
        anomaly = self.recover()
        self.memory.begin(self.anomaly())
        history = self.memory.feedback(error_fingerprint(anomaly))
        self.assertEqual(history[0]['failure'],'same_error_recurred')
        self.assertEqual(history[0]['consecutive_failures'],1)

    def test_task_failure_penalizes_provisional_lesson(self):
        anomaly = self.recover()
        self.memory.task_finished('Dummy',success=False)
        self.assertEqual(self.memory.feedback(error_fingerprint(anomaly))[0]['failure'],'original_task_failed')
        self.assertEqual(self.memory.stats()['confirmed_solutions'],0)

    def test_identical_task_failure_is_not_double_counted(self):
        anomaly = self.recover()
        self.memory.task_finished('Dummy',success=False)
        self.memory.task_finished('Dummy',success=False)
        self.assertEqual(self.memory.feedback(error_fingerprint(anomaly))[0]['consecutive_failures'],1)

    def test_deferred_task_failure_still_penalizes_provisional_lesson(self):
        anomaly = self.recover()
        self.now += 3600
        self.reopen().task_finished('Dummy',success=False)
        self.assertEqual(self.memory.feedback(error_fingerprint(anomaly))[0]['consecutive_failures'],1)

    def test_unconfirmed_recurrence_after_worker_restart_is_not_forgotten(self):
        anomaly = self.recover()
        self.now += 3600
        self.reopen().begin(self.anomaly())
        self.assertEqual(self.memory.feedback(error_fingerprint(anomaly))[0]['failure'],'same_error_recurred')

    def test_confirmed_task_resets_failure_streak(self):
        self.fail()
        self.recover()
        self.memory.task_finished('Dummy',success=True)
        self.assertEqual(self.memory.stats()['recent_errors'][0]['consecutive_failures'],0)

    def test_coordinate_jitter_does_not_evade_failed_plan_filter(self):
        case_id = self.memory.begin(self.anomaly())['case_id']
        plan = self.plan()
        self.memory.plan_failed(case_id,plan,'no effect')
        self.memory.plan_failed(case_id,plan,'no effect')
        jittered = self.plan()
        jittered.actions[0].x = .5001
        self.assertFalse(self.reopen().plan_allowed(case_id,jittered))
        jittered.actions[0].x = .65
        self.assertTrue(self.memory.plan_allowed(case_id,jittered))

    def test_interface_failure_breaker_survives_worker_restart(self):
        for _ in range(3):
            self.memory.provider_result(success=False,reason='AiBadResponse')
        self.assertFalse(self.reopen().provider_allowed())
        self.now += 601
        self.assertTrue(self.memory.provider_allowed())
        self.memory.provider_result(success=True)
        self.assertFalse(self.memory.stats()['provider_blocked'])

    def test_history_is_included_in_actual_model_prompt(self):
        message = build_user_text({'recovery_goal':'page_main','failed_plans':[{
            'actions':[{'type':'click','x':.5,'y':.5}], 'expected_page':'page_main',
            'failure':'same_page_still_stalled','consecutive_failures':2}]})
        self.assertIn('required_recovery_page',message)
        self.assertIn('same_page_still_stalled',message)

    def test_task_contract_overrides_models_different_goal(self):
        adapter = FakeAdapter(page='page_reward')
        anomaly = self.anomaly(evidence={'expected_page':'page_main'})
        plan = self.plan()
        plan.expected_page = 'page_reward'
        proof = verify_recovery(self.cfg,adapter,anomaly,ExecResult(True,[ActionRecord('click',True)]),
                                 diagnosis=plan,page_before='unknown')
        self.assertFalse(proof.verified)
        self.assertEqual(proof.reason,'expected_page_mismatch')

    def test_same_validated_solution_accumulates_one_entry(self):
        kb = KnowledgeBase(self.cfg,config_name=self.cfg.config_name,path=self.root/'kb.jsonl',clock=lambda:self.now)
        anomaly, plan = self.anomaly(), self.plan()
        for _ in range(10):
            kb.record(anomaly,plan,[a.to_dict() for a in plan.actions],True,dhash(make_screen(1)),(1280,720),'v1')
        self.assertEqual(len(kb.entries),1)
        self.assertEqual(next(iter(kb.entries.values())).success,10)

    def test_historically_good_solution_is_quarantined_after_two_failures(self):
        kb = KnowledgeBase(self.cfg,config_name=self.cfg.config_name,path=self.root/'kb.jsonl')
        anomaly, plan = self.anomaly(), self.plan()
        for _ in range(10):
            entry = kb.record(anomaly,plan,[a.to_dict() for a in plan.actions],True,'0000000000000000',(1280,720),'v1')
        kb.mark_reuse(entry,False)
        kb.mark_reuse(entry,False)
        self.assertGreater(entry.rate,.8)
        reloaded = KnowledgeBase(self.cfg,config_name=self.cfg.config_name,path=self.root/'kb.jsonl')
        self.assertIsNone(reloaded.lookup(anomaly,'0000000000000000',(1280,720),'v1'))
        self.assertEqual(reloaded.stats()['quarantined'],1)

    def test_knowledge_does_not_cross_different_script_errors(self):
        kb = KnowledgeBase(self.cfg,config_name=self.cfg.config_name,path=self.root/'kb.jsonl')
        anomaly, plan = self.anomaly(detail='first error'), self.plan()
        kb.record(anomaly,plan,[a.to_dict() for a in plan.actions],True,'0000000000000000',(1280,720),'v1')
        self.assertIsNone(kb.lookup(self.anomaly(detail='second error'),'0000000000000000',(1280,720),'v1'))

    def recovery(self, adapter, client):
        return AiRecovery(self.cfg,adapter,client=client,
                          kb=KnowledgeBase(self.cfg,config_name=self.cfg.config_name,path=self.root/'kb.jsonl',clock=lambda:self.now),
                          templates=TemplateLibrary(self.cfg,config_name=self.cfg.config_name,root=self.root/'templates'),
                          evidence=EvidenceStore(self.cfg.config_name,root=self.root/'evidence'),clock=lambda:self.now)

    def test_knowledge_remains_usable_while_provider_is_blocked(self):
        adapter, client = FakeAdapter(), FakeClient(error=AssertionError('must not call model'))
        recovery = self.recovery(adapter,client)
        anomaly = Anomaly('unknown_page',task='Dummy')
        plan = self.plan()
        recovery.kb.record(anomaly,plan,[a.to_dict() for a in plan.actions],True,
                           dhash(adapter.image),(1280,720),adapter.game_version)
        for _ in range(3):
            recovery.learning.provider_result(success=False)
        result = recovery.handle(anomaly)
        self.assertEqual(result.outcome,Outcome.RECOVERED)
        self.assertEqual(client.calls,0)

    def test_repeated_failed_AI_plan_is_blocked_before_input_after_restart(self):
        self.cfg.safety.replan_attempts = 0
        self.cfg.learning.repeated_error_limit = 10
        for _ in range(2):
            adapter = FakeAdapter(noop_click=True)
            client = FakeClient(self.plan())
            result = self.recovery(adapter,client).handle(Anomaly('unknown_page',task='Dummy'))
            self.assertEqual(result.outcome,Outcome.STOPPED)
            self.assertEqual(len(adapter.clicks),1)
            self.now += 601
        adapter, client = FakeAdapter(noop_click=True), FakeClient(self.plan())
        result = self.recovery(adapter,client).handle(Anomaly('unknown_page',task='Dummy'))
        self.assertEqual(result.outcome,Outcome.STOPPED)
        self.assertFalse(adapter.clicks)
        self.assertIn('连续失败',result.message)
        self.assertTrue(client.contexts[0]['failed_plans'])

    def scheduler(self, task):
        from script import Script
        scheduler = object.__new__(Script)
        scheduler.__dict__['config'] = SimpleNamespace(model=SimpleNamespace(dummy=SimpleNamespace(
            scheduler=SimpleNamespace(failure_interval=timedelta(seconds=700)))),task_delay=Mock(),notifier=SimpleNamespace(push=Mock()))
        scheduler.__dict__['device'] = SimpleNamespace(screenshot=lambda:None,image=None)
        scheduler.config_name = 'account_a'
        scheduler.instance_guard = None
        scheduler._try_acquire_queue_token = lambda:True
        scheduler._dismiss_system_dialog = lambda:None
        scheduler._apply_auto_lineup = lambda command:None
        scheduler.exception_handler = lambda e,command:scheduler._ai_task_outcome(False)
        scheduler.save_error_log = Mock()
        return scheduler

    def test_script_error_bridge_defers_instead_of_exiting(self):
        from module.exception import ScriptError
        task = SimpleNamespace(run=Mock(side_effect=ScriptError('mode mismatch')),
            ai_record_task_outcome=Mock(), ai_handle_task_error=Mock(return_value={'retry_after_s':1200,'message':'recorded'}))
        scheduler = self.scheduler(task)
        with patch('script.load_module',return_value=SimpleNamespace(ScriptTask=lambda **kwargs:task)):
            self.assertTrue(scheduler.run('Dummy'))
        task.ai_record_task_outcome.assert_called_once_with(False)
        args = scheduler.config.task_delay.call_args.kwargs
        self.assertFalse(args['server'])
        self.assertNotIn('success',args)

    def test_task_end_confirms_pending_lessons(self):
        from module.exception import TaskEnd
        task = SimpleNamespace(run=Mock(side_effect=TaskEnd),ai_record_task_outcome=Mock())
        scheduler = self.scheduler(task)
        with patch('script.load_module',return_value=SimpleNamespace(ScriptTask=lambda **kwargs:task)):
            self.assertTrue(scheduler.run('Dummy'))
        task.ai_record_task_outcome.assert_called_once_with(True)

    def test_task_defer_keeps_longer_user_failure_interval(self):
        task = SimpleNamespace(ai_handle_task_error=lambda error:{'retry_after_s':60,'message':'defer'})
        scheduler = self.scheduler(task)
        from datetime import datetime
        before = datetime.now()
        self.assertTrue(scheduler._ai_defer_failed_task(task,ValueError('x'),'Dummy'))
        target = scheduler.config.task_delay.call_args.kwargs['target']
        self.assertGreaterEqual((target-before).total_seconds(),700)

    def test_missing_scheduler_is_not_falsely_reported_as_deferred(self):
        task = SimpleNamespace(ai_handle_task_error=lambda error:{'retry_after_s':60,'message':'defer'})
        scheduler = self.scheduler(task)
        scheduler.config.model = SimpleNamespace()
        self.assertFalse(scheduler._ai_defer_failed_task(task,ValueError('x'),'Dummy'))
        scheduler.config.task_delay.assert_not_called()

    def idle_scheduler(self):
        from script import Script
        scheduler = object.__new__(Script)
        scheduler.__dict__['config'] = SimpleNamespace(script=SimpleNamespace(
            desktop=SimpleNamespace(enable=False),device=SimpleNamespace(serial='127.0.0.1:16384'),
            optimization=SimpleNamespace(close_game_wait_duration=None,
                close_emulator_wait_duration=SimpleNamespace(hour=0,minute=25,second=0),
                stagger_enable=False)))
        scheduler._emulator_down = False
        scheduler._wait_until_with_emulator_preheat = Mock(return_value=False)
        return scheduler

    def test_idle_reload_never_initializes_device_or_starts_emulator(self):
        from datetime import datetime
        scheduler = self.idle_scheduler()
        platform = Mock()
        platform._emulator_function_wrapper.return_value = True
        with patch('module.device.platform2.Platform',return_value=platform), \
                patch('script.Device',side_effect=AssertionError('idle must not auto-start a Device')):
            self.assertFalse(scheduler._wait_close_game(datetime.now()+timedelta(hours=1)))
        platform._emulator_function_wrapper.assert_called_once_with(platform._emulator_stop)
        platform.emulator_start.assert_not_called()
        self.assertNotIn('device',scheduler.__dict__)
        self.assertTrue(scheduler._emulator_down)
        scheduler._wait_until_with_emulator_preheat.assert_called_once()

    def test_idle_existing_device_preserves_native_shutdown(self):
        scheduler = self.idle_scheduler()
        device = Mock()
        scheduler.__dict__['device'] = device
        with patch('module.device.platform2.Platform',side_effect=AssertionError('already has Device')):
            scheduler._stop_emulator_for_idle()
        device.emulator_stop.assert_called_once()

    def test_idle_shutdown_failure_stops_without_starting_emulator(self):
        from module.exception import RequestHumanTakeover
        scheduler = self.idle_scheduler()
        platform = Mock()
        platform._emulator_function_wrapper.return_value = False
        with patch('module.device.platform2.Platform',return_value=platform), \
                patch('script.Device',side_effect=AssertionError('must not auto-start')):
            with self.assertRaises(RequestHumanTakeover):
                scheduler._stop_emulator_for_idle()
        platform.emulator_start.assert_not_called()

    def test_human_takeover_still_stops_and_is_not_retried(self):
        from module.exception import RequestHumanTakeover
        task = SimpleNamespace(run=Mock(side_effect=RequestHumanTakeover('manual')),
                               ai_record_task_outcome=Mock(),ai_handle_task_error=Mock())
        scheduler = self.scheduler(task)
        with patch('script.load_module',return_value=SimpleNamespace(ScriptTask=lambda **kwargs:task)), \
                patch('script.I18n.trans_zh_cn',return_value='Dummy'):
            with self.assertRaises(SystemExit):
                scheduler.run('Dummy')
        task.ai_handle_task_error.assert_not_called()

    def test_unconfigured_recovery_preserves_legacy_stop(self):
        from module.exception import ScriptError
        task = SimpleNamespace(run=Mock(side_effect=ScriptError('disabled')),
                               ai_record_task_outcome=Mock(),ai_handle_task_error=Mock(return_value=None))
        scheduler = self.scheduler(task)
        with patch('script.load_module',return_value=SimpleNamespace(ScriptTask=lambda **kwargs:task)), \
                patch('script.I18n.trans_zh_cn',return_value='Dummy'):
            with self.assertRaises(SystemExit):
                scheduler.run('Dummy')
        scheduler.config.task_delay.assert_not_called()

    def test_non_UI_code_error_is_archived_without_model_or_game_input(self):
        task = AiRecoveryMixin()
        task.ai_config = lambda:self.cfg
        task.get_task_name = lambda:'Dummy'
        task.config = SimpleNamespace(config_name=self.cfg.config_name)
        task.ai_recover = Mock(side_effect=AssertionError('must not call model'))
        result = task.ai_handle_task_error(ValueError('invalid configuration value'))
        self.assertGreaterEqual(result['retry_after_s'],60)
        task.ai_recover.assert_not_called()
        actual = LearningMemory(self.cfg,self.root/'evidence').stats()
        self.assertEqual(actual['cases'],1)
        self.assertEqual(actual['recent_errors'][0]['kind'],'task_exception')

    def test_learning_disabled_creates_no_memory_file(self):
        self.cfg.learning.enabled = False
        memory = self.reopen()
        memory.begin(self.anomaly())
        self.assertFalse(memory.path.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
