# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from time import sleep
from typing import Union

from module.atom.click import RuleClick
from module.atom.long_click import RuleLongClick
from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import ScriptError
from module.logger import logger

from tasks.base_task import BaseTask
from tasks.GameUi.assets import GameUiAssets
from tasks.Component.GeneralInvite.assets import GeneralInviteAssets
from tasks.Component.GeneralInvite.config_invite import InviteConfig, InviteNumber, FindMode
from tasks.Component.SwitchSoul.assets import SwitchSoulAssets
from tasks.Component.SwitchSoul.click_guard import clear_click_record



def switch_parser(switch_str: str) -> tuple:
    switch_list = switch_str.split(',')
    if len(switch_list) != 2:
        raise ValueError('Switch_str must be 2 length')
    return int(switch_list[0]), int(switch_list[1])


class SwitchSoul(BaseTask, SwitchSoulAssets):

    def should_switch_soul(self, enabled=True):
        """队伍预设开启时统一处理御魂，跳过独立装配及其页面往返。"""
        from tasks.Component.preset_team import uses_linked_preset
        return bool(enabled) and not uses_linked_preset(self)

    def goto_shikigami_records(self, button):
        """
        进入式神录
        """
        while 1:
            self.screenshot()
            if self.appear(GameUiAssets.I_CHECK_RECORDS):
                break
            if self.appear_then_click(button, interval=1.5):
                continue
        logger.info('Entry shikigami records')

    def exit_shikigami_records(self) -> None:
        """
        退出式神录的界面
        :return:
        """
        while 1:
            self.screenshot()
            if not self.appear(self.I_SOU_CHECK_IN):
                break
            if self.appear_then_click(self.I_RECORD_SOUL_BACK, interval=3.5):
                continue
        logger.info('Exit shikigami records')


    def run_switch_soul(self, target: tuple | list[tuple] | str):
        from tasks.Component.preset_team import uses_linked_preset
        if uses_linked_preset(self):
            logger.info('已启用队伍预设：御魂随该队伍一键应用，跳过独立御魂配置')
            return

        """
        保证在式神录的界面
        :return:
        """
        if isinstance(target, str):
            try:
                target = switch_parser(target)
            except ValueError:
                logger.error('Switch soul config error')
                return
        self.click_preset()
        self.switch_souls(target)

    def click_preset(self) -> None:
        """
        点击预设
        :return:
        """
        from tasks.Component.preset_team import record_preset_visible
        while 1:
            self.screenshot()
            if self.appear(self.I_SOU_SWITCH_1):
                break
            if self.appear(self.I_SOU_SWITCH_2):
                break
            if self.appear(self.I_SOU_SWITCH_3):
                break
            if self.appear(self.I_SOU_SWITCH_4):
                break
            if record_preset_visible(self):
                break
            if self.appear(self.I_SOUL_PRESET):
                self.click(self.I_SOUL_PRESET, interval=3)
                continue
        logger.info('Click preset in switch soul')

    def switch_soul_one(self, group: int, team: int) -> None:
        """装配任意正整数序号的预设御魂；按实际滚动位移定位。"""
        from copy import copy
        from tasks.Component.preset_list import (
            IndexedPresetList, SOUL_GROUPS, SOUL_TEAMS,
            foreground_rule, preset_index,
        )
        group, team = preset_index(group), preset_index(team)
        panel = lambda: foreground_rule(self, self.I_SOU_TEAM_PRESENT)
        navigator = IndexedPresetList(self, panel)
        navigator.select(group, SOUL_GROUPS)
        _, center_y = navigator.locate(team, SOUL_TEAMS)
        rules = (self.I_SOU_SWITCH_1, self.I_SOU_SWITCH_2, self.I_SOU_SWITCH_3,
                 self.I_SOU_SWITCH_4, self.I_SOU_CLICK_PRESENT)
        for attempt in range(3):
            self.screenshot()
            if not panel():
                raise ScriptError('御魂预设面板被遮挡，停止装配')
            for original in rules:
                target = copy(original)
                target.roi_front = list(original.roi_front)
                target.roi_back = (950, max(132, center_y - 28), 65,
                                   min(60, 625 - max(132, center_y - 28)))
                target.name = f'SOUL_PRESET_{group}_{team}'
                if self.appear_then_click(target, interval=1):
                    # 预设更换确认弹窗「即将为全队式神更换预设御魂」存在淡入延迟，
                    # 直接调 ui_click_until_disappear 会在首帧未出现时立刻退出导致漏点。
                    from module.base.timer import Timer
                    confirm_wait = Timer(2.5).start()
                    while not confirm_wait.reached():
                        self.screenshot()
                        if self.appear(self.I_UI_CONFIRM):
                            self.ui_click_until_disappear(self.I_UI_CONFIRM, timeout=8)
                            break
                        sleep(0.25)
                    logger.info(f'Switch soul_one group {group} team {team}')
                    return
            sleep(.5)
        raise ScriptError(f'第{group}组第{team}队未找到御魂装配按钮，停止选择')

    def switch_souls(self, target: tuple or list[tuple]) -> None:
        """
        切换御魂
        :param target: [(1, 1), (2, 2), (3, 3), (4, 4)]  或者是单独的一个元组(4, 4) 第一个是组, 第二个是队伍
        :return:
        """
        if isinstance(target, tuple):
            target = [target]
        for group, team in target:
            group = int(group)
            team = int(team)
            self.switch_soul_one(group, team)

    def run_switch_soul_by_name(self, groupName, teamName):
        from tasks.Component.preset_team import uses_linked_preset
        if uses_linked_preset(self):
            logger.info('已启用队伍预设：御魂随该队伍一键应用，跳过独立御魂配置')
            return

        """
        保证在式神录的界面
        :return:
        """
        if isinstance(groupName, str) and isinstance(teamName, str):
            self.click_preset()
            self.switch_soul_by_name(groupName, teamName)

    def _find_soul_name(self, rule, name, swipe, description, max_swipe: int = 12):
        """在分组/队伍列表里向下翻页查找 `name`。

        为什么这里的重复是**合法**的：分组/队伍列表比一屏长，翻到目标必须用同一个
        滑动控制名（`ss_group_swipe_down` / `ss_team_swipe_up`）连续翻页，次数由列表
        长度决定，而不是「点不动还狂点」。但设备层 `Device.handle_control_check()`
        把 swipe 和 click 记在同一份 click_record 里，同一个名字累计 10 次就抛
        GameTooManyClickError（2026-11-19 19:29 实测：`ss_group_swipe_down` 连滑
        第 10 屏整局失败，见 log/error/1795087758461）。
        所以每次滑动前清掉该控制名的记录，并给一个自身翻页上限（真滑不动就报错退出，
        不靠吞异常）。
        """
        previous = None
        unchanged = 0
        for _ in range(max_swipe):
            self.screenshot()
            names = tuple(result.ocr_text for result in rule.detect_and_ocr(self.device.image))
            if name in names:
                return
            unchanged = unchanged + 1 if names == previous else 0
            if unchanged >= 2:
                raise ScriptError(f'御魂{description}列表未找到「{name}」')
            previous = names
            clear_click_record(self.device, swipe)
            self.swipe(swipe)
            sleep(1.5)
        raise ScriptError(f'御魂{description}列表滑动 {max_swipe} 次仍未找到「{name}」')

    def switch_soul_by_name(self, groupName, teamName):
        """
        保证在式神录的界面
        :return:
        """
        logger.hr('Switch soul by name')
        # 滑动至分组最上层
        # 合法重复：回到列表顶部必须连续滑动同一个 `ss_group_swipe_up`，次数由当前
        # 滚动位置决定；滑动前清掉该控制名的点击记录，避免被当成连点熔断。
        last_group_text = ''
        for _ in range(20):
            self.screenshot()
            compare1 = self.O_SS_GROUP_NAME.detect_and_ocr(self.device.image)
            now_group_text = str([result.ocr_text for result in compare1])
            if now_group_text == last_group_text:
                break
            clear_click_record(self.device, self.S_SS_GROUP_SWIPE_UP)
            self.swipe(self.S_SS_GROUP_SWIPE_UP, 2)
            sleep(2.5)
            last_group_text = now_group_text
        logger.info('Swipe to top of group')

        self._find_soul_name(self.O_SS_GROUP_NAME, groupName, self.S_SS_GROUP_SWIPE_DOWN, '分组')
        logger.info('Swipe down to find target group')

        # 选中分组
        while 1:
            self.screenshot()
            self.O_SS_GROUP_NAME.keyword = groupName
            if self.ocr_appear_click(self.O_SS_GROUP_NAME, exact=True):
                break
        logger.info(f'Select group {groupName}')

        # 滑动至阵容最上层
        # 同分组：回顶部是合法连续滑动，滑动前清掉该控制名的点击记录。
        last_team_text = ''
        for _ in range(20):
            self.screenshot()
            compare1 = self.O_SS_TEAM_NAME.detect_and_ocr(self.device.image)
            now_team_text = str([result.ocr_text for result in compare1])
            # 向上滑动
            if now_team_text == last_team_text:
                break
            clear_click_record(self.device, self.S_SS_TEAM_SWIPE_DOWN)
            self.swipe(self.S_SS_TEAM_SWIPE_DOWN, 1.5)
            sleep(2)
            last_team_text = now_team_text
        logger.info('Swipe to top of team')

        self._find_soul_name(self.O_SS_TEAM_NAME, teamName, self.S_SS_TEAM_SWIPE_UP, '队伍')
        logger.info('Swipe up to find target team')

        # 选中分组
        while 1:
            self.screenshot()
            self.O_SS_TEAM_NAME.keyword = teamName
            if self.ocr_appear_click(self.O_SS_TEAM_NAME, exact=True):
                break
        logger.info(f'Select team {teamName}')
        # 切换御魂
        cnt_click: int = 0
        self.O_SS_TEAM_NAME.keyword = teamName
        while 1:
            self.screenshot()
            if cnt_click >= 4:
                break
            if self.appear_then_click(self.I_UI_CONFIRM, interval=0.8):
                continue
            if self.ocr_appear_click_by_rule(self.O_SS_TEAM_NAME, self.I_SOU_CLICK_PRESENT, interval=1.5):
                cnt_click += 1
                continue
        logger.info(f'Switch soul_one group {groupName} team {teamName}')

    def ocr_appear_click_by_rule(self,
                                 target: RuleOcr,
                                 action: Union[RuleClick, RuleLongClick] = None,
                                 interval: float = None,
                                 duration: float = None) -> bool:
        """
        ocr识别目标，如果目标存在，则触发动作
        :param target:
        :param action:
        :param interval:
        :param duration:
        :return:
        """
        appear = self.ocr_appear(target, interval)

        if not appear:
            return False

        x1, y1, w1, h1 = target.area
        x, y = action.coord()

        self.device.click(x=x, y=y1, control_name=target.name)
        return True


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device

    c = Config('oas1')
    d = Device(c)
    s = SwitchSoul(c, d)

    s.click_preset()
    # s.switch_soul_one(4, 1)
    # s.switch_soul_by_name('契灵', '茨球')
    s.switch_soul_by_name('默认分组', '队伍5')
