# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from cached_property import cached_property
from time import sleep

from module.atom.click import RuleClick
from module.atom.ocr import RuleOcr
from module.exception import GamePageUnknownError, GameStuckError, GameTooManyClickError, TaskEnd
from module.logger import logger
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_team, page_shikigami_records
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.GeneralRoom.general_room import GeneralRoom
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.GoldYoukai.assets import GoldYoukaiAssets
from tasks.GoldYoukai.config import GoldYoukaiConfig


class ScriptTask(GameUi, GeneralBattle, GeneralRoom, GeneralInvite, SwitchSoul, GoldYoukaiAssets):
    _C_MOBILE_GOLD_CONTINUE = RuleClick(
        roi_front=(628, 680, 24, 18), roi_back=(628, 680, 24, 18), name="mobile_gold_continue",
    )

    def run(self):
        # 切换御魂
        if self.config.gold_youkai.switch_soul.enable:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(self.config.gold_youkai.switch_soul.switch_group_team)

        if self.config.gold_youkai.switch_soul.enable_switch_by_name:
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(self.config.gold_youkai.switch_soul.group_name,
                                         self.config.gold_youkai.switch_soul.team_name)

        # 开启加成
        con = self.config.gold_youkai.gold_youkai
        if con.buff_gold_50_click or con.buff_gold_100_click:
            self.goto_page(page_main)
            self.open_buff()
            if con.buff_gold_50_click:
                self.gold_50()
            if con.buff_gold_100_click:
                self.gold_100()
            self.close_buff()
        count = 0
        while count < 2:
            try:
                if not self._enter_team_page():
                    return
                if not self.check_zones('金币妖怪'):
                    raise GamePageUnknownError('Could not verify the Gold Youkai zone')
            except GamePageUnknownError:
                if not self.device.is_desktop:
                    try:
                        self._return_and_disable_buffs(con)
                    except (GamePageUnknownError, GameStuckError, GameTooManyClickError) as error:
                        logger.warning(f'[GoldYoukai] Could not clean up after failed entry: {error}')
                raise
            # 开始
            if not self.create_room():
                self.gold_exit(con)
            self.ensure_public()
            self.create_ensure()
            # 进入到了房间里面
            wait_timer = Timer(50)
            wait_timer.start()
            while 1:
                self.screenshot()

                if not self.is_in_room():
                    continue
                if wait_timer.reached():
                    # 超过时间依然挑战
                    logger.warning('Wait for too long and start the challenge')
                    self.click_fire()
                    count += 1
                    self.run_general_battle()
                    break
                if not self.appear(self.I_ADD_5_1):
                    # 有人进来了，可以进行挑战
                    logger.info('There is someone in the room and start the challenge')
                    self.click_fire()
                    count += 1
                    self.run_general_battle()
                    break
        # 退出 (要么是在组队界面要么是在庭院)
        self.gold_exit(con)


    # 「大人N分钟未使用加成，已自动关闭，是否再次打开？」这类弹窗会整屏拦截点击，
    # 不关掉的话点底栏「组队」根本进不去，还会和登录兜底来回点成死循环（2026-10-28 实测）。
    _POPUP_OCR = RuleOcr(roi=(0, 0, 1280, 720), area=(0, 0, 1280, 720),
                         mode='Full', method='Default', keyword='', name='gold_popup')

    def _dismiss_popup_if_any(self) -> bool:
        """若出现加成类确认弹窗，点掉它。返回是否点过。"""
        try:
            items = self._POPUP_OCR.detect_and_ocr(self.device.image) or []
        except Exception:  # noqa: BLE001
            return False
        texts = {str(getattr(item, 'ocr_text', '') or '').strip(): item for item in items}
        hit = next((item for text, item in texts.items()
                    if '是否再次打开' in text or '退出游戏' in text), None)
        if hit is None:
            return False
        cancel = next((item for text, item in texts.items() if text == '取消'), None)
        target = cancel or hit
        box = getattr(target, 'box', None)
        if box is None:
            return False
        x = (int(box[0][0]) + int(box[2][0])) // 2
        y = (int(box[0][1]) + int(box[2][1])) // 2
        logger.warning(f'关闭加成提示弹窗 ({x},{y})')
        self.device.click(x, y, control_check=False)
        sleep(1.2)
        return True

    _NAV_OCR = RuleOcr(roi=(0, 600, 1280, 120), area=(0, 600, 1280, 120),
                       mode='Full', method='Default', keyword='', name='nav_bar')

    def _click_team_entry_by_ocr(self) -> bool:
        """在底部导航条里 OCR 找「组队」并点击；成功进入组队页返回 True。"""
        try:
            self.screenshot()
            items = self._NAV_OCR.detect_and_ocr(self.device.image) or []
        except InterruptedError:
            raise
        except Exception as error:  # noqa: BLE001
            logger.warning(f'[GoldYoukai] 底栏 OCR 失败：{type(error).__name__}')
            return False
        target = None
        for item in items:
            text_value = str(getattr(item, 'ocr_text', '') or '').strip()
            if '组队' not in text_value:
                continue
            box = getattr(item, 'box', None)
            if box is not None:
                target = box
                break
        if target is None:
            logger.warning('[GoldYoukai] 底栏没找到「组队」文字')
            return False
        # detect_and_ocr 返回的是 **ROI 相对坐标**，必须加回 ROI 原点
        # （踩过：ROI=(0,600,...) 时 y 只有 57，直接点会点到屏幕顶部）
        roi_x, roi_y = self._NAV_OCR.roi[0], self._NAV_OCR.roi[1]
        x = roi_x + (int(target[0][0]) + int(target[2][0])) // 2
        y = roi_y + (int(target[0][1]) + int(target[2][1])) // 2 - 12   # 文字上方是图标
        logger.info(f'[GoldYoukai] OCR 点击底栏组队 ({x},{y})')
        self.device.click(x, y, control_check=False)
        sleep(2.0)
        self.screenshot()
        if self.appear(self.I_CHECK_TEAM):
            logger.info('[GoldYoukai] 已进入组队页（OCR 点入口）')
            return True
        return False

    def _enter_team_page(self) -> bool:
        """进入组队页；进不去就放弃本任务，而不是让调度器崩进「重启循环」。

        2026-10-28 实测：本账号主界面是「町中」，庭院按钮不在屏上，`page_main->page_team`
        会点到相似度为 0.7+ 的底部导航「组队」图标，页面没切过去 → I_CHECK_MAIN 失效 →
        登录兜底反复点 login_scroll_close_area → 与 PAGE_MAIN_GOTO_TEAM 来回点 →
        GameTooManyClickError → task_call('Restart') → 再跑本任务 → 无限重启。
        入口模板已收紧（见 dev_tools/page_entry_guard_check.py），这里再兜一层：
        导航失败计入失败并跳过今天，不让单任务拖垮整个调度器。
        """
        # 先确保回到主界面：打完一场后可能停在式神录/结算页，底栏根本不在屏上，
        # 直接找「组队」会连续三次失败（实测 19:21 第二轮就是这样）。
        try:
            self.goto_page(page_main)
        except InterruptedError:
            raise
        except Exception as error:  # noqa: BLE001
            logger.warning(f'[GoldYoukai] 回主界面失败：{type(error).__name__}: {error}')
        self._dismiss_popup_if_any()
        # 优先 OCR 找底栏「组队」文字再点：底栏图标会随高亮/滚动/遮挡变化，
        # 模板匹配会间歇失效（实测同一入口 20 分钟内 True→False 反复），
        # 而文字是稳定的。点完用页面标志 I_CHECK_TEAM 确认到达。
        for attempt in range(3):
            if self._click_team_entry_by_ocr():
                return True
            if self.appear(self.I_CHECK_TEAM):
                return True
            logger.warning(f'[GoldYoukai] OCR 点组队第 {attempt + 1} 次未进入，重试')
            self._dismiss_popup_if_any()
            sleep(1.5)
        # 兜底：模板点一次，再退回导航器
        try:
            self.ui_click(self.I_MAIN_GOTO_TEAM, stop=self.I_CHECK_TEAM, interval=1.5)
            if self.appear(self.I_CHECK_TEAM):
                logger.info('[GoldYoukai] 已进入组队页（模板点入口）')
                return True
        except InterruptedError:
            raise
        except Exception as error:  # noqa: BLE001
            logger.warning(f'[GoldYoukai] 模板点组队入口失败：{type(error).__name__}: {error}')
        try:
            self.goto_page(page_team)
            return True
        except InterruptedError:
            raise
        except Exception as error:  # noqa: BLE001 - 任何导航/点击异常都按「进不去组队页」处理
            logger.warning(
                f'[GoldYoukai] 无法进入组队页（{type(error).__name__}: {error}），'
                '跳过本次并推迟一天。若主界面是「町中」，需要先适配组队入口。')
            self.config.task_delay(task='GoldYoukai', success=False, server=True)
            if not self.device.is_desktop:
                raise GamePageUnknownError('Could not open the Gold Youkai team page') from error
            raise TaskEnd('GoldYoukai') from None

    def battle_wait(self, random_click_swipt_enable: bool) -> bool:
        # 重写
        self.device.stuck_record_add('BATTLE_STATUS_S')
        self.device.click_record_clear()
        # 战斗过程 随机点击和滑动 防封
        logger.info("Start battle process")
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_PREPARE_HIGHLIGHT, interval=1):
                logger.info('click prepare')
            if self.appear(self.I_DE_WIN):
                logger.info('Win battle')
                self.ui_click_until_disappear(self.I_DE_WIN)
                return True
            if self.appear(self.I_GOLD_WIN):
                logger.info('Win battle')
                if self.device.is_desktop:
                    self.ui_click_until_disappear(self.I_GOLD_WIN)
                else:
                    self.ui_click_until_smt_disappear(self._C_MOBILE_GOLD_CONTINUE, self.I_GOLD_WIN)
                return True

            if self.appear(self.I_FALSE):
                logger.warning('False battle')
                self.ui_click_until_disappear(self.I_FALSE)
                return False

    def _return_and_disable_buffs(self, con):
        self.goto_page(page_main)
        if con.buff_gold_50_click or con.buff_gold_100_click:
            self.open_buff()
            if con.buff_gold_50_click:
                self.gold_50(False)
            if con.buff_gold_100_click:
                self.gold_100(False)
            self.close_buff()

    def gold_exit(self, con):
        self._return_and_disable_buffs(con)
        self.set_next_run(task='GoldYoukai', success=True, finish=False)
        raise TaskEnd('GoldYoukai')


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()
