"""Guard the Android exit confirmation before trying generic navigation fallbacks."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(sys.argv.pop(1))
sys.path.insert(0, str(ROOT))

from module.atom.list import RuleList
from module.exception import GamePageUnknownError
from tasks.Component.GeneralBuff.general_buff import GeneralBuff
from tasks.Component.GeneralRoom.assets import GeneralRoomAssets
from tasks.Component.GeneralRoom import general_room
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.CollectiveMissions.script_task import MC, ScriptTask as CollectiveMissions
from tasks.DailyTrifles.script_task import ScriptTask as DailyTrifles
from tasks.Delegation.config import DelegationConfig
from tasks.Delegation.script_task import ScriptTask as Delegation
from tasks.DemonEncounter.config import BoxBuyConfig
from tasks.DemonEncounter.script_task import ScriptTask as DemonEncounter
from tasks.GameUi import default_pages, navigator
from tasks.GameUi.page import page_main
from tasks.GoldYoukai.assets import GoldYoukaiAssets
from tasks.GoldYoukai.script_task import ScriptTask as GoldYoukai
from tasks.KekkaiUtilize.script_task import ScriptTask as KekkaiUtilize
from tasks.RichMan.script_task import ScriptTask as RichMan
from tasks.Tako.script_task import ScriptTask as Tako
from tasks.WeeklyTrifles.script_task import ScriptTask as WeeklyTrifles
from tasks.WantedQuests.script_task import ScriptTask as WantedQuests


class NavigationProbe:
    O_ANDROID_EXIT_PROMPT = navigator.GameUi.O_ANDROID_EXIT_PROMPT
    O_ANDROID_EXIT_CANCEL = navigator.GameUi.O_ANDROID_EXIT_CANCEL
    O_IN_GAME_EXIT_CONFIRM = navigator.GameUi.O_IN_GAME_EXIT_CONFIRM
    O_ANDROID_GRAPHICS_CONTINUE = navigator.GameUi.O_ANDROID_GRAPHICS_CONTINUE
    O_ANDROID_GRAPHICS_NOTICE = navigator.GameUi.O_ANDROID_GRAPHICS_NOTICE
    C_ANDROID_GRAPHICS_CONTINUE = navigator.GameUi.C_ANDROID_GRAPHICS_CONTINUE
    _close_android_graphics_notice = navigator.GameUi._close_android_graphics_notice
    _is_in_game_exit_prompt = navigator.GameUi._is_in_game_exit_prompt
    DEFAULT_UNKNOWN_CLOSERS = ()

    def __init__(self, title, cancel, desktop=False, continue_lines=(), notice_lines=(), prompt_lines=None):
        self.navigator = SimpleNamespace(unknown_close_history=[], local_unknown_closers=[])
        img = np.zeros((10, 10, 3), dtype=np.uint8) if prompt_lines is not None else object()
        self.device = SimpleNamespace(is_desktop=desktop, app_lost_login=lambda: False, adb_shell=self.back, image=img)
        self.title = title
        self.cancel = cancel
        self.continue_lines = continue_lines
        self.notice_lines = notice_lines
        self.prompt_lines = prompt_lines
        self.events = []
        self.clicks = []

    def maybe_screenshot(self, skip):
        pass

    def ocr_appear(self, rule):
        self.events.append(rule.name.lower())
        return self.title

    def ocr_appear_click(self, rule, interval):
        self.events.append(rule.name.lower())
        return self.cancel

    def _record_unknown_close_event(self, event):
        self.events.append(event)

    def back(self, args):
        self.events.append("back")

    def click(self, rule, interval):
        self.clicks.append(rule)
        return True

    def close_unknown(self):
        prompt_ret = [SimpleNamespace(ocr_text=line) for line in (self.prompt_lines or ())]
        with (
            patch.object(navigator.time, "sleep"),
            patch.object(self.O_ANDROID_EXIT_PROMPT, "detect_and_ocr", return_value=prompt_ret),
            patch.object(self.O_ANDROID_GRAPHICS_CONTINUE, "detect_and_ocr",
                         return_value=[SimpleNamespace(ocr_text=line) for line in self.continue_lines]),
            patch.object(self.O_ANDROID_GRAPHICS_NOTICE, "detect_and_ocr",
                         return_value=[SimpleNamespace(ocr_text=line) for line in self.notice_lines]),
        ):
            return navigator.GameUi.close_unknown_pages(self)


class CourtyardScrollTests(unittest.TestCase):
    def setUp(self):
        self.assets = default_pages.GameUiAssets
        self.show = [self.assets.I_MAIN_GOTO_EXPLORATION, self.assets.I_MAIN_GOTO_TOWN]
        self.taps = []
        self.task = SimpleNamespace(
            device=SimpleNamespace(is_desktop=False),
            appear=lambda icon: icon in self.show,
            appear_then_click=self.click_if_visible,
            click=self.click,
        )
        self.enter_friends = default_pages._courtyard_scroll_entry(self.assets.I_MAIN_GOTO_FRIENDS)

    def click(self, icon, interval):
        self.taps.append(icon)
        return True

    def click_if_visible(self, icon, interval):
        return icon in self.show and self.click(icon, interval)

    def test_collapsed_mobile_scroll_opens_before_clicking_the_target(self):
        self.assertFalse(self.enter_friends(self.task))
        self.assertEqual(self.taps, [default_pages._COURTYARD_SCROLL_TOGGLE])
        self.show.append(self.assets.I_MAIN_GOTO_FRIENDS)
        self.assertTrue(self.enter_friends(self.task))
        self.assertEqual(self.taps[-1], self.assets.I_MAIN_GOTO_FRIENDS)

    def test_expanded_scroll_is_not_toggled_when_the_target_is_unreadable(self):
        self.show.append(self.assets.I_MAIN_GOTO_GUILD)
        self.assertFalse(self.enter_friends(self.task))
        self.assertEqual(self.taps, [])

    def test_expanded_team_icon_can_use_its_exact_label(self):
        team_label = default_pages._COURTYARD_TEAM_LABEL
        enter_team = default_pages._courtyard_scroll_entry(self.assets.I_MAIN_GOTO_TEAM, mobile_label=team_label)
        label_click = []
        self.task.ocr_appear_click = lambda label, *, interval, exact: label_click.append((label, exact)) or True
        self.assertFalse(enter_team(self.task))
        self.assertEqual(label_click, [])
        self.show.append(self.assets.I_MAIN_GOTO_GUILD)
        self.assertTrue(enter_team(self.task))
        self.assertEqual(label_click, [(team_label, True)])

    def test_no_unverified_scroll_tap_in_other_scenes_or_on_desktop(self):
        self.task.device.is_desktop = True
        self.assertFalse(self.enter_friends(self.task))
        self.task.device.is_desktop = False
        self.show.remove(self.assets.I_MAIN_GOTO_TOWN)
        self.assertFalse(self.enter_friends(self.task))
        self.assertEqual(self.taps, [])


class TownNavigationTests(unittest.TestCase):
    def test_mobile_town_requires_the_back_button_and_night_parade_sign(self):
        back = navigator.GlobalGameAssets.I_UI_BACK_BLUE
        night_parade = default_pages._MOBILE_TOWN_NIGHT_PARADE
        for desktop, images, labels, expected in (
            (False, [back], [night_parade], True),
            (False, [back], [], False),
            (False, [], [night_parade], False),
            (True, [back], [night_parade], False),
        ):
            with self.subTest(desktop=desktop, images=images, labels=labels):
                task = SimpleNamespace(device=SimpleNamespace(is_desktop=desktop),
                                       appear=lambda rule: rule in images, ocr_appear=lambda rule: rule in labels)
                self.assertEqual(bool(default_pages._mobile_town_visible(task)), expected)

    def test_mobile_town_only_clicks_the_recognized_demon_sign(self):
        back = navigator.GlobalGameAssets.I_UI_BACK_BLUE
        labels = [default_pages._MOBILE_TOWN_NIGHT_PARADE]
        clicks = []
        click = lambda rule, interval: clicks.append(rule) or True
        task = SimpleNamespace(device=SimpleNamespace(is_desktop=False), appear=lambda rule: rule is back,
                               ocr_appear=lambda rule: rule in labels, click=click, appear_then_click=click)
        self.assertFalse(default_pages._enter_town_demon_encounter(task))
        self.assertEqual(clicks, [])
        labels.append(default_pages._MOBILE_TOWN_DEMON_LABEL)
        self.assertTrue(default_pages._enter_town_demon_encounter(task))
        self.assertTrue(default_pages._leave_town(task))
        self.assertEqual(clicks, [default_pages._MOBILE_TOWN_DEMON_ENTRY, back])
        task.device.is_desktop = True
        self.assertTrue(default_pages._enter_town_demon_encounter(task))
        self.assertTrue(default_pages._leave_town(task))
        self.assertEqual(clicks[-2:], [default_pages.GameUiAssets.I_TOWN_GOTO_DEMON_ENCOUNTER,
                                      default_pages.GameUiAssets.I_TOWN_GOTO_MAIN])


class DemonEncounterDefeatTests(unittest.TestCase):
    def test_mobile_defeat_dismisses_a_confirmed_reward_without_reporting_a_win(self):
        for desktop, reward_visible in ((False, True), (False, False), (True, True)):
            with self.subTest(desktop=desktop, reward_visible=reward_visible):
                calls = []
                task = SimpleNamespace(
                    I_DE_WIN=object(), I_WIN=object(), I_REWARD=object(), I_FALSE=object(),
                    device=SimpleNamespace(is_desktop=desktop, stuck_record_add=lambda marker: None,
                                           click_record_clear=lambda: None),
                    screenshot=lambda: None,
                    appear_then_click=lambda rule, interval: False,
                )
                task.appear = lambda rule: rule is task.I_FALSE
                task.wait_until_appear = lambda rule, **kwargs: reward_visible and rule is task.I_REWARD
                task.ui_click_until_disappear = lambda rule, **kwargs: calls.append(rule)
                self.assertFalse(DemonEncounter.battle_wait(task, False))
                expected = [task.I_FALSE, task.I_REWARD] if not desktop and reward_visible else [task.I_FALSE]
                self.assertEqual(calls, expected)


class OptionalAudioPromptTests(unittest.TestCase):
    def test_soul_switch_cancels_optional_download_before_tapping_preset(self):
        taps = []
        clicked = set()

        def appear(rule):
            if rule is SwitchSoul.I_SOUL_PRESET:
                return True
            return rule is SwitchSoul.I_SOU_SWITCH_1 and 'preset' in clicked

        def ocr_click(rule, *, interval, exact):
            taps.append((rule, exact))
            clicked.add('cancel')
            return True

        def click(rule, *, interval):
            taps.append((rule, False))
            clicked.add('preset')
            return True

        probe = SimpleNamespace(
            device=SimpleNamespace(is_desktop=False),
            screenshot=lambda: None,
            appear=appear,
            ocr_appear=lambda rule, interval: 'cancel' not in clicked,
            ocr_appear_click=ocr_click,
            click=click,
            I_SOU_SWITCH_1=SwitchSoul.I_SOU_SWITCH_1,
            I_SOU_SWITCH_2=SwitchSoul.I_SOU_SWITCH_2,
            I_SOU_SWITCH_3=SwitchSoul.I_SOU_SWITCH_3,
            I_SOU_SWITCH_4=SwitchSoul.I_SOU_SWITCH_4,
            I_SOU_TEAM_PRESENT=SwitchSoul.I_SOU_TEAM_PRESENT,
            I_SOUL_PRESET=SwitchSoul.I_SOUL_PRESET,
            _O_AUDIO_DOWNLOAD_TITLE=SwitchSoul._O_AUDIO_DOWNLOAD_TITLE,
            _O_AUDIO_DOWNLOAD_CANCEL=SwitchSoul._O_AUDIO_DOWNLOAD_CANCEL,
        )
        SwitchSoul.click_preset(probe)
        self.assertEqual(taps, [
            (SwitchSoul._O_AUDIO_DOWNLOAD_CANCEL, True),
            (SwitchSoul.I_SOUL_PRESET, False),
        ])


class DailyLuckRecognitionTests(unittest.TestCase):
    def test_observed_ocr_alias_requires_the_friends_page(self):
        taps = []
        probe = SimpleNamespace(
            device=SimpleNamespace(is_desktop=False, image=None),
            match_page_once=lambda page: False,
            click=lambda rule, interval: taps.append(rule) or True,
            O_LUCK_MSG=SimpleNamespace(ocr=lambda image: '吉同。'),
            _C_MOBILE_LUCK_MSG=DailyTrifles._C_MOBILE_LUCK_MSG,
        )
        self.assertFalse(DailyTrifles._click_luck_msg(probe))
        self.assertEqual(taps, [])
        probe.match_page_once = lambda page: page is default_pages.page_friends
        self.assertTrue(DailyTrifles._click_luck_msg(probe))
        self.assertEqual(taps, [DailyTrifles._C_MOBILE_LUCK_MSG])
        probe.O_LUCK_MSG.ocr = lambda image: '吉日'
        self.assertFalse(DailyTrifles._click_luck_msg(probe))
        self.assertEqual(len(taps), 1)


class AndroidGoldRecognitionTests(unittest.TestCase):
    def test_gold_result_match_tolerates_the_observed_one_pixel_shift(self):
        tile = cv2.cvtColor(cv2.imread(str(ROOT / 'tasks/GoldYoukai/gy/gy_gold_win.png')), cv2.COLOR_BGR2RGB)
        height, width = tile.shape[:2]
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[44:44 + height, 379:379 + width] = tile
        self.assertTrue(GoldYoukaiAssets.I_GOLD_WIN.match(image))

    def test_gold_entry_normalizes_only_the_observed_ticket_label(self):
        names = GeneralRoomAssets.L_TEAM_LIST.array
        aliases = GeneralRoomAssets.L_TEAM_LIST.ocr_aliases
        slots = RuleList('.', 'vertical', 'ocr', (26, 106, 360, 549), (216, 61), names,
                         ocr_aliases=aliases)
        gold = SimpleNamespace(ocr_text='全币妖怪', score=0.99,
                               box=np.array([[40, 20], [60, 20], [60, 40], [40, 40]]))
        exp = SimpleNamespace(ocr_text='经验妖怪', score=0.99, box=gold.box + 17)
        ocr = SimpleNamespace(detect_and_ocr=lambda image: [exp, gold])
        with patch.object(slots, 'target_check', return_value=True):
            slots._target = ocr
            self.assertEqual(slots.ocr_appear(None, '金币妖怪'), (76, 136))

    def test_gold_selection_confirms_only_known_title_readings(self):
        timer = SimpleNamespace(start=lambda: timer, reached=lambda: True, reset=lambda: None)
        expected_titles = ('金币妖怪', '全币妖怪', '金和妖怪', '金币妖陸', '金中妖怪', '金中妖修')
        for title in (*expected_titles, '经验妖怪', '金石其他妖怪', ''):
            with self.subTest(title=title):
                clicks = []
                room = GeneralRoom.__new__(GeneralRoom)
                room.list_find = lambda rule, name: (125, 269)
                room.screenshot = lambda: None
                room.device = SimpleNamespace(
                    is_desktop=False, image=None,
                    click=lambda x, y, **kwargs: clicks.append((x, y)),
                )
                frames = iter(('经验妖怪',))
                room.O_GR_ZONES_NAME = SimpleNamespace(ocr=lambda image: next(frames, title))
                expected = title in expected_titles
                with patch.object(general_room, 'Timer', return_value=timer):
                    self.assertEqual(room.check_zones('金币妖怪'), expected)
                self.assertEqual(clicks, [(125, 269)] * (1 if expected else 4))

    def test_courtyard_return_failure_keeps_the_completed_tasks_scheduled(self):
        cases = (
            (RichMan, 'RichMan', SimpleNamespace(rich_man=SimpleNamespace(
                thousand_things=None, shrine=None, guild_store=None,
            )), ('execute_tt', 'execute_shrine', 'execute_guild', 'execute_mall')),
            (Delegation, 'Delegation', SimpleNamespace(delegation=SimpleNamespace(
                delegation_config=DelegationConfig(),
            )), ('check_reward', 'delegate_one')),
            (WeeklyTrifles, 'WeeklyTrifles', SimpleNamespace(weekly_trifles=SimpleNamespace(
                trifles=SimpleNamespace(share_collect=False, share_area_boss=False,
                                       share_secret=False, save_touch_fish=False, broken_amulet=10),
            )), ('_broken_amulet',)),
        )
        for task_class, name, config, methods in cases:
            with self.subTest(task=name):
                sequence = []

                def goto(page):
                    if page is page_main:
                        sequence.append('courtyard')
                        raise GamePageUnknownError('Could not reach courtyard')

                task = SimpleNamespace(config=config, device=SimpleNamespace(is_desktop=False),
                                       goto_page=goto, set_next_run=lambda **options: sequence.append(options))
                for method in methods:
                    setattr(task, method, lambda *args: None)
                with self.assertRaisesRegex(GamePageUnknownError, 'Could not reach courtyard'):
                    task_class.run(task)
                self.assertEqual(sequence[0]['task'], name)
                self.assertTrue(sequence[0]['success'])
                self.assertEqual(sequence[1:], ['courtyard'])

    def test_demon_lantern_shop_honors_purchase_settings(self):
        def probe(settings, appear):
            return SimpleNamespace(config=SimpleNamespace(
                demon_encounter=SimpleNamespace(box_buy_config=settings),
            ), screenshot=lambda: None, I_JADE_50='price', I_MYSTERY_AMULET='amulet',
                I_SUSHI='stamina', I_DE_FIND='leave', appear=appear)

        disabled = probe(BoxBuyConfig(box_buy_amulet=False, box_buy_sushi=False),
                         lambda rule: self.fail('A disabled lantern shop should stay closed'))
        DemonEncounter._box(disabled, 'lantern')

        presses = []
        stamina_only = probe(BoxBuyConfig(box_buy_amulet=False, box_buy_sushi=True),
                             lambda rule: rule in {'price', 'amulet'})
        stamina_only.appear_then_click = lambda rule, **kw: presses.append(rule) or True
        DemonEncounter._box(stamina_only, 'lantern')
        self.assertEqual(presses, ['leave'])
        self.assertTrue(BoxBuyConfig().box_buy_amulet)

    def test_unconfirmed_gold_entry_turns_off_the_buff_and_reports_failure(self):
        switches = []
        settings = SimpleNamespace(buff_gold_50_click=False, buff_gold_100_click=True)
        task = SimpleNamespace(
            config=SimpleNamespace(gold_youkai=SimpleNamespace(
                switch_soul=SimpleNamespace(enable=False, enable_switch_by_name=False),
                gold_youkai=settings,
            )),
            device=SimpleNamespace(is_desktop=False),
            goto_page=lambda page: None,
            open_buff=lambda: None,
            close_buff=lambda: None,
            gold_100=lambda enabled=True: switches.append(enabled),
            _enter_team_page=lambda: True,
            check_zones=lambda name: False,
        )
        task._return_and_disable_buffs = lambda con: GoldYoukai._return_and_disable_buffs(task, con)
        with self.assertRaisesRegex(GamePageUnknownError, 'verify the Gold Youkai zone'):
            GoldYoukai.run(task)
        self.assertEqual(switches, [True, False])

    def test_mobile_gold_timer_recognizes_only_the_switch_colors(self):
        blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        for rgb, expected in (((225, 135, 0), True), ((187, 12, 36), False), ((198, 186, 168), None)):
            with self.subTest(rgb=rgb):
                blank[283:293, 777:792] = rgb
                self.assertIs(GeneralBuff._mobile_gold_timer_enabled(blank, 288), expected)
        self.assertIsNone(GeneralBuff._mobile_gold_timer_enabled(blank, 0))

    def test_mobile_buff_uses_one_matching_effect_and_percentage(self):
        box = np.array([[15, 10], [150, 10], [150, 33], [15, 33]])
        rows = [
            SimpleNamespace(ocr_text='觉醒副本掉落额外的觉醒材料', box=box),
            SimpleNamespace(ocr_text='战斗胜利获得的经验增加50%', box=box + 35),
            SimpleNamespace(ocr_text='战斗肚利获得的全币増加100%', box=box + 69),
        ]
        rule = SimpleNamespace(keyword='战斗胜利获得的金币增加100%', roi=(425, 118, 342, 397),
                               detect_and_ocr=lambda image: rows)
        self.assertEqual(GeneralBuff._mobile_buff_label_area(rule, None), (509, 197, 135, 23))
        rule.keyword = '战斗胜利获得的金币增加50%'
        self.assertIsNone(GeneralBuff._mobile_buff_label_area(rule, None))
        rule.keyword = '战斗胜利获得的金币增加100%'
        rows.append(rows[-1])
        self.assertIsNone(GeneralBuff._mobile_buff_label_area(rule, None))


class AndroidTakoResultTests(unittest.TestCase):
    def test_already_back_in_courtyard_skips_reward_input(self):
        task = Tako.__new__(Tako)
        task.screenshot = lambda: None
        task.match_page_once = lambda page: page is default_pages.page_main
        with patch.object(task, 'appear_then_click') as guarded_click:
            task._settle_mobile_reward()
        guarded_click.assert_not_called()

    def test_result_input_requires_a_reward_template(self):
        task = Tako.__new__(Tako)
        frames = iter((False, True))
        task.screenshot = lambda: setattr(task, 'courtyard', next(frames))
        task.match_page_once = lambda page: task.courtyard and page is default_pages.page_main
        with patch.object(task, 'appear_then_click', return_value=True) as guarded_click:
            task._settle_mobile_reward()
        guarded_click.assert_called_once_with(task.I_REWARD, action=task.C_REWARD_2, interval=1.5)


class CollectiveMissionTests(unittest.TestCase):
    def test_game_ocr_separators_do_not_hide_the_requested_mission(self):
        task = CollectiveMissions.__new__(CollectiveMissions)
        task.device = SimpleNamespace(image=None)
        task.screenshot = lambda: None
        rule = lambda text: SimpleNamespace(ocr=lambda image: text)
        self.assertEqual(task.detect_one(rule('远远不够-'), rule('-觉醒三')), MC.AW3)
        self.assertEqual(task.detect_one(rule('远远不够-'), rule('-御灵三,')), MC.GR3)
        self.assertEqual(task.detect_one(rule('契灵採查'), rule('')), MC.BL)
        self.assertEqual(task.detect_one(rule('远远不够'), rule('-御魂三')), MC.UNKNOWN)

    def test_mobile_refresh_remains_below_repeated_input_limit(self):
        task = CollectiveMissions.__new__(CollectiveMissions)
        task.device = SimpleNamespace(is_desktop=False, image=None)
        task.detect_one = lambda *args: MC.AW1
        task.I_CM_SWITCH = SimpleNamespace(
            match_brightness=lambda image: True,
            match_mean_color=lambda image, color: True,
        )
        with patch.object(task, 'appear_then_click', return_value=True) as refresh:
            self.assertFalse(task.select_mission('觉醒三'))
        self.assertEqual(refresh.call_count, 8)


class AndroidRealmRecognitionTests(unittest.TestCase):
    def test_shifted_realm_control_requires_gate_at_observed_location(self):
        task = KekkaiUtilize.__new__(KekkaiUtilize)
        task.device = SimpleNamespace(is_desktop=False, image=None)
        template = task.I_REALM_SHIN.image
        height, width = template.shape[:2]

        for x, y, expected in ((369, 488, True), (175, 460, False)):
            with self.subTest(gate_x=x):
                image = np.zeros((720, 1280, 3), dtype=np.uint8)
                image[y:y + height, x:x + width] = template
                task.device.image = image
                self.assertTrue(task.appear(task.I_REALM_SHIN))
                self.assertEqual(task._mobile_realm_controls_shifted(), expected)

        task.device.image.fill(0)
        self.assertFalse(task._mobile_realm_controls_shifted())


class WantedQuestsStoryTests(unittest.TestCase):
    def test_dialogue_requires_both_caption_elements(self):
        screenshot = np.zeros((720, 1280, 3), dtype=np.uint8)
        for x in (328, 835):
            with self.subTest(speaker_ribbon_x=x):
                screenshot.fill(0)
                screenshot[540:566, x:x + 115] = (155, 16, 28)
                self.assertFalse(WantedQuests._mobile_secret_dialogue_visible(screenshot))
                screenshot[620:634, 333:540] = (235, 225, 201)
                self.assertTrue(WantedQuests._mobile_secret_dialogue_visible(screenshot))
                screenshot[540:566] = 0
                self.assertFalse(WantedQuests._mobile_secret_dialogue_visible(screenshot))

    def test_secret_result_wait_only_advances_story_and_waits_for_challenge(self):
        screen = np.zeros((720, 1280, 3), dtype=np.uint8)
        story = screen.copy()
        story[540:566, 835:950] = (155, 16, 28)
        story[620:634, 333:540] = (235, 225, 201)
        task = WantedQuests.__new__(WantedQuests)
        task.device = SimpleNamespace(image=screen)
        states = iter(((screen, None), (story, None), (screen, task.I_WQSE_FIRE)))
        clicks = []

        def capture():
            task.device.image, task.current_marker = next(states)

        task.screenshot = capture
        task.appear = lambda rule: rule is task.current_marker
        task.appear_then_click = lambda *args, **kwargs: False
        task.click = lambda rule, **kwargs: clicks.append(rule) or True
        task._wait_for_mobile_secret_return(expect_challenge=True)
        self.assertEqual(clicks, [task.C_SECRET_CHAT])


class WantedQuestsInvitationTests(unittest.TestCase):
    def test_legacy_paths_honor_no_invite(self):
        trace = object()
        invite = object()
        sent = []
        probe = SimpleNamespace(
            get_config=lambda: SimpleNamespace(cooperation_type=SimpleNamespace(value='NoInvite')),
            goto_page=lambda page: None,
            screenshot=lambda: None,
            appear=lambda rule: rule in (trace, invite),
            ui_click_until_disappear=lambda rule: None,
            need_invite_vip=lambda: False,
            invite_five=lambda: sent.append('invite'),
            I_TRACE_DISABLE=trace,
            I_WQ_INVITE_1=invite,
            I_WQ_INVITE_2=object(),
            I_WQ_INVITE_3=object(),
            I_UI_BACK_RED=object(),
        )
        probe._cooperation_invites_enabled = lambda: WantedQuests._cooperation_invites_enabled(probe)
        self.assertTrue(WantedQuests.pre_work(probe))
        self.assertFalse(WantedQuests.pre_work_cooperation_only(probe))
        self.assertEqual(sent, [])
        probe.get_config = lambda: SimpleNamespace(cooperation_type=SimpleNamespace(value='Any'))
        self.assertTrue(WantedQuests.pre_work(probe))
        self.assertEqual(sent, ['invite'])


class NavigationRecoveryTests(unittest.TestCase):
    def test_android_exit_confirmation_uses_only_cancel(self):
        ui = NavigationProbe(title=True, cancel=True)
        self.assertTrue(ui.close_unknown())
        self.assertEqual(ui.events, ["android_exit_prompt", "android_exit_cancel", "android_exit_cancel"])

    def test_in_game_explore_exit_prompt_clicks_confirm_instead_of_cancel(self):
        ui = NavigationProbe(title=True, cancel=True, prompt_lines=("取消", "确认退出探索吗?", "确认"))
        self.assertTrue(ui.close_unknown())
        self.assertEqual(ui.events, ["android_exit_prompt", "in_game_exit_confirm", "in_game_exit_confirm"])

    def test_android_game_exit_prompt_still_clicks_cancel(self):
        ui = NavigationProbe(title=True, cancel=True, prompt_lines=("提示", "确定要退出游戏吗？", "取消", "确认"))
        self.assertTrue(ui.close_unknown())
        self.assertEqual(ui.events, ["android_exit_prompt", "android_exit_cancel", "android_exit_cancel"])

    def test_identified_exit_with_unreadable_cancel_skips_generic_actions(self):
        ui = NavigationProbe(title=True, cancel=False)
        self.assertFalse(ui.close_unknown())
        self.assertEqual(ui.events, ["android_exit_prompt", "android_exit_cancel"])

    def test_other_unknown_android_pages_keep_back_fallback(self):
        ui = NavigationProbe(title=False, cancel=False)
        self.assertTrue(ui.close_unknown())
        self.assertEqual(ui.events, ["android_exit_prompt", "back", "back_key"])

    def test_android_graphics_notice_requires_the_body_and_continue_text(self):
        notice = ("阴阳师大人想要更改设置，后续", "可以在设置 -> 音画 -> 画面中进", "行设置。")
        ui = NavigationProbe(False, False, continue_lines=("点击任意位置 继续",), notice_lines=notice)
        self.assertTrue(ui.close_unknown())
        self.assertEqual(ui.clicks, [ui.C_ANDROID_GRAPHICS_CONTINUE])
        self.assertEqual(ui.events, ["android_exit_prompt", "android_graphics_continue"])

        for body, continuation, desktop in (
            (("活动说明",), ("点击任意位置继续",), False),
            (notice, (), False),
            (notice, ("点击任意位置继续",), True),
        ):
            with self.subTest(body=body, continuation=continuation, desktop=desktop):
                ui = NavigationProbe(False, False, desktop, continuation, body)
                self.assertTrue(ui.close_unknown())
                self.assertEqual(ui.clicks, [])
                self.assertEqual(ui.events[-2:], ["back", "back_key"])

    def test_desktop_does_not_read_android_exit_prompt(self):
        ui = NavigationProbe(title=True, cancel=True, desktop=True)
        self.assertTrue(ui.close_unknown())
        self.assertEqual(ui.events, ["back", "back_key"])

    def test_does_not_press_back_immediately_after_exit_cancel(self):
        ui = NavigationProbe(title=False, cancel=False)
        ui.navigator.unknown_close_history.append("android_exit_cancel")
        self.assertFalse(ui.close_unknown())
        self.assertNotIn("back", ui.events)
        self.assertIn("skip_back_after_exit_cancel", ui.events)

    def test_delegation_page_has_mobile_ocr_check_non_overlapping_with_entry(self):
        check_roi = default_pages._MOBILE_CHECK_DELEGATION.roi
        entry_roi = default_pages._MOBILE_EXPLORATION_GOTO_DELEGATION.roi
        # Top-left title bar (y < 100) must never overlap bottom bar (y > 600)
        self.assertLess(check_roi[1] + check_roi[3], 120)
        self.assertGreater(entry_roi[1], 600)
        task = SimpleNamespace(
            appear=lambda rule: False,
            ocr_appear=lambda rule: rule is default_pages._MOBILE_CHECK_DELEGATION,
        )
        self.assertTrue(default_pages.page_delegation.recognizer.evaluate(task))

    def test_general_room_nian_title_matches_truncated_mobile_ocr_and_caps_retries(self):
        # 移动端红底白字“年兽”常被识别为单字“年”，应正常命中
        timer = SimpleNamespace(start=lambda: timer, reached=lambda: True, reset=lambda: None)
        clicks = []
        room = GeneralRoom.__new__(GeneralRoom)
        room.list_find = lambda rule, name: (180, 380)
        room.screenshot = lambda: None
        room.device = SimpleNamespace(
            is_desktop=False, image=None,
            click=lambda x, y, **kwargs: clicks.append((x, y)),
        )
        room.O_GR_ZONES_NAME = SimpleNamespace(ocr=lambda image: '年', keyword='')
        with patch.object(general_room, 'Timer', return_value=timer):
            self.assertTrue(room.check_zones('年兽'))
        self.assertEqual(clicks, [])

        # 持续无法匹配时应在 8 次点击后安全退出，不得死循环
        room.O_GR_ZONES_NAME = SimpleNamespace(ocr=lambda image: '经验妖怪', keyword='')
        with patch.object(general_room, 'Timer', return_value=timer):
            self.assertFalse(room.check_zones('年兽'))
        self.assertEqual(len(clicks), 8)

    def test_kekkai_activation_accepts_mobile_ocr_taiko_variant(self):
        # 移动端 PP-OCR 常将“勾玉+1/h”误识为“幻玉+1/h”
        from tasks.KekkaiActivation.config import CardType
        from tasks.KekkaiActivation.script_task import ScriptTask as KekkaiActivationTask

        task = KekkaiActivationTask.__new__(KekkaiActivationTask)
        task.config = SimpleNamespace(
            kekkai_activation=SimpleNamespace(
                activation_config=SimpleNamespace(
                    card_type=CardType.TAIKO,
                    min_taiko_num=0,
                    min_fish_num=0,
                )
            )
        )
        task.screenshot = lambda: None
        task.device = SimpleNamespace(image=None)
        box = [[10, 20], [90, 20], [90, 50], [10, 50]]
        task.O_CHECK_CARD_NUMBER = SimpleNamespace(
            roi=(305, 153, 107, 481),
            detect_and_ocr=lambda image: [
                SimpleNamespace(ocr_text='大鼓', box=box),
                SimpleNamespace(ocr_text='式神经验', box=box),
                SimpleNamespace(ocr_text='幻玉+1/h', box=box),
            ],
        )
        target = task.check_card_num()
        self.assertIsNotNone(target)
        self.assertEqual(target.roi_front, (315, 173, 80, 30))

    def test_shirin_exit_closes_red_overlay_popup_before_back(self):
        # 神社页上方若残留集体任务等带红叉弹窗，必须优先点红叉关闭
        from tasks.GlobalGame.assets import GlobalGameAssets

        clicked = []
        probe = SimpleNamespace(
            appear_then_click=lambda rule, interval=1.0: (
                clicked.append(rule) or True
                if rule is GlobalGameAssets.I_UI_BACK_RED else False
            )
        )
        self.assertTrue(default_pages._exit_shirin(probe))
        self.assertEqual(clicked, [GlobalGameAssets.I_UI_BACK_RED])

    def test_collective_missions_closes_popup_when_already_at_30_limit(self):
        # 今日 30/30 已达上限提前退出时，也必须关闭集体任务弹窗再结束
        from tasks.CollectiveMissions.script_task import ScriptTask as CollectiveTask
        from module.exception import TaskEnd

        events = []
        task = CollectiveTask.__new__(CollectiveTask)
        task.config = SimpleNamespace(
            collective_missions=SimpleNamespace(
                missions_config=SimpleNamespace(missions_rule='觉醒一', missions_select='觉醒一')
            )
        )
        task.device = SimpleNamespace(image=None)
        task.goto_page = lambda page: events.append('goto_guild')
        task.ui_click = lambda a, b: events.append('open_cm')
        task.screenshot = lambda: None
        task.O_CM_NUMBER = SimpleNamespace(ocr=lambda img: (30, 0, 30))
        task._exit_missions = lambda: events.append('exit_missions')
        task.set_next_run = lambda **kwargs: events.append('set_next_run')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertIn('exit_missions', events)
        self.assertLess(events.index('exit_missions'), events.index('set_next_run'))

    def test_weekly_trifles_skips_wechat_shares_on_mobile_and_runs_local_items(self):
        # 安卓端应跳过外部微信分享，但照常执行摸鱼存次数与抽破碎符咒
        from tasks.WeeklyTrifles.script_task import ScriptTask as WeeklyTriflesTask
        from module.exception import TaskEnd

        called = []
        task = WeeklyTriflesTask.__new__(WeeklyTriflesTask)
        task.device = SimpleNamespace(is_desktop=False)
        task.config = SimpleNamespace(
            weekly_trifles=SimpleNamespace(
                trifles=SimpleNamespace(
                    share_collect=True,
                    share_area_boss=True,
                    share_secret=True,
                    save_touch_fish=True,
                    broken_amulet=10,
                )
            )
        )
        task._share_collect = lambda: called.append('share_collect')
        task._share_area_boss = lambda: called.append('share_area_boss')
        task._share_secret = lambda: called.append('share_secret')
        task._save_touch_fish = lambda: called.append('save_touch_fish')
        task._broken_amulet = lambda n: called.append(f'broken_amulet:{n}')
        task.set_next_run = lambda **kwargs: called.append('set_next_run')
        task.goto_page = lambda page: called.append('goto_main')
        with self.assertRaises(TaskEnd):
            task.run()
        self.assertEqual(called, ['save_touch_fish', 'broken_amulet:10', 'set_next_run', 'goto_main'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
