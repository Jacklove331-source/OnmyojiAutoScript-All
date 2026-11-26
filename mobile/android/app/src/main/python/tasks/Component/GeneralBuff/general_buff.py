# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time

import cv2
import numpy as np

from tasks.Component.GeneralBuff.assets import GeneralBuffAssets
from module.atom.ocr import RuleOcr
from module.atom.image import RuleImage
from tasks.base_task import BaseTask
from module.logger import logger


class GeneralBuff(BaseTask, GeneralBuffAssets):

    def open_buff(self):
        """
        打开buff的总界面
        :return:
        """
        logger.info('Open buff')
        while 1:
            self.screenshot()
            if self.appear(self.I_CLOUD):
                break
            if self.appear_then_click(self.I_BUFF_1, interval=2):
                continue

        check_image = self.I_AWAKE
        while 1:
            self.screenshot()
            if self.appear(check_image):
                break

            self.swipe(self.S_BUFF_UP, interval=2)

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

    @staticmethod
    def _mobile_buff_label_area(buff: RuleOcr, image) -> tuple | None:
        keyword = buff.keyword
        for effect in ('金币增加', '经验增加'):
            if effect in keyword:
                keyword = keyword[keyword.index(effect):]
                break

        matches = []
        for line in buff.detect_and_ocr(image):
            text = ''.join(line.ocr_text.split()).replace('全币', '金币').replace('増', '增').replace('％', '%')
            if text.rstrip('。').endswith(keyword):
                matches.append(line.box)
        if len(matches) != 1:
            return None
        box = matches[0]
        left, top = np.min(box, axis=0)
        right, bottom = np.max(box, axis=0)
        return left + buff.roi[0], top + buff.roi[1], right - left, bottom - top

    def get_area(self, buff: RuleOcr) -> tuple | None:
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
        if self.device.is_desktop:
            area = buff.ocr(self.device.image)
        else:
            area = self._mobile_buff_label_area(buff, self.device.image)
        if not area or area[2] <= 0 or area[3] <= 0 or area == (432.0, 143.0, 325.0, 21.0):
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

    @staticmethod
    def _mobile_gold_timer_enabled(image, row_y: int) -> bool | None:
        if not 125 <= row_y <= 510:
            return None
        sample = image[row_y - 5:row_y + 5, 777:792]
        hue, saturation, value = cv2.split(cv2.cvtColor(sample, cv2.COLOR_RGB2HSV))
        strong = (saturation >= 155) & (value >= 135)
        amber = strong & (hue >= 13) & (hue <= 35)
        red = strong & ((hue <= 8) | (hue >= 165))
        if amber.mean() >= 0.65:
            return True
        if red.mean() >= 0.65:
            return False
        return None

    def _set_mobile_gold_buff(self, buff, is_open: bool):
        area = self.get_area(buff)
        if not area:
            return
        _, top, _, height = area
        row_y = top + height // 2
        tapped = False
        for _ in range(4):
            if not self.appear(self.I_CLOUD):
                break
            enabled = self._mobile_gold_timer_enabled(self.device.image, row_y)
            if enabled is is_open:
                logger.info(f'{buff.name} confirmed {"on" if enabled else "off"}')
                return
            if enabled is not None and not tapped:
                self.device.click(784, row_y, control_name=buff.name)
                tapped = True
            time.sleep(0.55)
            self.screenshot()
        logger.warning(f'Could not confirm the {buff.name} switch')

    def gold_50(self, is_open: bool = True):
        """
        金币50buff
        :param is_open: 是否打开
        :return:
        """
        logger.info(f'{"Open" if is_open else "Close"} gold 50 buff')
        if not self.device.is_desktop:
            self._set_mobile_gold_buff(self.O_GOLD_50, is_open)
            return
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
        if not self.device.is_desktop:
            self._set_mobile_gold_buff(self.O_GOLD_100, is_open)
            return
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
