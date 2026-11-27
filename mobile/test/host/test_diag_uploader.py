"""宿主单测：oas_mobile.diag_uploader 脱敏、打包、开关与节流校验。"""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from oas_mobile import diag_uploader


class DiagUploaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        (self.ws / "config").mkdir(parents=True, exist_ok=True)
        (self.ws / "log").mkdir(parents=True, exist_ok=True)
        diag_uploader._LAST_AUTO_AT.clear()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_public_build_is_disabled_by_default(self) -> None:
        with mock.patch.object(diag_uploader, "_read_bundled_asset_config", return_value={}):
            st = diag_uploader.get_status(str(self.ws))
            self.assertTrue(st["ok"])
            self.assertFalse(st["configured"])
            self.assertFalse(st["enabled"])
            self.assertFalse(st["auto_upload"])
            self.assertEqual(st["repo"], "")
            self.assertNotIn("token", st)
            self.assertFalse(
                diag_uploader.trigger_auto_upload(str(self.ws), trigger="task_failed", task="Exploration")
            )

    def test_bundled_personal_config_masks_secrets_and_packs_bundle(self) -> None:
        secret_tok = "SUPER_SECRET_GITCODE_TOK_9988"
        bundled = {
            "repo": "wrsndm1/oas-diag-private",
            "branch": "main",
            "token": secret_tok,
            "enabled": True,
            "auto_upload": True,
        }
        (self.ws / "config" / "oas1.json").write_text(
            json.dumps({
                "script": {
                    "optimization": {"when_task_queue_empty": "close_game"},
                    "push": {"webhook": "https://qyapi.example/hook/123", "api_key": "sk-live-secret"},
                },
                "auth_password": "my_password_123",
            }),
            encoding="utf-8",
        )
        (self.ws / "log" / "latest.txt").write_text(
            f"line1 ok\nline2 leak {secret_tok}\n",
            encoding="utf-8",
        )

        uploaded: dict = {}

        def fake_upload(cfg, remote_path, payload, message, timeout=35):
            uploaded["remote_path"] = remote_path
            uploaded["payload"] = payload
            uploaded["message"] = message
            return {"ok": True}

        with (
            mock.patch.object(diag_uploader, "_read_bundled_asset_config", return_value=bundled),
            mock.patch.object(diag_uploader, "_upload_bytes_to_gitcode", side_effect=fake_upload),
        ):
            st = diag_uploader.get_status(str(self.ws))
            self.assertTrue(st["configured"])
            self.assertTrue(st["auto_upload"])
            self.assertEqual(st["repo"], "wrsndm1/oas-diag-private")
            self.assertNotIn(secret_tok, json.dumps(st))

            res = diag_uploader.upload_report(
                str(self.ws),
                trigger="manual",
                task="WantedQuests",
                config_name="oas1",
                note=f"test note {secret_tok}",
                include_screenshot=True,
                screenshot_bytes=b"\xff\xd8\xff\xe0FAKEJPEG",
            )
            self.assertTrue(res["ok"])
            self.assertTrue(res["has_screenshot"])
            self.assertTrue(uploaded["remote_path"].startswith("reports/"))
            self.assertTrue(uploaded["remote_path"].endswith(".zip"))

            with zipfile.ZipFile(io.BytesIO(uploaded["payload"]), "r") as zf:
                names = set(zf.namelist())
                self.assertIn("summary.json", names)
                self.assertIn("scheduler_log.txt", names)
                self.assertIn("task_log.txt", names)
                self.assertIn("config_snapshot.json", names)
                self.assertIn("screen.jpg", names)

                all_text = (
                    zf.read("summary.json").decode("utf-8")
                    + zf.read("task_log.txt").decode("utf-8")
                    + zf.read("config_snapshot.json").decode("utf-8")
                )
                self.assertNotIn(secret_tok, all_text)
                self.assertNotIn("sk-live-secret", all_text)
                self.assertNotIn("my_password_123", all_text)
                self.assertNotIn("qyapi.example", all_text)

    def test_local_toggle_and_auto_cooldown(self) -> None:
        bundled = {
            "repo": "wrsndm1/oas-diag-private",
            "branch": "main",
            "token": "TOK_ABC_123",
            "enabled": True,
            "auto_upload": True,
        }
        with (
            mock.patch.object(diag_uploader, "_read_bundled_asset_config", return_value=bundled),
            mock.patch.object(diag_uploader, "upload_report", return_value={"ok": True}),
            mock.patch.object(diag_uploader, "capture_current_frame", return_value=b""),
        ):
            first = diag_uploader.trigger_auto_upload(str(self.ws), trigger="task_failed", task="Orochi")
            second = diag_uploader.trigger_auto_upload(str(self.ws), trigger="task_failed", task="Orochi")
            other = diag_uploader.trigger_auto_upload(str(self.ws), trigger="shizuku_lost", task="Orochi")
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertTrue(other)

            st = diag_uploader.save_config(str(self.ws), {"auto_upload": False})
            self.assertTrue(st["configured"])
            self.assertFalse(st["auto_upload"])
            self.assertFalse(
                diag_uploader.trigger_auto_upload(str(self.ws), trigger="scheduler_error", task="Demo")
            )


if __name__ == "__main__":
    unittest.main()
