# This Python file uses the following encoding: utf-8

"""恢复 BaseTask.runframe（Chess frame_cards 依赖）。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'tasks/base_task.py'

METHOD = '''    def runframe(self, target: RuleFrame, *range_results, interval=None, threshold=None) -> FrameResult:
        """读取 frame.data（坐标、裁剪图、文字）；仅 Click 显式 True 点击，其余参数可选比对。"""
        if not isinstance(target, RuleFrame):
            raise ScriptError('runframe 的第一个参数必须是 RuleFrame')
        try:
            return target.run(self, *range_results, interval=interval, threshold=threshold)
        except (TypeError, ValueError) as error:
            raise ScriptError(f'runframe 配置或区域无效: {error}') from error

'''

IMPORTS = '''from module.atom.frame import FrameResult, RuleFrame
from module.exception import ScriptError
'''


def main() -> None:
    text = TARGET.read_text(encoding='utf-8')
    if 'def runframe(' in text:
        print('already has runframe')
        return
    if 'from module.atom.frame import' not in text:
        anchor = 'from module.atom.animate import RuleAnimate\n'
        if anchor in text:
            text = text.replace(anchor, anchor + IMPORTS, 1)
        else:
            text = IMPORTS + text
    marker = '    def set_next_run(self, task: str, finish: bool = False,'
    if marker not in text:
        print('anchor not found')
        return
    text = text.replace(marker, METHOD + marker, 1)
    TARGET.write_text(text, encoding='utf-8')
    print('inserted runframe')


if __name__ == '__main__':
    main()
