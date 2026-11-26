"""Exercise zone selection, shikigami recovery and ADB retry without a device."""

from datetime import datetime
from functools import partial
from pathlib import Path
import os
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module.atom.list import RuleList
from module.device.method.adb import Adb, load_screencap
from module.device.method.utils import ImageTruncated
from module.exception import TaskEnd
from module.logger import cleanup_logs
from tasks.base_task import BaseTask
from tasks.Component.GeneralBuff.general_buff import GeneralBuff
from tasks.Component.ReplaceShikigami.replace_shikigami import ReplaceShikigami
from tasks.KekkaiActivation.script_task import ScriptTask as ActivationTask
from tasks.KekkaiUtilize.script_task import ScriptTask as UtilizeTask
from tasks.Tako.script_task import ScriptTask as TakoTask


class TeamRoomTests(unittest.TestCase):
    def setUp(self):
        self.rule = RuleList('.', 'vertical', 'ocr', (26, 106, 360, 549), (216, 61),
                             ['年兽', '石距', '愤怒的石距', '喷怒的石距', '结界突破'])
        self.rule.target_check = Mock()
        self.rule._target = Mock()

    def read_labels(self, *labels):
        self.rule._target.detect_and_ocr.return_value = [
            SimpleNamespace(ocr_text=label, score=1,
                            box=np.array([[160, y], [220, y], [220, y + 24], [160, y + 24]]))
            for y, label in enumerate(labels, start=100)
        ]

    def test_available_tako_alias_does_not_scroll(self):
        self.read_labels('年兽', '石距', '结界突破')
        task = SimpleNamespace(screenshot=Mock(), device=Mock())
        self.assertEqual(BaseTask.list_find(task, self.rule, ['愤怒的石距', '石距']), (216, 219))
        self.rule._target.detect_and_ocr.assert_called_once()
        task.device.swipe.assert_not_called()

    def test_requested_variant_wins_when_both_are_visible(self):
        self.read_labels('石距', '愤怒的石距')
        self.assertEqual(self.rule.ocr_appear(None, ['愤怒的石距', '石距']), (216, 219))
        self.assertEqual(self.rule.ocr_appear(None, ['石距', '愤怒的石距']), (216, 218))

    def test_unreadable_or_missing_in_range_does_not_swipe_or_click_origin(self):
        for labels in [(), ('年兽', '结界突破')]:
            with self.subTest(labels=labels):
                self.read_labels(*labels)
                task = SimpleNamespace(screenshot=Mock(), device=Mock())
                self.assertFalse(BaseTask.list_find(task, self.rule, ['石距', '愤怒的石距']))
                task.device.swipe.assert_not_called()

    def test_can_match_after_last_allowed_swipe(self):
        self.read_labels('年兽', '石距')
        found = self.rule._target.detect_and_ocr.return_value
        self.rule._target.detect_and_ocr.side_effect = [found[:1], found]
        task = SimpleNamespace(screenshot=Mock(), device=Mock())
        with patch('tasks.base_task.sleep'):
            self.assertEqual(BaseTask.list_find(task, self.rule, ['愤怒的石距', '石距'], max_swipe=1),
                             (216, 219))
        task.device.swipe.assert_called_once()

    def test_tako_defers_when_zone_was_not_confirmed(self):
        task = SimpleNamespace(
            config=SimpleNamespace(tako=SimpleNamespace(
                switch_soul=SimpleNamespace(enable=False, enable_switch_by_name=False),
                tako_config=SimpleNamespace(enable=False),
            )),
            start_time=datetime(2026, 11, 24), should_switch_soul=Mock(return_value=False),
            goto_page=Mock(), check_zones=Mock(return_value=False), create_room=Mock(),
            set_next_run=Mock(),
        )
        task.exit_task = partial(TakoTask.exit_task, task)
        with self.assertRaises(TaskEnd):
            TakoTask.run(task)
        task.check_zones.assert_called_once_with('石距')
        task.create_room.assert_not_called()
        task.set_next_run.assert_called_once_with(task='Tako', success=False, finish=True)

    def test_missing_buff_ocr_area_cannot_select_another_buff(self):
        task = SimpleNamespace(device=Mock(), reject_invite=Mock(), screenshot=Mock())
        buff = Mock()
        buff.name = "gold_50"
        buff.ocr.return_value = (0, 0, 0, 0)
        self.assertIsNone(GeneralBuff.get_area(task, buff))
        buff.ocr.assert_called_once_with(task.device.image, exact=True)
        buff.ocr.return_value = (432, 143, 325, 21)
        self.assertEqual(GeneralBuff.get_area(task, buff), (767, 133, 80, 41))


class ShikigamiPageTests(unittest.TestCase):
    def test_picker_does_not_click_after_leaving_growth_page(self):
        task = object.__new__(ReplaceShikigami)
        task.screenshot = Mock()
        task.appear = Mock(return_value=False)
        task.click = Mock(return_value=True)
        page_check = Mock(side_effect=[True, False])
        self.assertFalse(task.switch_shikigami_class(page_check=page_check))
        task.click.assert_called_once_with(task.C_SHIKIGAMI_SWITCH_1, interval=3.5)

    def test_empty_slot_is_not_detected_outside_growth_page(self):
        task = object.__new__(ReplaceShikigami)
        task.in_shikigami_growth = Mock(return_value=False)
        task.appear = Mock(return_value=True)
        self.assertFalse(task.detect_no_shikigami())
        task.appear.assert_not_called()

    def test_fills_verified_growth_slot_after_selecting_rarity(self):
        task = object.__new__(UtilizeTask)
        task.realm_goto_grown = Mock()
        task.screenshot = Mock()
        task.in_shikigami_growth = Mock(return_value=True)
        task.appear = Mock(side_effect=lambda rule, **kwargs: rule is task.I_REALM_SHIN)
        task.appear_multi_scale = Mock(return_value=True)
        task.detect_no_shikigami = Mock(return_value=True)
        task.switch_shikigami_class = Mock(return_value=True)
        task.set_shikigami = Mock()
        task.check_max_lv()
        task.switch_shikigami_class.assert_called_once()
        task.set_shikigami.assert_called_once_with(shikigami_order=7, stop_image=task.I_RS_NO_ADD)

    def test_activation_and_utilization_recover_from_guild_page(self):
        self.assertIs(ActivationTask.check_max_lv, UtilizeTask.check_max_lv)
        task = object.__new__(ActivationTask)
        task.realm_goto_grown = Mock()
        task.in_shikigami_growth = Mock(return_value=False)
        task.screenshot = Mock()
        task.appear = Mock(side_effect=lambda rule, **kwargs: rule is task.I_GUILD_INFO)
        task.switch_shikigami_class = Mock()
        task.set_shikigami = Mock()
        task.goto_realm = Mock()
        task.check_max_lv()
        task.switch_shikigami_class.assert_not_called()
        task.set_shikigami.assert_not_called()
        task.goto_realm.assert_called_once()


class AdbFrameTests(unittest.TestCase):
    def test_decodes_both_raw_screencap_header_sizes(self):
        pixels = bytes(range(8))
        for header in (struct.pack('<III', 2, 1, 1), struct.pack('<IIII', 2, 1, 1, 0)):
            with self.subTest(header=len(header)):
                image = load_screencap(header + pixels)
                np.testing.assert_array_equal(image, [[[0, 1, 2], [4, 5, 6]]])
                with self.assertRaises(ImageTruncated):
                    load_screencap((header + pixels)[:-1])

    def test_truncated_screenshot_retries_and_reconnects_after_repetition(self):
        task = object.__new__(Adb)
        task.config = SimpleNamespace(DEVICE_OVER_HTTP=False)
        expected = np.zeros((2, 3, 3), dtype=np.uint8)
        _, png = cv2.imencode('.png', expected)
        task.adb_shell = Mock(side_effect=[b'', b'not a frame', png.tobytes()])
        task.adb_reconnect = Mock()
        with patch('module.device.method.adb.retry_sleep'):
            np.testing.assert_array_equal(task.screenshot_adb(), expected)
        task.adb_reconnect.assert_called_once()

    def test_aborted_connection_reconnects_and_reads_again(self):
        task = object.__new__(Adb)
        task.config = SimpleNamespace(DEVICE_OVER_HTTP=False)
        _, png = cv2.imencode('.png', np.zeros((1, 2, 3), dtype=np.uint8))
        task.adb_shell = Mock(side_effect=[ConnectionAbortedError('socket closed'), png.tobytes()])
        task.adb_reconnect = Mock()
        with patch('module.device.method.adb.retry_sleep'):
            self.assertEqual(task.screenshot_adb().shape, (1, 2, 3))
        task.adb_reconnect.assert_called_once()


class LogCleanupTests(unittest.TestCase):
    def test_expired_log_removed_and_concurrent_missing_snapshot_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expired = root / '2026-01-01_oas1.txt'
            fresh = root / 'current.txt'
            snapshot = root / 'error' / 'expired'
            expired.touch()
            fresh.touch()
            snapshot.mkdir(parents=True)
            os.utime(expired, (1, 1))
            os.utime(snapshot, (1, 1))
            with patch('module.logger.shutil.rmtree', side_effect=FileNotFoundError) as remove_snapshot:
                cleanup_logs(tmp)
            remove_snapshot.assert_called_once_with(snapshot)
            self.assertFalse(expired.exists())
            self.assertTrue(fresh.exists())


if __name__ == '__main__':
    unittest.main()
