from __future__ import annotations
from pathlib import Path
import ast,json,tempfile,threading,unittest
from unittest.mock import patch
from module.exception import TaskEnd
from module.task_calendar.store import CalendarStore,task_key
from module.task_calendar.runtime import track_task,observe_schedule,mark_completed
from module.task_calendar.service import report
from module.task_calendar.backfill import import_logs
from module.task_calendar.catalog import rewards

AT='2026-11-14T18:00:00+08:00'

class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='oas-calendar-test-');self.root=Path(self.temp.name)
        (self.root/'config').mkdir();(self.root/'log').mkdir()
        self.config={k:{'scheduler':{'enable':True,'next_run':'2026-11-15 09:00:00'}} for k in ['area_boss','weekly_trifles','daily_trifles']}
        self.config['weekly_trifles']['trifles']={'share_collect':True}
        for name in ['oas1','oas3']:(self.root/'config'/f'{name}.json').write_text(json.dumps(self.config),encoding='utf-8')
        self.store=CalendarStore(self.root/'calendar.sqlite3')
    def tearDown(self):self.temp.cleanup()
    def entries(self,account='oas1'):
        return self.store.records(account,'2020-01-01','2040-01-01')
    def run_task(self,decision=True,*,account='oas1',task='AreaBoss',error=TaskEnd):
        try:
            with track_task(account,task,store=self.store):
                observe_schedule(account,task,decision)
                if error:raise error()
        except BaseException as actual:
            if not isinstance(actual,(TaskEnd,RuntimeError,KeyboardInterrupt)):raise
        return self.entries(account)[-1]
    def data(self,at=AT):return report('oas1',root=self.root,store=self.store,at=at)
    def seed(self,task,*,account='oas1',at=AT,outcome='completed'):
        rid=self.store.begin(account,task,at=at);self.store.finish(rid,outcome,at=at);return rid

    def test_successful_taskend_is_completed(self):self.assertEqual(self.run_task()['outcome'],'completed')
    def test_normal_success_return_is_completed(self):self.assertEqual(self.run_task(error=None)['outcome'],'completed')
    def test_deferred_taskend_is_not_completed(self):self.assertEqual(self.run_task(False)['outcome'],'deferred')
    def test_unconfirmed_taskend_is_not_completed(self):self.assertEqual(self.run_task(None)['outcome'],'unconfirmed')
    def test_exception_after_success_flag_is_failed(self):self.assertEqual(self.run_task(error=RuntimeError)['outcome'],'failed')
    def test_cancellation_after_success_flag_is_not_completed(self):self.assertEqual(self.run_task(error=KeyboardInterrupt)['outcome'],'cancelled')
    def test_recovered_outer_true_cannot_change_recorded_failure(self):
        entry=self.run_task(error=RuntimeError);outer_result=True
        self.assertTrue(outer_result);self.assertEqual(entry['outcome'],'failed')
    def test_explicit_custom_time_terminal_path_is_completed(self):
        with track_task('oas1','KekkaiActivation',store=self.store):mark_completed('KekkaiActivation')
        self.assertEqual(self.entries()[-1]['outcome'],'completed')
    def test_wrong_account_or_other_task_delay_is_ignored(self):
        with track_task('oas1','AreaBoss',store=self.store):
            observe_schedule('oas3','AreaBoss',True);observe_schedule('oas1','WantedQuests',True)
        self.assertEqual(self.entries()[-1]['outcome'],'unconfirmed')
    def test_api_reschedule_without_active_task_does_not_mark_completed(self):
        observe_schedule('oas1','AreaBoss',True);self.assertEqual(self.entries(),[])
    def test_context_does_not_leak_after_task(self):
        self.run_task();observe_schedule('oas1','AreaBoss',False)
        self.assertEqual(self.entries()[-1]['outcome'],'completed')
    def test_context_does_not_leak_to_other_thread(self):
        with track_task('oas1','AreaBoss',store=self.store):
            t=threading.Thread(target=lambda:observe_schedule('oas1','AreaBoss',True));t.start();t.join()
        self.assertEqual(self.entries()[-1]['outcome'],'unconfirmed')
    def test_duplicate_finish_cannot_rewrite_completed_result(self):
        rid=self.seed('AreaBoss');self.store.finish(rid,'failed',at=AT)
        self.assertEqual(self.entries()[-1]['outcome'],'completed')
    def test_persisted_records_survive_store_restart(self):
        self.seed('AreaBoss');again=CalendarStore(self.store.path)
        self.assertEqual(len(again.records('oas1','2026-11-14','2026-11-15')),1)
    def test_account_isolation(self):
        self.seed('AreaBoss',account='oas3');self.assertFalse(next(r for r in self.data()['rows'] if r['id']=='area_boss')['done'])
    def test_daily_completion_resets_next_natural_day(self):
        self.seed('AreaBoss');self.assertTrue(next(r for r in self.data()['rows'] if r['id']=='area_boss')['done'])
        self.assertFalse(next(r for r in self.data('2026-11-15T18:00:00+08:00')['rows'] if r['id']=='area_boss')['done'])
    def test_weekly_completion_remains_until_next_monday(self):
        self.seed('WeeklyTrifles',at='2026-11-10T11:00:00+08:00')
        self.assertTrue(next(r for r in self.data()['rows'] if r['id']=='weekly_trifles')['done'])
        self.assertFalse(next(r for r in self.data('2026-11-16T11:00:00+08:00')['rows'] if r['id']=='weekly_trifles')['done'])
    def test_later_failed_rerun_does_not_erase_completed_day(self):
        self.seed('AreaBoss',at='2026-11-14T10:00:00+08:00');self.seed('AreaBoss',at='2026-11-14T11:00:00+08:00',outcome='failed')
        row=next(r for r in self.data()['rows'] if r['id']=='area_boss');self.assertTrue(row['done']);self.assertEqual(row['week_count'],1)
    def test_monday_midnight_is_not_in_previous_week(self):
        self.seed('AreaBoss',at='2026-11-16T00:00:00+08:00');self.assertEqual(next(r for r in self.data()['rows'] if r['id']=='area_boss')['week_count'],0)
    def test_timezone_uses_shanghai_even_when_input_is_utc(self):
        self.assertEqual(self.data('2026-11-13T20:00:00+00:00')['today'],'2026-11-14')
    def test_future_next_run_is_not_completion_evidence(self):
        self.assertEqual(self.data()['summary']['completed'],0)
    def test_reading_report_does_not_modify_game_config(self):
        before=(self.root/'config/oas1.json').read_bytes();self.data();self.assertEqual(before,(self.root/'config/oas1.json').read_bytes())
    def test_ticket_spending_summon_is_not_ticket_income(self):
        self.assertEqual(rewards('daily_trifles',{'daily_trifles':{'trifles_config':{'one_summon':True}}}),[])
    def test_only_enabled_weekly_share_rewards_are_highlighted(self):
        values=rewards('weekly_trifles',self.config);self.assertEqual([r['currency'] for r in values],['ticket'])
    def test_mystery_shop_ticket_purchase_is_explicit_and_not_jade_income(self):
        values=rewards('mystery_shop',{'mystery_shop':{'shop_config':{'mystery_amulet':True}}})
        self.assertEqual(values[0]['kind'],'purchase');self.assertEqual(values[0]['currency'],'ticket');self.assertIn('花费勾玉',values[0]['detail'])
    def test_runtime_write_failure_does_not_interrupt_actual_task(self):
        class Broken:
            def begin(self,*args):raise OSError('test')
        called=[]
        with self.assertLogs('oas.task_calendar',level='WARNING'):
            with track_task('oas1','AreaBoss',store=Broken()):called.append(True)
        self.assertEqual(called,[True])
    def test_backend_hooks_exist_without_changing_exception_handlers(self):
        root=Path(__file__).resolve().parents[1]
        script=(root/'script.py').read_text(encoding='utf-8-sig');config=(root/'module/config/config.py').read_text(encoding='utf-8-sig')
        self.assertIn('with track_task(self.config_name, command):',script)
        self.assertIn('observe_schedule(self.config_name, task, success)',config)
        self.assertIn('self._ai_defer_failed_task(task_instance, e, command)',script)
        ast.parse(script);ast.parse(config)
    def log(self,name='oas1',decision='True',error=False):
        text='2026-11-14 10:00:00.000 | script.py:1000 | INFO | Scheduler: Start task `AreaBoss`\n'
        text+='2026-11-14 10:01:00.000 | config.py:0373 | INFO | Delay task `area_boss` to tomorrow (server_update=True,\n'
        text+=' success='+decision+')\n'
        if error:text+='2026-11-14 10:01:30.000 | script.py:1 | ERROR | failed\n'
        text+='2026-11-14 10:02:00.000 | script.py:1070 | INFO | Scheduler: End task `AreaBoss`\n'
        (self.root/'log'/('2026-11-14_'+name+'.txt')).write_text(text,encoding='utf-8')
    def test_legacy_wrapped_success_log_backfills_and_is_idempotent(self):
        self.log();a=import_logs(self.root,'oas1',store=self.store,at=AT);b=import_logs(self.root,'oas1',store=self.store,at=AT)
        self.assertEqual(a['imported_completed'],1);self.assertEqual(b['imported_completed'],0)
    def test_legacy_failure_or_errors_are_not_marked_completed(self):
        for decision,error in [('False',False),('None',False),('True',True)]:
            self.log(decision=decision,error=error);self.assertEqual(import_logs(self.root,'oas1',store=self.store,at=AT)['imported_completed'],0)
    def test_legacy_other_account_log_is_not_imported(self):
        self.log(name='oas3');self.assertEqual(import_logs(self.root,'oas1',store=self.store,at=AT)['imported_completed'],0)
    def test_legacy_log_does_not_duplicate_live_run(self):
        rid=self.store.begin('oas1','AreaBoss',at='2026-11-14T10:00:00+08:00');self.store.finish(rid,'completed',at='2026-11-14T10:02:00+08:00')
        self.log();self.assertEqual(import_logs(self.root,'oas1',store=self.store,at=AT)['imported_completed'],0)
    def test_invalid_account_paths_are_rejected(self):
        for name in ['../oas1','oas1/key','Home','template']:
            with self.assertRaises(ValueError):report(name,root=self.root,store=self.store)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_calendar_contract(self):
        from fastapi import FastAPI
        from module.server.calendar_router import calendar_app
        import module.task_calendar.service as service
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config').mkdir();(root/'config/oas1.json').write_text(json.dumps({'area_boss':{'scheduler':{'enable':True}}}))
            app=FastAPI();app.include_router(calendar_app)
            async def request(path,method='GET'):
                from urllib.parse import urlsplit
                url=urlsplit(path);messages=[]
                scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':method,'scheme':'http',
                       'path':url.path,'raw_path':url.path.encode(),'query_string':url.query.encode(),'root_path':'',
                       'headers':[],'client':('127.0.0.1',1234),'server':('test',80)}
                async def receive():return {'type':'http.request','body':b'','more_body':False}
                async def send(message):messages.append(message)
                await app(scope,receive,send)
                start=next(x for x in messages if x['type']=='http.response.start')
                body=b''.join(x.get('body',b'') for x in messages if x['type']=='http.response.body')
                return start['status'],dict((k.decode(),v.decode()) for k,v in start['headers']),json.loads(body)
            with patch.object(service,'ROOT',root):
                status,headers,body=await request('/calendar/oas1');self.assertEqual(status,200);self.assertEqual(body['config'],'oas1');self.assertEqual(headers['cache-control'],'no-store')
                self.assertEqual((await request('/calendar/missing'))[0],404)
                self.assertEqual((await request('/calendar/oas1?week=bad'))[0],422)
                self.assertEqual((await request('/calendar/oas1',method='POST'))[0],405)

if __name__=='__main__':unittest.main()
