"""回归单测：Kotlin ShellResult getOutput() 兼容 + 悬赏封印误触与退出修复。"""
from __future__ import annotations

import unittest
from unittest import mock

from oas_mobile import asset_monitor, bridge, scheduler


class _JavaOnlyShellResult:
    """模拟 Chaquopy 暴露 Kotlin data class：只提供 getOutput()/getCode()，无 .output/.code 属性。"""

    def __init__(self, code: int, output: str):
        self._code = code
        self._output = output

    def getCode(self) -> int:
        return self._code

    def getOutput(self) -> str:
        return self._output


class WantedPanelAndBridgeTests(unittest.TestCase):
    def test_game_running_reads_kotlin_get_output(self) -> None:
        fake_shizuku = mock.Mock()
        fake_shizuku.exec.side_effect = lambda cmd, timeout=5000: (
            _JavaOnlyShellResult(0, "14823\n") if cmd.startswith("pidof") else _JavaOnlyShellResult(0, "")
        )
        with mock.patch.object(bridge, "_SHIZUKU", fake_shizuku):
            self.assertTrue(bridge.game_running("com.netease.onmyoji"))

    def test_game_running_falls_back_to_ps_when_pidof_empty(self) -> None:
        fake_shizuku = mock.Mock()
        fake_shizuku.exec.side_effect = lambda cmd, timeout=5000: (
            _JavaOnlyShellResult(1, "")
            if cmd.startswith("pidof")
            else _JavaOnlyShellResult(0, "com.netease.onmyoji\n")
        )
        with mock.patch.object(bridge, "_SHIZUKU", fake_shizuku):
            self.assertTrue(bridge.game_running("com.netease.onmyoji"))

    def test_try_return_to_courtyard_closes_wanted_panel_top_right_x(self) -> None:
        taps: list[tuple[int, int]] = []
        fake_bridge = mock.Mock()
        fake_bridge.display_tap.side_effect = lambda disp, x, y: taps.append((x, y))

        wanted_rows = [
            {"text": "悬赏封印", "x": 580, "y": 40, "w": 120, "h": 30},
            {"text": "一键追踪", "x": 1140, "y": 600, "w": 90, "h": 40},
        ]
        courtyard_rows = [
            {"text": "探索", "x": 500, "y": 300, "w": 50, "h": 20},
            {"text": "商店", "x": 700, "y": 640, "w": 50, "h": 20},
        ]

        with (
            mock.patch.object(asset_monitor, "_capture_rgb", return_value=(object(), (1600, 900))),
            mock.patch.object(asset_monitor, "_ocr_rows_1280", return_value=courtyard_rows),
            mock.patch.object(asset_monitor, "_is_courtyard_frame", side_effect=[False, True, True]),
            mock.patch.object(asset_monitor.time, "sleep"),
        ):
            _, _, _, ok = asset_monitor._try_return_to_courtyard(
                fake_bridge, 11, object(), (1600, 900), wanted_rows, max_taps=2
            )
        self.assertTrue(ok)
        # 1215/1280 * 1600 = 1519, 108/720 * 900 = 135
        self.assertEqual(taps, [(1519, 135)])

    def test_idle_close_force_stops_even_if_game_running_false_when_vd_running(self) -> None:
        shell_cmds: list[str] = []
        idle_state = {"closed": False}
        with (
            mock.patch.object(scheduler, "_idle_close_policy", return_value=(True, 60)),
            mock.patch.object(bridge, "target_package", return_value="com.netease.onmyoji"),
            mock.patch.object(bridge, "game_running", return_value=False),
            mock.patch.object(bridge, "vdstream_running", return_value=True),
            mock.patch.object(bridge, "vdstream_stop", return_value=True) as stop_vd,
            mock.patch("oas_mobile.shizuku_exec.exec_shell", side_effect=lambda cmd: shell_cmds.append(cmd)),
        ):
            closed = scheduler._close_game_for_idle("oas1", 300.0, idle_state)
        self.assertTrue(closed)
        self.assertEqual(shell_cmds, ["am force-stop com.netease.onmyoji"])
        stop_vd.assert_called_once()


if __name__ == "__main__":
    unittest.main()
