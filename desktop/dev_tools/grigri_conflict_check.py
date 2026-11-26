# This Python file uses the following encoding: utf-8

"""符咒 OCR/图标冲突裁决规则的回归测试。

规则来源：2026-10-27 落盘的 12 份实盘冲突样本，用
`dev_tools/grigri_conflict_audit.py --replay` 复盘后得到的结论——

1. `朱印 / 纹章` 这一对图标模板几乎无法区分：同一张卡的两种模板分数差
   只有 0.000~0.010（分裂纹章 0.867 vs 分裂朱印 0.857；嗜战 0.844 vs 0.844），
   而 OCR 文本里的类型字是清晰的（`锻刃朱印`、`强体纹章` 文本映射分到 1.0）
   → **这种情况必须信文字**；
2. 只有图标「前二差距 ≥0.03 且最高分 ≥0.80」、同时文字映射 <0.90 时才信图标：
   样本 `剥金符咒·贰 0.836（次名 0.657，margin 0.179）` 对 OCR 的
   `返金符咒 0.75`（原始文本 `金符咒·武` 丢了「剥」字）。

用法：`.\\toolkit\\python.exe dev_tools\\grigri_conflict_check.py`
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Chess.strategy.grigri import (  # noqa: E402
    resolve_conflict,
    resolve_grigri_name,
)

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


# (ocr_name, text_score, icon_name, icon_score, icon_margin, expected_name, decision)
REAL_SAMPLES = [
    # 实盘：原始文本 `金符咒·武` 丢了「剥」，图标前二差距大 → 信图标
    ('返金符咒', 0.75, '剥金符咒·贰', 0.836, 0.179, '剥金符咒·贰', 'icon'),
    # 以下 朱印/纹章 冲突：图标前二差距 ≤0.01 → 信文字
    ('分裂朱印', 0.8889, '分裂纹章', 0.8672, 0.010, '分裂朱印', 'ocr'),
    ('分裂朱印', 0.75, '分裂纹章', 0.8672, 0.010, '分裂朱印', 'ocr'),
    ('嗜战纹章', 0.75, '嗜战朱印', 0.8443, 0.000, '嗜战纹章', 'ocr'),
    ('嗜战纹章', 0.75, '嗜战朱印', 0.8558, 0.001, '嗜战纹章', 'ocr'),
    ('分裂朱印', 0.75, '分裂纹章', 0.8676, 0.004, '分裂朱印', 'ocr'),
    ('锻刃朱印', 0.75, '锻刃纹章', 0.8573, 0.006, '锻刃朱印', 'ocr'),
    ('锻刃朱印', 1.0, '锻刃纹章', 0.8573, 0.006, '锻刃朱印', 'ocr'),
    ('强体纹章', 1.0, '强体朱印', 0.8439, 0.002, '强体纹章', 'ocr'),
    ('荒川纹章', 1.0, '荒川朱印', 0.8681, 0.004, '荒川纹章', 'ocr'),
    ('荒川纹章', 1.0, '荒川朱印', 0.8680, 0.004, '荒川纹章', 'ocr'),
]


def test_real_samples():
    for ocr, text, icon, icon_score, margin, expected, decision in REAL_SAMPLES:
        name, got = resolve_conflict(ocr, text, icon, icon_score, margin)
        check(
            f'实盘 {ocr}({text}) vs {icon}({icon_score}/m{margin}) -> {decision}',
            name == expected and got == decision,
            f'{name}/{got}',
        )


def test_edges():
    check('两侧一致时 agree', resolve_conflict('剥金符咒·贰', 1.0, '剥金符咒·贰', 0.9, 0.2)
          == ('剥金符咒·贰', 'agree'))
    check('只有文字', resolve_conflict('强体纹章', 1.0, None, 0.0, 0.0)
          == ('强体纹章', 'ocr'))
    check('只有图标', resolve_conflict(None, 0.0, '强体纹章', 0.9, 0.2)
          == ('强体纹章', 'icon'))
    check('都没有', resolve_conflict(None, 0.0, None, 0.0, 0.0) == (None, 'none'))
    check('图标分低于 0.80 时不信图标',
          resolve_conflict('返金符咒', 0.75, '剥金符咒·贰', 0.79, 0.3)[1] == 'ocr')
    check('文字很强(>=0.90)时仍信文字',
          resolve_conflict('返金符咒', 0.95, '剥金符咒·贰', 0.95, 0.3)[1] == 'ocr')


def test_text_mapping_thresholds():
    """文字映射的模糊匹配仍要能给出 朱印/纹章 的正确类型。"""
    for raw, expected in (
        ('锻刃朱印', '锻刃朱印'),
        ('锻刃未印', '锻刃朱印'),
        ('强体纹章', '强体纹章'),
        ('分裂朱印', '分裂朱印'),
    ):
        name, score = resolve_grigri_name(raw, 'silver')
        check(f'文字映射 {raw} -> {expected}',
              name == expected and score >= 0.72, f'{name}/{score:.3f}')


def main():
    for case in (test_real_samples, test_edges, test_text_mapping_thresholds):
        print(f'--- {case.__name__}')
        case()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('GRIGRI CONFLICT: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
