# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import copy
from time import sleep
from datetime import time, datetime, timedelta

from exceptiongroup import catch
from tasks.DailyTrifles.page import page_store_gift_room
from tasks.DailyTrifles.store_ui import select_daily_gifts
from winerror import NOERROR

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_summon, page_guild, page_mall, page_friends
from tasks.DailyTrifles.config import DailyTriflesConfig
from tasks.DailyTrifles.assets import DailyTriflesAssets
from tasks.Component.Summon.summon import Summon

from module.logger import logger
from module.exception import TaskEnd, ScriptError
from module.base.timer import Timer
from tasks.DailyTrifles.config import SummonType
import re

class ScriptTask(GameUi, Summon, DailyTriflesAssets):

    def run(self):
        con = self.config.daily_trifles.trifles_config
        # 每日召唤
        if con.one_summon:
            self.run_one_summon()
        if con.guild_wish:
            pass
        # 友情点
        if con.friend_love:
            self.run_friend_love()
        # 吉闻
        if con.luck_msg:
            self.run_luck_msg()
        # 商店签到 or 购买寿司
        if con.store_sign or con.buy_sushi_count > 0:
            self.run_store()
        self.set_next_run('DailyTrifles', success=True, finish=False)
        raise TaskEnd('DailyTrifles')

    def run_one_summon(self):
        self.goto_page(page_summon)
        config=self.config.daily_trifles.trifles_config
        if config.summon_type == SummonType.default:
            self.summon_one(draw_mystery_pattern=config.draw_mystery_pattern)
            self.check_time()
        elif config.summon_type == SummonType.recall:
            self.summon_recall()
        self.back_summon_main()

    def check_time(self):
        config = self.config.daily_trifles.trifles_config
        now = datetime.now()
        next_run = now + self.config.daily_trifles.scheduler.success_interval
        # 检查是否跨月（next_run的月份与当前月份不同）
        if next_run.month != now.month:
            # 跨月重置神秘图案触发状态
            if not config.draw_mystery_pattern:
                config.draw_mystery_pattern = True
                logger.info(
                    f"reset draw_mystery_pattern to True, next_run: {next_run}")
        else:
            # 如果还是在同一月份，则没必要再绘制神秘图案
            config.draw_mystery_pattern = False
        self.config.save()

    def summon_recall(self):
        """
        确保在召唤界面,每日召唤一次
        召唤结束后回到 召唤主界面
        :return:
        """
        list = [self.O_SELECT_SM2, self.O_SELECT_SM3, self.O_SELECT_SM4]
        count = 0
        while True:
            count += 1

            for i in range(len(list)):
                sleep(1)
                self.goto_page(page_main)
                self.goto_page(page_summon)
                self.appear_then_click(self.I_UI_BACK_RED, interval=1)
                x, y = list[i].coord()
                self.device.click(x, y)
                sleep(1)
                self.screenshot()
                if self.appear(self.I_RECALL_TICKET):
                    break
                logger.info("Select preset group RECALL")

            self.screenshot()
            if self.appear(self.I_RECALL_TICKET):
                break
            if count >= 3:
                self.config.notifier.push(title='今忆召唤抽卡失败', content='每日任务,今忆召唤抽卡失败!!!')
                return

        logger.info('Summon one RECALL')
        self.wait_until_appear(self.I_RECALL_TICKET)
        while True:
            ticket_info = self.O_RECALL_TICKET_AREA.ocr(self.device.image)
            # 处理 None 和空字符串
            if ticket_info is None or ticket_info == '':
                ticket_info = 0
            else:
                # 使用正则表达式提取字符串中的数字
                match = re.search(r'\d+', ticket_info)
                if match:
                    ticket_info = int(match.group())
                else:
                    logger.warning(f'Invalid ticket_info value: {ticket_info}, expected a numeric string')
                    ticket_info = 0  # 将无效值设置为默认值 0
            if ticket_info <= 0:
                logger.warning('There is no any one RECALL ticket')
                return
            # 某些情况下滑动异常
            self.S_RANDOM_SWIPE_1.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_2.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_3.name = 'S_RANDOM_SWIPE'
            self.S_RANDOM_SWIPE_4.name = 'S_RANDOM_SWIPE'
            while 1:
                self.screenshot()
                if self.appear(self.I_RECALL_ONE_TICKET):
                    break
                if self.appear_then_click(self.I_RECALL_TICKET, interval=1):
                    continue

            # 画一张票
            sleep(1)
            while 1:
                self.screenshot()
                if self.appear(self.I_RECALL_SM_CONFIRM, interval=0.6):
                    self.ui_click_until_disappear(self.I_RECALL_SM_CONFIRM)
                    break
                if self.appear(self.I_SM_CONFIRM_2, interval=0.6):
                    self.ui_click_until_disappear(self.I_SM_CONFIRM_2)
                    break
                if self.appear(self.I_RECALL_ONE_TICKET, interval=1):
                    # 某些时候会点击到 “语言召唤”
                    if self.appear_then_click(self.I_UI_CANCEL, interval=0.8):
                        continue
                    self.summon()
                    continue
            logger.info('Summon one success')

    def run_guild_wish(self):
        pass

    def run_luck_msg(self):
        self.goto_page(page_friends)
        while 1:
            self.screenshot()
            if self.appear(self.I_LUCK_TITLE):
                break
            if self.appear_then_click(self.I_FRIENDSHIP_UP, interval=1):
                continue
            if self.ocr_appear_click(self.O_LUCK_MSG, interval=1):
                continue
        logger.info('Start luck msg')
        check_timer = Timer(2)
        check_timer.start()
        while 1:
            self.screenshot()

            if self.appear_then_click(self.I_CLICK_BLESS, interval=1):
                continue
            if self.appear_then_click(self.I_ONE_CLICK_BLESS, interval=1):
                continue
            if self.ui_reward_appear_click():
                logger.info('Get reward of luck msg')
                break
            if check_timer.reached():
                logger.warning('There is no any luck msg')
                break

        self.ui_click(self.I_UI_BACK_RED, self.I_CHECK_MAIN)

    def run_friend_love(self):
        self.goto_page(page_friends)
        while 1:
            self.screenshot()
            if self.appear(self.I_L_LOVE):
                break
            if self.appear_then_click(self.I_FRIENDSHIP_UP, interval=1):
                continue
            if self.appear_then_click(self.I_L_FRIENDS, interval=1):
                continue
            if self.appear_then_click(self.I_FRIEND_TAB, interval=3):
                continue
        logger.info('Start friend love')
        check_timer = Timer(2)
        check_timer.start()
        while 1:
            self.screenshot()

            if self.appear_then_click(self.I_L_COLLECT, interval=1):
                continue
            if self.ui_reward_appear_click():
                logger.info('Get reward of friend love')
                break
            if check_timer.reached():
                logger.warning('There is no any love')
                break

        self.ui_click(self.I_UI_BACK_RED, self.I_CHECK_MAIN)

    def run_store(self):
        self.goto_page(page_mall, confirm_wait=3)

        if self.config.daily_trifles.trifles_config.store_sign:
            self.run_store_sign()
        if self.config.daily_trifles.trifles_config.buy_sushi_count > 0:
            self.run_buy_sushi()

        self.goto_page(page_mall)
        self.goto_page(page_main)

    def run_store_sign(self):

        self.goto_page(page_store_gift_room)
        self.screenshot()
        if not self.select_daily_gift_tab():
            logger.warning('礼包屋「日常」页签未识别（多形态 OCR + 一次重试都失败），'
                           '本次跳过礼包屋签到，继续后面的日常琐事')
            return
        logger.info('Enter store sign')
        sleep(1)  # 等个动画
        self.screenshot()
        if not self.appear(self.I_GIFT_SIGN):
            logger.warning('There is no gift sign')
            return

        if self.ui_get_reward(self.I_GIFT_SIGN, click_interval=2.5):
            logger.info('Get reward of gift sign')

    # 礼包屋右侧页签在活动期间会被改写/挤动：多形态关键字，按包含关系识别
    GIFT_TAB_KEYWORDS = ('日常', '日常礼包', '每日礼包')

    def select_daily_gift_tab(self) -> bool:
        """多种形态识别「日常」页签，失败重试一次。

        事件活动（如「奇惠购物节」）会在右侧页签插入新条目，把「日常」往下挤，
        页签框还可能被 OCR 粘连成「日常特权」之类；旧逻辑用 exact=True 全等匹配，
        一次失败就 raise ScriptError 导致整段日常不领取（log/error/1795175781813）。
        现在：精确匹配 → 模糊匹配并点框中心 → 等 1s 重试一轮 → 仍失败返回 False。
        """
        if select_daily_gifts(self):
            return True
        if self.click_gift_tab():
            return True
        sleep(1.0)
        self.screenshot()
        if select_daily_gifts(self):
            return True
        return self.click_gift_tab()

    def ocr_gift_tabs(self) -> list:
        """礼包屋右侧页签 OCR 结果：[(text, cx, cy)]，坐标已换回全屏。"""
        from tasks.DailyTrifles.store_ui import DAILY_GIFT_TAB
        x0, y0 = int(DAILY_GIFT_TAB.roi[0]), int(DAILY_GIFT_TAB.roi[1])
        try:
            rows = DAILY_GIFT_TAB.detect_and_ocr(self.device.image)
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            logger.warning(f'礼包屋页签 OCR 异常：{type(error).__name__}: {error}')
            return []
        found = []
        for row in rows:
            text = str(getattr(row, 'ocr_text', '') or '').replace(' ', '')
            try:
                xs = [float(point[0]) for point in row.box]
                ys = [float(point[1]) for point in row.box]
            except (AttributeError, IndexError, TypeError, ValueError) as error:
                logger.debug(f'礼包屋页签坐标异常（已跳过该框）：{type(error).__name__}: {error}')
                continue
            if not text or not xs or not ys:
                continue
            found.append((text, int(sum(xs) / len(xs)) + x0, int(sum(ys) / len(ys)) + y0))
        return found

    def click_gift_tab(self) -> bool:
        """按多形态关键字点礼包屋页签，命中返回 True。"""
        self.screenshot()
        tabs = self.ocr_gift_tabs()
        for text, cx, cy in tabs:
            if any(word in text for word in self.GIFT_TAB_KEYWORDS):
                logger.info(f'礼包屋页签识别为「{text}」，点击 ({cx}, {cy})')
                self.device.click(x=cx, y=cy, control_name='GIFT_TAB_DAILY')
                return True
        logger.info(f'礼包屋页签 OCR 结果：{[text for text, _, _ in tabs]}')
        return False

    def run_buy_sushi(self):

        # 进入Special
        while 1:
            from tasks.RichMan.assets import RichManAssets
            self.screenshot()
            if self.appear(RichManAssets.I_SIDE_CHECK_SPECIAL):
                break
            if self.appear_then_click(RichManAssets.I_MALL_SUNDRY, interval=1):
                continue
            if self.appear_then_click(RichManAssets.I_SIDE_SURE_SPECIAL, interval=1):
                continue

        def detect_buy_count(base_element) -> (int, int):
            # 返回count,price
            MAX_PRICE = 9999
            MAX_COUNT = 9999
            roi = copy.deepcopy(base_element.roi_front)
            roi[0] = roi[0] + roi[2]
            roi[1] = roi[1] + roi[3] - 30
            roi[2] = 60
            roi[3] = 30
            self.O_STORE_SUSHI_PRICE.roi = roi
            _price = self.O_STORE_SUSHI_PRICE.detect_text(self.device.image)
            # 保守策略，避免OCR错误购买
            try:
                _price = int(_price)
            except Exception as e:
                _price = MAX_PRICE

            if _price < 60:
                return 0, MAX_PRICE
            _count = (_price - 60) / 20
            return _count, _price

        roi = None
        # 购买体力
        while 1:
            self.screenshot()
            # count, price = detect_buy_count(roi)
            # if count >= self.config.model.daily_trifles.trifles_config.buy_sushi_count:
            #     break
            if self.appear(self.I_STORE_COST_TYPE_JADE):
                count, price = detect_buy_count(self.I_STORE_COST_TYPE_JADE)
                if count >= self.config.daily_trifles.trifles_config.buy_sushi_count:
                    break
                self.ui_click_until_disappear(self.I_STORE_COST_TYPE_JADE, interval=2)
                logger.info(f"Buy Sushi With {price} Jade")
                continue

            if self.appear(self.I_SPECIAL_SUSHI):
                # 此处确定当前购买体力所需勾玉数量的位置,用于后续识别
                count, price = detect_buy_count(self.I_SPECIAL_SUSHI)
                if count >= self.config.daily_trifles.trifles_config.buy_sushi_count:
                    break
                self.ui_click(self.I_SPECIAL_SUSHI, stop=self.I_STORE_COST_TYPE_JADE, interval=2)
                continue
        return


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    t.run_friend_love()
