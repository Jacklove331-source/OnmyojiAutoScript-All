"""Behavioral checks of the real mobile modules with an isolated fake game/device."""
import importlib.util
import pathlib
import sys
import threading
import time
import types
import unittest

ROOT = pathlib.Path(sys.argv.pop(1))
sys.path.insert(0, str(ROOT))


class Quiet:
    def __getattr__(self, key):
        return lambda *a, **k: None


def load(name):
    spec = importlib.util.spec_from_file_location('oas_mobile.' + name, ROOT / 'oas_mobile' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.instantiated = threading.Event()
        self.navigation_timer_reset = False
        self.task_started_with_fresh_timer = False
        self.login_ok = True
        self.paused = False
        self.runner = load('task_runner')
        self.runner.prepare_workspace = lambda: '/isolated'
        import logging

        self.task_log_handler = self.runner._task_log_handler
        self.runner._task_log_handler = lambda *args: logging.NullHandler()
        self.runner._make_config = lambda name: types.SimpleNamespace(model=types.SimpleNamespace())
        bridge = types.ModuleType('oas_mobile.bridge')
        bridge.paused = lambda: self.paused
        sys.modules['oas_mobile.bridge'] = bridge
        import oas_mobile
        oas_mobile.bridge = bridge
        login = types.ModuleType('oas_mobile.login')
        login.ensure_logged_in = lambda display: self.login_ok
        sys.modules['oas_mobile.login'] = login
        device = types.ModuleType('module.device.device')
        device.Device = lambda config: types.SimpleNamespace(
            app_start=lambda: 1, _ensure_display=lambda: 1,
            stuck_record_clear=lambda: setattr(self, 'navigation_timer_reset', True),
        )
        sys.modules['module.device.device'] = device
        errors = types.ModuleType('module.exception')
        errors.TaskEnd = type('TaskEnd', (Exception,), {})
        sys.modules['module.exception'] = errors
        task_module = types.ModuleType('tasks.Probe.script_task')
        outer = self

        class Task:
            def __init__(self, config, device):
                outer.task_started_with_fresh_timer = outer.navigation_timer_reset
                outer.instantiated.set()

            def run(self):
                outer.entered.set()
                while not outer.release.wait(.01):
                    if 'oas_mobile.runtime' in sys.modules:
                        sys.modules['oas_mobile.runtime'].checkpoint()
                return True

        task_module.ScriptTask = Task
        sys.modules['tasks.Probe.script_task'] = task_module
        logger = types.ModuleType('module.logger')
        logger.logger = Quiet()
        sys.modules['module.logger'] = logger

    def tearDown(self):
        self.paused = False
        self.release.set()

    def wait_done(self):
        deadline = time.monotonic() + 1
        while self.runner.status()['running'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.runner.status()['running'])

    def test_duplicate_start_returns_without_deadlock(self):
        self.runner.run_task('Probe')
        self.assertTrue(self.entered.wait(1))
        result = []
        caller = threading.Thread(target=lambda: result.append(self.runner.run_task('Probe')), daemon=True)
        caller.start()
        caller.join(.3)
        self.assertFalse(caller.is_alive(), 'duplicate start deadlocked')
        self.assertFalse(result[0]['ok'])

    def test_login_failure_never_starts_business_task(self):
        self.login_ok = False
        self.release.set()
        self.runner.run_task('Probe', wait=True)
        self.assertFalse(self.instantiated.is_set(), 'task started after failed login')
        self.assertFalse(self.runner.status()['ok'])

    def test_manual_restart_leaves_login_to_restart_itself(self):
        self.login_ok = False
        self.release.set()
        sys.modules['tasks.Restart.script_task'] = sys.modules['tasks.Probe.script_task']
        self.addCleanup(sys.modules.pop, 'tasks.Restart.script_task')
        self.runner.run_task('Restart', wait=True)
        self.assertTrue(self.runner.status()['ok'])
        self.assertTrue(self.instantiated.is_set())
        self.assertFalse(self.navigation_timer_reset)

    def test_successful_login_resets_the_navigation_timer_before_the_task(self):
        self.release.set()
        self.runner.run_task('Probe', wait=True)
        self.assertTrue(self.task_started_with_fresh_timer)

    def test_stop_cancels_current_task(self):
        self.assertTrue(callable(getattr(self.runner, 'stop_task', None)), 'no task stop operation')
        self.runner.run_task('Probe')
        self.assertTrue(self.entered.wait(1))
        result = self.runner.stop_task(wait=True)
        self.wait_done()
        self.assertTrue(result['stopped'])
        self.assertTrue(self.runner.status()['cancelled'])
        self.assertFalse(self.runner.status()['ok'])

    def test_status_log_snapshot_cannot_mutate_live_state(self):
        self.runner._log('keep this')
        snap = self.runner.status()
        snap['log_tail'].clear()
        self.assertTrue(self.runner.status()['log_tail'])

    def test_real_task_result_is_written_to_the_private_log_directory(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as workspace:
            self.runner.prepare_workspace = lambda: workspace
            self.runner._task_log_handler = self.task_log_handler
            self.release.set()
            result = self.runner.run_task('Probe', wait=True)
            log_dir = pathlib.Path(workspace) / 'log' / result['run_id']
            written = list(log_dir.glob('*_oas1_Probe.txt'))
            self.assertEqual(len(written), 1)
            text = written[0].read_text(encoding='utf-8')
            self.assertIn('task instantiated: tasks.Probe.script_task', text)
            self.assertIn('task finished: ok=True', text)

    def test_unavailable_log_storage_does_not_stop_the_task(self):
        def fail_to_open(*args):
            raise OSError('isolated disk failure')

        self.runner._task_log_handler = fail_to_open
        self.release.set()
        self.runner.run_task('Probe', wait=True)
        self.assertTrue(self.runner.status()['ok'])
        self.assertTrue(any('could not save task log' in line for line in self.runner.status()['log_tail']))

    def test_successful_task_finishes_normally(self):
        self.release.set()
        self.runner.run_task('Probe', wait=True)
        self.assertTrue(self.runner.status()['ok'])
        self.assertFalse(self.runner.status()['running'])

    def test_pause_blocks_work_until_resume(self):
        self.assertTrue((ROOT / 'oas_mobile/runtime.py').exists(), 'no cooperative runtime control')
        self.paused = True
        self.runner.run_task('Probe')
        time.sleep(.1)
        self.assertFalse(self.entered.is_set())
        self.paused = False
        self.assertTrue(self.entered.wait(1))

    def test_stop_while_paused_is_responsive(self):
        self.assertTrue(callable(getattr(self.runner, 'stop_task', None)))
        self.paused = True
        self.runner.run_task('Probe')
        time.sleep(.05)
        self.assertTrue(self.runner.stop_task(wait=True)['stopped'])


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RuntimeTests('test_successful_task_finishes_normally')
        self.fixture.setUp()
        import oas_mobile
        oas_mobile.task_runner = self.fixture.runner
        self.runner = self.fixture.runner
        self.scheduler = load('scheduler')
        from tempfile import TemporaryDirectory

        storage = TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        self.preferences = pathlib.Path(storage.name) / 'safety_limits.json'
        self.totals = pathlib.Path(storage.name) / 'scheduler_rounds.json'
        self.scheduler._round_files = lambda: (self.preferences, self.totals)
        self.configs = []
        self.scheduler.configuration_tasks = lambda config: self.configs.append(config) or [{'name': 'Probe', 'enabled': True}]
        self.scheduler._defer_if_due = lambda *a: None
        from datetime import datetime, timedelta
        self.future = datetime.now() + timedelta(hours=1)
        self.scheduler._native_next = lambda name: types.SimpleNamespace(command='Probe', next_run=self.future)

    def tearDown(self):
        self.fixture.tearDown()
        self.scheduler.stop(wait=True)

    def test_start_without_explicit_list_reads_enabled_selected_config(self):
        result = self.scheduler.start(config_name='oas3')
        self.assertTrue(result['ok'])
        self.assertEqual(result['tasks'], ['Probe'])
        self.assertEqual(result['config'], 'oas3')
        self.assertIn('oas3', self.configs)

    def test_scheduler_duplicate_start_is_bounded(self):
        self.scheduler.start()
        out = []
        caller = threading.Thread(target=lambda: out.append(self.scheduler.start()), daemon=True)
        caller.start()
        caller.join(.3)
        self.assertFalse(caller.is_alive())
        self.assertFalse(out[0]['ok'])

    def test_scheduler_stop_cancels_its_active_task(self):
        self.scheduler.start('Probe')
        self.assertTrue(self.fixture.entered.wait(1))
        result = self.scheduler.stop(wait=True)
        self.assertTrue(result['stopped'])
        self.assertFalse(self.runner.status()['running'])
        self.assertTrue(self.runner.status()['cancelled'])
        self.assertEqual(self.scheduler.status()['completed'], 0)

    def test_scheduler_stays_stopping_until_its_task_cancels(self):
        first_join = threading.Event()
        stop_task = self.runner.stop_task

        def stop_with_delayed_checkpoint(wait=False, timeout=5, run_id=None):
            if not wait or not first_join.is_set():
                if wait:
                    first_join.set()
                return {'ok': True, 'stopped': False, 'status': self.runner.status()}
            return stop_task(wait=wait, timeout=timeout, run_id=run_id)

        self.runner.stop_task = stop_with_delayed_checkpoint
        self.assertTrue(self.scheduler.start('Probe,Probe')['ok'])
        self.assertTrue(self.fixture.entered.wait(1))
        self.scheduler.stop()
        self.assertTrue(first_join.wait(1))
        self.scheduler._THREAD.join(timeout=2)
        status = self.scheduler.status()
        self.assertFalse(status['running'])
        self.assertEqual(status['error'], '')
        self.assertEqual(status['task_index'], 1)
        self.assertTrue(self.runner.status()['cancelled'])

    def test_failed_restart_prevents_the_next_scheduled_task(self):
        names = []

        def run_task(name, config, *, login):
            names.append((name, login))
            return {'ok': True, 'run_id': 'restart'}

        self.runner.run_task = run_task
        self.runner.status = lambda: {
            'run_id': 'restart', 'running': False, 'phase': 'failed', 'ok': False,
            'error': 'Role could not be confirmed',
        }
        self.assertTrue(self.scheduler.start('Restart,Probe')['ok'])
        self.scheduler._THREAD.join(timeout=1)
        status = self.scheduler.status()
        self.assertFalse(status['running'])
        self.assertIn('Role could not be confirmed', status['error'])
        self.assertEqual(names, [('Restart', False)])

    def test_healthy_long_task_is_not_cut_off_after_forty_minutes(self):
        clock = [0.0]
        self.scheduler.time = types.SimpleNamespace(monotonic=lambda: clock[0],
                                                    time=time.time, strftime=time.strftime)
        self.scheduler.start('Probe')
        self.assertTrue(self.fixture.entered.wait(1))
        clock[0] = 3601.0
        time.sleep(.15)
        self.assertTrue(self.runner.status()['running'], 'a healthy task was stopped by an unrelated outer timeout')

    def test_existing_manual_task_is_not_misreported_as_scheduler_work(self):
        self.runner.run_task('Probe')
        self.assertTrue(self.fixture.entered.wait(1))
        self.assertFalse(self.scheduler.start('Probe')['ok'])

    def test_no_enabled_tasks_rejects_start(self):
        self.scheduler.configuration_tasks = lambda name: [{'name': 'Probe', 'enabled': False}]
        self.assertFalse(self.scheduler.start()['ok'])
        self.assertFalse(self.scheduler.status()['running'])

    def test_future_task_waits_for_its_native_due_time(self):
        self.scheduler.start()
        time.sleep(.05)
        self.assertFalse(self.fixture.entered.is_set())
        self.assertGreater(self.scheduler.status()['next_at'], time.time() + 3000)

    def test_daily_round_limit_stops_and_survives_reopening_the_scheduler(self):
        self.preferences.write_text('{"maxRoundsPerDay": 1}', encoding='utf-8')
        self.fixture.release.set()
        self.assertTrue(self.scheduler.start('Probe')['ok'])
        self.scheduler._THREAD.join(timeout=2)
        ended = self.scheduler.status()
        self.assertFalse(ended['running'])
        self.assertTrue(ended['limit_reached'])
        self.assertEqual(ended['rounds_today'], 1)
        self.assertEqual(ended['completed'], 1)

        reloaded = load('scheduler')
        reloaded._round_files = lambda: (self.preferences, self.totals)
        self.assertEqual(reloaded.status()['rounds_today'], 1)
        self.assertTrue(reloaded.status()['limit_reached'])
        self.assertFalse(reloaded.start('Probe')['ok'])
        self.assertTrue(reloaded.status()['limit_reached'])

    def test_daily_round_counter_resets_on_the_next_local_day(self):
        self.preferences.write_text('{"maxRoundsPerDay": 1}', encoding='utf-8')
        self.totals.write_text('{"date": "2000-01-01", "rounds": 99}', encoding='utf-8')
        self.fixture.release.set()
        self.assertTrue(self.scheduler.start('Probe')['ok'])
        self.scheduler._THREAD.join(timeout=2)
        self.assertEqual(self.scheduler.status()['rounds_today'], 1)

    def test_scheduler_status_is_a_snapshot(self):
        self.scheduler.start()
        snapshot = self.scheduler.status()
        snapshot['tasks'].clear()
        self.assertEqual(self.scheduler.status()['tasks'], ['Probe'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
