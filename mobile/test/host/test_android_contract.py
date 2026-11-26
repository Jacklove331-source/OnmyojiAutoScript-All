import importlib.util
import json
import pathlib
import sys
import threading
import time
import types
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(sys.argv.pop(1))
sys.path.insert(0, str(ROOT))


class Quiet:
    def __getattr__(self, name):
        return lambda *a, **k: None


def load_file(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


class NotifierTests(unittest.TestCase):
    def test_task_notification_accepts_both_message_keywords(self):
        from module.notify.notify import Notifier

        notifier = Notifier(enable=True)
        self.assertFalse(notifier.push(title="Task result", content="Completed"))
        self.assertFalse(notifier.push(title="Task result", message="Retry later"))


class DeviceTests(unittest.TestCase):
    def setUp(self):
        import oas_mobile
        self.taps, self.swipes = [], []
        bridge = types.ModuleType('oas_mobile.bridge')
        bridge.target_package = lambda: 'test.game'
        bridge.vdstream_running = lambda: True
        bridge.vdstream_display = lambda: 7
        bridge.vdstream_jpeg = lambda: b''
        bridge.vdstream_info = lambda: {'frame_id': 1, 'age_ms': 10, 'width': 1280,
                                       'height': 720, 'target_on_display': True}
        bridge.paused = lambda: False
        bridge.hidden_preferred = lambda: False
        bridge.hidden_ready = lambda: False
        bridge.display_ensure_app = lambda pkg: -1
        bridge.display_tap = lambda *args: self.taps.append(args) or True
        bridge.display_swipe = lambda *args: self.swipes.append(args) or True
        bridge.display_alive = lambda display: display == 7
        oas_mobile.bridge = bridge
        sys.modules['oas_mobile.bridge'] = bridge
        self.bridge = bridge
        atom = types.ModuleType('module.atom.ocr')
        atom.RuleOcr = lambda **kwargs: object()
        sys.modules['module.atom.ocr'] = atom
        logger = types.ModuleType('module.logger')
        logger.logger = Quiet()
        sys.modules['module.logger'] = logger
        load_file('module.exception', ROOT / 'module/exception.py')
        self.module = load_file('mobile_device_under_test', ROOT / 'module/device/device.py')
        self.device = self.module.Device()

    def test_screenshot_decodes_real_rgb_and_increments_frame(self):
        import cv2
        import numpy as np
        image = np.zeros((32, 48, 3), np.uint8)
        image[:, :, :] = (10, 40, 190)
        ok, encoded = cv2.imencode('.jpg', image)
        self.assertTrue(ok)
        self.bridge.vdstream_jpeg = lambda: encoded.tobytes()
        self.bridge.vdstream_info = lambda: {'frame_id': 1, 'age_ms': 10, 'width': 48,
                                           'height': 32, 'target_on_display': True}
        result = self.device.screenshot()
        self.assertEqual(tuple(result.shape), (720, 1280, 3))
        # 48x32 (3:2) 按高度缩放到 1080x720 后左右各补 100px 黑边，中心像素为真实 RGB (190, 40, 10)
        self.assertEqual(int(result[0, 0, 0]), 0)
        self.assertLess(abs(int(result[360, 640, 0]) - 190), 4)
        self.assertLess(abs(int(result[360, 640, 1]) - 40), 4)
        self.assertLess(abs(int(result[360, 640, 2]) - 10), 4)
        self.assertEqual(self.device.image_frame_id, 1)

    def test_wide_and_narrow_native_points_match_normalization(self):
        # 20:9 宽屏（2400x1080）：高缩放到 720 后宽 1600，左右各裁 160 -> 中心 (640,360) 对应原生 (1200,540)
        self.bridge.vdstream_info = lambda: {'width': 2400, 'height': 1080, 'target_on_display': True}
        self.device.click(640, 360)
        self.assertEqual(self.taps[-1], (7, 1200, 540))
        # 3:2 窄屏（1080x720）：左右各补 100px 黑边 -> 归一 (100, 0) 对应原生 (0, 0)，中心 (640, 360) 对应 (540, 360)
        self.bridge.vdstream_info = lambda: {'width': 1080, 'height': 720, 'target_on_display': True}
        self.device.click(100, 0)
        self.device.click(640, 360)
        self.assertEqual(self.taps[-2:], [(7, 0, 0), (7, 540, 360)])

    def test_dismiss_system_dialog_scales_normalized_coordinates(self):
        import numpy as np
        from unittest.mock import patch

        self.bridge.vdstream_info = lambda: {'width': 1600, 'height': 900, 'target_on_display': True}
        self.device._image = np.zeros((720, 1280, 3), np.uint8)
        fake_ocr = types.SimpleNamespace(ocr=lambda img: '是否下载扩展包')
        with patch.object(self.module, 'O_EXIT_DIALOG', fake_ocr), \
                patch.object(self.module.time, 'sleep', lambda *_: None):
            self.assertTrue(self.device._dismiss_system_dialog())
        # C_PACK_MUTE=(312,366) -> (390,458), C_PACK_CANCEL=(480,506) -> (600,632)
        self.assertEqual(self.taps, [(7, 390, 458), (7, 600, 632)])

    def test_press_back_targets_only_background_display(self):
        keys = []
        self.bridge.display_keyevent = lambda display, code: keys.append((display, code)) or True
        self.device.press_back()
        self.assertEqual(keys, [(7, 4)])
        # 无后台屏时绝不能发给主屏
        self.bridge.vdstream_running = lambda: False
        self.bridge.display_alive = lambda display: False
        self.bridge.display_ensure_app = lambda pkg: -1
        with self.assertRaises(RuntimeError):
            self.device.press_back()
        self.assertEqual(keys, [(7, 4)])

    def test_screenshot_uses_on_demand_capture_when_available(self):
        import cv2
        import numpy as np

        captured = []
        _, encoded = cv2.imencode('.jpg', np.zeros((32, 48, 3), np.uint8))
        self.bridge.vdstream_capture = lambda quality=85: captured.append(quality) or encoded.tobytes()
        self.bridge.vdstream_jpeg = lambda: encoded.tobytes()
        self.bridge.vdstream_info = lambda: {'frame_id': 1, 'age_ms': 5, 'width': 48,
                                           'height': 32, 'target_on_display': True}
        self.device.screenshot()
        self.assertEqual(captured, [85])

    def test_cached_native_frame_is_not_counted_as_a_new_screenshot(self):
        import cv2
        import numpy as np
        _, encoded = cv2.imencode('.jpg', np.zeros((32, 48, 3), np.uint8))
        self.bridge.vdstream_jpeg = lambda: encoded.tobytes()
        self.bridge.vdstream_info = lambda: {'frame_id': 1, 'age_ms': 10, 'width': 48,
                                           'height': 32, 'target_on_display': True}
        self.device.screenshot()
        with self.assertRaisesRegex(RuntimeError, 'no fresh frame'):
            self.device.screenshot(timeout=.06)
        self.assertEqual(self.device.image_frame_id, 1)

    def test_screenshot_recovers_after_background_freeze_jump(self):
        import cv2
        import numpy as np
        from unittest.mock import patch
        import module.device.device as device_mod

        _, encoded = cv2.imencode('.jpg', np.zeros((32, 48, 3), np.uint8))
        clock = [100.0]
        frame_state = {'id': 1, 'age': 6000}

        def fake_monotonic():
            return clock[0]

        def fake_sleep(sec):
            # 模拟第一次 sleep 期间被系统墓碑冻结 306 秒，解冻后恢复产帧
            if clock[0] < 200.0:
                clock[0] += 306.0
                frame_state['id'] = 2
                frame_state['age'] = 20
            else:
                clock[0] += sec

        self.bridge.vdstream_jpeg = lambda: encoded.tobytes()
        self.bridge.vdstream_info = lambda: {
            'frame_id': frame_state['id'], 'age_ms': frame_state['age'],
            'width': 48, 'height': 32, 'target_on_display': True,
        }
        self.device._native_frame_id = 1
        self.device._last_capture = 90.0
        self.device._stuck_since = 95.0
        with patch.object(device_mod.time, 'monotonic', side_effect=fake_monotonic), \
                patch.object(device_mod.time, 'sleep', side_effect=fake_sleep):
            frame = self.device.screenshot(timeout=5.0)
        self.assertEqual(tuple(frame.shape), (720, 1280, 3))
        self.assertEqual(self.device._native_frame_id, 2)
        self.assertGreaterEqual(self.device._stuck_since, 400.0)

    def test_frames_from_the_wrong_display_are_not_used(self):
        self.bridge.vdstream_info = lambda: {'frame_id': 2, 'age_ms': 10, 'target_on_display': False}
        with self.assertRaisesRegex(RuntimeError, 'no fresh frame'):
            self.device.screenshot(timeout=.06)
        self.assertIsNone(self.device.image)

    def test_old_native_frames_are_not_used(self):
        self.bridge.vdstream_info = lambda: {'frame_id': 3, 'age_ms': 6000, 'target_on_display': True}
        with self.assertRaisesRegex(RuntimeError, 'no fresh frame'):
            self.device.screenshot(timeout=.06)
        self.assertIsNone(self.device.image)

    def test_click_targets_background_display(self):
        self.device.click(12, 34)
        self.assertEqual(self.taps, [(7, 12, 34)])

    def test_native_resolution_scales_both_taps_and_swipes(self):
        self.bridge.vdstream_info = lambda: {'width': 1600, 'height': 900, 'target_on_display': True}
        self.device.click(640, 360)
        self.device.swipe((128, 72), (640, 360))
        self.assertEqual(self.taps, [(7, 800, 450)])
        self.assertEqual(self.swipes[0][:5], (7, 160, 90, 800, 450))

    def test_input_is_blocked_until_the_game_reaches_the_display(self):
        self.bridge.vdstream_info = lambda: {'width': 1600, 'height': 900, 'target_on_display': False}
        # +43：`_ensure_display` 现在会先把任务搬到流所在屏；搬不动才停流重建，
        # 所以这里可能报 "target game is not ..."（旧路径）或 "background display unavailable"（重建失败路径）。
        # 契约不变：**绝不注入**。
        with self.assertRaisesRegex(RuntimeError, 'target game is not|background display unavailable'):
            self.device.click(640, 360)
        self.assertEqual(self.taps, [])

    def test_no_background_display_never_injects_main_screen(self):
        self.bridge.vdstream_running = lambda: False
        with self.assertRaises(RuntimeError):
            self.device.click(12, 34)
        self.assertEqual(self.taps, [])

    def test_failed_injection_is_an_error(self):
        self.bridge.display_tap = lambda *args: False
        with self.assertRaises(RuntimeError):
            self.device.click(12, 34)

    def test_multi_click_uses_both_coordinates(self):
        self.device.multi_click((12, 34), 2, interval=0)
        self.assertEqual(self.taps, [(7, 12, 34), (7, 12, 34)])

    def test_vector_swipe_uses_roi_width_height(self):
        self.device.swipe_vector((0, 50), box=(100, 200, 300, 400))
        self.assertEqual(self.swipes[0][:5], (7, 250, 400, 250, 450))

    def test_repeated_click_guard_is_active(self):
        errors = sys.modules['module.exception']
        with self.assertRaises(errors.GameTooManyClickError):
            for _ in range(10):
                self.device.click(10, 20, control_name='same button')
        self.assertEqual(len(self.taps), 9)


class ServerTests(unittest.TestCase):
    def setUp(self):
        import oas_mobile
        self.bridge = types.ModuleType('oas_mobile.bridge')
        self.bridge.available = lambda: False
        self.bridge.unavailable_reason = lambda: 'no accessibility'
        self.bridge.shizuku_available = lambda: True
        self.bridge.service_running = lambda: True
        self.bridge.paused = lambda: False
        self.bridge.set_paused = lambda value: False
        self.bridge.vdstream_stop = lambda: True
        oas_mobile.bridge = self.bridge
        sys.modules['oas_mobile.bridge'] = self.bridge
        runner = types.ModuleType('oas_mobile.task_runner')
        runner._valid_config = lambda name: name in ('oas1', 'oas3')
        runner.stop_task = lambda **kwargs: {'ok': True, 'stopped': True}
        sys.modules['oas_mobile.task_runner'] = runner
        scheduler = types.ModuleType('oas_mobile.scheduler')
        scheduler.stop = lambda **kwargs: {'ok': True, 'stopped': True}
        scheduler.configuration_tasks = lambda name: [{'name': 'Probe', 'enabled': name == 'oas3'}]
        sys.modules['oas_mobile.scheduler'] = scheduler
        self.server = load_file('oas_mobile.server', ROOT / 'oas_mobile/server.py')
        self.server.start(0)
        self.port = self.server._HTTPD.server_address[1]

    def tearDown(self):
        self.server.stop()

    def request(self, path, method='GET'):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', method=method)
        with urllib.request.urlopen(req, timeout=2) as response:
            return json.load(response)

    def test_duplicate_service_start_reuses_http_listener(self):
        self.assertIn('already running', self.server.start(self.port))
        self.assertTrue(self.request('/health')['ok'])

    def test_health_recognizes_shizuku_without_accessibility(self):
        health = self.request('/health')
        self.assertTrue(health['bridge_available'])
        self.assertFalse(health['accessibility_ready'])

    def test_config_task_state_is_scoped_to_selected_config(self):
        self.assertTrue(self.request('/config_tasks?config=oas3')['tasks'][0]['enabled'])
        self.assertFalse(self.request('/config_tasks?config=oas1')['tasks'][0]['enabled'])

    def test_config_path_traversal_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request('/config_tasks?config=../wrong')
        self.assertEqual(raised.exception.code, 400)

    def test_rejected_pause_does_not_report_success(self):
        self.assertFalse(self.request('/pause', 'POST')['ok'])

    def test_task_stop_endpoint_can_target_the_current_run(self):
        runner = sys.modules['oas_mobile.task_runner']
        from unittest.mock import patch
        with patch.object(runner, 'stop_task', wraps=runner.stop_task) as stop:
            self.assertTrue(self.request('/task/stop', 'POST')['stopped'])
            stop.assert_called_once_with(run_id=None)
            stop.reset_mock()
            self.assertTrue(self.request('/task/stop?run_id=example', 'POST')['stopped'])
            stop.assert_called_once_with(run_id='example')

    def test_catalog_keeps_configured_task_when_apk_directory_listing_omits_it(self):
        from unittest.mock import patch
        from module.config.config_model import ConfigModel

        self.assertIn('tako', ConfigModel.__fields__)
        with patch('os.listdir', return_value=[]):
            names = self.server._script_menu()['Tasks']
        self.assertIn('Tako', names)
        self.assertIn('Restart', names)

    def test_requested_log_is_read_by_path_and_cannot_escape_the_log_directory(self):
        import os
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary:
            folder = pathlib.Path(temporary)
            self.server._workspace = lambda: temporary
            logs = folder / 'log'
            logs.mkdir()
            previous = logs / 'previous.txt'
            latest = logs / 'active.log'
            previous.write_text('prior first line\nprior last line\n', encoding='utf-8')
            latest.write_text('latest file\n', encoding='utf-8')
            os.utime(previous, (10, 10))
            os.utime(latest, (20, 20))
            (folder / 'outside.txt').write_text('unrelated file', encoding='utf-8')
            listing = self.request('/log/files')['files']
            self.assertEqual([entry['path'] for entry in listing], ['log/active.log', 'log/previous.txt'])
            self.assertEqual(self.request('/log/tail')['lines'], ['latest file'])
            result = self.request('/log/tail?lines=1&path=log%2Fprevious.txt')
            self.assertEqual(result['lines'], ['prior last line'])
            self.assertFalse(self.request('/log/tail?path=log%2F..%2Foutside.txt')['ok'])
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request('/log/tail?lines=invalid')
            self.assertEqual(raised.exception.code, 400)

    def test_translations_return_only_requested_known_keys(self):
        data = self.request('/i18n?keys=Tako,DefinitelyUnknownOption')
        self.assertEqual(set(data['map']), {'Tako'})
        self.assertTrue(data['map']['Tako'])

    def test_monitor_stop_honors_home_false_and_no(self):
        homes = []
        self.bridge.target_package = lambda: 'test.game'
        self.bridge.display_move_task = lambda pkg, disp: True
        self.bridge.send_home = lambda: homes.append(True) or True
        self.assertTrue(self.request('/monitor/stop?home=false')['ok'])
        self.assertTrue(self.request('/monitor/stop?home=no')['ok'])
        self.assertTrue(self.request('/monitor/stop?home=0')['ok'])
        self.assertEqual(homes, [])
        self.assertTrue(self.request('/monitor/stop')['ok'])
        self.assertEqual(homes, [True])

    def test_args_and_put_reject_config_path_traversal(self):
        for bad_script in ('..', '..%2Fsecret'):
            with self.subTest(bad_script=bad_script):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self.request(f'/{bad_script}/Tako/args')
                self.assertEqual(raised.exception.code, 400)
                with self.assertRaises(urllib.error.HTTPError) as raised_put:
                    self.request(f'/{bad_script}/Tako/scheduler/enable/value?value=true', method='PUT')
                self.assertEqual(raised_put.exception.code, 400)

    def test_background_start_prefers_silent_virtual_launch_with_cold_fallback(self):
        # 1) 静默直启成功：建流后直接 display_ensure_app，不打扰主屏（不调 ensure_game_started）
        events = []
        self.bridge.target_package = lambda: 'test.game'
        self.bridge.ensure_game_started = lambda pkg: events.append(('main_launch', pkg)) or True
        self.bridge.vdstream_start = lambda w, h: events.append(('stream', w, h)) or True
        self.bridge.display_ensure_app = lambda pkg: events.append(('ensure', pkg)) or 5
        self.bridge.vdstream_info = lambda: {'display_id': 5, 'running': True}
        resp = self.request('/background/start', method='POST')
        self.assertTrue(resp['ok'])
        self.assertEqual(events, [('stream', 1280, 720), ('ensure', 'test.game')])

        # 2) 若直启返回 -1，才回退主屏拉起并重试一次
        events.clear()
        attempts = iter((-1, 5))
        self.bridge.display_ensure_app = lambda pkg: events.append(('ensure', pkg)) or next(attempts)
        resp2 = self.request('/background/start', method='POST')
        self.assertTrue(resp2['ok'])
        self.assertEqual(events, [('stream', 1280, 720), ('ensure', 'test.game'),
                                  ('main_launch', 'test.game'), ('ensure', 'test.game')])

    def test_background_foreground_moves_game_to_display_zero(self):
        moves = []
        self.bridge.target_package = lambda: 'test.game'
        self.bridge.display_move_task = lambda pkg, disp: moves.append((pkg, disp)) or True
        resp = self.request('/background/foreground', method='POST')
        self.assertTrue(resp['ok'])
        self.assertEqual(moves, [('test.game', 0)])

    def test_vd_keyevent_targets_stream_display(self):
        events = []
        self.bridge.vdstream_running = lambda: True
        self.bridge.vdstream_display = lambda: 4
        self.bridge.display_keyevent = lambda disp, code: events.append((disp, code)) or True
        resp = self.request('/vd/keyevent?code=4', method='POST')
        self.assertTrue(resp['ok'])
        self.assertEqual(resp['display'], 4)
        self.assertEqual(events, [(4, 4)])

    def test_display_screenshot_prefers_binder_stream_on_vdm(self):
        bridge_mod = load_file('oas_mobile.bridge_vdm_test', ROOT / 'oas_mobile/bridge.py')
        bridge_mod.vdstream_running = lambda: True
        bridge_mod.vdstream_display = lambda: 4
        bridge_mod.vdstream_capture = lambda quality=85: b'\xff\xd8fake-jpeg\xff\xd9'
        bridge_mod._capture_to_file = lambda disp: (_ for _ in ()).throw(AssertionError('should not call screencap'))
        self.assertEqual(bridge_mod.display_screenshot(4), b'\xff\xd8fake-jpeg\xff\xd9')


class WebSocketTests(unittest.TestCase):
    def test_disconnect_terminates_and_timeout_runs_tick(self):
        import socket as _socket

        ws_mod = load_file('oas_mobile.ws', ROOT / 'oas_mobile/ws.py')
        sent = []
        calls = iter([_socket.timeout('idle'), b''])

        class FakeSocket:
            def settimeout(self, t):
                pass

            def recv(self, n):
                nxt = next(calls, b'')
                if isinstance(nxt, BaseException):
                    raise nxt
                return nxt

            def sendall(self, data):
                sent.append(data)

            def close(self):
                self.closed = True

        sock = FakeSocket()
        ticks = []
        ws_mod.serve_connection(sock, 'oas1', lambda name, text: ticks.append(text) or ['pong'])
        self.assertEqual(ticks, ['__tick__'])
        self.assertTrue(sent)
        self.assertTrue(getattr(sock, 'closed', False))


if __name__ == '__main__':
    unittest.main(verbosity=2)
