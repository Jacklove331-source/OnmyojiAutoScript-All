# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from module.logger import logger
from module.exception import TaskEnd

from tasks.GameUi.game_ui import GameUi
from tasks.GameUi.page import page_main
from tasks.Pets.assets import PetsAssets
from tasks.Pets.config import PetsConfig
from tasks.Component.SwitchSoul.click_guard import clear_click_record, retry_once_then_soft_fail


class ScriptTask(GameUi, PetsAssets):

    def run(self):
        self.goto_page(page_main)
        con: PetsConfig = self.config.pets.pets_config
        # 进入
        # 合法重复：从庭院点「宠物屋/宠物爪印」进宠物页，入口图标会随庭院皮肤和
        # 加载状态变化，所以「点入口 → 再看一眼」要重复几次。这里给 6 轮上限，
        # 每次点击前清掉该控制名的点击记录（设备层把 click/swipe 记在同一份
        # click_record 里，同一名字 10 次就 GameTooManyClickError）。
        entered = False
        for _ in range(6):
            self.screenshot()
            if self.appear(self.I_PET_FEAST):
                entered = True
                break
            if self.appear(self.I_PET_HOUSE):
                clear_click_record(self.device, self.I_PET_HOUSE)
                self.appear_then_click(self.I_PET_HOUSE, interval=1)
                continue
            if self.appear(self.I_PET_CLAW):
                clear_click_record(self.device, self.I_PET_CLAW)
                self.appear_then_click(self.I_PET_CLAW, interval=1)
                continue
        if not entered:
            logger.warning('未能进入宠物页（6 轮点击后仍看不到投喂入口），本次跳过 Pets')
            self.set_next_run(task='Pets', success=False)
            raise TaskEnd('Pets')
        logger.info('Enter Pets')
        # if con.pets_happy:
        #     self._play()
        if con.pets_feast:
            # 熔断（GameTooManyClickError）在旧逻辑里会一路冒到 script.py 变成
            # app stop/start，几次之后实例进程直接 exit(1)。这里改成任务层自愈：
            # 先清记录 + 关一次弹窗重试，仍失败才软失败收尾。
            retry_once_then_soft_fail(self, self._feed, 'Pets')
        self.ui_click(self.I_PET_EXIT, self.I_CHECK_MAIN)

        self.set_next_run(task='Pets', success=True, finish=True)
        raise TaskEnd('Pets')

    def _open_pet_panel(self, entry, panel, description: str, max_click: int = 3) -> bool:
        """点宠物功能入口，直到子面板出现；返回是否打开。

        为什么这里的重复是**合法**的：宠物屋的「投喂 / 玩耍」入口点下去会进一个
        小面板，动画/加载期间点一次可能没反应，重试 1~2 次是正常路径。
        但旧写法用的是 `ui_click()`（内部 `while 1`，没有次数上限）：一旦子面板的
        模板不匹配（改版、被活动浮层挡住），就会对着同一个坐标一直点 ——
        2026-11-19 21:10 实测 `pet_pet_feast` 连点 10 次直接 GameTooManyClickError
        （log/error/1795093831244）。
        这里改成有界循环：每次点击前清掉该控制名的点击记录，最多 max_click 次。
        """
        for _ in range(max_click):
            self.screenshot()
            if self.appear(panel):
                return True
            if self.appear(entry):
                clear_click_record(self.device, entry)
                self.appear_then_click(entry, interval=1)
        self.screenshot()
        if self.appear(panel):
            return True
        logger.warning(f'点了 {max_click} 次「{description}」入口，目标面板仍未出现')
        return False

    def _feed(self):
        """
        投喂
        :return:
        """
        logger.hr('Feed', 3)
        if not self._open_pet_panel(self.I_PET_FEAST, self.I_PET_FEED, '投喂'):
            logger.warning('宠物投喂面板未打开，跳过投喂（不再重复点同一坐标）')
            return
        number = self.O_PET_FEED_AP.ocr(self.device.image)
        if number == 0:
            # 已经投喂过了
            logger.warning('Already feed')
            return
        self.ui_click(self.I_PET_FEED, self.I_PET_SKIP)
        self.wait_until_disappear(self.I_PET_SKIP)

    def _play(self):
        """
        玩耍
        :return:
        """
        logger.hr('Play', 3)
        if not self._open_pet_panel(self.I_PET_HAPPY, self.I_PET_PLAY, '玩耍'):
            logger.warning('宠物玩耍面板未打开，跳过玩耍')
            return
        number = self.O_PET_PLAY_GOLD.ocr(self.device.image)
        if number == 0:
            # 金币不足
            logger.warning('Gold not enough')
            return
        # 点击玩耍三次不出现就退出
        play_count = 0
        while 1:
            self.screenshot()
            if self.appear(self.I_PET_SKIP):
                break
            if play_count >= 3:
                logger.warning('Play count > 3')
                break
            if self.appear_then_click(self.I_PET_PLAY, interval=1):
                play_count += 1
                logger.info(f'Play {play_count}')
                continue
        self.wait_until_disappear(self.I_PET_SKIP)


if __name__ == '__main__':
    from module.config.config import Config
    from module.device.device import Device
    c = Config('oas1')
    d = Device(c)
    t = ScriptTask(c, d)
    t.screenshot()

    t.run()
