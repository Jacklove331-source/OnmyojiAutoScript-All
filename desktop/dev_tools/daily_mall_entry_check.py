"""Regression replay of the actual oas3 DailyTrifles navigation failure."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np
from ppocronnx.predict_system import TextSystem, BoxedResult

STAGE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('OAS_TEST_PROJECT_ROOT') or STAGE.parent)
sys.path.insert(0, str(ROOT))
# The production logger changes cwd and writes a root log at import time.
fake_logger = ModuleType('module.logger')
fake_logger.logger = Mock()
sys.modules['module.logger'] = fake_logger
candidate = STAGE / 'patched/tasks/GameUi/mall_entry.py'
spec = importlib.util.spec_from_file_location('mall_entry_under_test',
    candidate if candidate.exists() else ROOT / 'tasks/GameUi/mall_entry.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
candidate = STAGE / 'patched/tasks/DailyTrifles/store_ui.py'
spec = importlib.util.spec_from_file_location('store_ui_under_test',
    candidate if candidate.exists() else ROOT / 'tasks/DailyTrifles/store_ui.py')
store_ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store_ui)
page_module = ModuleType('tasks.GameUi.default_pages')
page_module.page_main = object()
sys.modules['tasks.GameUi.default_pages'] = page_module


def read_image(path):
    return cv2.cvtColor(cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


class FakeTask:
    def __init__(self, image, source=True):
        self.device = SimpleNamespace(image=image)
        self.confirm_page = Mock(return_value=source)
        self.clicks = []
        self.ocr_calls = []

    def appear_then_click(self, rule, interval):
        if rule.match(self.device.image):
            self.clicks.append(('template', rule.coord()))
            return True
        return False

    def appear(self, rule):
        return rule.match(self.device.image)

    def ocr_appear(self, rule, exact):
        return any(rule.ocr(self.device.image, exact=exact))

    def ocr_appear_click(self, rule, interval, exact):
        self.ocr_calls.append({'exact': exact, 'interval': interval})
        area = rule.ocr(self.device.image, exact=exact)
        if not any(area):
            return False
        self.clicks.append(('ocr', rule.coord()))
        return True


class MallEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        cls.frame[613:705, 601:765] = read_image(STAGE / 'fixtures/daily_mall_slot_20261112.png')
        cls.model = TextSystem(ort_providers=['CPUExecutionProvider'])
        entry.MALL_ENTRY_TEXT.__dict__['model'] = cls.model
        cls.rule = entry.GameUiAssets.I_MAIN_GOTO_MALL
        cls.rule.file = str(ROOT / 'tasks/GameUi/page/page_main_goto_mall.png')
        cls.original_front = list(cls.rule.roi_front)
        store_ui.DAILY_GIFT_TAB.__dict__['model'] = cls.model
        for rule, name in ((store_ui.DailyTriflesAssets.I_ROOM_GIFT, 'store_room_gift.png'),
                           (store_ui.DailyTriflesAssets.I_GIFT_RECOMMEND, 'store_gift_recommend.png')):
            rule.file = str(ROOT / 'tasks/DailyTrifles/store' / name)

    def setUp(self):
        self.rule.roi_front = list(self.original_front)
        entry.MALL_ENTRY_TEXT.__dict__['model'] = self.model
        store_ui.DAILY_GIFT_TAB.__dict__['model'] = self.model

    def test_actual_failure_is_below_existing_template_threshold(self):
        self.assertFalse(self.rule.match(self.frame))
        self.assertAlmostEqual(self.rule.last_score, 0.619857, places=4)
        self.assertEqual(self.rule.threshold, 0.7)

    def test_actual_failure_recovers_using_visible_mall_text(self):
        task = FakeTask(self.frame.copy())
        self.assertTrue(entry.enter_mall_from_main(task))
        self.assertEqual(len(task.clicks), 1)
        kind, (x, y) = task.clicks[0]
        self.assertEqual(kind, 'ocr')
        self.assertTrue(665 <= x <= 725 and 650 <= y <= 690)
        task.confirm_page.assert_called_once_with(page_module.page_main, skip_first_screenshot=False)
        self.assertTrue(task.ocr_calls[0]['exact'])

    def test_original_template_path_is_preserved(self):
        image = np.zeros_like(self.frame)
        mat = self.rule.image
        h, w = mat.shape[:2]
        image[650:650+h, 650:650+w] = mat
        task = FakeTask(image)
        self.assertTrue(entry.enter_mall_from_main(task))
        self.assertEqual(task.clicks[0][0], 'template')
        self.assertEqual(task.ocr_calls, [])

    def test_non_courtyard_page_never_clicks(self):
        task = FakeTask(self.frame.copy(), source=False)
        self.assertFalse(entry.enter_mall_from_main(task))
        self.assertEqual(task.clicks, [])
        self.assertEqual(task.ocr_calls, [])

    def test_missing_label_never_uses_fixed_coordinates(self):
        task = FakeTask(np.zeros_like(self.frame))
        self.assertFalse(entry.enter_mall_from_main(task))
        self.assertEqual(task.clicks, [])

    def test_text_outside_mall_slot_is_ignored(self):
        image = np.zeros_like(self.frame)
        image[100:190, 100:260] = self.frame[615:705, 605:765]
        task = FakeTask(image)
        self.assertFalse(entry.enter_mall_from_main(task))
        self.assertEqual(task.clicks, [])

    def test_unexpected_resolution_is_not_clicked(self):
        task = FakeTask(np.zeros((1080, 1920, 3), dtype=np.uint8))
        self.assertFalse(entry.enter_mall_from_main(task))
        self.assertEqual(task.clicks, [])

    def test_missing_frame_is_not_clicked(self):
        task = FakeTask(None)
        self.assertFalse(entry.enter_mall_from_main(task))
        self.assertEqual(task.clicks, [])

    def test_low_confidence_text_is_rejected(self):
        model = Mock()
        model.detect_and_ocr.return_value = [BoxedResult(
            box=np.array([[66, 42], [114, 40], [115, 70], [67, 71]], dtype=np.float32),
            ocr_text='商店', score=0.70, text_img=None)]
        entry.MALL_ENTRY_TEXT.__dict__['model'] = model
        task = FakeTask(self.frame.copy())
        self.assertFalse(entry.enter_mall_from_main(task))
        self.assertEqual(task.clicks, [])

    def test_similar_text_is_not_an_exact_mall_match(self):
        model = Mock()
        model.detect_and_ocr.return_value = [BoxedResult(
            box=np.array([[66, 42], [114, 40], [115, 70], [67, 71]], dtype=np.float32),
            ocr_text='商店街', score=0.99, text_img=None)]
        entry.MALL_ENTRY_TEXT.__dict__['model'] = model
        task = FakeTask(self.frame.copy())
        self.assertFalse(entry.enter_mall_from_main(task))
        self.assertEqual(task.clicks, [])

    def test_navigation_edge_is_wired_to_the_guarded_entry(self):
        import ast
        candidate = STAGE / 'patched/tasks/GameUi/default_pages.py'
        path = candidate if candidate.exists() else ROOT / 'tasks/GameUi/default_pages.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        edges = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == 'connect'
                 and any(k.arg == 'key' and isinstance(k.value, ast.Constant)
                         and k.value.value == 'page_main->page_mall_recommend'
                         for k in node.keywords)]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].args[1].id, 'enter_mall_from_main')

    def test_current_mall_scene_matches_existing_new_checker(self):
        from tasks.GameUi.page_check_assets import PageCheckAssets
        from tasks.GameUi.matcher import any_of
        current = PageCheckAssets.I_CHECK_MALL
        current.file = str(ROOT / 'tasks/GameUi/page_checks/page_check_mall.png')
        legacy = entry.GameUiAssets.I_CHECK_MALL
        legacy.file = str(ROOT / 'tasks/GameUi/page/page_check_mall.png')
        tile = read_image(STAGE / 'fixtures/daily_mall_checks_20261112.png')
        frame = np.zeros_like(self.frame)
        frame[432:553, 201:386] = tile[:, :185]
        frame[76:197, 771:956] = tile[:, 185:]
        self.assertFalse(legacy.match(frame))
        self.assertTrue(current.match(frame))
        task = SimpleNamespace(appear=lambda rule: rule.match(frame))
        self.assertTrue(any_of(legacy, current).evaluate(task))

    def test_combined_mall_check_keeps_legacy_and_rejects_courtyard(self):
        from tasks.GameUi.page_check_assets import PageCheckAssets
        from tasks.GameUi.matcher import any_of
        legacy, current = entry.GameUiAssets.I_CHECK_MALL, PageCheckAssets.I_CHECK_MALL
        legacy.file = str(ROOT / 'tasks/GameUi/page/page_check_mall.png')
        current.file = str(ROOT / 'tasks/GameUi/page_checks/page_check_mall.png')
        frame = np.zeros_like(self.frame)
        mat = legacy.image
        h, w = mat.shape[:2]
        frame[440:440+h, 220:220+w] = mat
        check = any_of(legacy, current)
        self.assertTrue(check.evaluate(SimpleNamespace(appear=lambda rule: rule.match(frame))))
        self.assertFalse(check.evaluate(SimpleNamespace(appear=lambda rule: rule.match(self.frame))))

    def test_store_return_uses_navigation_and_respects_purchase_setting(self):
        import ast
        candidate = STAGE / 'patched/tasks/DailyTrifles/script_task.py'
        path = candidate if candidate.exists() else ROOT / 'tasks/DailyTrifles/script_task.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == 'run_store')
        code = compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec')
        namespace = {'page_mall': 'mall', 'page_main': 'main'}
        exec(code, namespace)
        for purchases in (-1, 0, 1):
            with self.subTest(purchases=purchases):
                con = SimpleNamespace(store_sign=True, buy_sushi_count=purchases)
                task = SimpleNamespace(config=SimpleNamespace(daily_trifles=SimpleNamespace(trifles_config=con)),
                    goto_page=Mock(), run_store_sign=Mock(), run_buy_sushi=Mock())
                namespace['run_store'](task)
                self.assertEqual([call.args[0] for call in task.goto_page.call_args_list], ['mall', 'mall', 'main'])
                task.run_store_sign.assert_called_once()
                self.assertEqual(task.run_buy_sushi.call_count, int(purchases > 0))

    def gift_frame(self, variant):
        image = np.zeros_like(self.frame)
        tiles = read_image(STAGE / 'fixtures/daily_gift_tabs_20261112.png')
        tile = tiles[:, variant*105:(variant+1)*105]
        image[75:615, 1160:1265] = tile[:540]
        image[621:715, 1119:1222] = tile[540:, :103]
        return image

    def test_event_and_daily_gift_room_variants_are_recognized(self):
        for variant in (0, 1):
            with self.subTest(variant=variant):
                task = FakeTask(self.gift_frame(variant))
                self.assertTrue(store_ui.is_current_gift_room(task))
                self.assertTrue(store_ui.select_daily_gifts(task))
                self.assertEqual(task.clicks[0][0], 'ocr')
                x, y = task.clicks[0][1]
                self.assertTrue(1160 <= x <= 1265 and 75 <= y <= 615)

    def test_gift_tab_without_bottom_anchor_is_rejected(self):
        frame = self.gift_frame(0)
        frame[621:715, 1119:1222] = 0
        self.assertFalse(store_ui.is_current_gift_room(FakeTask(frame)))

    def test_gift_bottom_anchor_without_tab_is_rejected(self):
        frame = self.gift_frame(0)
        frame[75:615, 1160:1265] = 0
        self.assertFalse(store_ui.is_current_gift_room(FakeTask(frame)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
