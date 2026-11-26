# -*- coding: utf-8 -*-
"""E 类杂项离线回归（2026-11-17~20 OAS 错误大修复）。

一键跑：
    .\\toolkit\\python.exe dev_tools\\fix_misc_test.py

覆盖的 7 条：
1. GBK 控制台下 logger 写含 emoji 的日志不再抛 UnicodeEncodeError
   （子进程 PYTHONIOENCODING=gbk 断言，同时证明裸 GBK 流仍会抛，说明用例是有效的）；
2. 全仓非 GBK 字面量扫描：列出还有哪些 emoji/非 GBK 字符会进 logger；
3. 日志管道被关闭（BrokenPipeError / WinError 232）：判流状态 + 降级写文件日志，不打断任务；
4. DailyTrifles「日常」页签多形态识别 + 一次重试 + 明确 WARNING（不再 raise 整段不领取）；
5. AbyssShadows「切换狭间区域超时」降级为跳过本区域；
6. MetaDemon「进入活动超时」降级为跳过玩法 + set_next_run 推迟；
7. ai_recovery：恢复后重新截帧 / 连续帧判定（正反用例）、非法动作丢弃、预算耗尽 soft-fail。

全部使用假帧/假设备/假客户端，不 adb、不启动 OAS 实例。
"""
from __future__ import annotations

import ast
import io
import logging
import os
import re
import subprocess
import sys
import tokenize
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CHILD_FLAG = '--emoji-child'
SKIP_DIR_NAMES = {'.git', '__pycache__', 'log', 'toolkit', 'node_modules', '.idea', '.vscode'}


# ---------------------------------------------------------------------------
# 1) GBK 控制台 + emoji：子进程断言
# ---------------------------------------------------------------------------

def _emoji_child() -> int:
    """子进程体：在 PYTHONIOENCODING=gbk 下写 emoji 日志，必须不抛。"""
    from module.logger import logger

    logger.info('📋 扫描好友列表: same_server')          # 历史崩溃点 KekkaiUtilize:601
    logger.warning('⚠️ 结界卡值[67]低于所有预设')        # 历史崩溃点 KekkaiUtilize:590
    logger.info('\U0001f4cb 多形态页签测试')
    logger.hr('E 类冒烟', level=1)                       # 走 logger.rule/print 通道
    logger.print('📊 打印通道 emoji 测试')

    raw_raises = False
    try:
        sys.stdout.write('📋')
        sys.stdout.flush()
    except UnicodeEncodeError:
        raw_raises = True
    print(f'CHILD_RAW_RAISES={raw_raises}')
    print(f'CHILD_ENCODING={sys.stdout.encoding}')
    print(f'CHILD_LOG_FILE={logger.log_file}')
    print('CHILD_OK')
    return 0


def run_emoji_child(timeout: int = 180):
    """在 GBK 控制台环境下跑子进程，返回 (returncode, stdout, stderr, log_file)。"""
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'gbk'
    env['PYTHONUTF8'] = '0'
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), CHILD_FLAG],
                          cwd=str(REPO), env=env, capture_output=True, timeout=timeout)
    stdout = proc.stdout.decode('gbk', 'replace')
    stderr = proc.stderr.decode('gbk', 'replace')
    match = re.search(r'CHILD_LOG_FILE=(\S+)', stdout)
    log_file = (REPO / match.group(1).lstrip('./')) if match else None
    return proc.returncode, stdout, stderr, log_file


# ---------------------------------------------------------------------------
# 2) 全仓非 GBK 字面量扫描
# ---------------------------------------------------------------------------

def _iter_python_files():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [name for name in dirs
                   if name not in SKIP_DIR_NAMES and not name.startswith('.')]
        for name in files:
            if name.endswith('.py'):
                yield Path(root) / name


def scan_non_gbk_literals():
    """扫描仓库里不能编码成 GBK 的字符串字面量。

    返回 (risky, others)：
    - risky：出现在 logger/print 调用行上的（会进日志，是真正的风险点）；
    - others：其它位置的非 GBK 字面量（多数是界面文案，仅供参考）。
    """
    risky, others = [], []
    for path in _iter_python_files():
        try:
            source = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            continue
        lines = source.splitlines()
        for token in tokens:
            if token.type != tokenize.STRING:
                continue
            try:
                value = ast.literal_eval(token.string)
            except (ValueError, SyntaxError):
                # f-string（Python 3.10 里 literal_eval 解析不了）直接按源码文本判断；
                # 我们只关心「这个字面量里有没有 GBK 编不出来的字符」。
                value = token.string
            if not isinstance(value, str) or not value:
                continue
            bad = sorted({char for char in value if not _gbk_ok(char)})
            if not bad:
                continue
            lineno = token.start[0]
            text = lines[lineno - 1] if lineno <= len(lines) else ''
            entry = {
                'file': path.relative_to(REPO).as_posix(),
                'line': lineno,
                'chars': ' '.join(f'U+{ord(char):04X}' for char in bad),
                'sample': value[:40],
                'in_logger': ('logger' in text) or ('.print(' in text),
            }
            (risky if entry['in_logger'] else others).append(entry)
    return risky, others


def _safe_print(text: str) -> None:
    """控制台是 GBK 时，打印 emoji 本身就会抛；报告里改成占位符再打印。"""
    encoding = (sys.stdout.encoding or 'utf-8')
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        text = text.encode(encoding, 'replace').decode(encoding, 'replace')
    print(text)


def _gbk_ok(char: str) -> bool:
    try:
        char.encode('gbk')
        return True
    except UnicodeEncodeError:
        return False


# ---------------------------------------------------------------------------
# 3) 测试用例
# ---------------------------------------------------------------------------

class LoggerEncodingTests(unittest.TestCase):
    """GBK 控制台 / 关闭的日志管道。"""

    def test_emoji_logging_survives_gbk_console(self):
        code, stdout, stderr, log_file = run_emoji_child()
        self.assertEqual(code, 0, f'子进程退出码 {code}\nstdout={stdout}\nstderr={stderr[-2000:]}')
        self.assertIn('CHILD_OK', stdout)
        self.assertIn('gbk', stdout.lower())
        # 用例有效性：裸 GBK 流写 emoji 仍然会抛（说明这次修复发生在日志层而不是把测试绕过去）
        self.assertIn('CHILD_RAW_RAISES=True', stdout)
        # 文件日志（UTF-8）必须保留原文，不能静默丢字
        self.assertIsNotNone(log_file, '子进程没有输出日志文件路径')
        self.assertTrue(log_file.exists(), f'日志文件不存在：{log_file}')
        content = log_file.read_text(encoding='utf-8', errors='replace')
        self.assertIn('📋', content, '文件日志里 emoji 丢了，说明日志层静默替换了原文')
        self.assertIn('扫描好友列表', content)

    def test_closed_pipe_is_guarded_and_degrades_to_file(self):
        from module.logger import FlutterHandler, logger, set_func_logger

        calls = {'n': 0}

        def dead_send(message):
            calls['n'] += 1
            raise BrokenPipeError(232, '管道正在被关闭。')

        set_func_logger(dead_send, is_open=lambda: True)
        try:
            logger.info('管道关闭后的日志：仍然要写进文件 #1')
            logger.hr('管道关闭后的分节', level=0)      # 走 logger.print，历史崩溃点
            logger.info('管道关闭后的日志：仍然要写进文件 #2')
        finally:
            pass
        self.assertEqual(calls['n'], 1, '管道写失败后不应反复重试')
        streams = [h.console.file for h in list(logger.handlers)
                   if isinstance(h, FlutterHandler)]
        self.assertTrue(any(getattr(stream, 'broken', False) for stream in streams),
                        'FlutterLogStream 没有标记 broken')
        log_file = Path(getattr(logger, 'log_file', '') or '')
        self.assertTrue(log_file.exists(), '降级目标文件日志不存在')
        content = log_file.read_text(encoding='utf-8', errors='replace')
        self.assertIn('管道关闭后的日志：仍然要写进文件 #2', content)
        self.assertIn('降级为仅写文件日志', content)

        # 管道已经明确关闭（is_open=False）时，连一次都不该尝试写
        closed_calls = {'n': 0}

        def never_send(message):
            closed_calls['n'] += 1
            raise AssertionError('is_open=False 时不应该再调用管道 sink')

        set_func_logger(never_send, is_open=lambda: False)
        logger.info('管道已关闭：这条只写文件')
        self.assertEqual(closed_calls['n'], 0)

    def test_non_gbk_scan_lists_logger_risk(self):
        risky, others = scan_non_gbk_literals()
        kekkai = [item for item in risky if item['file'] == 'tasks/KekkaiUtilize/script_task.py']
        self.assertTrue(kekkai, '扫描没有发现 KekkaiUtilize 里进 logger 的非 GBK 字符')
        self.assertTrue(any('📋' in item['sample'] for item in kekkai),
                        f'扫描没找到 📋：{kekkai[:4]}')
        print('\n[扫描] 会进 logger 的非 GBK 字面量（需要关注）:')
        for item in risky:
            _safe_print(f"  - {item['file']}:{item['line']} {item['chars']} | {item['sample']}")
        print(f'[扫描] 其它（非 logger 行）非 GBK 字面量 {len(others)} 处，示例:')
        for item in others[:8]:
            _safe_print(f"  - {item['file']}:{item['line']} {item['chars']} | {item['sample']}")


class RecordingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class DailyTriflesTabTests(unittest.TestCase):
    """DailyTrifles「日常」页签多形态识别。"""

    def setUp(self):
        from tasks.DailyTrifles import store_ui

        self.store_ui = store_ui
        self._orig_rule = store_ui.DAILY_GIFT_TAB

    def tearDown(self):
        self.store_ui.DAILY_GIFT_TAB = self._orig_rule

    def _fake_rule(self, rows):
        class _Rule:
            roi = (1160, 75, 105, 540)

            def detect_and_ocr(self, image):
                return rows
        return _Rule()

    @staticmethod
    def _row(text, x, y):
        box = [(x, y), (x + 50, y), (x + 50, y + 20), (x, y + 20)]
        return SimpleNamespace(ocr_text=text, box=box, score=0.95)

    def _task(self, rows, select_result=False):
        from tasks.DailyTrifles.script_task import ScriptTask

        self.store_ui.DAILY_GIFT_TAB = self._fake_rule(rows)
        clicks = []

        class _Fake(ScriptTask):
            def __init__(self):
                self.device = SimpleNamespace(image=object(),
                                              click=lambda **kw: clicks.append(kw))

            def screenshot(self):
                return self.device.image

            def select_daily_gift_tab(self):  # 避免递归进真实 OCR 流程
                return ScriptTask.select_daily_gift_tab(self)

        task = _Fake()
        task.clicks = clicks
        task.select_calls = {'n': 0}

        def _select(instance):
            task.select_calls['n'] += 1
            return select_result

        self._patch_select(_select)
        return task

    def _patch_select(self, func):
        import tasks.DailyTrifles.script_task as module
        self._orig_select = module.select_daily_gifts
        module.select_daily_gifts = func
        self.addCleanup(setattr, module, 'select_daily_gifts', self._orig_select)

    def test_ocr_tabs_parses_fullscreen_coordinates(self):
        task = self._task([self._row('日常特权', 10, 400)])
        tabs = task.ocr_gift_tabs()
        self.assertEqual(len(tabs), 1)
        text, cx, cy = tabs[0]
        self.assertEqual(text, '日常特权')
        self.assertEqual(cx, 1160 + 10 + 25)
        self.assertEqual(cy, 75 + 400 + 10)

    def test_event_tab_variant_is_clicked_with_fuzzy_match(self):
        # 活动插入了「限时/购物节」，还把「日常」挤到下面并粘成「日常特权」
        rows = [self._row('限时', 10, 30), self._row('购物节', 10, 120),
                self._row('日常特权', 10, 400)]
        task = self._task(rows)
        self.assertTrue(task.select_daily_gift_tab())
        self.assertEqual(task.select_calls['n'], 1)      # 精确匹配失败一次
        self.assertEqual(len(task.clicks), 1)
        self.assertEqual(task.clicks[0]['control_name'], 'GIFT_TAB_DAILY')

    def test_retry_once_when_first_round_fails(self):
        task = self._task([self._row('限时', 10, 30)])
        self.assertFalse(task.select_daily_gift_tab())
        # 精确一次 + 模糊一次 + 重试后的精确一次 + 模糊一次
        self.assertGreaterEqual(task.select_calls['n'], 2)

    def test_run_store_sign_warns_instead_of_raising(self):
        from module.logger import logger
        import tasks.DailyTrifles.script_task as module

        handler = RecordingHandler()
        logger.addHandler(handler)

        class _Fake(module.ScriptTask):
            def __init__(self):
                self.device = SimpleNamespace(image=object(), click=lambda **kw: None)
                self.entered = []

            def goto_page(self, page, **kwargs):
                self.entered.append(page)

            def screenshot(self):
                return self.device.image

            def select_daily_gift_tab(self):
                return False

            def appear(self, *args, **kwargs):
                return False

        task = _Fake()
        try:
            task.run_store_sign()   # 修复前这里会 raise ScriptError
        finally:
            logger.removeHandler(handler)
        messages = [record.getMessage() for record in handler.records]
        self.assertTrue(any('日常' in message and '跳过' in message for message in messages),
                        f'没有 WARNING 说明跳过原因：{messages[-6:]}')


class AbyssShadowsDegradeTests(unittest.TestCase):
    """切换狭间区域超时 → 跳过本区域，不抛错拖死任务。"""

    def test_switch_area_timeout_is_degraded(self):
        from module.exception import ScriptError
        from tasks.AbyssShadows.script_task import AreaType, ScriptTask

        class _Fake(ScriptTask):
            def __init__(self):
                self.closes = 0

            def screenshot(self):
                return None

            def appear(self, *args, **kwargs):
                return False

            def appear_then_click(self, *args, **kwargs):
                return False

            def check_current_area(self):
                return AreaType.DRAGON

            def change_area(self, area):
                raise ScriptError('切换狭间区域超时，停止重复点击')

            def close_area_selector(self):
                self.closes += 1
                raise ScriptError('关闭狭间区域选择框超时')

        task = _Fake()
        result = task.switch_area()          # 不抛异常
        self.assertFalse(result)
        self.assertGreaterEqual(task.closes, 1, '没有尝试关闭区域选择框')

    def test_area_recognition_timeout_is_degraded(self):
        from module.exception import ScriptError
        from tasks.AbyssShadows.script_task import ScriptTask

        class _Fake(ScriptTask):
            def __init__(self):
                self.closes = 0

            def screenshot(self):
                return None

            def appear(self, *args, **kwargs):
                return False

            def appear_then_click(self, *args, **kwargs):
                return False

            def check_current_area(self):
                raise ScriptError('狭间当前区域识别超时，停止重复切换')

            def close_area_selector(self):
                return None

        self.assertFalse(_Fake().switch_area())


class MetaDemonDegradeTests(unittest.TestCase):
    """进入活动超时 → 跳过玩法 + set_next_run 推迟。"""

    def test_enter_timeout_skips_and_postpones(self):
        from module.exception import ScriptError, TaskEnd
        from tasks.MetaDemon.hzw_2026 import Hzw2026

        class _Fake(Hzw2026):
            def __init__(self):
                self.next_run = []
                self.returned = 0

            def hzw_enter_trial(self):
                raise ScriptError('祸至无日：进入活动超时，停止本轮')

            def hzw_normal_challenge(self):
                raise AssertionError('入口超时后不应继续挑战')

            def hzw_return_main(self):
                self.returned += 1

            def set_next_run(self, **kwargs):
                self.next_run.append(kwargs)

        task = _Fake()
        with self.assertRaises(TaskEnd):
            task.run_hzw_2026()
        self.assertEqual(len(task.next_run), 1)
        self.assertFalse(task.next_run[0].get('success'))
        self.assertTrue(task.next_run[0].get('finish'))
        self.assertEqual(task.returned, 1, '跳过后应尽力回庭院')

    def test_challenge_timeout_skips_and_postpones(self):
        from module.exception import ScriptError, TaskEnd
        from tasks.MetaDemon.hzw_2026 import Hzw2026

        class _Fake(Hzw2026):
            def __init__(self):
                self.next_run = []

            def hzw_enter_trial(self):
                return None

            def hzw_normal_challenge(self):
                raise ScriptError('祸至无日：准备普通挑战超时，停止本轮')

            def hzw_return_main(self):
                return None

            def set_next_run(self, **kwargs):
                self.next_run.append(kwargs)

        task = _Fake()
        with self.assertRaises(TaskEnd):
            task.run_hzw_2026()
        self.assertFalse(task.next_run[0].get('success'))


class _FakeImage:
    size = 1


class FrameAdapter:
    """假适配器：按顺序返回页面识别结果，并统计截图/刷新次数。"""

    def __init__(self, frames, known, page_before=''):
        self.frames = list(frames)
        self.known = list(known)
        self.index = 0
        self.refreshes = 0
        self.screenshots = 0
        self.page_before = page_before

    def refresh_page(self):
        self.refreshes += 1
        page = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return page

    def current_page(self):
        return self.frames[0]

    def known_pages(self):
        return self.known

    def screenshot(self):
        self.screenshots += 1
        return _FakeImage()

    def sleep(self, seconds):
        return None


class VerificationTests(unittest.TestCase):
    """ai_recovery 恢复验证：新鲜帧 + 连续帧判定。"""

    def setUp(self):
        from module.ai_recovery.selftest import base_config
        self.cfg = base_config()
        self.known = ['page_main', 'page_duel', 'page_battle', 'page_reward']

    def _executed(self):
        from module.ai_recovery.actions import ActionRecord, ExecResult
        return ExecResult(True, [ActionRecord('click', True)])

    def _anomaly(self, **kwargs):
        from module.ai_recovery.types import Anomaly
        return Anomaly(type=kwargs.pop('type', 'device_stuck'), page=kwargs.pop('page', 'page_main'),
                       evidence=kwargs.pop('evidence', {}), **kwargs)

    def test_recovery_frames_are_fresh_and_timestamped(self):
        from module.ai_recovery.verification import verify_recovery
        adapter = FrameAdapter(['page_battle', 'page_duel', 'page_duel'], self.known)
        proof = verify_recovery(self.cfg, adapter, self._anomaly(page='page_battle'),
                                self._executed(), page_before='page_battle')
        self.assertTrue(proof.verified)
        self.assertEqual(proof.reason, 'stable_page_transition')
        self.assertGreaterEqual(adapter.refreshes, 2, '每一帧都要重新识别，不能沿用旧帧')
        self.assertGreaterEqual(len(proof.frames), 2)
        for frame in proof.frames:
            self.assertRegex(frame['at'], r'^\d{2}:\d{2}:\d{2}$')
            self.assertEqual(frame['source'], 'refresh_page')

    def test_page_transition_counts_as_progress(self):
        # 1795092647822（EvoZone）：battle_prepare → battle → reward 都在变，旧实现判失败
        from module.ai_recovery.verification import verify_recovery
        adapter = FrameAdapter(['page_battle', 'page_reward', 'page_duel'], self.known)
        proof = verify_recovery(self.cfg, adapter, self._anomaly(type='unknown_page', page='page_main'),
                                self._executed(), page_before='page_main')
        self.assertTrue(proof.verified)
        self.assertEqual(proof.reason, 'page_advancing')

    def test_expected_page_reached_short_circuits(self):
        from module.ai_recovery.verification import verify_recovery
        adapter = FrameAdapter(['page_battle', 'page_duel'], self.known)
        proof = verify_recovery(self.cfg, adapter, self._anomaly(page='page_battle'),
                                self._executed(), page_before='page_battle',
                                expected_page='page_duel')
        self.assertTrue(proof.verified)
        self.assertEqual(proof.reason, 'expected_page_reached')
        self.assertEqual(proof.page_after, 'page_duel')

    def test_all_frames_unrecognized_still_fails(self):
        from module.ai_recovery.verification import verify_recovery
        adapter = FrameAdapter(['unknown', '', 'loading'], self.known)
        proof = verify_recovery(self.cfg, adapter, self._anomaly(type='unknown_page', page='unknown'),
                                self._executed(), page_before='unknown')
        self.assertFalse(proof.verified, '所有帧都识别不出页面时必须判失败')
        self.assertEqual(proof.reason, 'page_not_freshly_recognized')

    def test_same_page_without_progress_still_fails(self):
        from module.ai_recovery.verification import verify_recovery
        adapter = FrameAdapter(['page_main', 'page_main', 'page_main'], self.known)
        proof = verify_recovery(self.cfg, adapter, self._anomaly(type='device_stuck', page='page_main'),
                                self._executed(), page_before='page_main')
        self.assertFalse(proof.verified, '页面没变又没有进度证据时不能算恢复')
        self.assertEqual(proof.reason, 'same_page_still_stalled')


class ActionSchemaTests(unittest.TestCase):
    """非法动作逐条丢弃，不否掉整次诊断。"""

    def _payload(self, actions):
        return {
            'page': 'page_main', 'page_known': True, 'confidence': 0.9,
            'cause': 'test', 'risk': 'low', 'actions': actions,
        }

    def test_invalid_action_is_dropped_not_fatal(self):
        from module.ai_recovery.schema import parse_diagnosis
        diagnosis = parse_diagnosis(self._payload([
            {'type': 'back'},
            {'type': 'click', 'y': 0.5},                 # 缺 x，历史报错 $.actions[1].x
            {'type': 'teleport'},                        # 不在白名单
        ]))
        self.assertEqual([action.type for action in diagnosis.actions], ['back'])
        self.assertEqual(len(diagnosis.dropped_actions), 2)
        self.assertIn('x/y', diagnosis.dropped_actions[0]['reasons'][0])
        self.assertIn('enum', diagnosis.dropped_actions[1]['reasons'][0])

    def test_payload_level_errors_still_fail(self):
        from module.ai_recovery.schema import SchemaError, parse_diagnosis
        payload = self._payload('not-a-list')
        with self.assertRaises(SchemaError):
            parse_diagnosis(payload)


class BudgetSoftFailTests(unittest.TestCase):
    """预算耗尽 → 明确建议 + soft-fail，不伪报成功。"""

    def test_per_hour_budget_is_soft_fail(self):
        from module.ai_recovery.orchestrator import AiRecovery
        from module.ai_recovery.selftest import base_config
        from module.ai_recovery.types import Anomaly, Outcome

        recovery = AiRecovery.__new__(AiRecovery)
        recovery.cfg = base_config()
        result = recovery._budget_skip(Anomaly(type='device_stuck', task='Dummy'), 'per_hour_calls=12')
        self.assertEqual(result.outcome, Outcome.SKIPPED)
        self.assertTrue(result.soft_fail)
        self.assertEqual(result.retry_after_s, 3600.0)
        self.assertIn('预算耗尽', result.message)
        self.assertIn('建议', result.message)
        self.assertNotEqual(result.outcome, Outcome.RECOVERED)

    def test_per_day_budget_suggests_next_day(self):
        from module.ai_recovery.orchestrator import AiRecovery
        from module.ai_recovery.selftest import base_config
        from module.ai_recovery.types import Anomaly

        recovery = AiRecovery.__new__(AiRecovery)
        recovery.cfg = base_config()
        result = recovery._budget_skip(Anomaly(type='device_stuck', task='Dummy'), 'per_day_calls=60')
        self.assertEqual(result.retry_after_s, 86400.0)
        self.assertTrue(result.soft_fail)


def main() -> int:
    os.chdir(REPO)
    if CHILD_FLAG in sys.argv:
        return _emoji_child()
    print(f'仓库根：{REPO}')
    print(f'解释器：{sys.executable} ({sys.version.split()[0]})')
    print('说明：全部为离线回归（假帧/假设备/假客户端），不会 adb 或启动 OAS 实例。\n')
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print('')
    print(f'FIX MISC TEST: {"PASSED" if result.wasSuccessful() else "FAILED"}')
    print(f'TOTAL {result.testsRun} FAIL {len(result.failures)} ERROR {len(result.errors)}')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
