# This Python file uses the following encoding: utf-8

"""`_read_level()` 等级缓存兜底的行为回归测试（纯假对象）。

实战证据：棋盘日志里 `Chess level OCR invalid: []` 出现 7 次，每次都直接导致
`Chess lineup capacity unavailable: level=None` → 「停止本轮上阵」。单帧 OCR
抖动不该让整轮失去上阵机会，因此等级读取失败时用本局最后一次确认值兜底。

覆盖：
1. 正常读中文等级（六阶 -> 6）
2. 正常读阿拉伯数字（7 -> 7）
3. OCR 返回空串时用缓存兜底（六阶 -> 6）
4. 从没读成功过时仍然返回 None（不猜测）
5. 新等级会覆盖缓存（六阶 -> 八阶）
6. 新局重置缓存（模拟 `_start_chess_game`）

用法：`.\\toolkit\\python.exe dev_tools\\chess_level_cache_check.py`
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import re  # noqa: E402

from module.logger import logger  # noqa: E402
from tasks.Chess.runtime.round_state import ChessRoundStateMixin  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


class FakeOcr:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def ocr(self, image):
        self.calls += 1
        return self.text


class FakeImage:
    shape = (720, 1280, 3)


class FakeState(ChessRoundStateMixin):
    def __init__(self, text):
        self.O_LEVEL = FakeOcr(text)
        self.device = type('D', (), {'image': FakeImage()})()
        self._current_round_no = 1

    def _normalize_ocr_text(self, text):
        return ''.join(str(text or '').split())

    # 只测等级读取，回避父类其它依赖
    def _read_level(self):
        return super()._read_level()


def level_of(text, state=None):
    state = state or FakeState(text)
    return ChessRoundStateMixin._read_level(state), state


def test_chinese():
    level, _ = level_of('六阶')
    check('中文等级: 六阶 -> 6', level == 6, str(level))


def test_digit():
    level, _ = level_of('7')
    check('数字等级: 7 -> 7', level == 7, str(level))


def test_cache_fallback():
    state = FakeState('六阶')
    first = ChessRoundStateMixin._read_level(state)
    state.O_LEVEL.text = ''            # 下一帧 OCR 抖动
    second = ChessRoundStateMixin._read_level(state)
    check('首次读取: 六阶 -> 6', first == 6, str(first))
    check('OCR 空串: 用缓存兜底 -> 6', second == 6, str(second))


def test_no_cache():
    state = FakeState('')
    level = ChessRoundStateMixin._read_level(state)
    check('从未读成功: 返回 None 不猜测', level is None, str(level))


def test_cache_update():
    state = FakeState('六阶')
    ChessRoundStateMixin._read_level(state)
    state.O_LEVEL.text = '八阶'
    upgraded = ChessRoundStateMixin._read_level(state)
    state.O_LEVEL.text = ''
    fallback = ChessRoundStateMixin._read_level(state)
    check('升级后缓存更新: 八阶 -> 8', upgraded == 8, str(upgraded))
    check('升级后兜底用新值: -> 8', fallback == 8, str(fallback))


def test_new_game_reset():
    state = FakeState('七阶')
    ChessRoundStateMixin._read_level(state)
    # 模拟 _start_chess_game() 的重置
    state._last_confirmed_level = None
    state.O_LEVEL.text = ''
    level = ChessRoundStateMixin._read_level(state)
    check('新局重置缓存: OCR 失败返回 None', level is None, str(level))


def test_regex_consistency():
    """确认 `re` 从等级文本取数字的行为没被改坏。"""
    check('正则: 八阶里没有阿拉伯数字', re.search(r'([1-9])', '八阶') is None)


def main():
    for case in (
        test_chinese,
        test_digit,
        test_cache_fallback,
        test_no_cache,
        test_cache_update,
        test_new_game_reset,
        test_regex_consistency,
    ):
        print(f'--- {case.__name__}')
        case()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('CHESS LEVEL CACHE: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
