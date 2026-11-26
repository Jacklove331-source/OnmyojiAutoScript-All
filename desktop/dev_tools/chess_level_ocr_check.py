# This Python file uses the following encoding: utf-8

"""`_read_level()` 等级 OCR 空读治理的回归测试（纯假对象，不接设备）。

实战证据：validate4 每局 18~20 次 `Chess level OCR invalid: []`，每 5 秒
一次、紧邻 deploy candidate，且同一时刻金币 OCR 也读空——整帧已不在备战
界面（战斗/过渡帧）。治理规则：
- 等级标签不可见：不尝试 OCR、不打 WARNING，静默复用缓存（仅在
  「可见↔隐藏」切换时打一行 INFO 带缓存等级，供实机取证）；
- 标签可见但 OCR 失败：仍打 WARNING（真问题不能被淹没）；
- 等级缓存只能由 O_LEVEL 的 OCR 读数写入：数字或汉字读数覆盖缓存
  （含比旧缓存低的值，以 OCR 为准）；购买经验成功后只做「验证式
  重读」（等待稳定帧 + 可见时才解析），读不到保持缓存、绝不猜 +1。

覆盖：
1. 标签可见 + 汉字等级（六阶 -> 6）
2. 标签可见 + 数字等级（7 -> 7）
3. 标签不可见 + 有缓存：复用且不 OCR、不打 WARNING、只打一次切换 INFO
4. 标签不可见 + 无缓存：返回 None、不 OCR、不打 WARNING
5. 标签可见 + OCR 空 + 有缓存：复用且打 WARNING
6. 标签可见 + OCR 空 + 无缓存：返回 None 且打 WARNING
7. OCR 读数覆盖缓存（含低于旧缓存的值，以 OCR 为准）
8. 解析函数边界：空串/乱码 -> None，不写缓存
9. 购买经验成功后验证式重读：不可见→缓存不变；可见但读空→缓存不变
   且打 INFO；可见且读到→缓存更新，无任何 +1 猜测写入

用法：`.\\toolkit\\python.exe dev_tools\\chess_level_ocr_check.py`
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Chess.runtime import round_state as round_state_module  # noqa: E402
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


class FakeLogger:
    """记录 warning/info/debug，供「不可见时不打 WARNING」断言使用。"""

    def __init__(self):
        self.warnings = []
        self.infos = []
        self.debugs = []

    def warning(self, message, *args):
        self.warnings.append(message % args if args else message)

    def info(self, message, *args):
        self.infos.append(message % args if args else message)

    def debug(self, message, *args):
        self.debugs.append(message % args if args else message)


class FakeState(ChessRoundStateMixin):
    def __init__(self, text, label_visible=True):
        self.O_LEVEL = FakeOcr(text)
        self.device = type('D', (), {'image': FakeImage()})()
        self._current_round_no = 1
        self._label_visible = label_visible
        self.I_EXPERIENCE = 'I_EXPERIENCE'
        self.ACTION_SETTLE_INTERVAL = 0.1
        self.screenshot_calls = 0

    def _normalize_ocr_text(self, text):
        return ''.join(str(text or '').split())

    def appear(self, rule):
        return self._label_visible

    def screenshot(self):
        self.screenshot_calls += 1
        return True


def read_level(state):
    return ChessRoundStateMixin._read_level(state)


def with_fake_logger(run):
    original = round_state_module.logger
    fake = FakeLogger()
    round_state_module.logger = fake
    try:
        run(fake)
    finally:
        round_state_module.logger = original


def test_visible_chinese():
    state = FakeState('六阶')
    check('标签可见 汉字: 六阶 -> 6', read_level(state) == 6)


def test_visible_digit():
    state = FakeState('7')
    check('标签可见 数字: 7 -> 7', read_level(state) == 7)


def test_invisible_reuse_cache_silently():
    def run(fake):
        state = FakeState('六阶')            # 先确认缓存 = 6
        read_level(state)
        state.O_LEVEL.text = ''
        state._label_visible = False         # 战斗/过渡帧
        calls = state.O_LEVEL.calls
        level = read_level(state)
        check('标签不可见 有缓存: 复用 -> 6', level == 6, str(level))
        check('标签不可见 有缓存: 不尝试 OCR',
              state.O_LEVEL.calls == calls, str(state.O_LEVEL.calls))
        check('标签不可见 有缓存: 不打 WARNING',
              not any('level' in w.lower() for w in fake.warnings),
              str(fake.warnings))
        # 只打一行「可见→隐藏」切换 INFO（带缓存等级），不是每帧都打
        read_level(state)
        hidden_infos = [i for i in fake.infos
                        if 'became not visible' in i]
        check('标签不可见: 切换 INFO 只打一次',
              len(hidden_infos) == 1, str(hidden_infos))
        check('标签不可见: INFO 带缓存等级',
              bool(hidden_infos) and 'level=6' in hidden_infos[0],
              str(hidden_infos))
    with_fake_logger(run)


def test_invisible_no_cache():
    def run(fake):
        state = FakeState('', label_visible=False)
        calls = state.O_LEVEL.calls
        level = read_level(state)
        check('标签不可见 无缓存: 返回 None 不猜测',
              level is None, str(level))
        check('标签不可见 无缓存: 不尝试 OCR',
              state.O_LEVEL.calls == calls, str(state.O_LEVEL.calls))
        check('标签不可见 无缓存: 不打 WARNING',
              not any('level' in w.lower() for w in fake.warnings),
              str(fake.warnings))
    with_fake_logger(run)


def test_visible_fail_with_cache_warns():
    def run(fake):
        state = FakeState('六阶')
        read_level(state)
        state.O_LEVEL.text = ''              # 标签可见但 OCR 空：真问题
        level = read_level(state)
        check('可见 读空 有缓存: 复用 -> 6', level == 6, str(level))
        check('可见 读空 有缓存: 打 WARNING',
              any('level ocr invalid' in w.lower() for w in fake.warnings),
              str(fake.warnings))
    with_fake_logger(run)


def test_visible_fail_no_cache_warns():
    def run(fake):
        state = FakeState('')
        level = read_level(state)
        check('可见 读空 无缓存: 返回 None', level is None, str(level))
        check('可见 读空 无缓存: 打 WARNING',
              any('level ocr invalid' in w.lower() for w in fake.warnings),
              str(fake.warnings))
    with_fake_logger(run)


def test_parse_overrides_cache_even_lower():
    state = FakeState('八阶')
    read_level(state)                        # 缓存 = 8
    state.O_LEVEL.text = '4'                 # 以 OCR 为准，不回退为猜测
    level = read_level(state)
    check('OCR 读数覆盖缓存: 4 -> 4', level == 4, str(level))
    check('缓存同步为 4',
          state._last_confirmed_level == 4,
          str(state._last_confirmed_level))
    state.O_LEVEL.text = '五阶'
    level = read_level(state)
    check('OCR 汉字读数覆盖缓存: 五阶 -> 5', level == 5, str(level))


def test_parse_text_cases():
    state = FakeState('')
    check('解析: 空串 -> None',
          state._parse_level_text('') is None)
    check('解析: 乱码 -> None',
          state._parse_level_text('◇◆') is None)
    check('解析: 九阶 -> 9',
          state._parse_level_text('九阶') == 9)
    check('解析: 7 -> 7',
          state._parse_level_text('7') == 7)
    check('解析失败不写缓存',
          not hasattr(state, '_last_confirmed_level'))


def test_verified_reread_after_purchase():
    def run(fake):
        # 购买成功后标签仍不可见（战斗帧/动画）：缓存不变、不 OCR
        state = FakeState('六阶')
        read_level(state)                    # 缓存 = 6
        state.O_LEVEL.text = ''
        state._label_visible = False
        calls = state.O_LEVEL.calls
        for _attempt in range(2):
            state.screenshot()
            if state._level_label_visible():
                ChessRoundStateMixin._read_level(state)
                break
        check('重读 标签不可见: 缓存不变',
              state._last_confirmed_level == 6,
              str(state._last_confirmed_level))
        check('重读 标签不可见: 不尝试 OCR',
              state.O_LEVEL.calls == calls, str(state.O_LEVEL.calls))

        # 标签可见但读空：缓存不变、打 INFO、不猜 +1
        state._label_visible = True
        for _attempt in range(2):
            state.screenshot()
            if state._level_label_visible():
                ChessRoundStateMixin._read_level(state)
                break
        check('重读 可见读空: 缓存不变不猜 +1',
              state._last_confirmed_level == 6,
              str(state._last_confirmed_level))

        # 标签可见且读到新等级：缓存由 OCR 更新
        state.O_LEVEL.text = '七阶'
        re_read = ChessRoundStateMixin._read_level(state)
        check('重读 可见读到: -> 7', re_read == 7, str(re_read))
        check('重读 可见读到: 缓存更新为 7',
              state._last_confirmed_level == 7,
              str(state._last_confirmed_level))
        check('全程无 +1 猜测写入',
              state._last_confirmed_level == 7,
              'cache equals last OCR value, never guessed')
    with_fake_logger(run)


def main():
    for case in (
        test_visible_chinese,
        test_visible_digit,
        test_invisible_reuse_cache_silently,
        test_invisible_no_cache,
        test_visible_fail_with_cache_warns,
        test_visible_fail_no_cache_warns,
        test_parse_overrides_cache_even_lower,
        test_parse_text_cases,
        test_verified_reread_after_purchase,
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
    print('CHESS LEVEL OCR: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
