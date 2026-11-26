# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import time
from typing import Callable

from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.exception import GameStuckError
from module.logger import logger

from tasks.base_task import BaseTask
from tasks.Utils.config_enum import ShikigamiClass
from tasks.Component.ReplaceShikigami.assets import ReplaceShikigamiAssets
from tasks.Component.SwitchSoul.click_guard import clear_click_record


class ReplaceShikigami(BaseTask, ReplaceShikigamiAssets):
    O_FOSTERED_SHIKIGAMI = RuleOcr(roi=(510, 216, 270, 44), area=(510, 216, 270, 44),
                                  mode='Single', method='Default', keyword='',
                                  name='fostered_shikigami')


    def in_shikigami_growth(self, screenshot=False) -> bool:
        # 判定是否在式神育成界面
        # 判定的依据是是否出现了 式神录 这个图片
        if screenshot:
            self.screenshot()
        return self.appear(self.I_RS_RECORDS_SHIKI)

    def switch_shikigami_class(
        self, shikigami_class: ShikigamiClass = ShikigamiClass.N, *,
        page_check: Callable[[], bool] | None = None,
    ) -> bool:
        """Select a rarity while the caller's shikigami picker is visible."""
        page_check = page_check or self.in_shikigami_growth
        match_selected = {ShikigamiClass.MATERIAL: self.I_RS_MATERIAL_SELECTED,
                          ShikigamiClass.N: self.I_RS_N_SELECTED,
                          ShikigamiClass.R: self.I_RS_R_SELECTED,
                          ShikigamiClass.SR: self.I_RS_SR_SELECTED,
                          ShikigamiClass.SSR: self.I_RS_SSR_SELECTED,
                          ShikigamiClass.SP: self.I_RS_SP_SELECTED,
                          ShikigamiClass.UR: self.I_RS_UR_SELECTED}
        match_click = {ShikigamiClass.MATERIAL: self.I_RS_MATERIAL,
                       ShikigamiClass.N: self.I_RS_N,
                       ShikigamiClass.R: self.I_RS_R,
                       ShikigamiClass.SR: self.I_RS_SR,
                       ShikigamiClass.SSR: self.I_RS_SSR,
                       ShikigamiClass.SP: self.I_RS_SP,
                       ShikigamiClass.UR: self.I_RS_UR}
        check_selected = match_selected[shikigami_class]
        check_click = match_click[shikigami_class]
        deadline = time.monotonic() + 24
        tab_clicks = 0
        picker_clicks = 0
        while time.monotonic() < deadline:
            self.screenshot()
            if not page_check():
                logger.warning('Shikigami picker page was lost while selecting %s', shikigami_class)
                return False
            if self.appear(check_selected):
                logger.info('Select shikigami class: %s', shikigami_class)
                return True
            if self.appear(check_click):
                if tab_clicks >= 4:
                    break
                if not self.wait_until_pos_stable(check_click, stable_time=1.5, timeout=3.5):
                    continue
                if not page_check() or not self.appear(check_click):
                    continue
                if not self.appear(check_selected) and self.click(check_click, interval=2):
                    tab_clicks += 1
                continue
            if picker_clicks >= 3:
                break
            if self.click(self.C_SHIKIGAMI_SWITCH_1, interval=3.5):
                picker_clicks += 1
        logger.warning('Could not select shikigami class %s', shikigami_class)
        return False

    def unset_shikigami_max_lv(self):
        """
        要求在式神育成的界面
        拉下满级的式神，留空位置
        :return:
        """
        # 合法重复：I_RS_LEVEL_MAX 是「只看满级」筛选开关，点一次就翻一次状态，
        # 判据是「这张卡是否还在」，所以同一控制名会被点几次。
        # 但旧逻辑是 while 1 无限点，一旦开关点不动（模板失配/被弹窗挡住）就会
        # 累积到 10 次同名点击 → GameTooManyClickError（2026-11-19 19:15 实测
        # log/error/1795086937321）。这里每次点击前清记录 + 上限 6 次后退出。
        max_click = 6
        for _ in range(max_click):
            self.screenshot()
            if not self.appear(self.I_RS_LEVEL_MAX):
                break
            clear_click_record(self.device, self.I_RS_LEVEL_MAX)
            self.appear_then_click(self.I_RS_LEVEL_MAX, interval=0.5)
        else:
            logger.warning(f'满级筛选点了 {max_click} 次仍然在，放弃取消筛选')
        logger.info('Unset all shikigami max lv')

    def set_shikigami(self, shikigami_order: int = 7, stop_image: RuleImage = None):
        """
        要求在式神育成的界面
        选择式神 1-7
        :param stop_image:  结束的图片，如果不出现就结束
        :param shikigami_order:
        :return:
        """
        # 选择式神
        _click_match = {1: self.C_SHIKIGAMI_LEFT_1,
                        2: self.C_SHIKIGAMI_LEFT_2,
                        3: self.C_SHIKIGAMI_LEFT_3,
                        4: self.C_SHIKIGAMI_LEFT_4,
                        5: self.C_SHIKIGAMI_LEFT_5,
                        6: self.C_SHIKIGAMI_LEFT_6,
                        7: self.C_SHIKIGAMI_LEFT_7}
        own_realm = stop_image is self.I_RS_NO_ADD
        empty_slots = (
            self.I_DETECT_EMPTY_1, self.I_DETECT_EMPTY_2, self.I_DETECT_EMPTY_3,
            self.I_DETECT_EMPTY_4, self.I_DETECT_EMPTY_5, self.I_DETECT_EMPTY_6,
        )
        orders = [shikigami_order, 6]
        if own_realm:
            orders.extend(range(7, 0, -1))
        candidates = iter(dict.fromkeys(orders))
        order = next(candidates)
        attempts = 0
        last_vacancies = 0
        next_click = 0.0
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            self.screenshot()

            if (self.appear_then_click(self.I_U_CONFIRM_SMALL, interval=0.5)
                    or self.appear_then_click(self.I_U_CONFIRM_ALTERNATE, interval=1.5)
                    or self.appear_then_click(self.I_UI_CONFIRM, interval=1)
                    or self.appear_then_click(self.I_UI_CONFIRM_SAMLL, interval=1)):
                next_click = time.monotonic() + 1.8
                continue

            vacancies = sum(bool(self.appear(rule)) for rule in empty_slots) if own_realm else 0
            if not vacancies:
                vacancies = int(bool(self.appear(stop_image)))
            if not vacancies:
                if time.monotonic() < next_click:
                    continue
                logger.info(f'育成式神选择完成（最后选择位置 {order}）')
                return

            if self.appear_then_click(self.I_U_CIRCLE_ALTERNATE, interval=2.5):
                next_click = time.monotonic() + 1.8
                continue

            if attempts == 1:
                if vacancies < last_vacancies:
                    attempts = 2
                elif '正在寄养中' in self.O_FOSTERED_SHIKIGAMI.ocr(self.device.image):
                    logger.info(f'位置 {order} 的式神正在寄养，尝试其他位置')
                    attempts = 2
            if time.monotonic() < next_click:
                continue

            if attempts >= 2:
                order = next(candidates, None)
                if order is None:
                    if own_realm:
                        logger.warning('当前分类可见的候选式神未补满育成位置')
                        return
                    raise GameStuckError('指定位置和后备位置的式神均无法寄养')
                attempts = 0
            # 合法重复：同一个育成位（C_SHIKIGAMI_LEFT_7 等）可能要连点几次才被游戏
            # 接受（会弹确认框、或该位式神正在寄养中），所以点击前清掉该控制名的
            # 点击记录；每个位置的尝试次数由上面的 attempts（>=2 换下一个位置）与
            # 120 秒 deadline 双重限制，不会变成「点了没反应还狂点」。
            clear_click_record(self.device, _click_match[order])
            self.click(_click_match[order])
            attempts += 1
            last_vacancies = vacancies
            next_click = time.monotonic() + 1.8
        raise GameStuckError('式神选择超时（120 秒）')

    def detect_no_shikigami(self) -> bool:
        if not self.in_shikigami_growth(screenshot=True):
            return False
        return any(self.appear(slot) for slot in (
            self.I_DETECT_EMPTY_1, self.I_DETECT_EMPTY_2, self.I_DETECT_EMPTY_3,
            self.I_DETECT_EMPTY_4, self.I_DETECT_EMPTY_5, self.I_DETECT_EMPTY_6,
        ))
