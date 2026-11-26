# This Python file uses the following encoding: utf-8
# @brief    Ryou Dokan Toppa (阴阳竂道馆突破功能)
# @author   jackyhwei
# @note     draft version without full test
# github    https://github.com/roarhill/oas

from datetime import datetime, timedelta
import random
import numpy as np
import time
import re
from enum import Enum
from cached_property import cached_property
from time import sleep

from tasks.Component.GeneralBattle.config_general_battle import GeneralBattleConfig
from tasks.Component.SwitchSoul.switch_soul import SwitchSoul
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.config_base import ConfigBase, Time
from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main, page_kekkai_toppa, page_shikigami_records, page_guild
from tasks.RealmRaid.assets import RealmRaidAssets

from module.logger import logger
from module.exception import TaskEnd, ScriptError
from module.atom.image_grid import ImageGrid
from module.base.utils import point2str
from module.base.timer import Timer
from module.exception import GamePageUnknownError
from pathlib import Path
from tasks.AbyssShadows.config import AbyssShadows
from tasks.AbyssShadows.assets import AbyssShadowsAssets

class AreaType:
    """ 暗域类型 """
    DRAGON = AbyssShadowsAssets.I_ABYSS_DRAGON  # 神龙暗域
    PEACOCK = AbyssShadowsAssets.I_ABYSS_PEACOCK  # 孔雀暗域
    FOX = AbyssShadowsAssets.I_ABYSS_FOX  # 白藏主暗域
    LEOPARD = AbyssShadowsAssets.I_ABYSS_LEOPARD # 黑豹暗域

    @cached_property
    def name(self) -> str:
        """

        :return:
        """
        return Path(self.file).stem.upper()

    def __str__(self):
        return self.name

    __repr__ = __str__

class EmemyType(Enum):

    """ 敌人类型 """
    BOSS = 1  #  首领
    GENERAL = 2  #  副将
    ELITE = 3  #  精英


class CilckArea:
    """ 点击区域 """
    GENERAL_1 = AbyssShadowsAssets.C_GENERAL_1_CLICK_AREA
    GENERAL_2 = AbyssShadowsAssets.C_GENERAL_2_CLICK_AREA
    ELITE_1 = AbyssShadowsAssets.C_ELITE_1_CLICK_AREA
    ELITE_2 = AbyssShadowsAssets.C_ELITE_2_CLICK_AREA
    ELITE_3 = AbyssShadowsAssets.C_ELITE_3_CLICK_AREA
    BOSS= AbyssShadowsAssets.C_BOSS_CLICK_AREA

    @cached_property
    def name(self) -> str:
        """

        :return:
        """
        return Path(self.file).stem.upper()

    def __str__(self):
        return self.name

    __repr__ = __str__


class ScriptTask(GeneralBattle, GameUi, SwitchSoul, AbyssShadowsAssets):
    

    boss_fight_count = 0  # 首领战斗次数
    general_fight_count = 0  # 副将战斗次数
    elite_fight_count = 0  # 精英战斗次数
    
    def run(self):
        """ 狭间暗域主函数

        :return:
        """
        cfg: AbyssShadows = self.config.abyss_shadows

        if self.should_switch_soul(cfg.switch_soul_config.enable):
            self.goto_page(page_shikigami_records)
            self.run_switch_soul(cfg.switch_soul_config.switch_group_team)
        if self.should_switch_soul(cfg.switch_soul_config.enable_switch_by_name):
            self.goto_page(page_shikigami_records)
            self.run_switch_soul_by_name(cfg.switch_soul_config.group_name, cfg.switch_soul_config.team_name)
        today = datetime.now().weekday()
        if today not in [4, 5, 6]:
            logger.info(f"Today is not abyss shadows day, exit")
            # 设置下次运行时间为本周五
            self.custom_next_run(task='AbyssShadows', custom_time=cfg.abyss_shadows_time.custom_run_time_friday, time_delta=4-today)
            raise TaskEnd
        success = True
        # 进入狭间
        self.goto_abyss_shadows()
        # Select a region with a verified progress badge; sealed regions cannot
        # be recognized reliably by their old vertical title templates.
        if not any(self.select_boss(area) for area in
                   (AreaType.DRAGON, AreaType.PEACOCK, AreaType.FOX, AreaType.LEOPARD)):
            logger.info('狭间区域均已封印，结束本轮')
            self.close_area_selector()
            self.goto_main()
            self.record_calendar_completion()
            next_time = (cfg.abyss_shadows_time.custom_run_time_friday if today == 6 else
                         cfg.abyss_shadows_time.custom_run_time_sunday if today == 5 else
                         cfg.abyss_shadows_time.custom_run_time_saturday)
            self.custom_next_run(task='AbyssShadows', custom_time=next_time, time_delta=5 if today == 6 else 1)
            raise TaskEnd
        
        # 等待可进攻时间  
        self.device.stuck_record_add('BATTLE_STATUS_S')
        # 集结中图片
        self.wait_until_disappear(self.I_WAIT_TO_START)
        self.device.stuck_record_clear()

        # 未开启智能伤害准备攻打精英、副将、首领
        if not cfg.abyss_shadows_combat_time.CombatTime_enable:
            while 1:
                # 点击战报按钮
                find_list = [EmemyType.BOSS, EmemyType.GENERAL, EmemyType.ELITE]
                for enemy_type in find_list:
                    # 寻找敌人并开始战斗,
                    if not self.find_enemy(enemy_type):
                        logger.warning(f"Failed to find {enemy_type.name} enemy, exit")
                        break
                logger.info(f"Current fight times: boss {self.boss_fight_count} times, general {self.general_fight_count}  times, elite {self.elite_fight_count} times")
                # 正常应该打完一个区域了，检查攻打次数，如没打够则切换到下一个区域，默认神龙 -> 孔雀 -> 白藏主 -> 黑豹
                if self.boss_fight_count >= 2 and self.general_fight_count >= 4 and self.elite_fight_count >= 6:
                    success = True
                    break
                else:
                    #切换区域之前关闭战报
                    self.appear_then_click(self.I_ABYSS_MAP_EXIT, interval=1)
                    try:
                        current_area = self.check_current_area()
                        logger.info(f"Current area is {current_area}, switch to next area")
                    except ScriptError as error:
                        # 区域识别超时不让整个任务抛错：交给 switch_area 走「保留当前区域」降级
                        logger.warning(f'狭间当前区域识别失败（将跳过区域切换）：{error}')
                    if not self.switch_area():
                        logger.warning("All enemy types have been defeated, but not enough emeny to fight, exit")
                        break

        # 开启智能伤害
        if cfg.abyss_shadows_combat_time.CombatTime_enable:
            while True:
                # 1. 先攻打 1 个 BOSS
                if self.boss_fight_count < 2:
                    self.boss_fight_count = self.fight_and_switch(EmemyType.BOSS, 2, self.boss_fight_count,
                                                             lambda: self.switch_area())

                # 2. 攻打 2 个 GENERAL
                if self.general_fight_count < 4:
                    self.general_fight_count = self.fight_and_switch(EmemyType.GENERAL, 4, self.general_fight_count,
                                                                lambda: self.switch_area())

                # 3. 攻打 3 个 ELITE
                if self.elite_fight_count < 6:
                    self.elite_fight_count = self.fight_and_switch(EmemyType.ELITE, 6, self.elite_fight_count,
                                                              lambda: self.switch_area())

                # 检查是否已完成所有任务
                print(f"Current fight times: boss {self.boss_fight_count} times, general {self.general_fight_count} times, elite {self.elite_fight_count} times")
                if self.boss_fight_count >= 2 and self.general_fight_count >= 4 and self.elite_fight_count >= 6:
                    logger.info("All fights completed")
                    success = True
                    break
                else:
                    #没打满我也没办法就最后一张图，看看有没有剩余的吧没有也不想跑了
                    find_list = [EmemyType.BOSS, EmemyType.GENERAL, EmemyType.ELITE]
                    for enemy_type in find_list:
                        # 寻找敌人并开始战斗,
                        if not self.find_enemy(enemy_type):
                            logger.warning(f"Failed to find {enemy_type.name} enemy, exit")
                            break
                    logger.info(f"Current fight times: boss {self.boss_fight_count} times, general {self.general_fight_count}  times, elite {self.elite_fight_count} times")
                    logger.warning("All enemy types have been defeated, but not enough emeny to fight, exit")
                    success = True
                    break

        # 保持好习惯，一个任务结束了就返回到庭院，方便下一任务的开始
        self.goto_main()

        # 设置下次运行时间
        if success:
            print("我要重新设置时间了")
            self.record_calendar_completion()
            if today == 4:
                # 周五推迟到周六
                logger.info(f"The next abyss shadows day is Saturday")
                self.custom_next_run(task='AbyssShadows', custom_time=cfg.abyss_shadows_time.custom_run_time_saturday, time_delta=1)
            elif today == 5:
                # 周六推迟到周日
                logger.info(f"The next abyss shadows day is Sunday")
                self.custom_next_run(task='AbyssShadows', custom_time=cfg.abyss_shadows_time.custom_run_time_sunday, time_delta=1)
            elif today == 6:
                # 周日推迟到下周五
                logger.info(f"The next abyss shadows day is Friday")
                self.custom_next_run(task='AbyssShadows', custom_time=cfg.abyss_shadows_time.custom_run_time_friday, time_delta=5)
        else:
            self.set_next_run(task='AbyssShadows', finish=True, server=True, success=False)
        raise TaskEnd



    #攻击并进行区域切换
    def fight_and_switch(self, enemy_type, required_count, fight_count, next_area_func):
        while fight_count < required_count: # 0-2
            if not self.find_enemy(enemy_type):
                logger.warning(f"Failed to find {enemy_type.name} enemy, exit")
                return fight_count
            else:
                if enemy_type == EmemyType.BOSS:
                    fight_count += 1
                elif enemy_type == EmemyType.GENERAL:
                    fight_count += 2
                elif enemy_type == EmemyType.ELITE:
                    fight_count += 3
            logger.info(
                f"Current fight times: boss {self.boss_fight_count} times, general {self.general_fight_count} times, elite {self.elite_fight_count} times")

            # 完成攻打后切换区域
            current_area = self.check_current_area()
            if fight_count < required_count and current_area != AreaType.LEOPARD:
                if not next_area_func():
                    return fight_count
        return fight_count

    def switch_area(self):
        """切换到后续暗域；切不过去时降级跳过本区域，不再抛错拖死整个任务。

        2026-11 复盘（log/error/1795174593027、1795175867800 两次
        「切换狭间区域超时，停止重复点击」）：重复点击保护本身是对的（防止连点熔断），
        但它以 ScriptError 抛出，直接把 AbyssShadows 整轮打断，只有一个区域切不过去而已。
        现在：单个区域失败 → WARNING 记原因 + 关掉选择框 → 试下一个区域；
        全部失败 → 保留当前区域继续打，返回 False。
        """
        self.appear_then_click(self.I_ABYSS_MAP_EXIT, interval=1)
        try:
            current_area = self.check_current_area()
        except ScriptError as error:
            logger.warning(f'狭间当前区域识别失败，跳过本次区域切换（继续当前区域）：{error}')
            self.close_area_selector_safely()
            return False
        logger.info(f"Current area is {current_area}, switch to next area")
        areas = (AreaType.DRAGON, AreaType.PEACOCK, AreaType.FOX, AreaType.LEOPARD)
        skipped = []
        for area in areas[areas.index(current_area) + 1:]:
            try:
                if self.change_area(area):
                    return True
                skipped.append(f'{area.name}: 不可用（无怪或已封印）')
            except ScriptError as error:
                # 单个区域切不过去只记原因并换下一个，不让整个任务失败
                logger.warning(f'切换狭间区域 {area.name} 失败（降级跳过该区域）：{error}')
                skipped.append(f'{area.name}: {error}')
                self.close_area_selector_safely()
        if skipped:
            logger.warning(f'狭间区域切换降级：没有成功切换到后续区域（{skipped}），'
                           f'保留当前区域 {current_area.name} 继续战斗')
        else:
            logger.info('没有后续可用暗域，保留当前区域并结束切换')
        self.close_area_selector_safely()
        return False

    def close_area_selector_safely(self):
        """关掉区域选择框；关不掉只记 WARNING，后续流程仍能自己退出。"""
        try:
            self.close_area_selector()
        except ScriptError as error:
            logger.warning(f'关闭狭间区域选择框失败（继续后续流程）：{error}')



    def check_current_area(self) -> AreaType:
        ''' 获取当前区域
        :return AreaType
        '''
        timer = Timer(15).start()
        while not timer.reached():
            self.screenshot()
            if self.appear(self.I_PEACOCK_AREA):
                return AreaType.PEACOCK
            elif self.appear(self.I_DRAGON_AREA):
                return AreaType.DRAGON
            elif self.appear(self.I_FOX_AREA):
                return AreaType.FOX
            elif self.appear(self.I_LEOPARD_AREA):
                return AreaType.LEOPARD
            else:
                continue
        raise ScriptError('狭间当前区域识别超时，停止重复切换')

    def change_area(self, area_name: AreaType) -> bool:
        ''' 切换到下个区域
        :return 
        '''
        options = ((AreaType.DRAGON, self.C_ABYSS_DRAGON, self.O_AS_DRAGON_PROGRESS),
                   (AreaType.PEACOCK, self.C_ABYSS_PEACOCK, self.O_AS_PEACOCK_PROGRESS),
                   (AreaType.FOX, self.C_ABYSS_FOX, self.O_AS_FOX_PROGRESS),
                   (AreaType.LEOPARD, self.C_ABYSS_LEOPARD, self.O_AS_LEOPARD_PROGRESS))
        option = next((item for item in options if item[0] == area_name), None)
        if option is None:
            raise ScriptError('不支持的狭间区域')
        _, target, progress = option
        timer = Timer(25).start()
        menu_clicks = target_clicks = unreadable = 0
        selected = False
        while not timer.reached():
            self.screenshot()
            # Resolve the foreground selector before checking the old area's
            # title and switch button, both of which remain visible behind it.
            if self.area_selector_open():
                text = str(progress.ocr_single(self.device.image)).strip()
                if '封印' in text:
                    logger.info(f'狭间 {area_name.name} 已封印，跳过')
                    return False
                match = re.fullmatch(r'(100|[1-9]?\d)\s*%', text)
                if not match:
                    unreadable += 1
                    if unreadable >= 3:
                        raise ScriptError(f'狭间 {area_name.name} 进度识别不完整，不点击区域')
                    continue
                if int(match[1]) == 0:
                    return False
                if target_clicks >= 3:
                    raise ScriptError('狭间区域点击三次仍未进入，停止重复点击')
                if self.click(target, interval=2):
                    target_clicks += 1
                    selected = True
                continue
            if selected:
                if (self.appear(self.I_ABYSS_NAVIGATION)
                        and self.check_current_area() == area_name):
                    logger.info(f'已确认切换狭间区域：{area_name.name}')
                    return True
                continue
            if self.appear_then_click(self.I_ABYSS_MAP_EXIT, interval=1):
                continue
            if (self.area_foreground(self.I_CHANGE_AREA)
                    and self.appear(self.I_CHANGE_AREA, interval=3)):
                if menu_clicks >= 3:
                    raise ScriptError('狭间区域选择框连续三次未打开')
                self.click(self.I_CHANGE_AREA)
                menu_clicks += 1
        raise ScriptError('切换狭间区域超时，停止重复点击')

    def area_foreground(self, rule):
        x, y, width, height = map(int, rule.roi_front)
        crop = self.device.image[y:y + height, x:x + width]
        return (crop.shape == rule.image.shape and bool(crop.size)
                and float(crop.mean()) >= float(rule.image.mean()) * .78)

    def area_selector_open(self):
        return (self.appear(self.I_AS_SELECTOR_HINT)
                and self.area_foreground(self.I_AS_SELECTOR_HINT))

    def close_area_selector(self):
        timer = Timer(10).start()
        clicks = 0
        while not timer.reached():
            self.screenshot()
            if not self.area_selector_open():
                return
            if clicks >= 3:
                raise ScriptError('狭间区域选择框关闭失败')
            if self.click(self.I_AS_SELECTOR_HINT, interval=1):
                clicks += 1
        raise ScriptError('关闭狭间区域选择框超时')
    
    def goto_main(self):
        ''' 保持好习惯，一个任务结束了就返回庭院，方便下一任务的开始或者是出错重启
        '''
        logger.info("Exiting abyss_shadows")
        self.goto_page(page_main)

    def goto_abyss_shadows(self) -> bool:
        ''' 进入狭间
        :return bool
        '''
        logger.info("Entering abyss_shadows")
        self.goto_page(page_guild)
        
        while 1:
            self.screenshot()
            # Entering Shenshe can resume an active Abyss Shadows map directly.
            if self.appear(self.I_ABYSS_NAVIGATION):
                logger.info("Already in abyss_shadows")
                break
            # 进入神社
            if self.appear_then_click(self.I_RYOU_SHENSHE,interval=1):
                logger.info("Enter Shenshe")
                continue
            # 查找狭间
            if not self.appear(self.I_ABYSS_SHADOWS, threshold=0.8):
                self.swipe(self.S_TO_ABBSY_SHADOWS,interval=3)
                continue
            # 进入狭间
            if self.appear_then_click(self.I_ABYSS_SHADOWS):
                logger.info("Enter abyss_shadows")
                break
        return True
 
    def select_boss(self, area_name: AreaType) -> bool:
        ''' 选择暗域类型
        :return 
        '''
        return self.change_area(area_name)

    def find_enemy(self, enemy_type: EmemyType) -> bool:
        ''' 寻找敌人,并开始寻路进入战斗
        :return 是否找到敌人，若目标已死亡则返回False，否则返回True
        True 找到敌人，并已经战斗完成
        '''
        print(f"Find enemy: {enemy_type}")
        while 1:
            self.screenshot()
            # 点击战报按钮
            if self.appear(self.I_ABYSS_MAP):
                break
            if self.appear_then_click(self.I_ABYSS_NAVIGATION,interval=1):
                continue
 
        match enemy_type:
            case EmemyType.BOSS: success = self.run_boss_fight()
            case EmemyType.GENERAL: success = self.run_general_fight()
            case EmemyType.ELITE: success = self.run_elite_fight()
            
        return success    

    def run_boss_fight(self) -> bool:
        ''' 首领战斗
        只要进入了战斗都返回成功
        :return 
        '''
        if self.boss_fight_count >= 2:
            logger.info(f"boss fight count {self.boss_fight_count} times, skip")
            return True
        success = True
        logger.info(f"Run boss fight") 
        if self.click_emeny_area(CilckArea.BOSS):
            logger.info(f"Click {CilckArea.BOSS.name}")
            self.run_general_battle_back(Monster_type="BOSS")
            self.boss_fight_count += 1
            logger.info(f'Fight, boss_fight_count {self.boss_fight_count} times')
        else:
            success = False
        return success

    def run_general_fight(self) -> bool:
        ''' 副将战斗
        :return 
        '''
        general_list = [CilckArea.GENERAL_1, CilckArea.GENERAL_2]
        logger.info(f"Run general fight") 
        for general in general_list:
            # 副将战斗次数达到4个时，退出循环
            if self.general_fight_count >= 4:
                logger.info(f"general fight count {self.general_fight_count} times, skip")
                break
            if self.click_emeny_area(general):
                logger.info(f"Click {general.name}")
                self.general_fight_count += 1
                self.run_general_battle_back(Monster_type="GENERAL")
                logger.info(f'Fight, general_fight_count {self.general_fight_count} times')
        return True


    def run_elite_fight(self) -> bool:
        ''' 精英战斗
        :return 
        '''
        elite_list = [CilckArea.ELITE_1, CilckArea.ELITE_2, CilckArea.ELITE_3]
        logger.info(f"Run elite fight")  
        for elite in elite_list:
            # 精英战斗次数达到6个时，退出循环
            if self.elite_fight_count >= 6:
                logger.info(f"Elite fight count {self.elite_fight_count} times, skip")
                break
            if self.click_emeny_area(elite):
                logger.info(f"Click {elite.name}")
                self.elite_fight_count += 1
                self.run_general_battle_back(Monster_type="ELITE")
                logger.info(f'Fight, elite_fight_count {self.elite_fight_count} times')
        return True

    def click_emeny_area(self, click_area: CilckArea) -> bool:
        suceess = True
        ''' 点击敌人区域
        
        :return 
        '''
        logger.info(f"Click emeny area: {click_area.name}")
        # 点击战报
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_ABYSS_NAVIGATION, interval=1.5):
                logger.info(f"Click {self.I_ABYSS_NAVIGATION.name}")
                continue
            if self.appear(self.I_ABYSS_MAP):
                logger.info("Find abyss map, exit")
            
            click_times = 0
            # 点击攻打区域
            while 1:
                self.screenshot()
                # 如果点3次还没进去就表示目标已死亡,跳过
                if click_times >= 3:
                    logger.warning(f"Failed to click {click_area}")
                    return
                # 出现前往按钮就退出
                if self.appear(self.I_ABYSS_GOTO_ENEMY):
                    break
                if self.click(click_area,interval=1.5):
                    click_times += 1
                    continue
                if self.appear_then_click(self.I_ENSURE_BUTTON,interval=1):
                    continue
        
            # 点击前往按钮
            while 1:
                self.screenshot()
                if self.appear_then_click(self.I_ABYSS_GOTO_ENEMY,interval=1):
                    logger.info(f"Click {self.I_ABYSS_GOTO_ENEMY.name}")
                    # 点击敌人后，如果是不同区域会确认框，点击确认                
                    if self.appear_then_click(self.I_ENSURE_BUTTON, interval=1):
                        logger.info(f"Click {self.I_ENSURE_BUTTON.name}")
                    # 跑动画比较花时间
                    sleep(3)
                    continue
                else:
                    break
            
            # 如果遇到点击前往按钮后不动的 bug，则再次尝试进入
            if self.wait_until_appear(self.I_ABYSS_FIRE, wait_time=20):
                break
            logger.warning("Failed to enter fire")
        
        # 点击战斗按钮
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_ABYSS_FIRE,interval=1):
                  
                logger.info(f"Click {self.I_ABYSS_FIRE.name}")        
                # 挑战敌人后，如果是奖励次数上限，会出现确认框   
                if self.appear_then_click(self.I_ENSURE_BUTTON, interval=1):
                    logger.info(f"Click {self.I_ENSURE_BUTTON.name}")
                continue
            if self.appear(self.I_PREPARE_HIGHLIGHT):
                break
            
        return suceess

    def run_general_battle_back(self, Monster_type: str) -> bool:
        """
        重写父类方法，因为狭间暗域的准备和战斗流程不一样
        进入挑战然后直接返回
        :param config:
        :return:
        """
        cfg: AbyssShadows = self.config.abyss_shadows
        preset_selected = False
        while 1:
            #确保进入战斗
            self.screenshot()
            if self.wait_until_appear(self.I_EQUIPPING, wait_time=4):
                if not preset_selected:
                    battle = cfg.general_battle_config
                    self.switch_preset_team(battle.preset_enable, battle.preset_group, battle.preset_team)
                    preset_selected = True
                    self.screenshot()
                self.click(self.I_EQUIPPING, interval=1.5)
            if not self.appear(self.I_EQUIPPING):
                break
        logger.info(f"Click {self.I_EQUIPPING.name}")
        logger.info(f"点击准备了")

        # 进入战斗后，开始计时
        start_time = time.time()
        if cfg.abyss_shadows_combat_time.CombatTime_enable:
            self.device.stuck_record_add('BATTLE_STATUS_S')
            if Monster_type == "BOSS":  # BOSS战斗
                combat_time = cfg.abyss_shadows_combat_time.boss_combat_time
            elif Monster_type == "GENERAL":  # 是副将战斗
                combat_time = cfg.abyss_shadows_combat_time.general_combat_time
            elif Monster_type == "ELITE":  #  精英战斗
                combat_time = cfg.abyss_shadows_combat_time.elite_combat_time
            else:
                combat_time = 60  # 默认为 60 秒
            # 等待设定的战斗时间
            while time.time() - start_time < combat_time:
                self.screenshot()
                if self.appear_then_click(self.I_WIN, interval=1.5):
                    break
            logger.info("Combat time ended, proceeding to exit.")
            self.device.stuck_record_clear()
        # 战斗提前结束这时没有返回按钮
        if self.appear_then_click(self.I_WIN, interval=1.5):
            return True

        # 点击返回
        while 1:
            self.screenshot()
            if self.appear_then_click(self.I_EXIT, interval=2):
                continue
            if self.appear_then_click(self.I_UI_CONFIRM, interval=2):
                continue
            if self.appear_then_click(self.I_WIN, interval=2):
                continue
            if self.appear(self.I_ABYSS_NAVIGATION):
                break
        logger.info(f"Click {self.I_UI_CONFIRM.name}")

        return True



if __name__ == "__main__":
    from module.config.config import Config
    from module.device.device import Device

    config = Config('zhu')
    device = Device(config)
    t = ScriptTask(config, device)
    t.run()
