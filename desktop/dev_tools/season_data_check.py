# This Python file uses the following encoding: utf-8

"""赛季数据一致性核对：season_data.py、图鉴/符咒/御魂表、实机日志三方对齐。

用来自动回答「当前赛季数据是否覆盖实际游戏内容」：
1. season_data.py 里登记的赛季新增式神 / 符咒 / 羁绊，是否真的在对应表里；
2. 实机日志里被识别过的式神名 / 符咒名，是否都能在表里查到
   （查不到说明图鉴缺条目，需要补资源）；
3. 商店费用曲线（season_data 记录）与图鉴费用是否自洽。

用法：
    .\\toolkit\\python.exe dev_tools\\season_data_check.py [log ...]
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Chess.strategy import adaptive as A  # noqa: E402
from tasks.Chess.strategy import grigri as G  # noqa: E402
from tasks.Chess.strategy import season_data as S  # noqa: E402
from tasks.Chess.strategy.shikigami_catalog import (  # noqa: E402
    SHIKIGAMI_BY_CHINESE_NAME,
)
from tasks.Chess.strategy.soul_catalog import SOUL_ENTRIES  # noqa: E402

L_SHOP = re.compile(r'Chess shop recognized \(left->right\): (.*)$')
L_GRIGRI = re.compile(r'Chess grigri option: option=\d+, quality=\S+, '
                      r'ocr=\[([^\]]*)\], name=(\S+),')
L_TIME = re.compile(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d+)')

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def detect_encoding(path):
    with open(path, 'rb') as stream:
        head = stream.read(2)
    if head == b'\xff\xfe':
        return 'utf-16'
    if head == b'\xfe\xff':
        return 'utf-16-be'
    return 'utf-8'


def test_season_metadata():
    check('赛季名已登记', bool(S.SEASON_NAME), S.SEASON_NAME)
    check('赛季开始日期已登记', bool(S.SEASON_STARTED_AT), S.SEASON_STARTED_AT)
    check('有官方出处', bool(S.SEASON_SOURCES), str(S.SEASON_SOURCES))
    check('有核对日期', bool(S.SEASON_VERIFIED_AT), S.SEASON_VERIFIED_AT)


def test_new_shikigami_covered():
    missing = [
        name for name in S.NEW_SHIKIGAMI
        if name not in SHIKIGAMI_BY_CHINESE_NAME
    ]
    check('赛季新增式神都在图鉴里', not missing, str(missing))
    for name in S.NEW_SHIKIGAMI:
        entry = SHIKIGAMI_BY_CHINESE_NAME.get(name)
        if entry is None:
            continue
        check(f'{name} 有羁绊标注', bool(entry.bonds), str(entry.bonds))


def test_bonds_covered():
    for bond in S.BOND_EFFECTS:
        check(f'羁绊 {bond} 在强度表里', bond in A.DEFAULT_BOND_TIER,
              str(list(A.DEFAULT_BOND_TIER)))
        members = A._entries_with_bond(bond)
        check(f'羁绊 {bond} 有可用式神', bool(members), str(len(members)))
    check('六道被标记为不随人数增强',
          '六道' in S.NON_SCALING_BONDS and '六道' in A.NON_SCALING_BONDS)


def test_new_grigri_covered():
    known = set(G.GRIGRI_CATEGORY_BY_NAME)
    for name in S.NEW_GRIGRI:
        check(f'新符咒 {name} 在符咒表里', name in known)


def test_souls_covered():
    check('御魂表非空', bool(SOUL_ENTRIES), str(len(SOUL_ENTRIES)))
    for name in ('狰', '青女房', '招财猫', '蚌精'):
        check(f'御魂 {name} 有登记',
              name in {entry.chinese_name for entry in SOUL_ENTRIES})


def test_log_evidence(logs):
    """实机日志里出现过的名字必须都能查到（缺条目=需要补图鉴）。"""
    shikigami_seen = set()
    grigri_seen = set()
    for name in logs:
        path = Path(name)
        if not path.is_absolute():
            path = path if path.exists() else (ROOT / path)
        if not path.exists():
            print(f'   skip 不存在的日志: {path}')
            continue
        text = open(path, encoding=detect_encoding(path), errors='replace').read()
        for line in text.splitlines():
            shop = L_SHOP.search(line)
            if shop:
                for cell in shop.group(1).split('|'):
                    cell = cell.strip()
                    if cell and cell not in ('未识别', '空'):
                        shikigami_seen.add(cell)
            grigri = L_GRIGRI.search(line)
            if grigri:
                name = grigri.group(2).strip()
                if name and name not in ('未知', 'None'):
                    grigri_seen.add(name)
    print(f'   日志里出现：式神 {len(shikigami_seen)} 种、符咒 {len(grigri_seen)} 种')
    missing_shikigami = sorted(
        name for name in shikigami_seen
        if name not in SHIKIGAMI_BY_CHINESE_NAME
    )
    missing_grigri = sorted(
        name for name in grigri_seen if name not in G.GRIGRI_CATEGORY_BY_NAME
    )
    check('日志里的式神全部在图鉴里', not missing_shikigami,
          str(missing_shikigami))
    check('日志里的符咒全部在符咒表里', not missing_grigri, str(missing_grigri))
    return sorted(shikigami_seen), sorted(grigri_seen)


def test_shop_curve_selfconsistent():
    """实测商店最高费不应超过图鉴里的最高费用。"""
    catalog_max = max(entry.cost for entry in SHIKIGAMI_BY_CHINESE_NAME.values())
    for level, cost in A.SHOP_MAX_COST_BY_LEVEL.items():
        check(f'阶{level} 可及费用 {cost} 不超过图鉴最高费 {catalog_max}',
              cost <= catalog_max, f'{cost} > {catalog_max}')
    monotonic = [
        A.SHOP_MAX_COST_BY_LEVEL[level]
        for level in sorted(A.SHOP_MAX_COST_BY_LEVEL)
    ]
    check('费用上限随阶数单调不减',
          all(b >= a for a, b in zip(monotonic, monotonic[1:])),
          str(monotonic))


def main() -> int:
    test_season_metadata()
    test_new_shikigami_covered()
    test_bonds_covered()
    test_new_grigri_covered()
    test_souls_covered()
    test_shop_curve_selfconsistent()
    test_log_evidence([a for a in sys.argv[1:] if not a.startswith('--')])

    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('SEASON DATA: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
