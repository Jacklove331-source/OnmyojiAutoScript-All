# This Python file uses the following encoding: utf-8

"""修复 BaseActivity.verify_zero_ticket（历史恢复时被截断）。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'tasks/Component/BaseActivity/base_activity.py'

METHOD = '''
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
'''


def main() -> None:
    text = TARGET.read_text(encoding='utf-8')
    marker = '    def verify_zero_ticket('
    index = text.find(marker)
    if index >= 0:
        text = text[:index].rstrip() + '\n'
    if 'def verify_zero_ticket' in text:
        print('already fixed')
        return
    TARGET.write_text(text + METHOD, encoding='utf-8')
    print('patched')


if __name__ == '__main__':
    main()
