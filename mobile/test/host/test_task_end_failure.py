"""TaskEnd 失败传播契约：mark_failure() 之后运行器必须记 ok=False。

跑法（工作副本根目录）：
    set PYTHONPATH=<workspace>\\.host-deps
    python test\\host\\test_task_end_failure.py android\\app\\src\\main\\python
"""
import pathlib
import sys
import unittest

PY = pathlib.Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(PY))


class TaskEndResultTests(unittest.TestCase):
    def test_marked_failure_reports_false_with_reason(self):
        from oas_mobile.task_runner import task_end_result

        class Task:
            mobile_failure_reason = '大神签到依赖缺失: oas-checkin-biggod is not installed'

        ok, reason = task_end_result(Task())
        self.assertFalse(ok)
        self.assertIn('大神签到', reason)

    def test_unmarked_task_end_stays_success(self):
        from oas_mobile.task_runner import task_end_result

        class Task:
            pass

        ok, reason = task_end_result(Task())
        self.assertTrue(ok)
        self.assertEqual(reason, '')

    def test_empty_marker_is_not_failure(self):
        from oas_mobile.task_runner import task_end_result

        class Task:
            mobile_failure_reason = ''

        self.assertTrue(task_end_result(Task())[0])


class SourceContractTests(unittest.TestCase):
    def test_base_task_exposes_mark_failure(self):
        text = (PY / "tasks" / "base_task.py").read_text(encoding="utf-8")
        self.assertIn("def mark_failure(self, reason: str) -> None:", text)
        self.assertIn("self.mobile_failure_reason = ''", text)

    def test_biggod_marks_every_hard_failure_branch(self):
        text = (PY / "tasks" / "AutoCheckinBigGod" / "script_task.py").read_text(encoding="utf-8")
        self.assertEqual(text.count("self.mark_failure("), 5)
        for marker in ('大神签到依赖缺失', '未检测到ADB设备', 'Frida Server 启动失败',
                       '无法启动大神 APP', '无法获取大神 Token'):
            self.assertIn(marker, text)

    def test_runner_reads_failure_marker_after_task_end(self):
        text = (PY / "oas_mobile" / "task_runner.py").read_text(encoding="utf-8")
        self.assertIn("except TaskEnd:", text)
        self.assertIn("task_end_result(task)", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
