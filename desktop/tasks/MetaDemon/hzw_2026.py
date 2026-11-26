"""2026-11-11 formal-server MetaDemon navigation and one normal challenge.

Templates were recropped using OAS Annotator from oas3 1280x720 frames.
The first release performs at most one ordinary challenge per task invocation.
"""
from time import monotonic

from module.exception import ScriptError, TaskEnd
from module.logger import logger
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.GameUi.page import page_main


class Hzw2026:
    def _hzw_deadline(self, deadline, stage):
        if monotonic() >= deadline:
            raise ScriptError(f'祸至无日：{stage}超时，停止本轮')

    def _hzw_is_trial(self):
        return self.appear(self.I_HZW_TRIAL_TITLE) and self.appear(self.I_HZW_OWN_BOSS_LIST)

    def _hzw_is_courtyard(self):
        return (self.appear(self.I_CHECK_MAIN, threshold=0.82)
                and (self.appear(self.I_HZW_COURTYARD_ENTRY)
                     or self.appear(self.I_HZW_COURTYARD_NPC)))

    def _hzw_foreground(self, rule):
        """NCC also matches dimmed buttons behind modals; reject those targets."""
        x, y, width, height = map(int, rule.roi_front)
        crop = self.device.image[y:y + height, x:x + width]
        template = rule.image
        if crop.shape != template.shape or not crop.size:
            return False
        return float(crop.mean()) >= float(template.mean()) * 0.78

    def _hzw_center_click(self, rule):
        # The long entry title includes decoration outside the active button.
        x, y, width, height = rule.roi_front
        self.device.click(int(x + width / 2), int(y + height / 2), control_name=rule.name)

    def _hzw_overlay(self):
        """Consume recognized overlays before considering background buttons."""
        for rule in (self.I_HZW_FUSION_TITLE, self.I_HZW_RENTAL_TITLE, self.I_HZW_SPECIAL_TITLE):
            if self.appear(rule):
                raise ScriptError(f'祸至无日：当前处于 {rule.name}，停止本轮以保护弹窗')
        if self.appear(self.I_HZW_NO_EYES_TITLE):
            self.appear_then_click(self.I_HZW_NO_EYES_CLOSE, interval=2)
            return True
        if self.appear(self.I_HZW_PAID_PASS_TITLE):
            self.appear_then_click(self.I_HZW_PAID_PASS_CLOSE, interval=2)
            return True
        if (self.appear(self.I_HZW_HUNT_REWARD_FRIEND_TITLE)
                and self.appear(self.I_HZW_HUNT_REWARD_TITLE)):
            if (self._hzw_foreground(self.I_HZW_HUNT_REWARD_FRIEND_TITLE)
                    and self._hzw_foreground(self.I_HZW_HUNT_REWARD_TITLE)
                    and self.appear(self.I_HZW_HUNT_REWARD_CLAIM)
                    and self._hzw_foreground(self.I_HZW_HUNT_REWARD_CLAIM)):
                self.click(self.I_HZW_HUNT_REWARD_CLAIM, interval=2)
            return True
        if self.appear(self.I_HZW_SUPPLY_TITLE):
            if (self.appear(self.I_HZW_SUPPLY_CLAIMED_EYE)
                    and self.appear(self.I_HZW_SUPPLY_CLAIMED_RENTAL)):
                self.appear_then_click(self.I_HZW_SUPPLY_CLOSE, interval=2)
            return True
        if (self.appear(self.I_HZW_REWARD)
                or (self.appear(self.I_HZW_HUNT_REWARD_RESULT)
                    and self._hzw_foreground(self.I_HZW_HUNT_REWARD_RESULT))):
            self.click(self.C_WIN_1, interval=2)
            return True
        if self.appear_then_click(self.I_HZW_GUIDE_CONTINUE, interval=2):
            return True
        if self.appear_then_click(self.I_HZW_STORY_SKIP, interval=2):
            return True
        return False

    def hzw_enter_trial(self):
        self.screenshot()
        if not (self._hzw_is_trial() or self.appear(self.I_HZW_HALL_TITLE)
                or self._hzw_is_courtyard() or self.appear(self.I_HZW_PASS_TITLE)
                or self.appear(self.I_HZW_SHOP_TITLE) or self.appear(self.I_HZW_PAID_PASS_TITLE)
                or self.appear(self.I_HZW_NO_EYES_TITLE)
                or self.appear(self.I_HZW_STORY_SKIP) or self.appear(self.I_HZW_SUPPLY_TITLE)):
            self.goto_page(page_main)
        # Raise a task error before the device's 60-second stuck watchdog.
        deadline = monotonic() + 45
        transition_until = 0
        courtyard_clicks = hall_clicks = 0
        while True:
            self._hzw_deadline(deadline, '进入活动')
            self.screenshot()
            if monotonic() < transition_until:
                continue
            if self._hzw_overlay():
                transition_until = monotonic() + 2
                continue
            if self._hzw_is_trial():
                return
            if self.appear(self.I_HZW_PASS_TITLE) or self.appear(self.I_HZW_SHOP_TITLE):
                if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=3):
                    transition_until = monotonic() + 4
                continue
            if self.appear(self.I_HZW_HALL_TITLE):
                if hall_clicks >= 3:
                    raise ScriptError('祸至无日：大厅入口重复三次无进展')
                for entry in (self.I_HZW_TRIAL_ENTRY, self.I_HZW_TRIAL_ENTRY_DARK):
                    if (self.appear(entry, interval=4)
                            and self._hzw_foreground(entry)):
                        self._hzw_center_click(entry)
                        hall_clicks += 1
                        transition_until = monotonic() + 5
                        break
                continue
            if self._hzw_is_courtyard():
                if courtyard_clicks >= 3:
                    raise ScriptError('祸至无日：庭院入口重复三次无进展')
                target = (self.I_HZW_COURTYARD_ENTRY if self.appear(self.I_HZW_COURTYARD_ENTRY)
                          else self.I_HZW_COURTYARD_NPC)
                self._hzw_center_click(target)
                courtyard_clicks += 1
                transition_until = monotonic() + 6
                continue

    def _hzw_exhaustion(self):
        current, _, total = self.O_MD_EXHAUSTION.ocr(self.device.image)
        if int(total) != 100 or not 0 <= int(current) <= 100:
            raise ScriptError('祸至无日：疲劳读数不完整，停止挑战')
        return int(current)

    def hzw_normal_challenge(self):
        """One summon at most, one challenge at most; no purchases or fusion."""
        deadline = monotonic() + 30
        summoned = False
        damage_switched = False
        damage_settle_until = 0
        while True:
            self._hzw_deadline(deadline, '准备普通挑战')
            self.screenshot()
            if self.appear(self.I_HZW_NO_EYES_TITLE):
                self.appear_then_click(self.I_HZW_NO_EYES_CLOSE, interval=2)
                logger.info('祸至无日：没有裂变之眼，本轮正常结束，不购买道具')
                return None
            if self._hzw_overlay():
                continue
            if not self._hzw_is_trial():
                continue
            if monotonic() < damage_settle_until:
                continue
            if self._hzw_exhaustion() >= 90:
                logger.info('祸至无日：接近疲劳上限，本轮结束')
                return None
            if (self.appear(self.I_HZW_OWN_EMPTY)
                    and self.appear(self.I_HZW_SUMMON_BUTTON)
                    and self._hzw_foreground(self.I_HZW_SUMMON_BUTTON)):
                if summoned:
                    # A failed/no-ticket summon must not turn into repeated clicks.
                    continue
                self.click(self.I_HZW_SUMMON_BUTTON)
                summoned = True
                continue
            if (not self.appear(self.I_HZW_OWN_EMPTY)
                    and self.appear(self.I_HZW_OWN_CONVENE)
                    and self.appear(self.I_HZW_CHALLENGE_BUTTON)
                    and self._hzw_foreground(self.I_HZW_CHALLENGE_BUTTON)):
                if not (self.appear(self.I_HZW_DAMAGE_NORMAL)
                        and self._hzw_foreground(self.I_HZW_DAMAGE_NORMAL)):
                    if damage_switched:
                        raise ScriptError('祸至无日：切回普通伤害未生效，不点击挑战')
                    if not (self.appear(self.I_HZW_DAMAGE_BOOSTED)
                            and self._hzw_foreground(self.I_HZW_DAMAGE_BOOSTED)):
                        raise ScriptError('祸至无日：未确认普通伤害状态，不点击挑战')
                    self.click(self.I_HZW_DAMAGE_BOOSTED)
                    damage_switched = True
                    damage_settle_until = monotonic() + 2
                    logger.info('祸至无日：关闭2.5倍伤害，等待新画面确认普通伤害')
                    continue
                self.click(self.I_HZW_CHALLENGE_BUTTON)
                break

        config = GeneralBattleConfig()
        config.preset_enable = False
        config.lock_team_enable = False
        config.green_enable = False
        config.random_click_swipt_enable = False
        self._hzw_2026_active = True
        try:
            return self.run_general_battle(config)
        finally:
            self._hzw_2026_active = False

    def hzw_battle_wait(self):
        deadline = monotonic() + 240
        entry_deadline = monotonic() + 20
        entered = False
        won = False
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        while True:
            self._hzw_deadline(deadline, '战斗及返回')
            self.screenshot()
            if self.appear(self.I_HZW_WIN) or self.appear(self.I_WIN):
                won = True
                self.click(self.C_WIN_1, interval=1.5)
                continue
            if self.appear_then_click(self.I_MD_BATTLE_FAILURE, interval=2):
                return False
            if self._hzw_overlay():
                continue
            if self.is_in_real_battle(False):
                entered = True
            if self._hzw_is_trial():
                if entered or won:
                    return won
                # A screenshot can still show the challenge page while the tap
                # is being processed. Do not back out or spend another ticket.
                self._hzw_deadline(entry_deadline, '确认进入战斗')

    def hzw_return_main(self):
        deadline = monotonic() + 35
        transition_until = 0
        while True:
            self._hzw_deadline(deadline, '返回庭院')
            self.screenshot()
            if monotonic() < transition_until:
                continue
            if self._hzw_overlay():
                continue
            if self._hzw_is_courtyard():
                return
            if (self._hzw_is_trial() or self.appear(self.I_HZW_HALL_TITLE)
                    or self.appear(self.I_HZW_PASS_TITLE) or self.appear(self.I_HZW_SHOP_TITLE)):
                if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=3):
                    transition_until = monotonic() + 4

    def hzw_skip(self, reason):
        """跳过本次祸至无日：记录原因 + 尽力回庭院 + 推迟下一次运行，不抛错。

        2026-11 复盘（log/error/1794905377780、1794815054533 等「祸至无日：进入活动超时」）：
        入口判定/超时以 ScriptError 抛出会打断整条任务链，一个玩法进不去把后面的任务都拖掉；
        这里降级成「本次跳过 + success=False 推迟」，让调度器按失败间隔重排。
        """
        logger.warning(f'祸至无日：跳过本次挑战（{reason}）')
        try:
            self.hzw_return_main()
        except ScriptError as error:
            logger.warning(f'祸至无日：跳过后返回庭院失败（不影响其它任务，已推迟下次运行）：{error}')
        self.set_next_run(task='MetaDemon', finish=True, success=False)
        raise TaskEnd

    def run_hzw_2026(self):
        logger.info('祸至无日正式服适配：每次任务最多一局普通挑战')
        try:
            self.hzw_enter_trial()
        except ScriptError as error:
            return self.hzw_skip(error)
        try:
            success = self.hzw_normal_challenge()
        except ScriptError as error:
            return self.hzw_skip(error)
        try:
            self.hzw_return_main()
        except ScriptError as error:
            logger.warning(f'祸至无日：返回庭院超时（挑战已完成，继续推迟下次运行）：{error}')
        self.set_next_run(task='MetaDemon', success=success is not False)
        raise TaskEnd
