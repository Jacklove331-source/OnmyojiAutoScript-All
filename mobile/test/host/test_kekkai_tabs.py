"""KekkaiUtilize 好友/跨区页签切换：坐标 + 亮度启发式的单元测试（不需要设备）。

跑法（工作副本根目录）：
    set PYTHONPATH=<workspace>\\.host-deps
    python test\\host\\test_kekkai_tabs.py android\\app\\src\\main\\python

背景：旧实现用 I_UTILIZE_FRIEND_GROUP / I_UTILIZE_ZONES_GROUP 的 coord() 去点，
实测点 5 次页签都不高亮（run 8aa4258c / e4876c53）。新实现改成
「量出来的固定坐标 (270,136) / (382,136) + 两块矩形平均亮度比判断选中态」。
"""
import inspect
import pathlib
import sys
import unittest

import numpy as np

PY = pathlib.Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(PY))

ROI_FRIEND = (244, 112, 96, 48)
ROI_ZONES = (356, 112, 96, 48)


def frame(friend_bright, zones_bright, size=(720, 1280, 3)):
    img = np.zeros(size, dtype=np.uint8)
    x, y, w, h = ROI_FRIEND
    img[y:y + h, x:x + w] = friend_bright
    x, y, w, h = ROI_ZONES
    img[y:y + h, x:x + w] = zones_bright
    return img


class TabSwitchTests(unittest.TestCase):
    def _make_task(self, frames):
        from tasks.KekkaiUtilize.script_task import ScriptTask

        class Device:
            def __init__(self):
                self.image = None
                self.clicks = []

            def click(self, x, y, control_check=True, control_name="Click"):
                self.clicks.append((x, y, control_name))

            def click_record_remove(self, name):
                return 1

            def screenshot(self, timeout=5.0):
                return self.image

        task = ScriptTask.__new__(ScriptTask)
        task.device = Device()
        task._frames = list(frames)
        task.screenshot = lambda *a, **k: setattr(task.device, "image", task._pop())
        return task

    # 让每帧序列循环使用最后一个
    def _attach(self, task, frames):
        queue = list(frames)

        def _pop():
            return queue.pop(0) if len(queue) > 1 else queue[0]

        task._pop = _pop
        return task

    def test_already_on_friend_tab_needs_no_click(self):
        task = self._make_task([])
        self._attach(task, [frame(200, 60)])
        self.assertTrue(task.switch_friend_list(self._friend(), timeout=3))
        self.assertEqual(task.device.clicks, [])

    def test_switches_to_zones_then_stops(self):
        task = self._make_task([])
        # 只有「点击发生之后」才切到跨区高亮的画面，模拟真实点击→高亮的过程
        before, after = frame(200, 60), frame(60, 200)
        task.screenshot = lambda *a, **k: setattr(
            task.device, "image", after if task.device.clicks else before)
        self.assertTrue(task.switch_friend_list(self._zones(), timeout=5))
        self.assertEqual(task.device.clicks[0][:2], (382, 136))

    def test_gives_up_after_five_taps(self):
        task = self._make_task([])
        self._attach(task, [frame(200, 60)])
        self.assertFalse(task.switch_friend_list(self._zones(), timeout=30))
        self.assertEqual(len(task.device.clicks), 5)
        self.assertTrue(all(c[1] == 136 for c in task.device.clicks))

    @staticmethod
    def _friend():
        from tasks.KekkaiUtilize.script_task import SelectFriendList
        return SelectFriendList.SAME_SERVER

    @staticmethod
    def _zones():
        from tasks.KekkaiUtilize.script_task import SelectFriendList
        return SelectFriendList.DIFFERENT_SERVER


class SourceContractTests(unittest.TestCase):
    def test_no_longer_depends_on_utilize_group_templates(self):
        from tasks.KekkaiUtilize import script_task as module

        source = inspect.getsource(module)
        start = source.index("def switch_friend_list")
        end = source.index("def ", source.index("return True", start))
        body = source[start:end]
        self.assertIn("TAB_FRIEND = (270, 136)", body)
        self.assertIn("TAB_ZONES = (382, 136)", body)
        self.assertNotIn("check_image.coord()", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
