# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
from datetime import time, datetime, timedelta

from module.logger import logger
from module.exception import TaskEnd
from module.base.timer import Timer

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_delegation
from tasks.Delegation.config import DelegationConfig
from tasks.Delegation.assets import DelegationAssets


class ScriptTask(GameUi, DelegationAssets):

    def run(self):
        self.goto_page(page_delegation)
        self.check_reward()
        con: DelegationConfig = self.config.delegation.delegation_config
        if con.miyoshino_painting:
            self.delegate_one('画')
        if con.bird_feather:
            self.delegate_one('鸟羽')
        if con.find_earring:
            self.delegate_one('寻找耳环')
        if con.cat_boss:
            self.delegate_one('猫老大')
        if con.miyoshino:
            self.delegate_one('接送')
        if con.strange_trace:
            self.delegate_one('痕迹')


        self.set_next_run(task='Delegation', success=True, finish=True)
        raise TaskEnd

    def delegate_one(self, name: str) -> bool:
        """
        委派一个任务
        :param name:
        :return:
        """
        def ui_click(click, stop):
            while 1:
                self.screenshot()
                if self.appear(stop):
                    break
                if self.click(click, interval=1.5):
                    continue
        logger.hr('Delegation one', 2)
        self.O_D_NAME.keyword = name
        self.screenshot()
        if not self.ocr_appear(self.O_D_NAME):
            logger.warning(f'Delegation: {name} not found')
            return False
        enter_timer = Timer(15).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_D_START):
                break
            if enter_timer.reached():
                logger.warning(f'Delegation: {name} enter timeout')
                return False

            # 检测到弹出的勾玉确认框，优先点击取消保护勾玉
            if self.appear_then_click(self.I_UI_CANCEL, interval=1):
                continue

            # 如果出现’召回‘ ’返回‘ 说明这个是现在委派中
            # 需要退出
            if self.appear(self.I_D_BACK):
                logger.warning(f'Delegation: {name} is in delegation')
                back_timer = Timer(6).start()
                while 1:
                    self.screenshot()
                    if not self.appear(self.I_D_BACK):
                        break
                    if back_timer.reached():
                        logger.warning('Exit delegation detail timeout')
                        break
                    # 若此时被取消弹窗遮挡返回，优先点取消
                    if self.appear_then_click(self.I_UI_CANCEL, interval=1):
                        continue
                    if self.appear_then_click(self.I_D_BACK, interval=1.2):
                        continue
                self.wait_until_appear(self.I_REWARDS_MIN, wait_time=3)
                return False
            if self.appear_then_click(self.I_D_SKIP, interval=0.8):
                continue
            if self.appear_then_click(self.I_D_CONFIRM, interval=0.8):
                continue
            # 点击委派任务名：防抖由 1s 提升至 3s，杜绝跨页面过渡时误触右上角"完成(30勾玉)"
            if self.ocr_appear_click(self.O_D_NAME, interval=3):
                continue
        # 进入委派  fefe e  fe
        logger.info(f'Enter Delegation: {name}')
        ui_click(self.C_D_1, self.I_D_SELECT_1)
        ui_click(self.C_D_2, self.I_D_SELECT_2)
        ui_click(self.C_D_3, self.I_D_SELECT_3)
        ui_click(self.C_D_4, self.I_D_SELECT_4)
        # 委派开始：先最多补选 2 次第 5 位式神，随后立刻点「出发」（原逻辑每 0.8s 死循环点 d_5 导致出发按钮永远点不到）
        logger.info(f'Delegation: {name} start')
        start_timer = Timer(15).start()
        slot5_tries = 0
        while 1:
            self.screenshot()
            if not self.appear(self.I_D_START):
                break
            if start_timer.reached():
                logger.warning(f'Delegation: {name} start timeout')
                self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1)
                break
            if slot5_tries < 2 and not self.appear(self.I_D_SELECT_5):
                if self.click(self.C_D_5, interval=0.8):
                    slot5_tries += 1
                    continue
            if self.appear_then_click(self.I_D_START, interval=1.5):
                continue
        # ui_click(self.C_D_5, self.I_D_SELECT_5)
        # self.ui_click_until_disappear(self.I_D_START)

    def check_reward(self):
        check_timer = Timer(3)
        check_timer.start()
        chat_clicks = 0
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_REWARDS_GET, interval=1):
                chat_clicks = 0
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_REWARDS_FALSE, interval=1):
                chat_clicks = 0
                check_timer.reset()
                continue
            # 「完成任务」必须优先于 I_REWARDS_CHAT：领奖确认页右上角折叠灯笼会以 0.996 命中 I_REWARDS_CHAT，
            # 旧顺序把「完成任务」堵死，连点 10 次 REWARDS_REWARDS_CHAT 触发 GameTooManyClickError。
            if self.appear_then_click(self.I_REWARDS_DONE, interval=1):
                chat_clicks = 0
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_CHAT_1, interval=1):
                chat_clicks = 0
                check_timer.reset()
                continue
            if self.appear_then_click(self.I_CHAT_2, interval=1):
                chat_clicks = 0
                check_timer.reset()
                continue
            if chat_clicks < 3 and self.appear_then_click(self.I_REWARDS_CHAT, interval=1):
                chat_clicks += 1
                check_timer.reset()
                continue
            if chat_clicks >= 3 and not self.appear(self.I_REWARDS_MIN):
                logger.warning('[Delegation] I_REWARDS_CHAT 连点 3 次无进展，尝试退出当前详情页')
                self.appear_then_click(self.I_D_BACK, interval=1)
                self.appear_then_click(self.I_UI_BACK_YELLOW, interval=1)
                chat_clicks = 0
                check_timer.reset()
                continue


            if not self.appear(self.I_REWARDS_MIN):
                continue
            if check_timer.reached():
                break
            # O_D_DONE 命中的是卡片上“完成”缎带，可点的领取位置在其正下方同样大小的一块
            if self.ocr_appear(self.O_D_DONE, interval=1):
                # 同一帧里可能有多张卡都显示“完成”，此时 Full 模式会把多个命中框合并成一个，
                # 合并框正下方不一定是可点的领取位置；这里取单个命中框，点它正下方同样大小的一块
                results = self.O_D_DONE.detect_and_ocr(self.device.image, logDisplay=False)
                index_list = self.O_D_DONE.filter(results, self.O_D_DONE.keyword) if results else None
                if index_list:
                    box = results[index_list[0]].box
                    x = box[0, 0] + self.O_D_DONE.roi[0]
                    y = box[0, 1] + self.O_D_DONE.roi[1]
                    w = box[1, 0] - box[0, 0]
                    h = box[2, 1] - box[0, 1]
                    self.device.click(x=x + w / 2, y=y + h + h / 2, control_name=self.O_D_DONE.name)
                check_timer.reset()
                continue


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)

    # t.delegate_one('弥助的画')
    t.run()



