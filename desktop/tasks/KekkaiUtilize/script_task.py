# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
import time
from cached_property import cached_property
from datetime import timedelta, datetime

from module.base.timer import Timer
from module.atom.image_grid import ImageGrid
from module.logger import logger
from module.exception import TaskEnd

from tasks.GameUi.game_ui import GameUi
from tasks.Utils.config_enum import ShikigamiClass
from tasks.KekkaiUtilize.assets import KekkaiUtilizeAssets
from tasks.KekkaiUtilize.config import UtilizeRule, SelectFriendList
from tasks.KekkaiUtilize.utils import CardClass, target_to_card_class
from tasks.Component.ReplaceShikigami.replace_shikigami import ReplaceShikigami
from tasks.GameUi.page import page_main, page_guild
from module.base.utils import point2str
import random

# 结界「经验箱 → 提取」防连点：2026-11-23 11:48（oas3）实测 BOX_EXP 停在 59374/160000 不动，
# I_EXP_EXTRACT 每轮都命中 → 连点 10 次 → GameTooManyClickError。设备侧「同一按钮连点 10 次」就熔断。
# 判据不是「相邻两帧逐字相同」（OCR 抖 ±1 就会把计数清零），而是**自上次见到真实进展以来的轮数**：
# 只有 cur 明显下降（提取生效）或明显上升（经验箱被重新灌满）才算有变化，抖动/不动都计数累加。
EXP_EXTRACT_CLICK_LIMIT = 5
EXP_PROGRESS_MIN_DELTA = 50   # cur 至少变动这么多才算「真的动了」，低于此值一律视为 OCR 抖动


def exp_extract_stuck(reading, baseline, stuck_checks,
                      limit: int = EXP_EXTRACT_CLICK_LIMIT,
                      min_delta: int = EXP_PROGRESS_MIN_DELTA):
    """结界经验箱「提取没进展」判定：返回 (baseline, stuck_checks, stuck)。

    纯函数，`dev_tools/kekkai_exp_stuck_check.py` 直接测。计数单位是「检查轮数」。
    - reading 是 (cur, remain, total)，只用 cur；None/空串/非数字 = 本轮没读到；
    - baseline 是上一次「真实进展点」的 cur，首个有效读数即基线；
    - cur <= baseline - min_delta → 提取生效，基线跟随、计数清零；
    - cur >= baseline + min_delta → 经验箱被重新灌满，换基线、计数清零；
    - 其余情况（抖动、微小变化、读不到）→ 计数 +1，基线不动；
    - stuck_checks >= limit → stuck=True，调用方应放弃提取（避免把按钮点成风暴）。
    """
    # 本轮读数不可用：按「没进展」计数，但不丢基线（这是保护最该生效的场景）
    try:
        cur = int(reading[0]) if reading is not None else None
    except (TypeError, ValueError, IndexError):
        cur = None
    if baseline is None:
        # 还没建立基线：这一轮只建基线，不计数
        return (cur if cur is not None else None), 0, False
    if cur is None:
        stuck_checks += 1
        return baseline, stuck_checks, stuck_checks >= limit
    if cur <= baseline - min_delta or cur >= baseline + min_delta:
        return cur, 0, False
    stuck_checks += 1
    return baseline, stuck_checks, stuck_checks >= limit

""" 结界蹭卡 """


class ScriptTask(GameUi, ReplaceShikigami, KekkaiUtilizeAssets):
    last_best_index = 99
    utilize_add_count = 0
    ap_max_num = 0
    jade_max_num = 0
    first_utilize = True

    def run(self):
        con = self.config.kekkai_utilize.utilize_config
        self.goto_page(page_guild)

        # 进入寮结界
        self.goto_realm()
        # 育成界面去蹭卡
        if con.utilize_enable:
            self.check_utilize_add()

        # 查看育成满级
        self.check_max_lv(con.shikigami_class)
        # 检查蹭卡收获
        self.check_utilize_harvest()
        # 收体力盒子或者是经验盒子
        self.check_box_ap_or_exp(con.box_ap_enable, con.box_exp_enable, con.box_exp_waste)

        # 收取寮资金和体力
        self.recive_guild_ap_or_assets(con.harvest_guild_max_times)
        if not con.utilize_enable:
            self.set_next_run(task='KekkaiUtilize', finish=True, success=True)
        self.record_calendar_completion()
        raise TaskEnd

    def recive_guild_ap_or_assets(self, max_tries: int = 3):
        for i in range(1, max_tries+1):
            self.goto_page(page_guild)
            # 在寮的主界面 检查是否有收取体力或者是收取寮资金
            if self.check_guild_ap_or_assets():
                logger.warning(f'第[{i}]次检查寮收获,成功')
                self.goto_page(page_main)
                break
            else:
                logger.warning(f'第[{i}]次检查寮收获寮收获,失败')
            self.goto_page(page_main)

    def check_utilize_add(self):
        con = self.config.kekkai_utilize.utilize_config
        while 1:
            self.utilize_add_count += 1
            if self.utilize_add_count >= 5:
                logger.warning('没有合适可以蹭的卡, 5分钟后再次执行蹭卡')
                self.push_notify(content=f"没有合适可以蹭的卡, 5分钟后再次执行蹭卡")
                self.set_next_run(task='KekkaiUtilize', target=datetime.now() + timedelta(minutes=5))
                return

            # 无论收不收到菜，都会进入看看至少看一眼时间还剩多少
            time.sleep(0.5)
            # 进入育成界面
            self.realm_goto_grown()
            self.screenshot()

            if not self.appear(self.I_UTILIZE_ADD):
                remaining_time = self.O_UTILIZE_RES_TIME.ocr(self.device.image)
                if not isinstance(remaining_time, timedelta):
                    logger.warning('Ocr remaining time error')
                logger.info(f'Utilize remaining time: {remaining_time}')
                # 已经蹭上卡了，设置下次蹭卡时间  # 减少30秒
                # remaining_time = remaining_time - timedelta(seconds=30)
                next_time = datetime.now() + remaining_time
                min_interval = con.min_run_interval
                if min_interval and min_interval.total_seconds() > 0:
                    next_time = max(next_time, datetime.now() + min_interval)
                self.set_next_run(task='KekkaiUtilize', target=next_time)
                return
            if not self.grown_goto_utilize():
                logger.info('Utilize failed, exit')
            # 开始执行寄养
            if self.run_utilize(con.select_friend_list, con.shikigami_class, con.shikigami_order):
                # 退出寮结界
                self.back_guild()
                # 进入寮结界
                self.goto_realm()
            else:
                self.back_realm()

    def check_max_lv(self, shikigami_class: ShikigamiClass = ShikigamiClass.N):
        """Replace max-level shikigami, fill empty slots and return to the realm."""
        self.realm_goto_grown()
        if self.in_shikigami_growth(screenshot=True):
            has_max_level = self.appear(self.I_RS_LEVEL_MAX)
            if has_max_level:
                logger.info('Replacing max-level shikigami')
                self.unset_shikigami_max_lv()
            if has_max_level or self.detect_no_shikigami():
                if self.switch_shikigami_class(shikigami_class):
                    self.set_shikigami(shikigami_order=7, stop_image=self.I_RS_NO_ADD)

        timeout = Timer(12).start()
        while not timeout.reached():
            self.screenshot()
            if self.appear(self.I_REALM_SHIN, threshold=0.75) and self.appear_multi_scale(self.I_SHI_GROWN):
                return
            if self.in_shikigami_growth():
                self.appear_then_click(self.I_UI_BACK_BLUE, interval=2.5)
            elif self.appear(self.I_GUILD_INFO):
                self.goto_realm()
                return
        logger.warning('Realm page was lost after shikigami growth, reopening it from the guild')
        self.goto_page(page_guild)
        self.goto_realm()

    def check_guild_ap_or_assets(self, ap_enable: bool = True, assets_enable: bool = True) -> bool:
        """
        在寮的主界面 检查是否有收取体力或者是收取寮资金
        如果有就顺带收取
        :return:
        """
        timer_check = Timer(2)
        timer_check.start()
        click_ap = False
        while 1:
            self.screenshot()

            # 获得奖励
            if self.ui_reward_appear_click():
                timer_check.reset()
                continue

            if timer_check.reached():
                return False

            if click_ap and not self.appear(self.I_GUILD_AP) and not self.appear(self.I_UI_REWARD):
                return True

            # 关闭展开的寮活动横幅
            if self.appear_then_click(self.I_GUILD_EXPAND):
                timer_check.reset()
                continue

            # 资金收取确认
            if self.appear_then_click(self.I_GUILD_ASSETS_RECEIVE, interval=1):
                time.sleep(1)
                timer_check.reset()
                continue

            # 收资金
            if self.appear_then_click(self.I_GUILD_ASSETS, interval=1.5, threshold=0.6):
                timer_check.reset()
                continue

            # 收体力
            if self.appear_then_click(self.I_GUILD_AP, interval=1):
                # 等待1秒，看到获得奖励
                time.sleep(1)
                logger.info('appear_click guild_ap success')
                if self.ui_reward_appear_click(True):
                    logger.info('appear_click reward success')
                    click_ap = True
                    timer_check.reset()
                continue

    def goto_realm(self):
        """
        从寮的主界面进入寮结界
        :return:
        """
        timer_fallback = Timer(15)
        timer_fallback.start()
        fallback = 0
        while 1:
            self.screenshot()
            # 已在寮结界。通知弹窗的半透明遮罩会压低所有模板分数（实测 0.90 阈值下
            # 结界页只有 0.7745），阈值放宽到 0.75；寮页同区域实测 0.446，不会误判
            if self.appear(self.I_REALM_SHIN, threshold=0.75):
                break
            if self.appear_multi_scale(self.I_SHI_DEFENSE):
                break
            # 收起展开的寮活动横幅：横幅展开会让底部导航按钮模板失配
            # （2026-10 枫下留念活动期间实测 I_GUILD_REALM 从 >0.8 掉到 0.67）
            if self.appear_then_click(self.I_GUILD_EXPAND, interval=1):
                timer_fallback.reset()
                continue
            if self.appear_then_click(self.I_PLANT_TREE_CLOSE):
                timer_fallback.reset()
                continue
            if self.appear_then_click(self.I_GUILD_REALM, interval=1):
                timer_fallback.reset()
                continue
            # 无进展兜底：防止 60 秒空转触发 GameStuckError
            if not timer_fallback.reached():
                continue
            fallback += 1
            if self.appear(self.I_GUILD_INFO):
                # 确认在寮页但入口按钮失配（活动装饰），按固定坐标直点“结界”入口
                logger.warning(f'goto_realm fallback {fallback}: click realm entry by fixed coordinate')
                self.device.click(1098, 650, control_name='KU_GUILD_REALM_FIXED')
            else:
                # 点空白处关闭可能存在的通知弹窗（如抽签结果横幅）
                logger.warning(f'goto_realm fallback {fallback}: dismiss popup')
                self.device.click(640, 130)
            timer_fallback.reset()
            if fallback >= 3:
                logger.warning('goto_realm stuck, re-navigate from page_main')
                self.goto_page(page_main)
                self.goto_page(page_guild)
                fallback = 0
                timer_fallback.reset()

    def check_box_ap_or_exp(self, ap_enable: bool = True, exp_enable: bool = True, exp_waste: bool = True) -> bool:
        """
        顺路检查盒子
        :param ap_enable:
        :param exp_enable:
        :return:
        """

        # 退出到寮结界
        def _exit_to_realm():
            # 右上方关闭红色
            while 1:
                self.screenshot()
                if self.appear(self.I_REALM_SHIN):
                    break
                if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                    continue

        # 先是体力盒子
        def _check_ap_box(appear: bool = False):
            if not appear:
                return False
            # 点击盒子
            timer_ap = Timer(6)
            timer_ap.start()
            while 1:
                self.screenshot()

                if self.appear(self.I_UI_REWARD):
                    while 1:
                        self.screenshot()
                        if not self.appear(self.I_UI_REWARD):
                            break
                        if self.appear_then_click(self.I_UI_REWARD, self.C_UI_REWARD, interval=1, threshold=0.6):
                            continue
                    logger.info('Reward box')
                    break

                if self.appear_then_click(self.I_BOX_AP, interval=1):
                    continue
                if self.appear_then_click(self.I_AP_EXTRACT, interval=2):
                    continue
                if timer_ap.reached():
                    logger.warning('Extract ap box timeout')
                    break
            logger.info('Extract AP box finished')
            _exit_to_realm()

        # 经验盒子
        def _check_exp_box(appear: bool = False):
            if not appear:
                logger.info('No exp box')
                return False

            time_exp = Timer(12)
            time_exp.start()
            # 2026-11-23 11:48（oas3）实测：BOX_EXP 一直停在 59374/160000，
            # I_EXP_EXTRACT 却每轮都命中 → 连点 10 次 → GameTooManyClickError，结界任务报错。
            # 记「自上次见到真实进展以来的轮数」，超限就放弃提取，别把点击打成风暴。
            exp_baseline = None
            stuck_reading = 0
            self._exp_extract_gave_up = False
            while not time_exp.reached():
                self.screenshot()
                # 如果出现结界皮肤， 表示收取好了
                if self.appear(self.I_REALM_SHIN) and not self.appear(self.I_BOX_EXP, threshold=0.6):
                    break
                # 如果出现收取确认，表明进入到了有满级的
                if self.appear(self.I_UI_CONFIRM):
                    self.screenshot()
                    if not self.appear(self.I_UI_CANCEL):
                        logger.info('No cancel button')
                        continue
                    if exp_waste:
                        check_button = self.I_UI_CONFIRM
                    else:
                        check_button = self.I_UI_CANCEL
                    while not time_exp.reached():
                        self.screenshot()
                        if not self.appear(check_button):
                            break
                        if self.appear_then_click(check_button, interval=1):
                            continue
                    break

                if self.appear(self.I_EXP_EXTRACT):
                    # 如果达到今日领取的最大，就不领取了
                    cur, res, totol = self.O_BOX_EXP.ocr(self.device.image)
                    if cur == res == totol == 0:
                        continue
                    if cur == totol and cur + res == totol:
                        logger.info('Exp box reach max do not collect')
                        break
                    exp_baseline, stuck_reading, stuck = exp_extract_stuck(
                        (cur, res, totol), exp_baseline, stuck_reading)
                    if stuck:
                        # 与正常「收满/超时」退出区分开：留标记给上层与日志排查用
                        self._exp_extract_gave_up = True
                        logger.warning(f'Exp box baseline {exp_baseline} unchanged for '
                                       f'{stuck_reading} checks, give up extracting '
                                       f'(avoid GameTooManyClickError)')
                        break
                if self.appear_then_click(self.I_BOX_EXP, threshold=0.6, interval=1):
                    continue
                if self.appear_then_click(self.I_EXP_EXTRACT, interval=1):
                    continue
            if time_exp.reached():
                logger.warning('Extract exp box timeout')
            if getattr(self, '_exp_extract_gave_up', False):
                # 标记在这里被消费并清掉：本次没提取成功，经验箱还留着，下一次运行再来
                self._exp_extract_gave_up = False
                logger.info('Exp box extraction gave up this run '
                            '(button matched but box exp never moved); '
                            'will retry on the next run')
            _exit_to_realm()

        self.screenshot()
        box_ap = self.appear(self.I_BOX_AP)
        box_exp = self.appear(self.I_BOX_EXP, threshold=0.6) or self.appear(self.I_BOX_EXP_MAX, threshold=0.6)
        if ap_enable:
            _check_ap_box(box_ap)
        if exp_enable:
            _check_exp_box(box_exp)

    def check_utilize_harvest(self) -> bool:
        """
        在寮结界界面检查是否有收获
        :return: 如果没有返回False, 如果有就收菜返回True
        """
        self.screenshot()
        appear = self.appear(self.I_UTILIZE_EXP)
        if not appear:
            logger.info('No utilize harvest')
            return False

        # 收获
        self.ui_get_reward(self.I_UTILIZE_EXP)
        return True

    def realm_goto_grown(self):
        """
        进入式神育成界面
        :return:
        """
        while 1:
            self.screenshot()

            if self.in_shikigami_growth():
                break

            if self.appear_then_click_multi_scale(self.I_SHI_GROWN, interval=1):
                continue
        logger.info('Enter shikigami grown')

    def grown_goto_utilize(self):
        """
        从式神育成界面到 蹭卡界面
        :return:
        """
        self.screenshot()
        if not self.appear(self.I_UTILIZE_ADD):
            logger.warning('No utilize add')
            return False

        while 1:
            self.screenshot()

            if self.appear(self.I_U_ENTER_REALM):
                break
            if self.appear_then_click(self.I_UTILIZE_ADD, interval=2):
                continue
        logger.info('Enter utilize')
        return True

    def switch_friend_list(self, friend: SelectFriendList = SelectFriendList.SAME_SERVER,
                           timeout: int = 30) -> bool:
        """
        切换不同的服务区
        :param friend: 目标列表；传 BOTH 时按本区处理
        :param timeout: 等待页签高亮的最长时间，超时返回 False，避免死循环
        :return: 是否切换成功
        """
        logger.info('Switch friend list to %s', friend)
        if friend == SelectFriendList.BOTH:
            friend = SelectFriendList.SAME_SERVER
        if friend == SelectFriendList.SAME_SERVER:
            check_image = self.I_UTILIZE_FRIEND_GROUP
        else:
            check_image = self.I_UTILIZE_ZONES_GROUP

        timer_click = Timer(1)
        timer_click.start()
        timer_timeout = Timer(timeout)
        timer_timeout.start()
        while 1:
            self.screenshot()
            if self.appear(check_image):
                break
            if timer_timeout.reached():
                logger.warning(f'Switch friend list to {friend} timeout after {timeout}s')
                return False
            if timer_click.reached():
                timer_click.reset()
                x, y = check_image.coord()
                self.device.click(x=x, y=y, control_name=check_image.name)
        if friend == SelectFriendList.DIFFERENT_SERVER:
            time.sleep(1)
        time.sleep(0.5)
        return True

    @cached_property
    def order_targets(self) -> ImageGrid:
        rule = self.config.kekkai_utilize.utilize_config.utilize_rule
        if rule == UtilizeRule.DEFAULT:
            return ImageGrid([self.I_U_FISH_6, self.I_U_TAIKO_6, self.I_U_FISH_5, self.I_U_TAIKO_5])
        elif rule == UtilizeRule.FISH:
            return ImageGrid([self.I_U_FISH_6, self.I_U_FISH_5])
        elif rule == UtilizeRule.TAIKO:
            return ImageGrid([self.I_U_TAIKO_6, self.I_U_TAIKO_5])
        else:
            logger.error('Unknown utilize rule')
            raise ValueError('Unknown utilize rule')

    @cached_property
    def order_cards(self) -> list[CardClass]:
        rule = self.config.kekkai_utilize.utilize_config.utilize_rule
        result = []
        if rule == UtilizeRule.DEFAULT:
            result = [CardClass.FISH6, CardClass.TAIKO6, CardClass.FISH5, CardClass.TAIKO5,
                      CardClass.TAIKO4, CardClass.FISH4, CardClass.TAIKO3, CardClass.FISH3]
        elif rule == UtilizeRule.FISH:
            result = [CardClass.FISH6, CardClass.FISH5,
                      CardClass.TAIKO6, CardClass.TAIKO5, CardClass.FISH4, CardClass.TAIKO4, CardClass.FISH3,
                      CardClass.TAIKO3]
        elif rule == UtilizeRule.TAIKO:
            result = [CardClass.TAIKO6, CardClass.TAIKO5,
                      CardClass.FISH6, CardClass.FISH5, CardClass.TAIKO4, CardClass.FISH4, CardClass.TAIKO3,
                      CardClass.FISH3]
        else:
            logger.error('Unknown utilize rule')
            raise ValueError('Unknown utilize rule')
        return result

    @staticmethod
    def _friend_lists(friend: SelectFriendList) -> list:
        """把好友列表选项展开为实际要扫描的顺序。

        BOTH 时先本区后跨区：本区优先（延迟低、好友多），本区没有合适的卡
        再去跨区碰运气。
        """
        if friend == SelectFriendList.BOTH:
            return [SelectFriendList.SAME_SERVER, SelectFriendList.DIFFERENT_SERVER]
        return [friend]

    def run_utilize(self, friend: SelectFriendList = SelectFriendList.SAME_SERVER,
                    shikigami_class: ShikigamiClass = ShikigamiClass.N,
                    shikigami_order: int = 7):
        """
        执行寄养
        :param shikigami_class:
        :param friend: 本区 / 跨区 / 两者都要
        :param rule:
        :return:
        """
        lists = self._friend_lists(friend)
        logger.hr('Start utilize')
        if self.first_utilize:
            self.swipe(self.S_U_END, interval=3)
            self.first_utilize = False
            if len(lists) == 1:
                current = lists[0]
                other = (SelectFriendList.DIFFERENT_SERVER
                         if current == SelectFriendList.SAME_SERVER
                         else SelectFriendList.SAME_SERVER)
                self.switch_friend_list(other)
                self.switch_friend_list(current)
            else:
                # 两个列表都刷一遍，最后停在第一个列表
                for one in lists[1:] + lists[:1]:
                    self.switch_friend_list(one)
        else:
            self.switch_friend_list(lists[0])

        # --------------- 结界卡选择 ---------------
        if not self._select_optimal_resource_card(lists):
            return False

        # 找到卡,重置次数
        self.utilize_add_count = 0
        logger.info('开始执行进入结界蹭卡流程')
        self.screenshot()
        # 进入结界
        if not self.appear(self.I_U_ENTER_REALM):
            logger.warning('Cannot find enter realm button')
            # 可能是滑动的时候出错
            logger.warning('The best reason is that the swipe is wrong')
            return
        wait_timer = Timer(20)
        wait_timer.start()
        while 1:
            self.screenshot()
            if self.appear(self.I_U_ADD_1) or self.appear(self.I_U_ADD_2):
                logger.info('Appear enter friend realm button')
                break
            if self.appear(self.I_CHECK_FRIEND_REALM_1):
                self.wait_until_stable(self.I_CHECK_FRIEND_REALM_1)
                logger.info('Appear enter friend realm button')
                break
            if self.appear(self.I_CHECK_FRIEND_REALM_3):
                self.wait_until_stable(self.I_CHECK_FRIEND_REALM_3)
                logger.info('Appear enter friend realm button')
                break
            if wait_timer.reached():
                self.save_image(wait_time=0, push_flag=False, content='进入好友结界超时', image_type='png')
                logger.warning('Appear friend realm timeout')
                return
            if self.appear_then_click(self.I_CHECK_FRIEND_REALM_2, interval=1.5):
                logger.info('Click too fast to enter the friend\'s realm pool')
                continue
            if self.appear_then_click(self.I_U_ENTER_REALM, interval=2.5):
                time.sleep(0.5)
                continue
        logger.info('Enter friend realm')

        # 判断好友的有两个位置还是一个坑位
        stop_image = None
        self.screenshot()
        if self.appear(self.I_U_ADD_1):  # 右侧第一个有（无论左侧有没有）
            logger.info('Right side has one')
            stop_image = self.I_U_ADD_1
        elif self.appear(self.I_U_ADD_2) and not self.appear(self.I_U_ADD_1):  # 右侧第二个有 但是最左边的没有，这表示只留有一个坑位
            logger.info('Right side has two')
            stop_image = self.I_U_ADD_2
        if not stop_image:
            # 没有坑位可能是其他人的手速太快了抢占了
            self.save_image(content='没有坑位了', wait_time=0, push_flag=False, image_type='png')
            logger.warning('没有坑位可能是其他人的手速太快了抢占了')
            return True
        # 切换式神的类型
        if not self.switch_shikigami_class(shikigami_class, page_check=lambda: self.appear(stop_image)):
            return False
        # 上式神
        self.set_shikigami(shikigami_order, stop_image)
        return True

    def _select_optimal_resource_card(self, friends=None):
        """整合后的智能选卡主逻辑（无嵌套函数版）

        :param friends: 要扫描的好友列表，可以是单个 SelectFriendList，也可以是
            列表（本区 + 跨区）。传多个时先全部扫一遍记录最佳值，再逐个列表找目标卡，
            命中后停在该列表，方便调用方直接进入结界。
        """
        # 类常量声明（需在类中定义）
        RESOURCE_PRESETS = {
            '斗鱼': [151, 143, 134, 126, 101, 84],
            '太鼓': [76,  76,  67,  67,  59,  50]
        }
        MAX_INDEX = 99

        if friends is None:
            friends = [self.config.kekkai_utilize.utilize_config.select_friend_list]
        elif isinstance(friends, SelectFriendList):
            friends = [friends]
        friends = [one for one in friends if one is not None]

        def get_resource_index(resource_name, current_value, preset_values):
            """获取资源匹配的档位索引"""
            for idx, val in enumerate(preset_values):
                if current_value >= val:
                    logger.info(f'📊 {resource_name}区间匹配: {current_value} ≥ {val} (档位{idx})')
                    return idx
            logger.warning(f'⚠️ {resource_name}值[{current_value}]低于所有预设')
            return MAX_INDEX

        while True:
            self.screenshot()

            # 第一阶段：初始记录获取（多个列表时逐个扫描，记录各自最高值）
            if self.ap_max_num == 0 and self.jade_max_num == 0:
                logger.hr('第一阶段：初始记录获取', 2)
                for one in friends:
                    if len(friends) > 1:
                        logger.info(f'📋 扫描好友列表: {one.value}')
                    if not self.switch_friend_list(one):
                        logger.warning(f'切换好友列表 {one.value} 失败，跳过该列表')
                        continue
                    if self._current_select_best():
                        logger.info(f'✅ 完美结界卡确认成功，重置状态')
                        self.ap_max_num, self.jade_max_num = 0, 0
                        return True
                logger.info(f'📝 记录最佳值 | 斗鱼:{self.ap_max_num} 太鼓:{self.jade_max_num}')
                return False

            logger.hr('第二阶段：资源优先级判断', 2)
            # 获取双资源档位
            ap_index = get_resource_index('斗鱼', self.ap_max_num, RESOURCE_PRESETS['斗鱼'])
            jade_index = get_resource_index('太鼓', self.jade_max_num, RESOURCE_PRESETS['太鼓'])

            # 双资源超限处理
            if ap_index == MAX_INDEX and jade_index == MAX_INDEX:
                logger.warning('🔄 斗鱼和太鼓均低于预设，重置初始记录')
                self.ap_max_num, self.jade_max_num = 0, 0
                return False

            # 决策优先级
            res_type, target = ('斗鱼', self.ap_max_num) if ap_index <= jade_index else ('太鼓', self.jade_max_num)
            logger.info(f'⚖️ 选择{res_type}卡 | 目标: {target}')

            # 第三阶段：执行选卡操作（逐个列表找，找到即停）
            logger.hr('第三阶段：执行选卡操作', 2)
            for one in friends:
                if len(friends) > 1:
                    logger.info(f'📋 在 {one.value} 列表里找 {res_type} 卡')
                if not self.switch_friend_list(one):
                    logger.warning(f'切换好友列表 {one.value} 失败，跳过该列表')
                    continue
                if self._current_select_best(res_type, target, selected_card=True):
                    logger.info(f'✅ {res_type}卡确认成功，重置状态')
                    self.ap_max_num, self.jade_max_num = 0, 0
                    return True
            logger.warning(f'❌ {res_type}卡在 {len(friends)} 个列表里都没找到，重置状态')
            self.ap_max_num, self.jade_max_num = 0, 0
            return False

    def _current_select_best(self, best_card_type=None, best_card_num=0, selected_card=False):
        """结界卡选择核心逻辑（集成版）
        功能：滑动屏幕寻找最优资源卡，支持两种模式：
        - 探索模式：记录当前遇到的最佳结界卡数值
        - 确认模式：根据给定条件选择指定类型结界卡

        :param best_card_type: 目标卡类型('太鼓'/'斗鱼')
        :param best_card_num:  要求的最低数值
        :param selected_card:  是否处于确认选择模式
        :return: 找到符合条件返回True，否则None
        """
        # ============== 配置常量 ==============#
        RESOURCE_CONFIG = {
            '斗鱼': {'max': 151, 'record_attr': 'ap_max_num'},
            '太鼓': {'max': 76, 'record_attr': 'jade_max_num'}
        }
        MAX_SWIPES = 20  # 最大滑动次数
        CONSEC_MISS = 3  # 允许连续无卡次数
        TIMEOUT = 120  # 操作超时(秒)

        # ============== 初始化阶段 ==============#
        logger.info(f'启动{"探索模式" if not selected_card else f"确认模式 | 目标: {best_card_type} @ {best_card_num}"}')
        timer = Timer(TIMEOUT).start()
        miss_count = 0  # 连续无卡计数器

        # ============== 主滑动循环 ==============#
        for swipe_count in range(MAX_SWIPES + 1):
            # 超时检测
            if timer.reached():
                logger.warning('⏰ 操作超时，终止流程')
                return None

            # ------ 步骤1: 截图识别结界卡 ------#
            self.screenshot()
            cards = self.order_targets.find_everyone(self.device.image)

            # 处理无卡情况
            if not cards:
                miss_count += 1
                logger.info(f'第{swipe_count}次滑动 | 未检测到结界卡' if swipe_count > 0 else '初始界面 | 未检测到结界卡')
                # 连续无卡超过阈值则终止
                if miss_count > CONSEC_MISS:
                    logger.warning(f'⚠️ 连续{miss_count}次 | 未检测到结界卡, 终止流程')
                    return None
                # 执行滑动操作
                self.perform_swipe_action()
                continue

            miss_count = 0  # 重置无卡计数器

            # ------ 步骤2: 处理识别到的结界卡 ------
            cards_list = [target for target, _, _ in cards]
            logger.info((f'第{swipe_count}次滑动' if swipe_count > 0 else '初始界面') + f' | 检测到结界卡：{cards_list}')

            # 遍历所有结界卡（已按位置排序）
            for _, _, area in cards:
                # 设置点击区域并获取结界卡详情
                self.C_SELECT_CARD.roi_front = area
                self.click(self.C_SELECT_CARD)
                time.sleep(2)  # 等待结界卡详情加载

                # 解析结界卡类型和数值
                card_type, card_value = self.check_card_num()

                # 跳过无效结界卡（类型未知或数值异常）
                if card_type == 'unknown' or card_value <= 0 or card_type not in RESOURCE_CONFIG:
                    logger.info(f'⏭️ 跳过无效卡: {card_type}@{card_value}')
                    continue

                # ====== 模式分支处理 ======#
                current_max = RESOURCE_CONFIG[card_type]['max']
                record_attr = RESOURCE_CONFIG[card_type]['record_attr']
                current_record = getattr(self, record_attr, 0)
                logger.info(f'🔍 识别卡片: {card_type} | 当前值: {card_value}, 最优值: {current_record}')

                # 更新最佳记录
                if card_value > current_record:
                    logger.info(f'📈 更新记录: {card_type} | {current_record} → {card_value}')
                    setattr(self, record_attr, card_value)

                if selected_card:  # 确认选择模式
                    # 检查是否符合选择条件
                    if (card_type == best_card_type) and (card_value >= best_card_num):
                        logger.info(f'🎉 确认蹭卡: {card_type} | 当前值: {card_value} ≥ 目标值: {best_card_num}')
                        self.save_image(push_flag=False, wait_time=0, content=f'🎉 确认蹭卡（{card_type}: {card_value}）')
                        return True
                else:  # 探索记录模式
                    # 发现完美卡直接返回
                    if card_value >= current_max:
                        message = f'🎉 完美蹭卡 | {card_type}: {card_value}'
                        logger.info(message)
                        self.save_image(push_flag=False, wait_time=0, content=message)
                        return True

            # ------ 步骤3: 滑动到下一屏 ------#
            self.perform_swipe_action()

        # ============== 终止处理 ==============#
        logger.warning(f'⚠️ 已达到最大滑动次数{MAX_SWIPES}, 终止流程')
        return None

    def perform_swipe_action(self):
        """统一滑动操作"""
        duration = 2
        safe_pos_x = random.randint(340, 600)
        safe_pos_y = random.randint(500, 565)
        p1 = (safe_pos_x, safe_pos_y)
        p2 = (safe_pos_x, safe_pos_y - 416)
        logger.info('Swipe %s -> %s, %sS ' % (point2str(*p1), point2str(*p2), duration))
        self.device.swipe_adb(p1, p2, duration=duration)

        # self.swipe(self.S_U_UP, duration=1, wait_up_time=1)
        self.device.click_record_clear()
        time.sleep(2)

    def check_card_num(self) -> tuple[str, int]:
        """优化版数值提取方法，返回结界卡类型及对应数值"""
        self.screenshot()
        # OCR识别
        raw_text = self.O_CARD_NUM.ocr(self.device.image)
        # logger.info(f'OCR原始结果: {raw_text}')

        # 判断结界卡类型
        if any(c in raw_text for c in ['体', 'カ', '力']):
            card_type = '斗鱼'
        elif any(c in raw_text for c in ['勾', '玉']):
            card_type = '太鼓'
        else:
            logger.warning(f'结界卡类型识别失败，原始内容: {raw_text}')
            # self.push_notify(content=f'结界卡类型识别失败: {raw_text}')
            return 'unknown', 0  # 未知类型返回0

        # 提取纯数字部分（兼容带+号的情况，如+100）
        cleaned = re.sub(r'[^\d+]', '', raw_text)  # 保留数字和加号
        match = re.search(r'\d+', cleaned)  # 匹配连续数字

        try:
            value = int(match.group()) if match else 0
        except ValueError:
            logger.warning(f'数值转换异常，清理后文本: {cleaned}')
            value = 0

        if value <= 0:
            self.push_notify(content=f'数值异常: {raw_text} -> 解析值: {value}')
            return card_type, 0

        # logger.info(f'识别成功: 卡类型: {card_type}, 数值: {value}')
        return card_type, value

    def back_guild(self):
        """
        回到寮的界面
        :return:
        """
        while 1:
            self.screenshot()

            if self.appear(self.I_GUILD_INFO):
                break
            if self.appear(self.I_GUILD_REALM):
                break
            if self.appear_then_click(self.I_PLANT_TREE_CLOSE):
                continue

            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_BLUE, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1):
                continue

    def back_realm(self):
        # 回到寮结界
        while 1:
            self.screenshot()
            if self.appear(self.I_REALM_SHIN):
                break
            if self.appear_multi_scale(self.I_SHI_DEFENSE):
                break
            if self.appear_then_click(self.I_UI_BACK_RED, interval=1):
                continue
            if self.appear_then_click(self.I_UI_BACK_BLUE, interval=1):
                continue


if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device

    c = Config('switch')
    d = Device(c)
    t = ScriptTask(c, d)
    for i in range(10):
        t.perform_swipe_action()
    t.recive_guild_ap_or_assets()
    # t.check_utilize_add()
    # t.check_card_num('勾玉', 67)
    # t.screenshot()
    # print(t.appear(t.I_BOX_EXP, threshold=0.6))
    # print(t.appear(t.I_BOX_EXP_MAX, threshold=0.6))
