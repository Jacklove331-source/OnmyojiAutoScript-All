"""Regression tests for Realm Raid's attack and battle-entry confirmation."""
import pathlib
import sys
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(sys.argv.pop(1))
sys.path.insert(0, str(ROOT))

from module.exception import GameStuckError, TaskEnd
from tasks.RealmRaid import script_task


@dataclass(frozen=True)
class Frame:
    list_visible: bool = True
    attack_visible: bool = False
    preparing: bool = False
    in_battle: bool = False
    ticket_short: bool = False
    soul_warning: bool = False


class RaidStub:
    FIRE_CONFIRM_TIMEOUT = 5.0

    def __init__(self, frames):
        self.frames = frames
        self.screenshots = 0
        self.clicks = []
        self.next_run = None
        self.destination = None
        self.partition = [f'partition_{index}' for index in range(1, 10)]
        self.I_RR_PERSON = object()
        self.I_FIRE = object()
        self.I_UI_CONFIRM = object()
        self.I_SOUL_RAID = object()
        self.O_TICKET_SHORT = object()
        self.I_DISABLE_7DAYS_DIFF_SOUL = object()
        self.I_CONFIRM_CLOSE_DIFF_SOUL = object()
        self.I_BACK_RED = object()
        self.I_CHECK_EXPLORATION = object()

    @property
    def frame(self):
        return self.frames[min(self.screenshots, len(self.frames) - 1)]

    def screenshot(self):
        self.screenshots += 1

    def is_in_prepare(self, is_screenshot):
        return self.frame.preparing

    def is_in_real_battle(self, is_screenshot):
        return self.frame.in_battle

    def appear(self, target, threshold=None):
        if target is self.I_RR_PERSON:
            return self.frame.list_visible
        if target is self.I_CONFIRM_CLOSE_DIFF_SOUL:
            return self.frame.soul_warning
        return False

    def appear_then_click(self, target, interval=None):
        if target is self.I_FIRE and self.frame.attack_visible:
            self.clicks.append('attack')
            return True
        return False

    def click(self, target, interval=None):
        self.clicks.append(target)
        return True

    def ocr_appear(self, target):
        return target is self.O_TICKET_SHORT and self.frame.ticket_short

    def ui_click(self, button, destination):
        self.clicks.append('leave')

    def goto_page(self, page):
        self.destination = page

    def set_next_run(self, **kwargs):
        self.next_run = kwargs

    def fire(self):
        def now():
            return float(self.screenshots)

        clock = SimpleNamespace(monotonic=now, time=now, sleep=lambda seconds: None)
        with patch.object(script_task, 'time', clock):
            script_task.ScriptTask.fire(self, 4)


class RealmRaidFireTests(unittest.TestCase):
    def test_waits_for_ready_through_dimmed_list_and_blank_transition(self):
        selected = Frame(attack_visible=True)
        loading = Frame(list_visible=False)
        ready = Frame(list_visible=False, preparing=True)
        raid = RaidStub([selected, selected, selected, loading, ready])
        raid.fire()
        self.assertEqual(raid.clicks, ['attack'])
        self.assertEqual(raid.screenshots, 4)

    def test_selects_card_once_and_waits_for_attack_button(self):
        unselected = Frame()
        selected = Frame(attack_visible=True)
        ready = Frame(list_visible=False, preparing=True)
        raid = RaidStub([unselected, unselected, unselected, selected, selected, ready])
        raid.fire()
        self.assertEqual(raid.clicks, ['partition_4', 'attack'])
        self.assertEqual(raid.screenshots, 5)

    def test_locked_team_can_go_directly_into_battle(self):
        selected = Frame(attack_visible=True)
        raid = RaidStub([selected, selected, Frame(list_visible=False, in_battle=True)])
        raid.fire()
        self.assertEqual(raid.clicks, ['attack'])

    def test_existing_pre_battle_team_warning_is_recognized(self):
        selected = Frame(attack_visible=True)
        raid = RaidStub([selected, selected, Frame(list_visible=False, soul_warning=True)])
        raid.fire()
        self.assertEqual(raid.clicks, ['attack'])

    def test_stuck_on_selected_card_reports_battle_entry_timeout(self):
        raid = RaidStub([Frame(attack_visible=True)])
        with self.assertRaisesRegex(GameStuckError, 'battle entry'):
            raid.fire()
        self.assertEqual(raid.clicks, ['attack'])

    def test_missing_list_without_ready_frame_reports_battle_entry_timeout(self):
        selected = Frame(attack_visible=True)
        raid = RaidStub([selected, selected, Frame(list_visible=False)])
        with self.assertRaisesRegex(GameStuckError, 'battle entry'):
            raid.fire()
        self.assertEqual(raid.clicks, ['attack'])

    def test_missing_attack_button_reports_timeout_after_card_selection(self):
        raid = RaidStub([Frame()])
        with self.assertRaisesRegex(GameStuckError, 'attack button'):
            raid.fire()
        self.assertEqual(raid.clicks, ['partition_4'])

    def test_ticket_shortage_still_ends_task_and_returns_to_main(self):
        selected = Frame(attack_visible=True)
        raid = RaidStub([selected, selected, Frame(ticket_short=True)])
        with self.assertRaises(TaskEnd):
            raid.fire()
        self.assertEqual(raid.clicks, ['attack', 'leave'])
        self.assertIs(raid.destination, script_task.page_main)
        self.assertEqual(raid.next_run, {'task': 'RealmRaid', 'success': False, 'finish': True})


if __name__ == '__main__':
    unittest.main(verbosity=2)
