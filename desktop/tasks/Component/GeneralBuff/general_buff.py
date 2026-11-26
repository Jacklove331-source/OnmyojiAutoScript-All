# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

import cv2
import numpy as np

from tasks.Component.GeneralBuff.assets import GeneralBuffAssets
from tasks.Component.SwitchSoul.click_guard import clear_click_record
from module.atom.ocr import RuleOcr
from module.atom.image import RuleImage
from tasks.base_task import BaseTask
from module.logger import logger


class GeneralBuff(BaseTask, GeneralBuffAssets):

    def open_buff(self) -> bool:
        """打开 buff 总界面，并把列表滚到「觉醒」图标处。

        返回 True = 正常到达；False = 有界重试用尽（软退出，不抛 GameTooManyClickError）。

        为什么这里的重复是**合法**的：
        * `I_BUFF_1` 是庭院上的 buff 入口，点一次要等面板动画，重试 1~2 次是正常路径；
        * `S_BUFF_UP`（控制名 `buff_up`）是 buff 列表的翻页滑动，次数由列表长度决定。
        但两者都写成 `while 1`：设备层 `Device.handle_control_check()` 把 swipe 和 click
        记进**同一份** click_record，同一个控制名累计 10 次就抛 GameTooManyClickError
        （2026-11-17 17:41 实测 Orochi 打开 buff 连滑 9~10 次 `buff_up` 整局失败，
        log/error/1794908480760）。
        修法：每次动作前清掉该控制名的记录（必须传**字符串控制名**，
        `click_record_remove(obj)` 传规则对象是 no-op），并加 8 次上限。
        上限内行为与旧实现完全一致（找到 I_AWAKE 就立刻返回 True）。
        """
        logger.info('Open buff')
        max_retry = 8
        opened = False
        for _ in range(max_retry):
            self.screenshot()
            if self.appear(self.I_CLOUD):
                opened = True
                break
            clear_click_record(self.device, self.I_BUFF_1)
            self.appear_then_click(self.I_BUFF_1, interval=2)
        if not opened:
            logger.warning(f'点了 {max_retry} 次 buff 入口仍没打开 buff 面板，跳过 buff 步骤')
            return False

        check_image = self.I_AWAKE
        for _ in range(max_retry):
            self.screenshot()
            if self.appear(check_image):
                return True

            clear_click_record(self.device, self.S_BUFF_UP)
            self.swipe(self.S_BUFF_UP, interval=2)
        self.screenshot()
        if self.appear(check_image):
            return True
        logger.warning(f'Buff 列表滑了 {max_retry} 次仍未看到觉醒图标，跳过该步骤')
        return False

    def close_buff(self):
        """
        关闭buff的总界面, 但是要确保buff界面已经打开了
        :return:
        """
        logger.info('Close buff')
        while 1:
            self.screenshot()
            if not self.appear(self.I_CLOUD):
                break
            if self.appear_then_click(self.I_BUFF_1, interval=2):
                continue

    def get_area(self, buff: RuleOcr) -> tuple[int, int, int, int] | None:
        """
        获取要点击的开关buff的区域
        :param cls:
        :param image:
        :param buff:
        :return:  如果没有就返回None
        """
        # 防止邀请框挡住BUFF框架
        self.reject_invite()
        self.screenshot()
        area = buff.ocr(self.device.image, exact=True)
        if not area or area[2] <= 0 or area[3] <= 0:
            logger.info(f'No {buff.name} buff')
            return None

        # 开始的x坐标就是文字的右边
        start_x = area[0] + area[2] + 10  # 10是文字和开关之间的间隔
        start_y = area[1] - 10
        width = 80  # 开关的宽度 80够了
        height = area[3] + 20
        return int(start_x), int(start_y), int(width), int(height)

    def set_switch_area(self, area):
        """
        设置开关的区域
        :param area:
        :return:
        """
        self.I_OPEN_YELLOW.roi_back = list(area)  # 动态设置roi
        self.I_CLOSE_RED.roi_back = list(area)

    def gold_50(self, is_open: bool = True):
        """
        金币50buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} gold 50 buff')
        self.screenshot()
        area = self.get_area(self.O_GOLD_50)
        if not area:
            logger.warning('No gold 50 buff')
            return None
        self.set_switch_area(area)
        if is_open:
            if not self.ui_click(click=self.I_CLOSE_RED, stop=self.I_OPEN_YELLOW, interval=1, timeout=10):
                logger.warning('Open gold 50 buff failed')
        else:
            if not self.ui_click(click=self.I_OPEN_YELLOW, stop=self.I_CLOSE_RED, interval=1, timeout=10):
                logger.warning('Close gold 50 buff failed')

    def gold_100(self, is_open: bool = True):
        """
        金币100buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} gold 100 buff')
        self.screenshot()
        area = self.get_area(self.O_GOLD_100)
        if not area:
            logger.warning('No gold 100 buff')
            return None
        self.set_switch_area(area)
        if is_open:
            if not self.ui_click(click=self.I_CLOSE_RED, stop=self.I_OPEN_YELLOW, interval=1, timeout=10):
                logger.warning('Open gold 100 buff failed')
        else:
            if not self.ui_click(click=self.I_OPEN_YELLOW, stop=self.I_CLOSE_RED, interval=1, timeout=10):
                logger.warning('Close gold 100 buff failed')

    def exp_50(self, is_open: bool = True):
        """
        经验50buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} exp 50 buff')
        while 1:
            self.screenshot()
            area = self.get_area(self.O_EXP_50)
            if not area:
                logger.warning('No exp 50 buff')
                continue
            self.set_switch_area(area)

            if not self.appear(self.I_OPEN_YELLOW) and not self.appear(self.I_CLOSE_RED):
                self.device.swipe(p2=(530, 240), p1=(580, 320))
                time.sleep(1)
            else:
                break

        if is_open:
            if not self.ui_click(click=self.I_CLOSE_RED, stop=self.I_OPEN_YELLOW, interval=1, timeout=10):
                logger.warning('Open exp 50 buff failed')
        else:
            if not self.ui_click(click=self.I_OPEN_YELLOW, stop=self.I_CLOSE_RED, interval=1, timeout=10):
                logger.warning('Close exp 50 buff failed')

    def exp_100(self, is_open: bool = True):
        """
        经验100buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} exp 100 buff')
        while 1:
            self.screenshot()
            area = self.get_area(self.O_EXP_100)
            if not area:
                logger.warning('No exp 100 buff')
                continue
            self.set_switch_area(area)

            if not self.appear(self.I_OPEN_YELLOW) and not self.appear(self.I_CLOSE_RED):
                self.device.swipe(p2=(530, 240), p1=(580, 320))
                time.sleep(1)
            else:
                break

        if is_open:
            if not self.ui_click(click=self.I_CLOSE_RED, stop=self.I_OPEN_YELLOW, interval=1, timeout=10):
                logger.warning('Open exp 100 buff failed')
        else:
            if not self.ui_click(click=self.I_OPEN_YELLOW, stop=self.I_CLOSE_RED, interval=1, timeout=10):
                logger.warning('Close exp 100 buff failed')

    def get_area_image(self, target: RuleImage) -> list:
        """
        获取觉醒加成或者是御魂加成所要点击的区域
        因为实在的图片比ocr快
        :param image:
        :param target:
        :return:
        """
        self.reject_invite()
        self.screenshot()

        if not target.match(self.device.image):
            logger.warning(f'No {target.name} buff')
            return None
            # logger.info(f'front area: {target.roi_front}')
            # logger.info(f'front center: {target.front_center()}')
        start_x = int(target.front_center()[0] + 364)
        start_y = int(target.roi_front[1])
        width = 80
        height = int(target.roi_front[3])
        return [start_x, start_y, width, height]

    def awake(self, is_open: bool = True):
        """
        觉醒buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} awake buff')
        self.screenshot()
        area = self.get_area_image(self.I_AWAKE)
        if not area:
            logger.warning('No awake buff')
            return None
        self.set_switch_area(area)
        if is_open:
            if not self.ui_click(click=self.I_CLOSE_RED, stop=self.I_OPEN_YELLOW, interval=1, timeout=10):
                logger.warning('Open awake buff failed')
        else:
            if not self.ui_click(click=self.I_OPEN_YELLOW, stop=self.I_CLOSE_RED, interval=1, timeout=10):
                logger.warning('Close awake buff failed')

    def soul(self, is_open: bool = True):
        """
        御魂buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} soul buff')
        self.screenshot()
        area = self.get_area_image(self.I_SOUL)
        if not area:
            logger.warning('No soul buff')
            return None
        self.set_switch_area(area)
        if is_open:
            if not self.ui_click(click=self.I_CLOSE_RED, stop=self.I_OPEN_YELLOW, interval=1, timeout=10):
                logger.warning('Open soul buff failed')
        else:
            if not self.ui_click(click=self.I_OPEN_YELLOW, stop=self.I_CLOSE_RED, interval=1, timeout=10):
                logger.warning('Close soul buff failed')

    def reject_invite(self):
        from tasks.Component.GeneralInvite.assets import GeneralInviteAssets as gia
        while 1:
            self.screenshot()
            if not (self.appear(gia.I_I_REJECT_1) or self.appear(gia.I_I_REJECT_2) or self.appear(gia.I_I_REJECT_3)):
                break
            if self.appear(gia.I_I_REJECT_3):
                self.click(gia.I_I_REJECT_3, 6)
                continue
            if self.appear(gia.I_I_REJECT_2):
                self.click(gia.I_I_REJECT_2, 6)
                continue
            if self.appear(gia.I_I_REJECT_1):
                self.click(gia.I_I_REJECT_1, 6)
                continue


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    t = GeneralBuff(c, d)

    t.open_buff()
    # t.screenshot()
    #
    t.awake(is_open=True)
    t.soul(is_open=True)
    t.gold_50(is_open=False)
    t.gold_100(is_open=False)
    t.exp_50(is_open=True)
    t.exp_100(is_open=True)
