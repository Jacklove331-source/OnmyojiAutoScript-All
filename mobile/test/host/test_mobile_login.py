"""Android login regression scenarios using existing UI templates and synthetic frames."""
from contextlib import ExitStack
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(sys.argv.pop(1))
sys.path.insert(0, str(ROOT))

from oas_mobile import login, matcher
from tasks.GameUi.assets import GameUiAssets
from tasks.KekkaiActivation.assets import KekkaiActivationAssets
from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets
from tasks.Restart.assets import RestartAssets
from tasks.WantedQuests.assets import WantedQuestsAssets


def line(text, x, y):
    return SimpleNamespace(
        ocr_text=text,
        box=[(x - 10, y - 8), (x + 10, y - 8), (x + 10, y + 8), (x - 10, y + 8)],
    )


def scene(name, *, title=False, region=False, close=False, width=1600, results=(), crop_results=()):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    def place(rule, x, y):
        height, width = rule.image.shape[:2]
        frame[y:y + height, x:x + width] = rule.image

    if title:
        place(RestartAssets.I_LOGIN_8, 12, 564)
    if region:
        place(RestartAssets.I_LOGIN_SPECIFIC_SERVE, 505, 35)
    if close:
        place(RestartAssets.I_LOGIN_RED_CLOSE, 1086, 109)
    if width != 1280:
        frame = cv2.resize(frame, (width, width * 9 // 16))
    return SimpleNamespace(name=name, frame=frame, results=results, crop_results=crop_results)


SAVED_SERVER_TEXT = (line("示例区服名称", 589, 522),)
TITLE = scene(b"title", title=True, results=SAVED_SERVER_TEXT)
COURTYARD = scene(b"courtyard")


def frozen_scene(name=b"frozen"):
    """Bright, textured, motionless frame: the +35 "stuck courtyard" signature."""
    frame = np.random.default_rng(7).integers(60, 180, (720, 1280, 3), dtype=np.uint8)
    return SimpleNamespace(name=name, frame=frame, results=(), crop_results=())


FROZEN = frozen_scene()


def bright_scene(name, *, results=(), crop_results=(), width=1600):
    """Bright textured frame carrying OCR results (a dialog on top of the courtyard)."""
    frame = np.random.default_rng(7).integers(60, 180, (720, 1280, 3), dtype=np.uint8)
    if width != 1280:
        frame = cv2.resize(frame, (width, width * 9 // 16))
    return SimpleNamespace(name=name, frame=frame, results=results, crop_results=crop_results)


class LoginFlow:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.current = None
        self.elapsed = 0.0
        self.taps = []
        self.restarts = []

    def capture(self, display):
        self.current = next(self.frames, self.current)
        return self.current.name, self.current.frame

    def match(self, asset, png, **kwargs):
        width = self.current.frame.shape[1]
        if asset == "login_enter_game.png" and png in (b"title", b"region"):
            scale = width / 540
            return {
                "ok": True, "score": 0.92, "loc": (round(607 * width / 1600), round(711 * width / 1600)),
                "template": (131, 23), "downscale": scale,
            }
        return {"ok": False}

    def sleep(self, seconds):
        self.elapsed += seconds

    def run(self, timeout=30.0, *, character=""):
        with ExitStack() as stack:
            patches = {
                "_capture_login_frame": self.capture,
                "_courtyard_visible": lambda frame: self.current.name == b"courtyard",
                "_courtyard_entrance_visible": lambda frame: self.current.name == b"modal",
                "_in_game_menu_visible": lambda frame: self.current.name == b"talisman",
                "_ocr_with_timeout": lambda frame, timeout: (self.current.results if frame.shape[1] == 1280
                                                        else self.current.crop_results),
                "checkpoint": lambda: None,
                "time": SimpleNamespace(monotonic=lambda: self.elapsed, sleep=self.sleep),
            }
            for name, value in patches.items():
                stack.enter_context(patch.object(login, name, value))
            stack.enter_context(patch.object(login.matcher, "match", self.match))
            stack.enter_context(patch.object(login.bridge, "display_tap", lambda *args: self.taps.append(args) or True))
            stack.enter_context(patch.object(
                login, "_restart_client", lambda display: self.restarts.append(display) or 2))
            return login.ensure_logged_in(2, timeout, character=character)


class AndroidLoginTests(unittest.TestCase):
    def test_center_is_converted_to_original_capture_coordinates(self):
        hit = {"loc": (607, 711), "template": (131, 23), "downscale": 1600.0 / 540}
        self.assertEqual(matcher.tap_center(hit), (801, 745))

    def test_unscaled_center_uses_capture_pixels(self):
        hit = {"loc": (101, 205), "template": (30, 40), "downscale": 1.0}
        self.assertEqual(matcher.tap_center(hit), (116, 225))

    def test_closes_visible_region_then_enters_saved_server(self):
        region = scene(b"region", title=True, region=True, close=True)
        run = LoginFlow([region, TITLE, TITLE, COURTYARD])
        self.assertTrue(run.run())
        self.assertEqual(run.taps, [(2, 1382, 150), (2, 801, 745)])

    def test_modal_without_verified_close_needs_user_choice(self):
        run = LoginFlow([scene(b"region", title=True, region=True)])
        self.assertFalse(run.run())
        self.assertEqual(run.taps, [])

    def test_close_attempts_are_bounded_if_modal_keeps_returning(self):
        region = scene(b"region", title=True, region=True, close=True)
        run = LoginFlow([region])
        self.assertFalse(run.run())
        self.assertEqual(run.taps, [(2, 1382, 150)] * 3)

    def test_server_selection_prompt_does_not_tap_enter(self):
        title = scene(b"title", title=True, results=(line("请选择服务器", 589, 522),))
        run = LoginFlow([title])
        self.assertFalse(run.run())
        self.assertEqual(run.taps, [])

    def test_switch_label_alone_does_not_confirm_saved_server(self):
        title = scene(b"title", title=True, results=(line("切换", 735, 522), line("进入游戏", 640, 599)))
        run = LoginFlow([title])
        self.assertFalse(run.run(timeout=4))
        self.assertEqual(run.taps, [])

    def test_explicit_authentication_stops_even_with_enter_button(self):
        title = scene(b"title", title=True,
                      results=SAVED_SERVER_TEXT + (line("请输入验证码", 640, 365),))
        run = LoginFlow([title])
        self.assertFalse(run.run())
        self.assertEqual(run.taps, [])

    def test_saved_server_can_be_read_from_title_strip(self):
        title = scene(b"title", title=True, crop_results=(line("示例区服名称", 75, 20),))
        run = LoginFlow([title, title, COURTYARD])
        self.assertTrue(run.run())
        self.assertEqual(run.taps, [(2, 801, 745)])

    def test_visible_title_changing_to_region_cannot_use_stale_ocr(self):
        region = scene(b"region", title=True, region=True, close=True)
        run = LoginFlow([TITLE, region, region, TITLE, TITLE, COURTYARD])
        self.assertTrue(run.run())
        self.assertEqual(run.taps, [(2, 1382, 150), (2, 801, 745)])

    def test_configured_character_opens_picker_before_entering_game(self):
        region = scene(b"region", region=True, crop_results=(line("山 风", 220, 84),))
        run = LoginFlow([TITLE, TITLE, region, region, TITLE, TITLE, COURTYARD])
        self.assertTrue(run.run(character="山风"))
        self.assertEqual(run.taps, [(2, 931, 658), (2, 745, 278), (2, 801, 745)])

    def test_configured_character_opens_picker_even_when_enter_game_ocr_is_visible(self):
        # D-2 回归：标题页 OCR 读出「进入游戏」时，如果配置了指定角色且尚未完成选角，
        # 绝不能跳过选角器直接点「进入游戏」（否则会直接进庭院并触发 could not verify the configured character）。
        title_with_enter = scene(
            b"title", title=True,
            results=SAVED_SERVER_TEXT + (line("进入游戏", 640, 599),),
        )
        region = scene(b"region", region=True, crop_results=(line("山风", 220, 84),))
        run = LoginFlow([title_with_enter, title_with_enter, region, region, title_with_enter, COURTYARD])
        self.assertTrue(run.run(character="山风"))
        self.assertEqual(run.taps[0], (2, 931, 658))
        self.assertEqual(run.taps[1], (2, 745, 278))
        self.assertEqual(run.taps[2], (2, 800, 749))

    def test_enter_game_ocr_without_title_template_still_requires_saved_server(self):
        unverified = scene(b"other", title=False, results=(line("切换", 735, 522), line("进入游戏", 640, 599)))
        run = LoginFlow([unverified])
        self.assertFalse(run.run(timeout=4))
        self.assertEqual(run.taps, [])

    def test_configured_character_opens_picker_when_title_template_misses(self):
        # Onmyoji 2.8.85 实测：新底图导致 I_LOGIN_8 模板未命中，但 OCR 清晰读出「永远的平安京 ✿切換」和「进入游戏」。
        ocr_title = scene(
            b"title_new_bg", title=False,
            results=(line("永远的平安京 ✿切換", 640, 511), line("进入游戏", 640, 572)),
        )
        region = scene(b"region", region=True, crop_results=(line("山风", 220, 84),))
        run = LoginFlow([ocr_title, ocr_title, region, region, ocr_title, COURTYARD])
        self.assertTrue(run.run(character="山风"))
        self.assertEqual(run.taps[0], (2, 931, 658))
        self.assertEqual(run.taps[1], (2, 745, 278))
        self.assertEqual(run.taps[2], (2, 800, 715))

    def test_wide_phone_login_frame_crops_by_height_and_maps_coordinates(self):
        wide = np.zeros((1080, 2400, 3), dtype=np.uint8)
        wide[:, 300:2100] = (120, 140, 160)
        norm = login._normalized(wide)
        self.assertEqual(norm.shape, (720, 1280, 3))
        self.assertEqual(tuple(norm[360, 640]), (120, 140, 160))
        self.assertEqual(login._display_point((640, 360), wide), (1200, 540))

    def test_configured_character_needs_a_unique_complete_box(self):
        for crop_results in (
            (),
            (line("山风·初翎", 220, 84), line("山", 220, 164), line("风", 220, 220)),
            (line("山风", 220, 84), line("山风", 580, 84)),
            (line("山风", 4, 84),),
        ):
            with self.subTest(crop_results=crop_results):
                region = scene(b"region", region=True, close=True, crop_results=crop_results)
                run = LoginFlow([TITLE, TITLE, region])
                self.assertFalse(run.run(character="山风"))
                self.assertEqual(run.taps, [(2, 931, 658)])
                self.assertLess(run.elapsed, 30)

    def test_stale_role_list_cannot_select_or_confirm_a_character(self):
        region = scene(b"region", region=True, crop_results=(line("山风", 220, 84),))
        run = LoginFlow([region, TITLE, COURTYARD])
        self.assertFalse(run.run(character="山风"))
        self.assertEqual(run.taps, [])

    def test_already_in_courtyard_is_not_a_verified_character_login(self):
        run = LoginFlow([COURTYARD])
        self.assertFalse(run.run(character="山风"))
        self.assertEqual(run.taps, [])

    def test_role_picker_that_does_not_close_is_bounded(self):
        region = scene(b"region", region=True, crop_results=(line("山风", 220, 84),))
        run = LoginFlow([region])
        self.assertFalse(run.run(character="山风"))
        self.assertEqual(run.taps, [(2, 745, 278)])
        self.assertLess(run.elapsed, 30)

    def test_next_business_task_accepts_an_authenticated_menu(self):
        run = LoginFlow([scene(b"talisman")])
        self.assertTrue(run.run())
        self.assertEqual(run.taps, [])

    def test_authenticated_game_menu_markers_are_recognized(self):
        for rule in (GameUiAssets.I_CHECK_EXPLORATION,
                     GameUiAssets.I_CHECK_DEMON_ENCOUNTER_REALWORLD,
                     GameUiAssets.I_CHECK_SECRET_ZONES,
                     GameUiAssets.I_CHECK_GUILD,
                     KekkaiUtilizeAssets.I_REALM_SHIN,
                     KekkaiActivationAssets.I_A_CHECK_CARD,
                     WantedQuestsAssets.I_WQSE_FIRE):
            with self.subTest(rule=rule.name):
                frame = np.zeros((720, 1280, 3), dtype=np.uint8)
                x, y = rule.roi_back[:2]
                height, width = rule.image.shape[:2]
                frame[y:y + height, x:x + width] = rule.image
                self.assertTrue(login._in_game_menu_visible(frame))
        self.assertFalse(login._in_game_menu_visible(login._normalized(TITLE.frame)))

    def test_authenticated_menu_cannot_verify_a_configured_role(self):
        run = LoginFlow([scene(b"talisman")])
        self.assertFalse(run.run(character="山风"))
        self.assertEqual(run.taps, [])

    def test_opening_can_exceed_two_minutes(self):
        intro = scene(b"intro")
        run = LoginFlow([intro] * 80 + [TITLE, TITLE, COURTYARD])
        self.assertTrue(run.run(timeout=240))
        self.assertGreater(run.elapsed, 120)
        self.assertEqual(run.taps, [(2, 801, 745)])

    def test_late_courtyard_after_saved_server_tap_can_still_complete(self):
        loading = scene(b"intro")
        frames = [loading] * 94 + [TITLE, TITLE] + [loading] * 34 + [COURTYARD]
        run = LoginFlow(frames)
        self.assertTrue(run.run(timeout=login.LOGIN_TIMEOUT))
        self.assertGreater(run.elapsed, 240)
        self.assertEqual(run.taps, [(2, 801, 745)])

    def test_1280_display_scales_back_to_its_own_coordinates(self):
        title = scene(b"title", title=True, width=1280, results=SAVED_SERVER_TEXT)
        run = LoginFlow([title, title, COURTYARD])
        self.assertTrue(run.run())
        self.assertEqual(run.taps, [(2, 641, 596)])

    def test_courtyard_confirm_dialog_is_dismissed_with_cancel(self):
        # +36 实测（02:12:33 帧）：进庭院后被「是否打开大人之前被自动关闭的加成？」挡住，
        # 登录守卫必须点取消把框关掉，而不是空等、更不能替用户确认。
        dialog = bright_scene(b"dialog", results=(
            line("是否打开大人之前被自动关闭的加成", 640, 330), line("取消", 640, 400)))
        run = LoginFlow([dialog, COURTYARD])
        self.assertTrue(run.run())
        self.assertEqual(run.taps, [(2, 800, 500)])

    def test_shikigami_collection_page_is_an_authenticated_state(self):
        # 实测：上一任务留在新版「式神图鉴」页时，下一任务的登录守卫应直接放行
        collection = bright_scene(b"collection", results=(
            line("式神图鉴", 180, 45), line("收集进度超过全服82%的阴阳师", 600, 620)))
        run = LoginFlow([collection])
        self.assertTrue(run.run())
        self.assertEqual(run.taps, [])

    def test_town_street_is_an_authenticated_state(self):
        # +40 实测（run 8743ac67…，03:21:36 帧）：退出商店后游戏停在町中商店街，
        # 既不是庭院也不是标题页，登录守卫空转到 480s。店铺名 OCR 可靠，两个以上即放行。
        street = bright_scene(b"street", width=1280, results=(
            line("寄售屋", 100, 640), line("秘魂屋", 300, 640), line("杂货铺", 500, 640)))
        run = LoginFlow([street])
        self.assertTrue(run.run())
        self.assertEqual(run.taps, [])

    def test_activity_page_is_an_authenticated_state(self):
        # +41 实测（run c202de39 / 6f80fcee，帧 frames-MetaDemon/f-118-140434.jpg）：
        # MetaDemon 收尾后停在逢魔页（祸至无日 / 活动已结束 / 笼中残梦 / 总优胜…），
        # 下一轮 KekkaiUtilize 因此在登录阶段空转到 480s 超时。这些文案是该页特有，≥2 个即放行。
        page = bright_scene(b"activity", width=1280, results=(
            line("祸至无日", 200, 40), line("活动已结束", 640, 300), line("笼中残梦", 300, 620)))
        run = LoginFlow([page])
        self.assertTrue(run.run())
        self.assertEqual(run.taps, [])

    def test_courtyard_behind_a_modal_taps_the_cancel_point(self):
        # 实测：庭院探索灯笼命中 0.884，但町中按钮 0.228（被「是否打开…加成？」框压住）。
        # 这时必须直接点卷轴框的取消位，把人从弹窗里救出来，而不是等 30s 静止帧阶梯。
        modal = bright_scene(b"modal", width=1280)
        run = LoginFlow([modal, modal, COURTYARD])
        self.assertTrue(run.run())
        self.assertEqual(run.taps, [(2, 550, 419), (2, 550, 419)])

    def test_frozen_bright_screen_taps_the_dialog_cancel_point_first(self):
        # +35 实测：游戏画面亮、静止、但登录守卫一个分支都不认（score 恒 0.630）。
        # +36 实测：这一屏其实是「是否打开…加成？」卷轴框，所以第一次补点要打取消位。
        run = LoginFlow([FROZEN])
        self.assertFalse(run.run(timeout=40))
        self.assertEqual(run.taps, [(2, 550, 419)])
        self.assertEqual(run.restarts, [])

    def test_frozen_screen_restarts_the_client_once(self):
        run = LoginFlow([FROZEN])
        self.assertFalse(run.run(timeout=200))
        self.assertEqual(len(run.taps), login.STALL_TAPS)
        self.assertEqual(run.restarts, [2])

    def test_black_loading_screen_is_not_treated_as_frozen(self):
        # 正常冷启动的纯黑加载帧（实测灰度均值 1.0）不能被当成卡住。
        run = LoginFlow([scene(b"intro")])
        self.assertFalse(run.run(timeout=200))
        self.assertEqual(run.taps, [])
        self.assertEqual(run.restarts, [])


def short_name_strip(frame, x, *, mark_y=288, descending=True):
    frame[285:303, x-28:x-18] = (85, 61, 48)
    cv2.rectangle(frame, (x, 290), (x + 9, 300), (230, 232, 234), thickness=-1)
    first_x, last_x = x + 20, x + 25
    if not descending:
        first_x, last_x = last_x, first_x
    cv2.line(frame, (first_x, mark_y), (last_x, mark_y + 5), (230, 232, 234), thickness=2)


class ShortRoleNameTests(unittest.TestCase):
    def test_upper_right_stroke_recognizes_a_complete_short_name(self):
        for x in (717, 1134):
            with self.subTest(x=x):
                frame = np.full((900, 1600, 3), 90, dtype=np.uint8)
                short_name_strip(frame, x)
                with patch.object(login, "_ocr_with_timeout", side_effect=([], [line("q\u3001", 54, 54)])):
                    point, count = login._character_point(frame, "q\u4e36", timeout=10)
                self.assertEqual(count, 1)
                self.assertTrue(x < point[0] < x + 27)

    def test_same_mark_low_or_sloping_left_does_not_match(self):
        for mark_y, descending in ((296, True), (288, False)):
            with self.subTest(mark_y=mark_y, descending=descending):
                frame = np.full((900, 1600, 3), 90, dtype=np.uint8)
                short_name_strip(frame, 1134, mark_y=mark_y, descending=descending)
                with patch.object(login, "_ocr_with_timeout", return_value=[]) as ocr:
                    self.assertEqual(login._character_point(frame, "q\u4e36", timeout=10), (None, 0))
                self.assertEqual(ocr.call_count, 1)

    def test_ocr_can_omit_only_the_visually_verified_stroke(self):
        frame = np.full((900, 1600, 3), 90, dtype=np.uint8)
        short_name_strip(frame, 1134)
        with patch.object(login, "_ocr_with_timeout", side_effect=([], [line("q", 54, 54)])):
            self.assertEqual(login._character_point(frame, "q\u4e36", timeout=10)[1], 1)
        with patch.object(
            login, "_ocr_with_timeout",
            side_effect=([], [line("g", 54, 54)], [line("g\u3001", 54, 54)],
                         [line("g", 54, 54)], [line("g", 54, 54)])
        ):
            self.assertEqual(login._character_point(frame, "q\u4e36", timeout=10), (None, 0))
        frame[284:298, 1150:1164] = 90
        with patch.object(login, "_ocr_with_timeout", return_value=[line("q", 54, 54)]):
            self.assertEqual(login._character_point(frame, "q\u4e36", timeout=10), (None, 0))

    def test_letter_only_crop_confirms_when_name_crops_are_unreadable(self):
        # Real ML Kit dropped both upscaled name crops but read the tight
        # leading-glyph crop; geometry had already proven the trailing stroke.
        frame = np.full((900, 1600, 3), 90, dtype=np.uint8)
        short_name_strip(frame, 1134)
        with patch.object(login, "_ocr_with_timeout",
                          side_effect=([], [], [], [line("q", 54, 54)])) as ocr:
            point, count = login._character_point(frame, "q\u4e36", timeout=10)
        self.assertEqual(count, 1)
        self.assertTrue(1134 < point[0] < 1161)
        letter_crop = np.asarray(ocr.call_args_list[-1].args[0])
        self.assertTrue((letter_crop[:8] == 0).all())
        self.assertLess(letter_crop.shape[1], 100)

    def test_letter_only_crop_rejects_a_different_letter(self):
        frame = np.full((900, 1600, 3), 90, dtype=np.uint8)
        short_name_strip(frame, 1134)
        with patch.object(login, "_ocr_with_timeout",
                          side_effect=([], [], [], [line("x", 54, 54)], [line("x", 54, 54)])):
            self.assertEqual(login._character_point(frame, "q\u4e36", timeout=10), (None, 0))

    def test_duplicate_short_names_are_rejected_as_ambiguous(self):
        frame = np.full((900, 1600, 3), 90, dtype=np.uint8)
        for x in (717, 1134):
            short_name_strip(frame, x)
        reading = [line("q\u3001", 54, 54)]
        with patch.object(login, "_ocr_with_timeout", side_effect=([], reading, reading)):
            self.assertEqual(login._character_point(frame, "q\u4e36", timeout=10), (None, 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
