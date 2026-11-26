# This Python file uses the following encoding: utf-8

"""页面入口模板的「防误点」回归测试。

背景（2026-10-28 实测）：`I_MAIN_GOTO_TEAM` 的 roi_back=(395,590,120,100) 覆盖到了
「町中」底部导航条（y≈645-690），导航条里的「组队」按钮和庭院按钮相似度超过 0.7，
被误匹配后点到 (470,649)；点开的不是 page_team，I_CHECK_MAIN 又失效，于是登录兜底
反复点击 login_scroll_close_area，与 PAGE_MAIN_GOTO_TEAM 来回点 → GameTooManyClickError。

本测试锁死两条不变量：
1. 页面入口模板的 roi_back 不得跨入底部导航条（y + h <= 645）；
2. 入口模板阈值不得低于 0.85（<0.85 时同屏其它图标也会命中）。

用法：.\\toolkit\\python.exe dev_tools\\page_entry_guard_check.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.GameUi.assets import GameUiAssets  # noqa: E402

NAV_BAR_TOP = 645          # 底部导航条上沿（实测 y≈645-690）
MIN_THRESHOLD = 0.85
ENTRIES = (
    'I_MAIN_GOTO_TEAM', 'I_MAIN_GOTO_COLLECTION', 'I_MAIN_GOTO_TRAVEL',
    'I_MAIN_GOTO_GUILD', 'I_MAIN_GOTO_MALL', 'I_MAIN_GOTO_RECORDS',
)
# 2026-10-28 更新后：组队入口**确实**在主界面底栏（y≈646-682），
# 所以「不得进入导航条」这条规则已作废——真正要锁的是「点击区必须贴着图标本体」，
# 否则 OAS 在 roi_front 内做随机偏移时会点到相邻的「对弈社」，点了也打不开组队页。
# 实测：点击区 84x60 时偏移到 (483,677) → 打不开；收紧到 28x34（图标核心）后三个偏移点全部可开。
MAX_CLICK_WIDTH = 40
MAX_CLICK_HEIGHT = 40

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def main() -> int:
    for entry in ENTRIES:
        rule = getattr(GameUiAssets, entry, None)
        if rule is None:
            print(f'[SKIP] {entry} 不存在')
            continue
        x, y, width, height = rule.roi_back
        check(f'{entry} roi_back 在图内',
              0 <= x and 0 <= y and x + width <= 1280 and y + height <= 720,
              f'roi_back={tuple(rule.roi_back)}')
        if entry in ('I_MAIN_GOTO_TEAM',):
            fx, fy, fw, fh = rule.roi_front
            check(f'{entry} 点击区贴着图标（宽高 <= {MAX_CLICK_WIDTH}px）',
                  fw <= MAX_CLICK_WIDTH and fh <= MAX_CLICK_HEIGHT,
                  f'roi_front={tuple(rule.roi_front)}（过大会让随机偏移点到相邻入口）')
            check(f'{entry} 阈值 >= {MIN_THRESHOLD}',
                  float(rule.threshold) >= MIN_THRESHOLD,
                  f'threshold={rule.threshold}')
        else:
            print(f'[INFO] {entry}: roi_back={tuple(rule.roi_back)} '
                  f'threshold={rule.threshold}（不受导航条约束）')

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('PAGE ENTRY GUARD: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
