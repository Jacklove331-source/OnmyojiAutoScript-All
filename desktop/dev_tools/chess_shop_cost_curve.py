# This Python file uses the following encoding: utf-8

"""从历史日志拟合「阶数/回合 → 商店可出式神费用」曲线。

为什么需要：自适应阵容原来只按「最强 8 张」选人（多为 2~5 费），而低阶商店
根本刷不出高费卡 → 前 10 回合 0~3 次购买、棋盘空着。要修这个问题，先得知道
**当前版本商店在各阶能出什么费用**，而不是拍脑袋定「前 6 回合买 1~2 费」。

数据来源：日志里的 `[LEVEL] [N阶]` 与 `Chess shop recognized (left->right): ...`
（把中文名映射回图鉴费用）。这是**游戏内实测**，比攻略可靠。

用法：
    .\\toolkit\\python.exe dev_tools\\chess_shop_curve.py <log> [<log> ...]
    .\\toolkit\\python.exe dev_tools\\chess_shop_cost_curve.py --json <log>
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.Chess.strategy.shikigami_catalog import (  # noqa: E402
    SHIKIGAMI_BY_CHINESE_NAME,
)

L_TIME = re.compile(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d+)')
L_LEVEL = re.compile(r'\[LEVEL[^\]]*\]\s*\[([^\]]+)\]')
L_SHOP = re.compile(r'Chess shop recognized \(left->right\): (.*)$')
L_ROUND = re.compile(r'\[ROUND(?:_2)?[^\]]*\]\s*\[(\d+)回目\]')
L_PHASE = re.compile(r'Chess phase detected: mode=(\S+), round=(\d+)')

LEVEL_VALUE = {
    '一阶': 1, '二阶': 2, '三阶': 3, '四阶': 4, '五阶': 5,
    '六阶': 6, '七阶': 7, '八阶': 8, '九阶': 9,
}


def detect_encoding(path):
    with open(path, 'rb') as stream:
        head = stream.read(2)
    if head == b'\xff\xfe':
        return 'utf-16'
    if head == b'\xfe\xff':
        return 'utf-16-be'
    return 'utf-8'


def parse(path):
    encoding = detect_encoding(path)
    lines = open(path, encoding=encoding, errors='replace').read().splitlines()
    level = None
    round_no = 0
    rows = []          # 每次商店扫描一行
    unknown = Counter()
    for line in lines:
        level_match = L_LEVEL.search(line)
        if level_match:
            text = level_match.group(1).strip()
            value = LEVEL_VALUE.get(text)
            if value is None:
                digit = re.search(r'([1-9])', text)
                value = int(digit.group(1)) if digit else None
            if value:
                level = value
            continue
        phase_match = L_PHASE.search(line)
        if phase_match:
            round_no = int(phase_match.group(2))
            continue
        round_match = L_ROUND.search(line)
        if round_match:
            round_no = max(round_no, int(round_match.group(1)))
            continue
        shop_match = L_SHOP.search(line)
        if not shop_match:
            continue
        cells = [cell.strip() for cell in shop_match.group(1).split('|')]
        costs = []
        for cell in cells:
            if cell in ('未识别', '空', ''):
                if cell == '未识别':
                    unknown[cell] += 1
                continue
            entry = SHIKIGAMI_BY_CHINESE_NAME.get(cell)
            if entry is None:
                unknown[f'unknown_name:{cell}'] += 1
                continue
            costs.append(entry.cost)
        if not costs:
            continue
        rows.append({
            'level': level,
            'round': round_no,
            'costs': costs,
            'time': L_TIME.search(line).group(1) if L_TIME.search(line) else '',
        })
    return {'path': str(path), 'encoding': encoding, 'rows': rows,
            'unknown': dict(unknown)}


def report(payload):
    rows = payload['rows']
    print(f"== {payload['path']}  (encoding={payload['encoding']}, "
          f"shop scans with costs={len(rows)})")
    if payload['unknown']:
        print(f"   unknown cells: {payload['unknown']}")
    if not rows:
        print('   (没有可用样本)')
        return

    by_level = defaultdict(list)
    for row in rows:
        if row['level']:
            by_level[row['level']].append(row['costs'])

    print('   == 按阶数')
    print(f"   {'阶':>2} {'样本':>4} {'卡数':>4} {'费用分布':<28} {'最高':>4} {'1费占比':>7}")
    for level in sorted(by_level):
        groups = by_level[level]
        flat = [cost for costs in groups for cost in costs]
        hist = Counter(flat)
        hist_text = ' '.join(
            f'{cost}:{hist[cost]}' for cost in sorted(hist)
        )
        max_cost = max(flat)
        one_ratio = hist.get(1, 0) / len(flat) * 100
        print(f"   {level:>2} {len(groups):>4} {len(flat):>4} "
              f"{hist_text:<28} {max_cost:>4} {one_ratio:>6.1f}%")

    print('   == 按回合段（同一阶内的早期/后期差异）')
    buckets = ((1, 5), (6, 10), (11, 16), (17, 25))
    for low, high in buckets:
        flat = [
            cost for row in rows
            if low <= row['round'] <= high for cost in row['costs']
        ]
        if not flat:
            continue
        hist = Counter(flat)
        hist_text = ' '.join(f'{c}:{hist[c]}' for c in sorted(hist))
        print(f"   round {low:>2}-{high:<2} 卡数={len(flat):>3} "
              f"最高费={max(flat)} {hist_text}")

    print('   == 建议的「可及费用」上限（按阶，取该阶实测最高费）')
    curve = {}
    for level in sorted(by_level):
        flat = [cost for costs in by_level[level] for cost in costs]
        curve[level] = max(flat)
    print('   ' + json.dumps(curve, ensure_ascii=False))
    return curve


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    as_json = '--json' in sys.argv
    if not args:
        print(__doc__)
        return 2
    for name in args:
        path = Path(name)
        if not path.is_absolute():
            path = path if path.exists() else (ROOT / path)
        if not path.exists():
            print(f'!! 日志不存在: {path}')
            continue
        payload = parse(path)
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            report(payload)
            print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
