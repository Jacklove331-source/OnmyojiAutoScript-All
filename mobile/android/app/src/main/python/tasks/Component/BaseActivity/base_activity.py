# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from abc import abstractmethod

class BaseActivity(GeneralBattle):
    @abstractmethod
    def run(self) -> None:
        pass

    @abstractmethod
    def home_main(self) -> bool:
        """
        从庭院到活动的爬塔界面
        :return:
        """
        pass

    @abstractmethod
    def main_home(self) -> bool:
        """
        从活动的爬塔界面到庭院
        :return:
        """

    @staticmethod
    def verify_zero_ticket(ticket_name, fallback_action) -> bool:
        """门票 OCR 为 0 时执行一次真实入口操作，确认是否真的没票。"""
        from module.logger import logger
        logger.warning(f'{ticket_name} OCR is zero, try one fallback action')
        if fallback_action():
            logger.info(f'{ticket_name} fallback succeeded')
            return True
        logger.info(f'{ticket_name} confirmed unavailable')
        return False
