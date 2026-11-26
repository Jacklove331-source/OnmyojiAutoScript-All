"""Host unit tests for mobile daily asset monitor and GitCode private OTA updater."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PY_ROOT = Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "main" / "python"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from oas_mobile import asset_monitor, device_guard, ota_updater  # noqa: E402


class TestAssetMonitor(unittest.TestCase):
    def test_parse_cn_number(self):
        self.assertEqual(asset_monitor.parse_cn_number("2667万"), (26670000, False))
        self.assertEqual(asset_monitor.parse_cn_number("132+"), (132, True))
        self.assertEqual(asset_monitor.parse_cn_number("20,475"), (20475, True))
        self.assertEqual(asset_monitor.parse_cn_number("2.0万"), (20000, False))

    def test_extract_courtyard_stamina_and_shop_anchor(self):
        courtyard_rows = [
            {"text": "探索", "x": 100, "y": 400, "w": 50, "h": 30},
            {"text": "2667万", "x": 534, "y": 22, "w": 60, "h": 20},
            {"text": "132+", "x": 749, "y": 20, "w": 45, "h": 20},
            {"text": "2.0万", "x": 906, "y": 20, "w": 50, "h": 20},
        ]
        self.assertTrue(asset_monitor.looks_courtyard(courtyard_rows))
        top = asset_monitor.extract_top_values(courtyard_rows)
        self.assertEqual([v["value"] for v in top], [26670000, 132, 20000])

        stamina_rows = [{"text": "已拥有: 20475", "x": 500, "y": 200, "w": 120, "h": 25}]
        self.assertEqual(asset_monitor.extract_stamina_exact(stamina_rows), 20475)

        shop_ok = [
            {"text": "820", "x": 674, "y": 20, "w": 40, "h": 20},
            {"text": "6", "x": 863, "y": 20, "w": 20, "h": 20},
            {"text": "0", "x": 1037, "y": 20, "w": 20, "h": 20},
            {"text": "132", "x": 1180, "y": 20, "w": 40, "h": 20},
        ]
        parsed = asset_monitor.extract_shop_bar(shop_ok, courtyard_jade=132)
        self.assertEqual(parsed["skin_token"]["value"], 820)
        self.assertEqual(parsed["mystery_amulet"]["value"], 6)
        self.assertEqual(parsed["s_jade"]["value"], 0)
        self.assertEqual(parsed["jade"]["value"], 132)

        # Shifted bar fails anchor validation cleanly
        self.assertEqual(asset_monitor.extract_shop_bar(shop_ok, courtyard_jade=999), {})

    def test_merge_snapshot_delta_and_once_per_day(self):
        with tempfile.TemporaryDirectory() as ws:
            # Seed yesterday's history
            hist_path = Path(ws) / "assets" / "oas1_history.json"
            hist_path.parent.mkdir(parents=True, exist_ok=True)
            hist_path.write_text(json.dumps([
                {"date": "2026-01-01", "captured_at": "2026-01-01T22:00:00",
                 "values": {"jade": 300, "ap": 20000, "coin": 25000000}}
            ]), encoding="utf-8")

            self.assertFalse(asset_monitor.already_updated_today(ws, "oas1"))
            snap = asset_monitor.merge_snapshot(ws, "oas1", {
                "jade": {"value": 380, "precision": "exact", "source": "shop.topbar"},
                "ap": {"value": 20475, "precision": "exact", "source": "tooltip.stamina"},
                "coin": {"value": 26670000, "precision": "approx", "source": "courtyard.topbar"},
                "mystery_amulet": {"value": 8, "precision": "exact", "source": "shop.topbar"},
                "skin_token": {"value": 820, "precision": "exact", "source": "shop.topbar"},
                "s_jade": {"value": 0, "precision": "exact", "source": "shop.topbar"},
            })
            self.assertEqual(snap["status"], "ok")
            self.assertEqual(snap["delta"]["jade"], 80)
            self.assertEqual(snap["delta"]["ap"], 475)
            self.assertTrue(asset_monitor.already_updated_today(ws, "oas1"))

            # Subsequent approx reading must not downgrade today's exact AP
            snap2 = asset_monitor.merge_snapshot(ws, "oas1", {
                "ap": {"value": 20000, "precision": "approx", "source": "courtyard.topbar"},
            })
            self.assertEqual(snap2["items"]["ap"]["value"], 20475)
            self.assertEqual(snap2["items"]["ap"]["precision"], "exact")

    def test_refresh_assets_accepts_shizuku_without_accessibility(self):
        # Shizuku 就绪但未开启无障碍服务时，不能误报“Shizuku 桥接未就绪”
        from types import SimpleNamespace
        from unittest.mock import patch

        fake_bridge = SimpleNamespace(
            available=lambda: False,
            shizuku_available=lambda: True,
            vdstream_running=lambda: True,
            vdstream_display=lambda: 7,
            display_screenshot=lambda d: b"",
        )
        with tempfile.TemporaryDirectory() as ws:
            with patch.dict(sys.modules, {"oas_mobile.bridge": fake_bridge}):
                res = asset_monitor.refresh_assets(ws, config="oas1", force=True)
            self.assertFalse(res["ok"])
            self.assertNotEqual(res["error"], "Shizuku 桥接未就绪")
            self.assertEqual(res["error"], "无法获取虚拟屏截图")


class TestOtaUpdater(unittest.TestCase):
    def _make_zip(self, entries: dict[str, bytes]) -> tuple[bytes, str]:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for k, v in entries.items():
                zf.writestr(k, v)
        raw = buf.getvalue()
        return raw, hashlib.sha256(raw).hexdigest()

    def test_config_masks_token(self):
        with tempfile.TemporaryDirectory() as ws:
            res = ota_updater.save_config(ws, {
                "repo": "example-org/oas-datapack",
                "branch": "main",
                "token": "secret-gitcode-token-9876",
            })
            self.assertTrue(res["ok"])
            self.assertTrue(res["has_token"])
            self.assertEqual(res["token_hint"], "****9876")
            self.assertNotIn("secret-gitcode-token", json.dumps(res))

    def test_apply_sha_check_zipslip_and_rollback(self):
        with tempfile.TemporaryDirectory() as ws:
            # 1. SHA mismatch rejected
            z1, sha1 = self._make_zip({"tasks/test_mod.py": b"VAL = 1\n"})
            bad = ota_updater.apply_update(ws, zip_bytes=z1, manifest={"version": "1.0", "sha256": "0" * 64, "file": "datapack/oas-patch.zip"})
            self.assertFalse(bad["ok"])

            # 2. Zip-slip rejected
            z_bad, sha_bad = self._make_zip({"../evil.py": b"bad\n"})
            bad2 = ota_updater.apply_update(ws, zip_bytes=z_bad, manifest={"version": "1.0", "sha256": sha_bad, "file": "datapack/oas-patch.zip"})
            self.assertFalse(bad2["ok"])

            # 3. Valid v1 apply -> overlay activated
            ok1 = ota_updater.apply_update(ws, zip_bytes=z1, manifest={"version": "2026.11.25.01", "sha256": sha1, "file": "datapack/oas-patch.zip"})
            self.assertTrue(ok1["ok"])
            self.assertEqual(ok1["installed_version"], "2026.11.25.01")
            self.assertEqual(sys.path[0], str(Path(ws) / "ota_overlay"))

            # 4. Valid v2 apply -> rollback restores v1
            z2, sha2 = self._make_zip({"tasks/test_mod.py": b"VAL = 2\n"})
            ok2 = ota_updater.apply_update(ws, zip_bytes=z2, manifest={"version": "2026.11.25.02", "sha256": sha2, "file": "datapack/oas-patch.zip"})
            self.assertTrue(ok2["ok"])
            self.assertEqual((Path(ws) / "ota_overlay" / "tasks" / "test_mod.py").read_text(), "VAL = 2\n")

            rb = ota_updater.rollback_update(ws)
            self.assertTrue(rb["ok"])
            self.assertEqual((Path(ws) / "ota_overlay" / "tasks" / "test_mod.py").read_text(), "VAL = 1\n")

            # Clean sys.path after test
            if str(Path(ws) / "ota_overlay") in sys.path:
                sys.path.remove(str(Path(ws) / "ota_overlay"))

    def test_apk_update_summary_and_pydantic_funcs_purge(self):
        summary = ota_updater._build_apk_update_summary({
            "version": "2026.11.25",
            "apk": {
                "version_name": "0.3.4",
                "build_number": 99,
                "file_name": "oasx-mobile-0.3.4+99-arm64-v8a.apk",
                "size_bytes": 150000000,
                "sha256": "a" * 64,
                "download_url": "https://gitcode.com/example-org/oas-datapack/releases/download/v0.3.4-b99/a.apk",
                "note": "test release",
            },
        })
        self.assertIsNotNone(summary)
        self.assertTrue(summary["has_update"])
        self.assertEqual(summary["build_number"], 99)
        self.assertEqual(summary["version"], "0.3.4+99")

        # Invalid URL ignored
        self.assertIsNone(ota_updater._build_apk_update_summary({
            "apk": {"build_number": 99, "download_url": "file:///etc/passwd"},
        }))

    def test_github_public_raw_and_release_url_without_token(self):
        # 验证 GitHub 公开源免令牌优先走 raw.githubusercontent.com，且支持 Release 直链
        from unittest.mock import patch

        called = []

        def fake_http_get(url, headers, timeout=30):
            called.append((url, dict(headers)))
            return b'{"version":"2026.11.26","sha256":"' + b"a" * 64 + b'"}'

        cfg = {
            "base_url": "https://github.com",
            "repo": "Jacklove331-source/OnmyojiAutoScript-All",
            "branch": "main",
            "token": "",
        }
        with patch.object(ota_updater, "_http_get", side_effect=fake_http_get):
            data = ota_updater._download_bytes(cfg, "datapack/manifest.json")
            self.assertIn(b"2026.11.26", data)
            direct_url = "https://github.com/Jacklove331-source/OnmyojiAutoScript-All/releases/download/v0.3.4-b78/oas-patch.zip"
            ota_updater._download_bytes(cfg, direct_url)

        self.assertEqual(
            called[0][0],
            "https://raw.githubusercontent.com/Jacklove331-source/OnmyojiAutoScript-All/main/datapack/manifest.json",
        )
        self.assertNotIn("PRIVATE-TOKEN", called[0][1])
        self.assertNotIn("Authorization", called[0][1])
        self.assertEqual(called[1][0], direct_url)


class TestDeviceGuard(unittest.TestCase):
    def test_time_window_same_day_and_overnight(self):
        self.assertTrue(device_guard.is_in_time_window("00:30", "00:05", "02:30"))
        self.assertFalse(device_guard.is_in_time_window("02:30", "00:05", "02:30"))
        self.assertTrue(device_guard.is_in_time_window("23:30", "23:00", "02:00"))
        self.assertTrue(device_guard.is_in_time_window("01:15", "23:00", "02:00"))
        self.assertFalse(device_guard.is_in_time_window("14:00", "23:00", "02:00"))

    def test_evaluate_guard_rules_and_hysteresis(self):
        cfg = dict(device_guard.DEFAULT_CONFIG)
        # Overheat triggers pause
        r1 = device_guard.evaluate_guard(cfg, {"battery_temp_c": 42.5, "battery_pct": 80, "charging": False, "network_online": True})
        self.assertTrue(r1["should_pause"])
        self.assertEqual(r1["code"], "overheat")

        # Still cooling down (was_overheat=True, 39.5 > 38.0) -> keep paused
        r2 = device_guard.evaluate_guard(cfg, {"battery_temp_c": 39.5, "battery_pct": 80, "charging": False, "network_online": True}, was_overheat=True)
        self.assertTrue(r2["should_pause"])
        self.assertEqual(r2["code"], "overheat")

        # Cooled to 37.5 -> resume
        r3 = device_guard.evaluate_guard(cfg, {"battery_temp_c": 37.5, "battery_pct": 80, "charging": False, "network_online": True}, was_overheat=True)
        self.assertFalse(r3["should_pause"])

        # Low battery while not charging -> pause; while charging -> ok
        r4 = device_guard.evaluate_guard(cfg, {"battery_temp_c": 34.0, "battery_pct": 12, "charging": False, "network_online": True})
        self.assertTrue(r4["should_pause"])
        self.assertEqual(r4["code"], "low_battery")
        r5 = device_guard.evaluate_guard(cfg, {"battery_temp_c": 34.0, "battery_pct": 12, "charging": True, "network_online": True})
        self.assertFalse(r5["should_pause"])

        # Offline -> pause
        r6 = device_guard.evaluate_guard(cfg, {"battery_temp_c": 34.0, "battery_pct": 80, "charging": False, "network_online": False})
        self.assertTrue(r6["should_pause"])
        self.assertEqual(r6["code"], "offline")

        # Schedule window sleep vs active
        cfg_sched = dict(cfg, schedule_enabled=True, start_time="00:05", stop_time="02:30")
        r7 = device_guard.evaluate_guard(cfg_sched, {"battery_temp_c": 34.0, "battery_pct": 80, "charging": False, "network_online": True}, now_hhmm="10:00")
        self.assertTrue(r7["should_pause"])
        self.assertEqual(r7["code"], "sleep_window")
        r8 = device_guard.evaluate_guard(cfg_sched, {"battery_temp_c": 34.0, "battery_pct": 80, "charging": False, "network_online": True}, now_hhmm="01:00")
        self.assertFalse(r8["should_pause"])
        self.assertTrue(r8["should_auto_start"])

    def test_config_clamping(self):
        with tempfile.TemporaryDirectory() as ws:
            res = device_guard.save_config(ws, {
                "max_temp_c": 99.0,
                "cooldown_temp_c": 90.0,
                "min_battery_pct": 1,
                "start_time": "bad",
                "stop_time": "03:00",
            })
            self.assertTrue(res["ok"])
            self.assertEqual(res["config"]["max_temp_c"], 55.0)
            self.assertEqual(res["config"]["cooldown_temp_c"], 54.0)
            self.assertEqual(res["config"]["min_battery_pct"], 5)
            self.assertEqual(res["config"]["start_time"], "00:05")
            self.assertEqual(res["config"]["stop_time"], "03:00")


if __name__ == "__main__":
    unittest.main()
