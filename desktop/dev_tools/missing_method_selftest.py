# This Python file uses the following encoding: utf-8

"""dev_tools/missing_method_check.py 的离线自检。

覆盖报告 F-1 的回归：
- 同一文件里**其它类**的 self.xxx() 不能被算到 ScriptTask 头上（183 条误报的根因）；
- 真实调用不存在的方法必须仍然报出来（证明判定没被放宽）；
- 协作式 mixin（self 是组合后的任务实例）、MRO 继承、实例属性、嵌套类都不误报；
- 真实仓库 63 个 script_task 全量跑一遍必须是 problems 0。

用法：``python dev_tools/missing_method_selftest.py``
退出码 0 表示全过。
"""

import contextlib
import io
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / 'dev_tools')):
    if path not in sys.path:
        sys.path.insert(0, path)

from missing_method_check import check  # noqa: E402


class Checker:
    def __init__(self) -> None:
        self.total = 0
        self.passed = 0
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = '') -> None:
        self.total += 1
        if ok:
            self.passed += 1
            print(f'  PASS  {name}')
        else:
            self.failures.append(f'{name}: {detail}')
            print(f'  FAIL  {name}  {detail}')

    def summary(self) -> bool:
        print('')
        print(f'MISSING METHOD SELFTEST: {"PASSED" if not self.failures else "FAILED"}')
        print(f'TOTAL {self.total} PASS {self.passed} FAIL {len(self.failures)}')
        for failure in self.failures:
            print(f'  - {failure}')
        return not self.failures


def _module(source: str):
    """把一段源码写进临时模块并返回 (path, module_name)；用完由调用方清理。"""
    folder = Path(tempfile.mkdtemp(prefix='mm_selftest_'))
    name = 'mm_case_' + uuid.uuid4().hex[:10]
    path = folder / f'{name}.py'
    path.write_text(source, encoding='utf-8')
    sys.path.insert(0, str(folder))
    return folder, path, name


def _cleanup(folder: Path, name: str) -> None:
    sys.modules.pop(name, None)
    try:
        sys.path.remove(str(folder))
    except ValueError:
        pass
    # import 会生成 __pycache__，Windows 上偶尔拒绝删除；残留临时目录无害，不因此失败
    shutil.rmtree(folder, ignore_errors=True)


def _problems(source: str) -> list:
    folder, path, name = _module(source)
    try:
        return check(path, module_name=name, root=ROOT)
    finally:
        _cleanup(folder, name)


HELPER_AND_TASK = '''
class OcrHelper:
    """同文件里的 OCR 辅助类：它的 self.current_page 属于它自己。"""

    def current_page(self):
        return 'page_main'

    def _strict_full_ocr(self, image):
        return []

    def detect_and_ocr(self, image):
        return []

    def probe(self):
        # 这三个调用在 F-1 里被错误算成了 ScriptTask 缺失的方法
        page = self.current_page()
        rows = self._strict_full_ocr(None)
        return page, rows, self.detect_and_ocr(None)


class ScriptTask:
    def run(self):
        return 'ok'
'''


REAL_MISSING = '''
class ScriptTask:
    def run(self):
        return self.really_missing_method()
'''


MIXIN_METHOD = '''
class HelperMixin:
    def shared_helper(self):
        return 1


class ScriptTask(HelperMixin):
    def run(self):
        return self.shared_helper()
'''


COOPERATIVE_MIXIN = '''
class TaskBase:
    def get_task_name(self):
        return 'Dummy'


class RecoveryMixin:
    """协作式 mixin：假设 self 是组合后的任务实例。"""

    def tick(self):
        return self.get_task_name()


class ScriptTask(RecoveryMixin, TaskBase):
    def run(self):
        return self.tick()
'''


NESTED_CLASS = '''
class ScriptTask:
    class Nested:
        def peek(self):
            return self.only_in_nested()

    def run(self):
        return None
'''


INSTANCE_ATTR = '''
class ScriptTask:
    def __init__(self):
        self.callback = None

    def run(self):
        if self.callback is not None:
            return self.callback()
        return None
'''


def main() -> int:
    c = Checker()
    print('[F-1 跨类误报]')
    problems = _problems(HELPER_AND_TASK)
    c.check('同文件其它类的 self 调用不算到 ScriptTask', problems == [], f'{problems}')
    names = ' '.join(message for _, message in problems)
    for target in ('current_page', '_strict_full_ocr', 'detect_and_ocr'):
        c.check(f'误报消失: {target}', target not in names, names)

    print('\n[真缺失仍然报出]')
    problems = _problems(REAL_MISSING)
    c.check('缺少的方法被报出', len(problems) == 1, f'{problems}')
    if problems:
        where, message = problems[0]
        c.check('报错信息含方法名', 'really_missing_method' in message, message)
        c.check('报错信息含调用者类', 'called in ScriptTask' in message, message)
        c.check('报错位置指向源码文件', where.endswith('.py'), where)

    print('\n[不该误报的结构]')
    c.check('MRO 继承的方法通过', _problems(MIXIN_METHOD) == [], f'{_problems(MIXIN_METHOD)}')
    c.check('协作式 mixin 通过（self 是组合实例）',
            _problems(COOPERATIVE_MIXIN) == [], f'{_problems(COOPERATIVE_MIXIN)}')
    c.check('嵌套类的 self 不算外层类', _problems(NESTED_CLASS) == [], f'{_problems(NESTED_CLASS)}')
    c.check('实例属性赋值不算缺方法', _problems(INSTANCE_ATTR) == [], f'{_problems(INSTANCE_ATTR)}')

    print('\n[真实仓库]')
    files = sorted(ROOT.glob('tasks/*/script_task.py'))
    c.check('扫到全部任务', len(files) >= 60, f'{len(files)} 个')
    total_problems = []
    # import 任务模块会打印 OAS 的 logger 横幅；扫描结果走返回值，这里吞掉噪声保持输出可解析
    with contextlib.redirect_stdout(io.StringIO()):
        for path in files:
            total_problems.extend(check(path))
    c.check('真实仓库 problems 0', total_problems == [], f'{total_problems[:5]}')
    c.check('报告 F-1 的三个方法与线上任务无关',
            all(target not in ' '.join(m for _, m in total_problems)
                for target in ('current_page', '_strict_full_ocr', 'detect_and_ocr')),
            f'{total_problems[:5]}')
    return 0 if c.summary() else 1


if __name__ == '__main__':
    sys.exit(main())
