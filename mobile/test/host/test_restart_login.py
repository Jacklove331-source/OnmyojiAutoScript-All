"""Android Restart verifies the configured role before running other tasks."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(sys.argv.pop(1))
sys.path.insert(0, str(ROOT))

from module.exception import RequestHumanTakeover
from oas_mobile import login
from oas_mobile.runtime import TaskCancelled
from tasks.Restart.login import LoginHandler


class LoginProbe:
    def __init__(self, *, desktop=False, character="", harvest=True):
        self.calls = []
        self.character = character
        self.device = SimpleNamespace(
            is_desktop=desktop,
            stuck_record_clear=lambda: self.calls.append("reset navigation"),
            click_record_clear=lambda: self.calls.append("reset clicks"),
            desktop_mark_logged_in=lambda: self.calls.append("mark desktop ready"),
            _ensure_display=lambda: 2,
        )
        self.config = SimpleNamespace(
            restart=SimpleNamespace(harvest_config=SimpleNamespace(enable=harvest)),
        )

    def _app_handle_login(self):
        self.calls.append("original login")
        return True

    def harvest(self):
        self.calls.append("harvest")

    def authenticate(self, display, *, character):
        self.calls.append(("mobile login", display, character))
        return True

    def run(self, authenticate=None, *, verify_configured_character=False):
        with patch.object(login, "ensure_logged_in", authenticate or self.authenticate):
            return LoginHandler.app_handle_login(self, verify_configured_character=verify_configured_character)


class RestartLoginTests(unittest.TestCase):
    def test_default_character_uses_mobile_login_and_keeps_harvest(self):
        handler = LoginProbe()
        self.assertTrue(handler.run())
        self.assertEqual(handler.calls, [
            "reset navigation", "reset clicks", ("mobile login", 2, ""), "reset navigation", "harvest",
        ])

    def test_mobile_failure_reports_failure_before_harvest(self):
        handler = LoginProbe()
        with self.assertRaisesRegex(RequestHumanTakeover, "Android"):
            handler.run(lambda display, *, character: False)
        self.assertEqual(handler.calls, ["reset navigation", "reset clicks"])

    def test_cancel_propagates_before_harvest(self):
        handler = LoginProbe()

        def cancel(display, *, character):
            raise TaskCancelled

        with self.assertRaises(TaskCancelled):
            handler.run(cancel)
        self.assertEqual(handler.calls, ["reset navigation", "reset clicks"])

    def test_android_restart_verifies_explicit_character_before_harvest(self):
        handler = LoginProbe(character="指定测试角色")
        self.assertTrue(handler.run(verify_configured_character=True))
        self.assertEqual(handler.calls, [
            "reset navigation", "reset clicks",
            ("mobile login", 2, "指定测试角色"), "reset navigation", "harvest",
        ])

    def test_android_restart_role_failure_skips_harvest(self):
        handler = LoginProbe(character="指定测试角色")
        with self.assertRaisesRegex(RequestHumanTakeover, "Android"):
            handler.run(lambda display, *, character: False, verify_configured_character=True)
        self.assertEqual(handler.calls, ["reset navigation", "reset clicks"])

    def test_other_explicit_character_caller_retains_original_login(self):
        handler = LoginProbe(character="指定测试角色")
        self.assertTrue(handler.run())
        self.assertEqual(handler.calls, ["reset navigation", "reset clicks", "original login", "harvest"])

    def test_desktop_login_is_unchanged(self):
        handler = LoginProbe(desktop=True)
        self.assertTrue(handler.run())
        self.assertEqual(handler.calls, [
            "reset navigation", "reset clicks", "original login", "mark desktop ready", "harvest",
        ])

    def test_invalid_display_stops_without_login(self):
        handler = LoginProbe()
        handler.device._ensure_display = lambda: -1
        with self.assertRaisesRegex(RequestHumanTakeover, "Android"):
            handler.run()
        self.assertEqual(handler.calls, ["reset navigation", "reset clicks"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
