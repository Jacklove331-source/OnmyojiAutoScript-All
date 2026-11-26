"""SoulsTidy 排序循环必须有出口（防止 O_ST_SORT_LEVEL_1 读不到时死循环）。

跑法（工作副本根目录）：
    set PYTHONPATH=<workspace>\\.host-deps
    python test\\host\\test_soulstidy_sort_exit.py android\\app\\src\\main\\python
"""
import inspect
import pathlib
import sys
import unittest

PY = pathlib.Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(PY))


class SortExitTests(unittest.TestCase):
    def test_sort_loop_has_deadline(self):
        from tasks.SoulsTidy import script_task as module

        source = inspect.getsource(module)
        start = source.index('确保是按照等级来排序的')
        end = source.index("logger.info('Sort by level')", start)
        body = source[start:end]
        # 必须有 deadline 与警告日志，否则 OCR 读不到排序标签时会 while 1 死循环
        self.assertIn('sort_deadline', body)
        self.assertIn('skip sorting and continue', body)
        self.assertIn('datetime.now() >= sort_deadline', body)

    def test_sort_deadline_is_short(self):
        from tasks.SoulsTidy import script_task as module

        source = inspect.getsource(module)
        start = source.index('sort_deadline = datetime.now()')
        line = source[start:source.index('\n', start)]
        self.assertIn('seconds=20', line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
