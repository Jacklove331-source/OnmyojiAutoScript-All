"""Code repair end-to-end regressions; fake model/device and temporary repos."""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

from module.ai_recovery.client import DeepSeekClient,AiBadResponse,AiUnavailable
from module.ai_recovery.config import AiRecoveryConfig
from module.ai_recovery.repair_agent import RepairAgent,RepairDeferredError
from module.ai_recovery.repair_context import (SourceWorkspace,source_path,digest,redact,
                                               assert_unclaimed,active_claims)
from module.ai_recovery.repair_deploy import RepairDeployer,OASControl,atomic_json
from module.ai_recovery.repair_runtime import RuntimeReporter
from module.ai_recovery.repair_sandbox import replay
from module.ai_recovery.repair_store import RepairStore
from module.ai_recovery.repair_worker import (enqueue_fault,RepairEngine,
                                              _resolve_primary_source,prune_old_workdirs)
from module.ai_recovery.selftest import base_config,diagnosis
from module.ai_recovery.types import Anomaly,RecoveryResult,Outcome

SOURCE='class ScriptTask:\n    def ratio(self, values):\n        return sum(values) / len(values)\n'
OLD='        return sum(values) / len(values)'
NEW='        return sum(values) / len(values) if values else 0'
REGRESSION='''import unittest
class Regression(unittest.TestCase):
    def call_ratio(self, values):
        fn=load_symbol('tasks/Dummy/script_task.py','ScriptTask.ratio')
        try: return fn(None,values)
        except Exception as e: self.fail(type(e).__name__)
    def test_empty(self): self.assertEqual(self.call_ratio([]),0)
    def test_normal(self): self.assertEqual(self.call_ratio([1,3]),2)
'''


class StubClient(DeepSeekClient):
    def __init__(self,cfg,responses):super().__init__(cfg);self.responses=list(responses);self.sent=[]
    def _post(self,payload,key):
        self.sent.append(deepcopy(payload))
        value=self.responses.pop(0)
        if isinstance(value,Exception):raise value
        return value


class FakeControl:
    def __init__(self):
        self.values={'oas1':1,'oas3':0};self.busy=False;self.fail_freeze=False
        self.fail_health=False;self.stops=[];self.starts=[];self.instances={'oas1':'old'}
    def states(self):return dict(self.values)
    def safe(self,states):return not self.busy
    def heartbeat(self,name):return {'instance':self.instances.get(name,'old')}
    def freeze(self,states):
        if self.fail_freeze:raise RuntimeError('no acknowledgement')
        active=[n for n,s in states.items() if s==1]
        for n in active:self.values[n]=0;self.stops.append(n)
        return active
    def clear(self):pass
    def resume(self,names):
        for n in names:
            if self.values[n]==0:self.values[n]=1;self.starts.append(n);self.instances[n]='new'
    def healthy(self,names,old):return not self.fail_health


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='oas_code_repair_')
        self.root=Path(self.temp.name)
        self.repo=self.root/'repo';self.logs=self.root/'logs'
        target=self.repo/'tasks/Dummy/script_task.py';target.parent.mkdir(parents=True)
        target.write_text(SOURCE,encoding='utf-8')
        self.original_bytes=target.read_bytes()
        (self.repo/'AI_COORD.md').write_text('# Board\n|---|---|---|---|\n## 交接记录（最新在上）\n',encoding='utf-8')
        self.cfg=base_config();self.cfg.config_name='oas1';self.cfg.api_key='sk-secret-for-test'
        self.env=patch.dict(os.environ,{'OAS_AI_LOG_DIR':str(self.logs)});self.env.start()
        self.store=RepairStore(self.logs)
        self.payload={'task':'Dummy','kind':'task_exception','error':'ZeroDivisionError',
            'fingerprint':'same-error','revision':digest(target.read_bytes())[:16],
            'source_path':'tasks/Dummy/script_task.py','frames':[]}
        self.control=FakeControl()
        self.deployer=RepairDeployer(self.repo,self.store,control=self.control,
                                    lock_factory=lambda owner:nullcontext(),checkpoint=False)

    def tearDown(self):self.env.stop();self.temp.cleanup()

    def workspace(self,suffix='one'):
        return SourceWorkspace(self.repo,self.logs/'code_repair/work'/suffix,
                               self.cfg.code_repair,self.payload,secrets=[self.cfg.api_key]).create()

    def ready(self,suffix='ready'):
        job=self.store.enqueue('oas1',self.payload)
        ws=self.workspace(suffix);ws.patch('tasks/Dummy/script_task.py',OLD,NEW);ws.set_test(REGRESSION)
        proof=replay(ws,10);self.assertTrue(proof['passed'],proof)
        ws.manifest();self.store.update(job['id'],'ready',artifact=str(ws.folder))
        return self.store.get(job['id']),ws

    def response(self,content=None,calls=None,finish='stop'):
        msg={'content':content}
        if calls is not None:msg['tool_calls']=calls
        return {'choices':[{'message':msg,'finish_reason':finish}],'usage':{'prompt_tokens':10,'completion_tokens':10}}

    def valid_ui(self):
        return json.dumps(diagnosis().to_dict())

    def test_config_enables_requested_repair_and_normalizes_bounds(self):
        cfg=AiRecoveryConfig.from_dict({'code_repair':{'max_model_calls':100,'max_files':999,'test_timeout_s':-1}})
        self.assertTrue(cfg.code_repair.enabled);self.assertEqual(cfg.code_repair.max_model_calls,12)
        self.assertEqual(cfg.code_repair.max_files,5);self.assertEqual(cfg.code_repair.test_timeout_s,5)

    def test_empty_ui_output_has_one_corrective_retry(self):
        c=StubClient(self.cfg,[self.response('   '),self.response(self.valid_ui())])
        c.analyze(b'jpeg',{})
        self.assertEqual(len(c.sent),2)
        self.assertIn('response_format',c.sent[0]);self.assertNotIn('response_format',c.sent[1])
        self.assertEqual(c.sent[1]['thinking']['type'],'disabled')

    def test_reasoning_only_output_is_not_executed_as_answer(self):
        data=self.response(None,finish='length');data['choices'][0]['message']['reasoning_content']='private hidden text'
        c=StubClient(self.cfg,[data,self.response(self.valid_ui())])
        result=c.analyze(b'jpeg',{})
        self.assertNotIn('private hidden text',result.text)
        self.assertEqual(len(c.sent),2)

    def test_empty_response_cannot_retry_without_budget(self):
        self.cfg.api.max_retries=0
        c=StubClient(self.cfg,[self.response('')]);c.request_guard=Mock()
        c.request_guard.reserve.side_effect=[(True,''),(False,'limit')]
        with self.assertRaises(AiUnavailable):c.analyze(b'jpeg',{})
        self.assertEqual(len(c.sent),1)

    def test_two_empty_responses_stop_instead_of_looping(self):
        c=StubClient(self.cfg,[self.response(''),self.response('')])
        with self.assertRaises(AiBadResponse):c.analyze(b'jpeg',{})
        self.assertEqual(len(c.sent),2)

    def test_other_providers_do_not_receive_deepseek_thinking_extension(self):
        self.cfg.api.base_url='https://example.invalid/v1'
        c=StubClient(self.cfg,[self.response(self.valid_ui())]);c.analyze(b'jpeg',{})
        self.assertNotIn('thinking',c.sent[0])

    def test_fault_jobs_deduplicate_across_store_restart(self):
        a=self.store.enqueue('oas1',self.payload);b=RepairStore(self.logs).enqueue('oas1',self.payload)
        self.assertEqual(a['id'],b['id']);self.assertEqual(self.store.status()['jobs'],1)

    def test_account_and_source_revision_isolation(self):
        self.store.enqueue('oas1',self.payload);self.store.enqueue('oas3',self.payload)
        self.store.enqueue('oas1',dict(self.payload,revision='new-version'))
        self.assertEqual(self.store.status()['jobs'],3)

    def test_persistent_max_attempts_prevent_repeated_repair_loop(self):
        job=self.store.enqueue('oas1',self.payload)
        for _ in range(2):
            self.assertIsNotNone(RepairStore(self.logs).claim_next('oas1',2))
            self.store.update(job['id'],'deferred',next_try=0)
        self.assertIsNone(self.store.claim_next('oas1',2))
        self.assertEqual(self.store.get(job['id'])['status'],'exhausted')

    def test_success_requires_original_task_completion(self):
        job=self.store.enqueue('oas1',self.payload);self.store.update(job['id'],'probation')
        self.store.task_finished('oas3','Dummy',True)
        self.assertEqual(self.store.get(job['id'])['status'],'probation')
        self.store.task_finished('oas1','Dummy',True)
        self.assertEqual(self.store.get(job['id'])['status'],'succeeded')

    def test_task_failure_requests_rollback(self):
        job=self.store.enqueue('oas1',self.payload);self.store.update(job['id'],'probation')
        RepairStore(self.logs).task_finished('oas1','Dummy',False)
        self.assertEqual(self.store.get(job['id'])['status'],'rollback_pending')

    def test_protected_paths_and_traversal_are_rejected(self):
        for name in ('../outside.py','C:/outside.py','module/ai_recovery/client.py','module/server/app.py',
                     'config/key.py','dev_tools/dual_agent/state.py','tasks/../../secret.py'):
            with self.subTest(name=name),self.assertRaises(ValueError):source_path(self.repo,name)

    def test_source_snapshot_omits_credentials_and_logs(self):
        secret=self.repo/'config/ai_recovery.key';secret.parent.mkdir();secret.write_text('PRIVATE')
        ws=self.workspace()
        self.assertFalse((ws.candidate/'config').exists());self.assertNotIn('PRIVATE',str(ws.read('tasks/Dummy/script_task.py')))

    def test_source_access_redacts_known_keys(self):
        path=self.repo/'tasks/Dummy/script_task.py';path.write_text(SOURCE+'\n# '+self.cfg.api_key)
        ws=self.workspace()
        self.assertNotIn(self.cfg.api_key,str(ws.read('tasks/Dummy/script_task.py')))

    def test_exact_patch_does_not_edit_live_source(self):
        ws=self.workspace();ws.patch('tasks/Dummy/script_task.py',OLD,NEW)
        self.assertEqual((self.repo/'tasks/Dummy/script_task.py').read_text(),SOURCE)
        with self.assertRaises(ValueError):ws.patch('tasks/Dummy/script_task.py','not present','anything')

    def test_patch_cannot_add_external_execution(self):
        ws=self.workspace()
        with self.assertRaises(ValueError):ws.patch('tasks/Dummy/script_task.py',OLD,"        return __import__('os').system('echo bad')")

    def test_patch_cannot_expand_to_other_tasks(self):
        target=self.repo/'tasks/Other/script_task.py';target.parent.mkdir();target.write_text(SOURCE)
        ws=self.workspace()
        with self.assertRaises(ValueError):ws.patch('tasks/Other/script_task.py',OLD,NEW)

    def test_active_collaborator_claim_blocks_apply(self):
        (self.repo/'AI_COORD.md').write_text('| `tasks/Dummy/` | Other | now | 进行中 |')
        with self.assertRaises(ValueError):assert_unclaimed(self.repo,['tasks/Dummy/script_task.py'])

    def test_behavior_replay_reproduces_old_failure_and_passes_fixed_code(self):
        ws=self.workspace();ws.patch('tasks/Dummy/script_task.py',OLD,NEW);ws.set_test(REGRESSION)
        proof=replay(ws,10)
        self.assertTrue(proof['passed'],proof);self.assertEqual(proof['baseline']['failures'],1)
        self.assertEqual(proof['candidate']['tests'],2);self.assertTrue(proof['changed_lines_exercised'])

    def test_trivial_always_passing_test_does_not_authorize_patch(self):
        ws=self.workspace();ws.patch('tasks/Dummy/script_task.py',OLD,NEW)
        ws.set_test('import unittest\nclass R(unittest.TestCase):\n def test_yes(self): self.assertTrue(True)\n')
        self.assertFalse(replay(ws,10)['passed'])
        with self.assertRaises(ValueError):ws.manifest()

    def test_broken_test_setup_is_not_fault_reproduction(self):
        ws=self.workspace();ws.patch('tasks/Dummy/script_task.py',OLD,NEW)
        ws.set_test('import unittest\nclass R(unittest.TestCase):\n def test_x(self): missing_function()\n')
        self.assertFalse(replay(ws,10)['passed'])

    def test_generated_test_cannot_read_host_credentials_or_start_process(self):
        ws=self.workspace();ws.patch('tasks/Dummy/script_task.py',OLD,NEW)
        ws.set_test("import unittest\nimport subprocess\nclass R(unittest.TestCase):\n def test_x(self): subprocess.run(['bad'])\n")
        proof=replay(ws,10)
        self.assertFalse(proof['passed']);self.assertIn('blocked',str(proof))

    def test_changed_source_after_test_cannot_be_submitted(self):
        job,ws=self.ready();(ws.candidate/'tasks/Dummy/script_task.py').write_text(SOURCE)
        with self.assertRaises(ValueError):ws.manifest()
        with self.assertRaises(ValueError):self.deployer.artifact(job)

    def test_apply_waits_for_idle_and_does_not_interrupt_battle(self):
        job,ws=self.ready();self.control.busy=True
        self.assertFalse(self.deployer.apply(job));self.assertFalse(self.control.stops)
        self.assertEqual((self.repo/'tasks/Dummy/script_task.py').read_text(),SOURCE)

    def test_missing_idle_ack_keeps_tested_patch_ready(self):
        job,ws=self.ready();self.control.fail_freeze=True
        self.assertFalse(self.deployer.apply(job));self.assertEqual(self.store.get(job['id'])['status'],'ready')
        self.assertFalse(self.control.stops)

    def test_apply_reload_and_task_completion_full_flow(self):
        job,ws=self.ready();self.assertTrue(self.deployer.apply(job))
        self.assertIn(NEW,(self.repo/'tasks/Dummy/script_task.py').read_text())
        self.assertEqual(self.control.starts,['oas1']);self.assertNotIn('oas3',self.control.starts)
        self.assertEqual(self.store.get(job['id'])['status'],'probation')
        self.store.task_finished('oas1','Dummy',True)
        self.assertEqual(self.store.get(job['id'])['status'],'succeeded')

    def test_concurrent_human_source_edit_blocks_deployment(self):
        job,ws=self.ready();(self.repo/'tasks/Dummy/script_task.py').write_text(SOURCE+'# user edit\n')
        with self.assertRaises(RuntimeError):self.deployer.apply(job)
        self.assertFalse(self.control.stops)

    def test_failed_original_task_rolls_back_to_exact_original_bytes(self):
        job,ws=self.ready();self.deployer.apply(job);self.store.task_finished('oas1','Dummy',False)
        self.assertTrue(self.deployer.rollback(self.store.get(job['id'])))
        self.assertEqual((self.repo/'tasks/Dummy/script_task.py').read_bytes(),self.original_bytes)
        self.assertEqual(self.store.get(job['id'])['status'],'rolled_back');self.assertTrue(self.store.rejected(job))

    def test_rollback_preserves_later_human_changes(self):
        job,ws=self.ready();self.deployer.apply(job)
        target=self.repo/'tasks/Dummy/script_task.py';target.write_text('USER CHANGE\n')
        self.assertFalse(self.deployer.rollback(self.store.get(job['id'])))
        self.assertEqual(target.read_text(),'USER CHANGE\n');self.assertEqual(self.store.get(job['id'])['status'],'needs_attention')

    def test_failed_worker_reload_restores_original_source(self):
        job,ws=self.ready();self.control.fail_health=True
        with self.assertRaises(RuntimeError):self.deployer.apply(job)
        self.assertEqual((self.repo/'tasks/Dummy/script_task.py').read_text(),SOURCE)
        self.assertEqual(self.store.get(job['id'])['status'],'rolled_back')

    def test_runtime_gate_acknowledges_maintenance_before_task_input(self):
        root=self.root/'runtime';reporter=RuntimeReporter('oas1',root)
        atomic_json(root/'maintenance.json',{'token':'maintenance','expires':time.time()+20})
        seen=[]
        def release(_):
            seen.append(json.loads(reporter.path.read_text()))
            (root/'maintenance.json').unlink()
        reporter.gate('running',sleep=release)
        self.assertEqual(seen[0]['phase'],'maintenance');self.assertEqual(seen[0]['maintenance'],'maintenance')
        self.assertEqual(json.loads(reporter.path.read_text())['phase'],'running')

    def test_stale_heartbeat_is_never_a_safe_deploy_window(self):
        control=OASControl(self.logs)
        with patch.object(control,'heartbeat',return_value={'phase':'idle','at':time.time()-60,'wake':time.time()+3600}):
            self.assertFalse(control.safe({'oas1':1}))

    def test_code_fault_enqueues_sanitized_persistent_job(self):
        anomaly=Anomaly('task_exception',task='Dummy',detail='bad '+self.cfg.api_key,
            evidence={'source_path':'tasks/Dummy/script_task.py','source_revision':'old'})
        enqueue_fault(self.cfg,self.logs,anomaly,RecoveryResult(Outcome.STOPPED,anomaly.incident_id))
        jobs=self.store.list();self.assertEqual(len(jobs),1);self.assertNotIn(self.cfg.api_key,str(jobs))

    def test_disabled_code_repair_does_not_create_jobs(self):
        self.cfg.code_repair.enabled=False
        anomaly=Anomaly('task_exception',task='Dummy')
        self.assertIsNone(enqueue_fault(self.cfg,self.logs,anomaly,RecoveryResult(Outcome.STOPPED,anomaly.incident_id)))
        self.assertFalse(self.store.path.exists())

    def test_native_agent_tools_complete_isolated_repair_and_tests(self):
        job=self.store.enqueue('oas1',self.payload);job=self.store.claim_next('oas1',2)
        calls=[]
        def call(name,args):calls.append({'id':'call_'+str(len(calls)),'type':'function','function':{
            'name':name,'arguments':json.dumps(args)}})
        call('edit_source',{'path':'tasks/Dummy/script_task.py','old_text':OLD,'new_text':NEW})
        call('write_regression',{'code':REGRESSION});call('run_tests',{});call('finish',{'summary':'empty input handled'})
        client=StubClient(self.cfg,[self.response(None,calls,'tool_calls')])
        manifest=RepairAgent(self.cfg,self.store,self.repo,client=client).run(job)
        self.assertTrue(manifest['test']['passed']);self.assertEqual(self.store.get(job['id'])['status'],'ready')
        self.assertEqual((self.repo/'tasks/Dummy/script_task.py').read_text(),SOURCE)

    def test_patch_cannot_introduce_new_game_inputs(self):
        ws=self.workspace()
        with self.assertRaises(ValueError):ws.patch('tasks/Dummy/script_task.py',OLD,'        return self.device.click(1, 1)')

    def test_fix_that_breaks_normal_input_cannot_pass_regression(self):
        ws=self.workspace();ws.patch('tasks/Dummy/script_task.py',OLD,'        return 0');ws.set_test(REGRESSION)
        report=replay(ws,10)
        self.assertFalse(report['passed']);self.assertEqual(report['candidate']['failures'],1)

    def test_interrupted_deployment_restores_source_and_restarts_only_original_account(self):
        job,ws=self.ready();manifest=json.loads((ws.folder/'manifest.json').read_text())
        backup=ws.folder/'backup/tasks/Dummy/script_task.py';backup.parent.mkdir(parents=True)
        backup.write_bytes(self.original_bytes)
        (self.repo/'tasks/Dummy/script_task.py').write_bytes((ws.candidate/'tasks/Dummy/script_task.py').read_bytes())
        atomic_json(ws.folder/'journal.json',{'phase':'writing','resume':['oas1'],'files':manifest['files']})
        self.store.update(job['id'],'applying');self.control.values={'oas1':0,'oas3':0}
        self.assertTrue(self.deployer.rollback(self.store.get(job['id'])))
        self.assertEqual((self.repo/'tasks/Dummy/script_task.py').read_bytes(),self.original_bytes)
        self.assertEqual(self.control.starts,['oas1'])

    def test_stale_fault_is_discarded_before_any_model_request(self):
        job=self.store.enqueue('oas1',self.payload)
        (self.repo/'tasks/Dummy/script_task.py').write_text(SOURCE+'# fixed elsewhere\n')
        factory=Mock(side_effect=AssertionError('must not call model'))
        engine=RepairEngine(self.repo,self.logs,agent_factory=factory,deployer=self.deployer)
        with patch('module.ai_recovery.repair_worker.load_config',return_value=self.cfg),patch.object(engine,'import_legacy'):
            result=engine.tick('oas1')
        self.assertEqual(result['status'],'superseded');factory.assert_not_called()

    def test_success_updates_existing_oasx_repair_queue(self):
        legacy_id='legacy-case'
        payload=dict(self.payload,legacy_id=legacy_id)
        path=self.logs/('repair_requests_'+hashlib.sha256(b'oas1').hexdigest()[:12]+'.jsonl')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'id':legacy_id,'status':'pending','config':'oas1'})+'\n')
        job=self.store.enqueue('oas1',payload);self.store.update(job['id'],'probation')
        self.store.task_finished('oas1','Dummy',True)
        last=json.loads(path.read_text().splitlines()[-1])
        self.assertEqual(last['status'],'resolved');self.assertIn('succeeded',last['message'])

    def test_expired_maintenance_request_cannot_hold_scheduler(self):
        reporter=RuntimeReporter('oas1',self.root/'runtime')
        atomic_json(reporter.root/'maintenance.json',{'token':'old','expires':time.time()-1})
        sleeper=Mock(side_effect=AssertionError('expired request must not wait'))
        reporter.gate('running',sleep=sleeper);sleeper.assert_not_called()

    def test_scheduler_checks_maintenance_before_native_popup_or_lineup_actions(self):
        from module.ai_recovery.learning_test import LearningTests
        from types import SimpleNamespace
        task=SimpleNamespace(run=Mock(),ai_record_task_outcome=Mock())
        scheduler=LearningTests().scheduler(task)
        order=[]
        scheduler._repair_runtime=SimpleNamespace(gate=lambda *args,**kwargs:order.append('maintenance_gate'))
        scheduler._dismiss_system_dialog=lambda:order.append('native_input')
        with patch('script.load_module',return_value=SimpleNamespace(ScriptTask=lambda **kwargs:task)):
            scheduler.run('Dummy')
        self.assertEqual(order[:2],['maintenance_gate','native_input'])

    def test_task_constructor_error_uses_code_repair_bridge(self):
        from module.ai_recovery.learning_test import LearningTests
        from types import SimpleNamespace
        scheduler=LearningTests().scheduler(None)
        with patch('script.load_module',return_value=SimpleNamespace(ScriptTask=Mock(side_effect=ValueError('constructor failed')))), \
             patch('module.ai_recovery.integration.AiRecoveryMixin.ai_handle_task_error',return_value={'retry_after_s':600,'message':'queued'}) as handle, \
             patch('module.ai_recovery.repair_runtime.task_feedback') as feedback:
            self.assertTrue(scheduler.run('Dummy'))
        handle.assert_called_once();feedback.assert_called_once_with('account_a','Dummy',False)
        scheduler.config.task_delay.assert_called_once()

    def test_reload_failure_waits_for_safe_boundary_before_rollback(self):
        job,ws=self.ready()
        def unhealthy(names,old):self.control.busy=True;return False
        self.control.healthy=unhealthy
        self.assertFalse(self.deployer.apply(job))
        self.assertEqual(self.store.get(job['id'])['status'],'rollback_pending')
        self.assertIn(NEW,(self.repo/'tasks/Dummy/script_task.py').read_text())
        self.control.busy=False
        self.assertTrue(self.deployer.rollback(self.store.get(job['id'])))
        self.assertEqual((self.repo/'tasks/Dummy/script_task.py').read_bytes(),self.original_bytes)

    def test_one_fault_only_test_cannot_replace_normal_behavior_coverage(self):
        ws=self.workspace();ws.patch('tasks/Dummy/script_task.py',OLD,NEW)
        ws.set_test(REGRESSION.replace('    def test_normal(self): self.assertEqual(self.call_ratio([1,3]),2)\n',''))
        self.assertFalse(replay(ws,10)['passed'])

    def test_rollback_waits_for_other_collaborators_active_claim(self):
        job,ws=self.ready();self.deployer.apply(job)
        self.store.update(job['id'],'rollback_pending')
        (self.repo/'AI_COORD.md').write_text('| `tasks/Dummy/` | Other | now | 进行中 |')
        with self.assertRaises(ValueError):self.deployer.rollback(self.store.get(job['id']))
        self.assertIn(NEW,(self.repo/'tasks/Dummy/script_task.py').read_text())

    def test_rollback_uses_verified_backup_even_if_candidate_was_damaged(self):
        job,ws=self.ready();self.deployer.apply(job)
        (ws.candidate/'tasks/Dummy/script_task.py').write_text('DAMAGED CANDIDATE')
        self.assertTrue(self.deployer.rollback(self.store.get(job['id'])))
        self.assertEqual((self.repo/'tasks/Dummy/script_task.py').read_bytes(),self.original_bytes)

    # ---------- 8 项升级新增覆盖：认领时效 / 软延期 / 独立预算 / 补丁安全 ----------

    def test_expired_collaborator_claim_does_not_block(self):
        stale=(datetime.now()-timedelta(days=2)).strftime('%Y-%m-%d %H:%M')
        (self.repo/'AI_COORD.md').write_text(f'| `tasks/Dummy/` | Other | {stale} | 进行中 |')
        self.assertEqual(active_claims(self.repo),[])
        assert_unclaimed(self.repo,['tasks/Dummy/script_task.py'])  # 陈旧认领不阻塞

    def test_fresh_and_dateless_claims_still_block(self):
        fresh=datetime.now().strftime('%Y-%m-%d %H:%M')
        for stamp in (fresh,datetime.now().strftime('%Y-%m-%d'),'now'):
            with self.subTest(stamp=stamp):
                (self.repo/'AI_COORD.md').write_text(f'| `tasks/Dummy/` | Other | {stamp} | 进行中 |')
                with self.assertRaises(ValueError):
                    assert_unclaimed(self.repo,['tasks/Dummy/script_task.py'])

    def test_claim_expires_with_time_and_owner_ignore(self):
        one_hour_ago=(datetime.now()-timedelta(hours=1)).strftime('%Y-%m-%d %H:%M')
        board=(f'| `tasks/Dummy/` | Me | {one_hour_ago} | 进行中 |\n'
               '| `tasks/Other/` | You | now | 进行中 |\n')
        (self.repo/'AI_COORD.md').write_text(board)
        with self.assertRaises(ValueError):  # 24h 窗口内仍有效
            assert_unclaimed(self.repo,['tasks/Dummy/script_task.py'])
        future=time.time()+48*3600  # 48 小时后同一认领视为陈旧；无日期认领仍保守有效
        self.assertEqual(active_claims(self.repo,now=future),['tasks/Other'])
        assert_unclaimed(self.repo,['tasks/Dummy/script_task.py'],now=future)
        self.assertEqual(active_claims(self.repo,ignore_owner='Me'),['tasks/Other'])

    def test_claim_conflict_soft_defers_without_consuming_attempts(self):
        job=self.store.enqueue('oas1',self.payload)
        (self.repo/'AI_COORD.md').write_text('| `tasks/Dummy/` | Other | now | 进行中 |')
        factory=Mock(side_effect=AssertionError('must not call model'))
        engine=RepairEngine(self.repo,self.logs,agent_factory=factory,deployer=self.deployer)
        with patch('module.ai_recovery.repair_worker.load_config',return_value=self.cfg),patch.object(engine,'import_legacy'):
            result=engine.tick('oas1')
        self.assertEqual(result['status'],'deferred');self.assertTrue(result.get('soft_defer'))
        current=self.store.get(job['id'])
        self.assertEqual(current['attempts'],0)  # 认领冲突绝不消耗 max_attempts
        self.assertGreater(current['next_try'],time.time())
        factory.assert_not_called()

    def test_repair_budget_defer_is_soft_and_retried_later(self):
        job=self.store.enqueue('oas1',self.payload)
        class DeferredAgent:
            def run(self,job):raise RepairDeferredError('代码维修预算暂缓: per_hour_calls=24')
        engine=RepairEngine(self.repo,self.logs,agent_factory=lambda cfg:DeferredAgent(),deployer=self.deployer)
        with patch('module.ai_recovery.repair_worker.load_config',return_value=self.cfg),patch.object(engine,'import_legacy'):
            result=engine.tick('oas1')
        self.assertEqual(result['status'],'deferred');self.assertTrue(result.get('soft_defer'))
        current=self.store.get(job['id'])
        self.assertEqual(current['attempts'],0)
        self.assertGreater(current['next_try'],time.time())

    def repair_tool_calls(self):
        calls=[]
        def call(name,args):
            calls.append({'id':'call_'+str(len(calls)),'type':'function','function':{
                'name':name,'arguments':json.dumps(args)}})
        call('edit_source',{'path':'tasks/Dummy/script_task.py','old_text':OLD,'new_text':NEW})
        call('write_regression',{'code':REGRESSION})
        call('run_tests',{})
        call('finish',{'summary':'empty input handled'})
        return calls

    def test_repair_model_override_and_independent_budget_file(self):
        job=self.store.enqueue('oas1',self.payload);job=self.store.claim_next('oas1',2)
        self.cfg.code_repair.model='deepseek-repair-x'
        client=StubClient(self.cfg,[self.response(None,self.repair_tool_calls(),'tool_calls')])
        agent=RepairAgent(self.cfg,self.store,self.repo,client=client)
        self.assertEqual(agent.api_cfg.api.model,'deepseek-repair-x')
        self.assertEqual(agent.api_cfg.budget.per_hour_calls,24)
        manifest=agent.run(job)
        self.assertTrue(manifest['test']['passed'])
        self.assertEqual(self.store.get(job['id'])['status'],'ready')
        self.assertEqual(client.sent[0]['model'],'deepseek-repair-x')
        # 独立预算文件：代码维修不挤占视觉自愈的 budget_oas1.json
        self.assertTrue((self.store.root/'budget_repair_oas1.json').exists())
        self.assertFalse((self.store.root/'budget_oas1.json').exists())
        self.assertTrue((Path(self.store.get(job['id'])['artifact'])/'tools.json').exists())

    def test_visual_budget_exhaustion_does_not_block_repair(self):
        self.store.root.mkdir(parents=True,exist_ok=True)
        (self.store.root/'budget_oas1.json').write_text(json.dumps({
            'day':time.strftime('%Y-%m-%d'),'hour':int(time.time()//3600),
            'calls_today':999,'calls_hour':999,'cost_today':0.0,'tokens_today':0,
            'last_call':0.0,'history':[],'task_calls_hour':{}}),encoding='utf-8')
        job=self.store.enqueue('oas1',self.payload);job=self.store.claim_next('oas1',2)
        client=StubClient(self.cfg,[self.response(None,self.repair_tool_calls(),'tool_calls')])
        manifest=RepairAgent(self.cfg,self.store,self.repo,client=client).run(job)
        self.assertTrue(manifest['test']['passed'])

    def test_failed_repair_still_writes_tools_transcript(self):
        job=self.store.enqueue('oas1',self.payload);job=self.store.claim_next('oas1',2)
        self.cfg.code_repair.max_model_calls=2
        client=StubClient(self.cfg,[self.response('先思考一下……'),self.response('再分析……')])
        with self.assertRaises(RuntimeError):
            RepairAgent(self.cfg,self.store,self.repo,client=client).run(job)
        transcript=Path(self.store.get(job['id'])['artifact'])/'tools.json'
        self.assertTrue(transcript.exists())
        self.assertIsInstance(json.loads(transcript.read_text(encoding='utf-8')),list)

    def test_exhausted_repair_budget_defers_before_model_call(self):
        fixed=1_760_000_000.0
        budget=self.store.root/'budget_repair_oas1.json'
        budget.parent.mkdir(parents=True,exist_ok=True)
        budget.write_text(json.dumps({'day':time.strftime('%Y-%m-%d',time.localtime(fixed)),
            'hour':int(fixed//3600),'calls_today':0,'calls_hour':999,'cost_today':0.0,
            'tokens_today':0,'last_call':0.0,'history':[],'task_calls_hour':{}}),encoding='utf-8')
        job=self.store.enqueue('oas1',self.payload);job=self.store.claim_next('oas1',2)
        client=StubClient(self.cfg,[self.response(self.valid_ui())])
        with patch('module.ai_recovery.repair_agent.time.time',return_value=fixed), \
             patch('module.ai_recovery.budget.time.time',return_value=fixed):
            with self.assertRaises(RepairDeferredError):
                RepairAgent(self.cfg,self.store,self.repo,client=client).run(job)
        self.assertEqual(client.sent,[])  # 预算拒绝发生在任何模型调用之前

    def test_patch_allows_safe_string_and_list_methods(self):
        (self.repo/'tasks/Dummy/script_task.py').write_text(
            'class ScriptTask:\n'
            '    def pick(self, items, text):\n'
            '        return [text, items]\n',encoding='utf-8')
        ws=self.workspace('safe_methods')
        result=ws.patch('tasks/Dummy/script_task.py','        return [text, items]',
                        "        items.remove(None)\n        return text.replace('a', 'b')")
        self.assertEqual(result['changed_lines'],3)

    def test_patch_still_blocks_dangerous_call_prefixes(self):
        (self.repo/'tasks/Dummy/script_task.py').write_text(
            'class ScriptTask:\n'
            '    def pick(self):\n'
            '        return 0\n',encoding='utf-8')
        ws=self.workspace('blocked_prefix')
        for new in ("        return __import__('os').system('id')",
                    '        return self.device.click(1, 1)',
                    '        return subprocess.run(["bad"])',
                    '        return os.remove("x")',
                    '        return shutil.rmtree("x")'):
            with self.subTest(new=new),self.assertRaises(ValueError):
                ws.patch('tasks/Dummy/script_task.py','        return 0',new)

    def test_resolve_primary_source_skips_toolkit_paths(self):
        payload=dict(self.payload,source_path='toolkit/Lib/site-packages/pkg.py',
                     frames=[{'path':'toolkit/Lib/site-packages/pkg.py','function':'f','line':1},
                             {'path':'module/ai_recovery/client.py','function':'g','line':2},
                             {'path':'tasks/Dummy/script_task.py','function':'run','line':3}])
        self.assertEqual(_resolve_primary_source(self.repo,{'task':'Dummy','payload':payload}),
                         'tasks/Dummy/script_task.py')

    def test_resolve_primary_source_falls_back_to_task_script(self):
        payload=dict(self.payload,source_path='',frames=[
            {'path':'toolkit/venv/site.py','function':'f','line':1},
            {'path':'module/server/app.py','function':'g','line':2}])
        self.assertEqual(_resolve_primary_source(self.repo,{'task':'Dummy','payload':payload}),
                         'tasks/Dummy/script_task.py')

    def test_engine_writes_back_resolved_primary_source(self):
        payload=dict(self.payload,source_path='toolkit/Lib/site-packages/x.py',
                     frames=[{'path':'tasks/Dummy/script_task.py','function':'run','line':1}])
        self.store.enqueue('oas1',payload)
        seen={}
        class CaptureAgent:
            def run(self,job):seen['primary']=job['payload']['source_path']
        engine=RepairEngine(self.repo,self.logs,agent_factory=lambda cfg:CaptureAgent(),deployer=self.deployer)
        with patch('module.ai_recovery.repair_worker.load_config',return_value=self.cfg),patch.object(engine,'import_legacy'):
            result=engine.tick('oas1')
        self.assertEqual(seen['primary'],'tasks/Dummy/script_task.py')
        self.assertEqual(result['status'],'planning')

    def test_prune_old_workdirs_keeps_active_and_recent(self):
        work=self.store.root/'code_repair'/'work'
        old_terminal=work/'old_succeeded';old_terminal.mkdir(parents=True)
        old_active=work/'old_deferred';old_active.mkdir(parents=True)
        fresh=work/'fresh_queued';fresh.mkdir(parents=True)
        job=self.store.enqueue('oas1',self.payload)
        self.store.update(job['id'],'deferred',artifact=str(old_active))
        old=time.time()-30*86400
        os.utime(old_terminal,(old,old));os.utime(old_active,(old,old))
        removed=prune_old_workdirs(self.store.root,self.store,keep_days=7.0,now=time.time())
        self.assertEqual(removed,1)
        self.assertFalse(old_terminal.exists())
        self.assertTrue(old_active.exists())  # 非终态任务引用的目录保留
        self.assertTrue(fresh.exists())

    def test_new_config_fields_and_bounds_normalize(self):
        cfg=AiRecoveryConfig.from_dict({
            'api':{'include_ocr_hints':True},
            'budget':{'per_task_hour_calls':999},
            'knowledge':{'share_across_accounts':True},
            'code_repair':{'model':'deepseek-repair-x','per_hour_calls':1,
                           'claim_max_age_hours':0.5,'work_retention_days':999}})
        self.assertTrue(cfg.api.include_ocr_hints)
        self.assertEqual(cfg.budget.per_task_hour_calls,60)
        self.assertTrue(cfg.knowledge.share_across_accounts)
        self.assertEqual(cfg.code_repair.model,'deepseek-repair-x')
        self.assertEqual(cfg.code_repair.per_hour_calls,4)
        self.assertEqual(cfg.code_repair.claim_max_age_hours,1.0)
        self.assertEqual(cfg.code_repair.work_retention_days,90.0)
        defaults=AiRecoveryConfig.from_dict({})
        self.assertTrue(defaults.api.include_ocr_hints)
        self.assertEqual(defaults.budget.per_task_hour_calls,0)
        self.assertFalse(defaults.knowledge.share_across_accounts)


if __name__=='__main__':unittest.main(verbosity=2)
