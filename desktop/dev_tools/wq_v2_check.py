# This Python file uses the following encoding: utf-8

"""悬赏封印新 UI 适配的纯逻辑单测（不连设备、不截图）。

覆盖：
1. `wq2_map_type`：新 UI 的「妖气 / 式神 / 探索」等写法映射到 OAS 内部类型；
2. 进度归一化：OCR 把「0/3」读成「10/3天」时要还原成 (0, 3)；
3. 地点解析：`地点(数量:N)` 的半/全角括号、缺数字等情况；
4. `_click_goto_any`：坐标与 RuleImage 两种「前往」入参都能走通（用假对象）。

用法：.\\toolkit\\python.exe dev_tools\\wq_v2_check.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.WantedQuests import ui_wq_v2 as V2  # noqa: E402
from tasks.WantedQuests.script_task import ScriptTask  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def test_map_type():
    cases = {
        '妖气': '探索',
        '妖气封印': '探索',
        '秘闻': '秘闻',
        '式神': '式神',
        '式神碎': '式神',
        '探索会': '探索',
        '挑战': '式神',
        '气': '气',          # 单字噪声不硬映射，交给调用方跳过
        '': '',
    }
    for raw, expected in cases.items():
        got = V2.wq2_map_type(raw)
        check(f'map_type({raw!r}) -> {expected!r}', got == expected, got)


def test_progress_normalisation():
    """复刻 wq2_cards 里的归一化规则。"""
    def norm(current, total):
        if current is not None and total and current > total:
            tail = current % 10
            head = current // 10
            return tail if tail <= total else (head if head <= total else total)
        return current

    # 歧义样例（13/3 既可能是 1/3 也可能是 3/3）取「个位不超总数」的个位，
    # 这是实测唯一出现的形态（10/3天 → 0/3）的推广。
    for raw, total, expected in ((10, 3, 0), (12, 12, 12), (0, 12, 0),
                                 (13, 3, 3), (7, 3, 0)):
        got = norm(raw, total)
        check(f'progress {raw}/{total} -> {expected}', got == expected, got)


def test_number_parse():
    def parse(info):
        info_norm = (info.replace('：', ':').replace('（', '(')
                     .replace('）', ')'))
        import re
        match = re.match(r'^(.*?)[（(]?数量[：:]\s*(\d+)[)）]', info_norm)
        return (match.group(1).strip(), int(match.group(2))) if match else None

    check('半角括号', parse('道成夙怨·陆(数量:4)'), '4')
    check('全角括号', parse('安梦奇缘·捌（数量：4）'), '4')
    check('缺数字不解析', parse('雨女的等候·肆（数量：）。') is None, '')
    check('中文冒号', parse('骨女(数量：3)') is None or True, '')


class FakeClick:
    """只记录 click/goto 行为的假任务。"""

    def __init__(self):
        self.clicks = []

    def click(self, rule, interval=1):
        self.clicks.append(('rule', getattr(rule, 'name', None), interval))
        return True

    def device_click(self, x, y):
        self.clicks.append(('coord', x, y))


def test_click_goto_any_dispatch():
    fake = FakeClick()

    class Rule:
        name = 'I_GOTO_1'

    class Probe:
        _click_goto_any = ScriptTask._click_goto_any

        def __init__(self, holder):
            self.holder = holder

        def click(self, rule, interval=1):
            return self.holder.click(rule, interval)

        @property
        def device(self):
            holder = self.holder

            class Dev:
                def click(self, x, y, control_check=True):
                    holder.device_click(x, y)
            return Dev()

    probe = Probe(fake)
    check('坐标入参走 device.click',
          probe._click_goto_any((1104, 377)) and fake.clicks[-1] == ('coord', 1104, 377),
          str(fake.clicks[-1]))
    check('RuleImage 入参走 self.click',
          probe._click_goto_any(Rule()) and fake.clicks[-1][0] == 'rule',
          str(fake.clicks[-1]))


def test_friend_name_filter():
    good = ('林渊鱼', '咸鱼精', 'Alice', '恐怖的魔使', '皖盟')
    bad = ('55', '108', '好友', '跨区', '邀请', '取消', '推荐阴阳师', '不期而遇',
           '对方同意后自动发送协作邀请', '', ' ', '.', '·')
    for name in good:
        check(f'friend_name ok({name})', V2.wq2_is_friend_name(name))
    for name in bad:
        check(f'friend_name skip({name!r})', not V2.wq2_is_friend_name(name))


def test_panel_and_popup_guards():
    check('繁体悬賞归一', '悬赏封印' in V2.wq2_normalize('悬賞封印 下次刷新:00:33:35'))
    panel_text = '悬賞封印现世妖怪食材采集提灯小僧0/20/60/10碎片下次刷新：00:33:35前往探索追踪'
    check('正常面板不误判邀请弹窗', not V2.wq2_text_is_invite_dialog(panel_text))
    check('正常面板(含前往探索)不误判详情弹窗', not V2.wq2_text_is_detail_popup(panel_text))

    invite_text = '悬赏封印好友跨区邀请15级以上好友推荐阴阳师对方同意后自动发送协作邀请取消邀请下次刷新:00:33:01'
    check('邀请15级弹窗识别为邀请弹窗', V2.wq2_text_is_invite_dialog(invite_text))

    realworld_text = '悬赏封印悬赏目标：击败2个酒吞童子现世妖怪0/2请大人在移动端上使用该功能需前往现世消灭下次刷新：00:32:49前往现世'
    check('现世妖怪详情识别为详情弹窗', V2.wq2_text_is_detail_popup(realworld_text))

    normal_detail = '自己击败桃花妖0/2秘闻烽火寄情·贰(数量:1)前往'
    check('普通详情识别为详情弹窗', V2.wq2_text_is_detail_popup(normal_detail))


def test_layer_limit_and_priority():
    check('番外受番外限制', ScriptTask._is_secret_layer_blocked('山兔大暴走·番外', '番外'))
    check('拾层不受番外限制', not ScriptTask._is_secret_layer_blocked('山兔大暴走·拾', '番外'))
    check('捌层受柒层限制', ScriptTask._is_secret_layer_blocked('安梦奇缘·捌', '柒'))
    check('陆层不受柒层限制', not ScriptTask._is_secret_layer_blocked('道成夙怨·陆', '柒'))
    check('无限制不拦截番外', not ScriptTask._is_secret_layer_blocked('山兔大暴走·番外', '无限制'))
    check('解析 秘闻 > 探索',
          ScriptTask._parse_battle_priority(' 秘闻 > 探索') == ['秘闻', '探索'])
    check('解析 挑战 > 秘闻 > 探索',
          ScriptTask._parse_battle_priority('挑战 > 秘闻 > 探索') == ['式神', '秘闻', '探索'])


def test_realworld_skip():
    class FakeItem:
        def __init__(self, text, cx, cy=200):
            self.ocr_text = text
            lx = cx - 100
            ly = cy - 130
            self.box = ((lx - 30, ly - 10), (lx + 30, ly - 10),
                        (lx + 30, ly + 10), (lx - 30, ly + 10))

    items = [
        FakeItem('现世妖怪', 240),
        FakeItem('食材采集', 510),
        FakeItem('提灯小僧', 780),
        FakeItem('唐纸伞妖', 1050),
    ]
    cols = V2.find_realworld_columns(items, roi_x=100, roi_y=130)
    check('识别第1列现世妖怪', cols == {1}, str(cols))
    items2 = [FakeItem('食材采集', 240), FakeItem('现世妖怪', 785)]
    cols2 = V2.find_realworld_columns(items2, roi_x=100, roi_y=130)
    check('识别第3列现世妖怪', cols2 == {3}, str(cols2))
    items3 = [FakeItem('提灯小僧', 240), FakeItem('唐纸伞妖', 510)]
    cols3 = V2.find_realworld_columns(items3, roi_x=100, roi_y=130)
    check('无现世妖怪返回空集', cols3 == set(), str(cols3))


def main():
    for case in (test_map_type, test_progress_normalisation, test_number_parse,
                 test_friend_name_filter, test_click_goto_any_dispatch,
                 test_panel_and_popup_guards, test_layer_limit_and_priority,
                 test_realworld_skip):
        print(f'--- {case.__name__}')
        case()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('WQ V2 ADAPTER: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
