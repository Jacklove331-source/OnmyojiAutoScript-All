# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from time import sleep

from module.exception import (
    TaskEnd,
    GameStuckError,
    GameNotRunningError,
    RequestHumanTakeover,
    EmulatorNotRunningError,
)
from module.logger import logger
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import (
    page_main,
    page_collection,
    page_area_boss,
    page_secret_zones,
    page_summon,
    page_guild,
    page_touch_fish,
    random_click,
)
from tasks.WeeklyTrifles.assets import WeeklyTriflesAssets


class ScriptTask(GameUi, WeeklyTriflesAssets):

    # 分享流程全部改成「有界等待 + 单步降级」。
    # 背景（2026-11-17 快照 1794908631901）：点 COLLECT_WT_SHARE 之后 67s 没有任何
    # 状态推进（日志 `Wait too long / Waiting for set()`），看门狗的 60s 短定时器
    # 直接把整个 WeeklyTrifles 任务判成 GameStuckError。分享面板/webview 会挡住
    # 页面、二维码浮层也可能根本不出现，所以每一步都必须有时间上限、失败只跳过该项。
    SHARE_STEP_TIMEOUT = 40      # 单步等待上限（秒）：低于看门狗 60s 短定时器
    SHARE_NAV_TIMEOUT = 40       # 进入分享面板（点入口/换页）的上限
    # 这些异常说明设备/任务本身已经不能继续，必须向上抛（降级不能把它们吃掉）
    SHARE_FATAL_ERRORS = (TaskEnd, RequestHumanTakeover, GameStuckError,
                          GameNotRunningError, EmulatorNotRunningError)

    def run(self):
        con = self.config.weekly_trifles.trifles
        if con.share_collect:
            self._run_share_section('图鉴分享', self._share_collect)
        if con.share_area_boss:
            self._run_share_section('地鬼分享', self._share_area_boss)
        if con.share_secret:
            self._run_share_section('秘闻分享', self._share_secret)
        if con.save_touch_fish:
            self._run_share_section('惠比寿摸鱼', self._save_touch_fish)
        if con.broken_amulet:
            self._run_share_section('破碎符咒', lambda: self._broken_amulet(con.broken_amulet))

        self.set_next_run(task='WeeklyTrifles', success=True, finish=True)
        raise TaskEnd('WeeklyTrifles')

    # ------------------------------------------------------------ 降级原语
    def _run_share_section(self, name: str, func) -> bool:
        """跑一个分享项。任何可降级异常只跳过该项（WARNING），不整任务死。"""
        try:
            func()
            return True
        except self.SHARE_FATAL_ERRORS:
            raise
        except Exception as error:  # noqa: BLE001 - 分享面板异常必须降级
            logger.warning(
                f'[Share] {name} 失败，跳过该项继续后续分享: '
                f'{type(error).__name__}: {error}')
            self._share_recover()
            return False

    def _share_recover(self) -> bool:
        """分享降级后回主界面，保证后续分享项还能从庭院重新进入。"""
        # 等待超时后先重置看门狗，避免刚进恢复流程就被残留的 60s 短定时器直接判 GameStuckError
        self.device.stuck_record_clear()
        try:
            # 先尝试从图鉴/式神纪事等子页退出到已知页面（式神纪事左上角方形返回键在 (42, 37)）
            for _ in range(3):
                self.screenshot()
                if self.match_page_once(page_main) or self.match_page_once(page_collection):
                    break
                if (self.appear_then_click(self.I_UI_BACK_RED, interval=0.8)
                        or self.appear_then_click(self.I_UI_BACK_YELLOW, interval=0.8)
                        or self.appear_then_click(self.I_UI_BACK_BLUE, interval=0.8)):
                    sleep(1.0)
                    continue
                self.device.click(42, 37, control_name='SHARE_RECOVER_BACK')
                sleep(1.2)
            self.device.stuck_record_clear()
            self.goto_page(page_main)
            self.device.stuck_record_clear()
            return True
        except self.SHARE_FATAL_ERRORS:
            raise
        except Exception as error:  # noqa: BLE001
            logger.warning(f'[Share] 回主界面失败: {type(error).__name__}: {error}')
            return False

    def _share_wait(self, step: str, done, actions=(), timeout: float = None) -> bool:
        """有界等待：反复截图 → 跑候选动作，直到 done() 命中。

        超时返回 False 并记 WARNING（由调用方降级跳过）。等待期间登记 `LONG_WAIT`
        占位符，退出时重置短看门狗，避免调用方刚做恢复就因超时残留触发 GameStuckError。

        Args:
            step: 步骤名，用于日志
            done: () -> bool，命中即结束
            actions: 可调用列表，命中即执行并 continue
            timeout: 秒；默认 SHARE_STEP_TIMEOUT
        """
        budget = self.SHARE_STEP_TIMEOUT if timeout is None else timeout
        timer = Timer(budget).start()
        self.device.stuck_record_add('LONG_WAIT')
        try:
            while 1:
                self.screenshot()
                if done():
                    return True
                for action in actions:
                    if action():
                        break
                if timer.reached():
                    logger.warning(f'[Share] 步骤「{step}」等待 {budget}s 超时，跳过该项')
                    return False
        finally:
            self.device.stuck_record_remove('LONG_WAIT')
            self.device.stuck_record_clear()

    def click_share(self, wechat) -> bool:
        """
        点击分享
        :param wechat:
        :return:
        """
        # 点击分享
        # self.ui_click(wechat, self.I_WT_QR_CODE)
        if not self._share_wait(
            'open-qrcode',
            done=lambda: bool(self.appear(self.I_WT_QR_CODE)),
            actions=(lambda: self.appear_then_click(wechat, interval=2.5),),
            timeout=self.SHARE_NAV_TIMEOUT,
        ):
            return False
        logger.info('Click share')
        get_timer = Timer(7)
        get_timer.start()
        while 1:
            self.screenshot()
            if self.ui_reward_appear_click():
                logger.info('Get reward')
                return True
            if self.appear_then_click(
                self.I_WT_QR_CODE, self.C_WT_WECHAT, interval=4.8
            ):
                continue
            if get_timer.reached():
                logger.warning('Share timeout. The reward may have been obtained')
                return False

    def _save_touch_fish(self):
        """
        惠比寿的摸鱼行动
        :return:
        """
        logger.hr('Touch fish start')
        self.goto_page(page_guild)
        # 防止入口被折叠（有界：公会窗打不开就跳过摸鱼，不拖死任务）
        if not self._share_wait(
            'open-guild-fold',
            done=lambda: bool(self.appear(self.I_WT_FOLD_WINDOW, interval=1)),
            actions=(lambda: self.appear_then_click(self.I_WT_OPEN_FOLD_WINDOW, interval=1),),
            timeout=self.SHARE_NAV_TIMEOUT,
        ):
            self._share_recover()
            return
        # 保存次数，后续做了也会邮件返还福运御守
        self.goto_page(page_touch_fish)
        self.ui_click_until_smt_disappear(
            random_click(ltrb=(True, False, False, False)), self.I_WT_LAST_SAVE
        )
        sleep(0.5)  # 等待动画
        if self.appear(self.I_WT_SAVE_ALL):
            cu_tickts, _, _ = self.O_WT_LUCKY_TICKETS.ocr(self.device.image)
            logger.info(f"当前的福运御守数量：{cu_tickts}")
            cost_tickts = self.O_WT_SAVE_COST.ocr(self.device.image)
            logger.info(f"全部存储需要花费的福运御守数量：{cost_tickts}")
            not_save_flag = True
            get_timer = Timer(7)
            get_timer.start()
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_WT_HAPPY_GET, interval=1):
                    continue
                if cu_tickts >= cost_tickts and not_save_flag:
                    self.appear_then_click(self.I_WT_SAVE_ALL, interval=1)
                    not_save_flag = False
                    continue
                if self.appear_then_click(self.I_WT_TF_CONFIRM, interval=1):
                    continue
                if self.appear(self.I_WT_TF_SAVE_SUCCESS) and not self.appear(
                    self.I_WT_SAVE_ALL
                ):
                    logger.info('Touch fish save success')
                    break
                if cu_tickts < cost_tickts and not_save_flag:
                    logger.warning('Touch fish tickts not enough, exit!')
                    break
                if get_timer.reached():
                    logger.warning('Touch fish timeout, exit!')
                    break
        logger.hr('Touch fish finished')
        self.goto_page(page_main)

    def _share_collect(self):
        """
        图鉴分享
        :return:
        """
        logger.hr('Share collect')
        self.goto_page(page_collection)
        # 一路进去（有界：面板/浮层不响应就跳过本次图鉴分享，别把任务拖死）
        if not self._share_wait(
            'enter-collect',
            done=lambda: bool(self.appear(self.I_WT_LOGO)),
            actions=(
                lambda: self.appear_then_click(self.I_WT_SHIKIAGMI, interval=1),
                lambda: self.appear_then_click(self.I_WT_SHARE, interval=1),
            ),
            timeout=self.SHARE_NAV_TIMEOUT,
        ):
            self._share_recover()
            return
        # 点击分享
        if not self._share_wait(
            'open-collect-qrcode',
            done=lambda: bool(self.appear(self.I_WT_QR_CODE)),
            actions=(lambda: self.appear_then_click(self.I_WT_COLLECT_WECHAT, interval=4),),
            timeout=self.SHARE_NAV_TIMEOUT,
        ):
            self._share_recover()
            return
        logger.info('Click share')
        get_timer = Timer(3)
        get_timer.start()
        while 1:
            self.screenshot()

            if self.ui_reward_appear_click():
                logger.info('Get reward')
                break

            if self.appear_then_click(
                self.I_WT_QR_CODE, self.C_WT_WECHAT, interval=2.8
            ):
                continue
            if get_timer.reached():
                logger.warning('Share timeout. The reward may have been obtained')
                break
        # 返回
        if not self._share_wait(
            'back-from-collect',
            done=lambda: bool(self.appear(self.I_WT_SHIKIAGMI)),
            actions=(
                lambda: self.appear_then_click(self.I_UI_BACK_RED, interval=1),
                lambda: self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1),
            ),
            timeout=self.SHARE_NAV_TIMEOUT,
        ):
            self._share_recover()

    def _share_area_boss(self):
        """
        地鬼分享
        :return:
        """

        def back_boss():
            return self._share_wait(
                'back-to-boss',
                done=lambda: bool(self.appear(self.I_WT_DAY_BATTLE)
                                  or self.appear(self.I_CHECK_EXPLORATION)),
                actions=(
                    lambda: self.appear_then_click(self.I_UI_BACK_RED, interval=1),
                    lambda: self.appear_then_click(self.I_UI_BACK_BLUE, interval=1),
                    lambda: self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1),
                ),
                timeout=self.SHARE_NAV_TIMEOUT,
            )

        logger.hr('Share area boss')
        self.goto_page(page_area_boss)

        # 一路进去
        obtained = False
        entered = self._share_wait(
            'enter-area-boss-share',
            done=lambda: bool(self.appear(self.I_WT_AB_WECHAT) or self.appear(self.I_WT_NO_DAY)),
            actions=(
                lambda: self.click(self.C_WT_AB_CLICK, interval=2.5),
                lambda: self.appear_then_click(self.I_WT_DAY_BATTLE, interval=2),
                lambda: self.appear_then_click(self.I_WT_SHARE_AB, interval=1),
            ),
            timeout=self.SHARE_NAV_TIMEOUT,
        )
        if not entered:
            self._share_recover()
            return
        if self.appear(self.I_WT_NO_DAY):
            obtained = True
        # 再次检查一次这周有没有领取
        time.sleep(1)
        self.screenshot()
        if not self.appear(self.I_WT_AB_JADE):
            logger.warning('This week has been obtained')
            obtained = True
        if not obtained:
            # 点击分享（超时内部已降级，不阻断后续）
            self.click_share(self.I_WT_AB_WECHAT)
            obtained = True
        if obtained:
            if not back_boss():
                self._share_recover()

    def _share_secret(self):
        """
        秘闻分享
        :return:
        """
        logger.hr('Share secret')
        self.goto_page(page_secret_zones)
        # 一路进去
        valid = False

        def enter_secret():
            return self._share_wait(
                'enter-secret-share',
                done=lambda: bool(self.appear(self.I_WT_SE_WECHAT)),
                actions=(
                    lambda: self.appear_then_click(self.I_WT_ENTER_SE, interval=1),
                    lambda: self.appear_then_click(self.I_WT_SE_SHARE, interval=5),
                ),
                timeout=self.SHARE_NAV_TIMEOUT,
            )

        entered = enter_secret()
        if not entered:
            # 「有排名但没分享」= 这周还没打；其余情况一律降级跳过
            self.screenshot()
            if self.appear(self.I_WT_SE_RANK) and not valid:
                logger.warning('This week has not been obtained')
            self._share_recover()
            return
        self.wait_until_stable(self.I_WT_SE_WECHAT, skip_first_screenshot=True)
        logger.info('Enter secret')
        # 判断是否已经领取
        self.screenshot()
        obtained = False
        if not self.appear(self.I_WT_SE_JADE):
            obtained = True
            logger.warning('This week has been obtained')
        # 点击分享
        if not obtained:
            self.click_share(self.I_WT_SE_WECHAT)
        # 返回
        self.ui_click(self.I_UI_BACK_BLUE, self.I_UI_BACK_YELLOW)
        self.ui_click(self.I_UI_BACK_YELLOW, self.I_CHECK_MAIN)

    def _broken_amulet(self, dest_num: int):
        """

        :param dest_num:
        :return:
        """

        def exit_amulet():
            self.wait_until_appear(self.I_BM_CONFIRM, wait_time=3)
            while 1:
                self.screenshot()
                if not self.appear(self.I_BM_CONFIRM):
                    break
                else:
                    self.appear_then_click(self.I_BM_CONFIRM, interval=1)
            logger.info('Exit broken amulet')

        logger.hr('Broken amulet')
        self.goto_page(page_summon)
        self.screenshot()
        real_num = self.O_BA_AMOUNT_1.ocr(self.device.image)
        if real_num <= 0:
            logger.warning('No broken amulet')
            return
        logger.info(f'Broken amulet: {real_num}')
        count = 0
        self.wait_until_appear(self.I_BM_ENTER)
        logger.info('Enter broken amulet')
        while count < dest_num:
            self.screenshot()
            real_num = self.O_BA_AMOUNT_1.ocr(self.device.image)
            if real_num <= 0:
                exit_amulet()
                return
            if self.appear_then_click(
                self.I_BM_ENTER, interval=1
            ) or self.appear_then_click(self.I_BM_AGAIN, interval=1):
                sleep(0.4)  # 等待动画开始
                timeout_timer = Timer(5).start()
                # 随机点击直到再次召唤出现或者超时
                while not timeout_timer.reached():
                    if not self.click(random_click(), interval=0.8):
                        continue
                    self.screenshot()
                    if self.appear(self.I_BM_AGAIN, interval=0.8):
                        break
                else:
                    logger.warning(
                        f'Wait for again timeout:Count[{count}], Remain[{real_num}]'
                    )
                    exit_amulet()
                    return
            else:
                # 既没有出现召唤破碎符咒也没有出现再次召唤, 则退出(一般不会出现这种情况, 只是为了异常处理)
                exit_amulet()
                return
            x_10, _, _, _ = self.O_BA_TIMES.ocr(self.device.image, '10次')
            x_50, _, _, _ = self.O_BA_TIMES.ocr(self.device.image, '50次')
            self.I_BMT_CHECK.match(self.device.image)
            x_check, y_check, width_check, height_check = self.I_BMT_CHECK.roi_front
            selected_10 = min(abs(x_10 - x_check), abs(x_50 - x_check)) == abs(
                x_10 - x_check
            )
            logger.info(f'Current selected {"10" if selected_10 else "50"} amulet')
            count += 10 if selected_10 else 50
            logger.info(f'Broken amulet:Count[{count}], Remain[{real_num}]')
            # 一次50票不超过限制且当前选择的是10票则切换50票
            if count + 50 < dest_num and selected_10:
                logger.hr('Switch to 50 amulet')
                self.device.click(
                    x=x_50 - width_check // 2,
                    y=y_check + height_check // 2,
                    control_name='Click_50',
                )
                self.device.click_record_clear()
            # 一次50票会超过限制且当前选择的是50票则切换10票
            if count + 50 >= dest_num and not selected_10:
                logger.hr('Switch to 10 amulet')
                self.device.click(
                    x=x_10 - width_check // 2,
                    y=y_check + height_check // 2,
                    control_name='Click_10',
                )
                self.device.click_record_clear()
        # 正常结束且还有票, 则执行一次退出
        exit_amulet()


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()
    # t._share_collect()
    t._share_area_boss()
    # t.click_share(t.I_WT_SE_WECHAT)
