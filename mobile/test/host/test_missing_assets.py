"""缺图规则必须降级而不是崩任务：available / available_rules 契约测试。

跑法（工作副本根目录）：
    set PYTHONPATH=<workspace>\\.host-deps
    python test\\host\\test_missing_assets.py android\\app\\src\\main\\python
"""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(ROOT))


class AvailableRuleTests(unittest.TestCase):
    def test_existing_template_reports_available(self):
        from module.atom.image import RuleImage

        rule = RuleImage(roi_front=(0, 0, 10, 10), roi_back=(0, 0, 10, 10),
                         method="Template matching", threshold=0.8,
                         file="./tasks/SixRealms/gate1/gate1_skill101.png")
        self.assertTrue(rule.available)

    def test_missing_template_reports_unavailable_without_raising(self):
        from module.atom.image import RuleImage

        rule = RuleImage(roi_front=(0, 0, 10, 10), roi_back=(0, 0, 10, 10),
                         method="Template matching", threshold=0.8,
                         file="./tasks/SixRealms/gate1/gate1_skill103.png")
        self.assertFalse(rule.available)
        # 访问 .available 不能触发 load_image()，否则 fromfile 会抛 FileNotFoundError
        self.assertIsNone(rule._image)

    def test_available_rules_filters_missing_and_keeps_order(self):
        from module.atom.image import RuleImage, available_rules

        def rule(name):
            return RuleImage(roi_front=(0, 0, 10, 10), roi_back=(0, 0, 10, 10),
                             method="Template matching", threshold=0.8,
                             file=f"./tasks/SixRealms/gate1/{name}.png")

        rules = [rule('gate1_skill101'), rule('gate1_skill103'),
                 rule('gate1_skill102'), rule('gate1_skill104')]
        kept = available_rules(rules, label='单元测试')
        self.assertEqual([r.name for r in kept], ['GATE1_SKILL101', 'GATE1_SKILL102'])


class ActivityShikigamiRuleTests(unittest.TestCase):
    def test_pass_8_is_dropped_from_challenge_rotation(self):
        from module.atom.image import available_rules
        from tasks.ActivityShikigami.assets import ActivityShikigamiAssets as A

        rotation = [A.I_PASS_5, A.I_PASS_6, A.I_PASS_7, A.I_PASS_8, A.I_PASS_9]
        self.assertFalse(A.I_PASS_8.available)
        kept = available_rules(rotation, label='活动副本挑战入口')
        self.assertNotIn('I_PASS_8', [r.name for r in kept])
        self.assertEqual(len(kept), 4)

    def test_script_task_uses_available_rules_guard(self):
        source = (ROOT / 'tasks' / 'ActivityShikigami' / 'script_task.py').read_text(
            encoding='utf-8')
        self.assertIn('available_rules(clicks', source)
        self.assertIn('raise TaskEnd(', source)


class WantedQuestsInviteTests(unittest.TestCase):
    def test_friend_avatar_templates_are_reported_missing(self):
        from tasks.WantedQuests.assets import WantedQuestsAssets as A

        names = ('I_WQ_FRIEND_1', 'I_WQ_FRIEND_2', 'I_WQ_FRIEND_3',
                 'I_WQ_FRIEND_4', 'I_WQ_FRIEND_5')
        unavailable = [n for n in names if not getattr(A, n).available]
        self.assertEqual(unavailable, list(names))

    def test_invite_branch_is_guarded(self):
        source = (ROOT / 'tasks' / 'WantedQuests' / 'script_task.py').read_text(encoding='utf-8')
        self.assertIn('def invite_friend_rules_available', source)
        self.assertIn('if not self.invite_friend_rules_available():', source)


class SixRealmsDeadReferenceTests(unittest.TestCase):
    def test_skill103_104_rules_have_no_live_reference(self):
        source = (ROOT / 'tasks' / 'SixRealms' / 'moon_sea' / 'skills.py').read_text(
            encoding='utf-8')
        for name in ('I_SKILL103', 'I_SKILL104'):
            live = [line for line in source.splitlines()
                    if re.search(rf'\b{name}\b', line) and not line.lstrip().startswith('#')]
            self.assertEqual(live, [], f'{name} 有活跃引用但模板图缺失，必须先补图')


if __name__ == '__main__':
    unittest.main()
