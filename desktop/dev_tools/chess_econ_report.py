# This Python file uses the following encoding: utf-8

"""百鬼棋局经济/运营复盘：按局统计等级、金币、动作次数，用来对比不同名次的打法。

回答的问题：拿到第 1 名的局和拿第 2/3 名的局，在「升阶进度、金币水位、刷新/买卡/上阵次数」
上有什么差别——这是调参（保留金币吃利息 vs 刷商店找牌）的证据，而不是凭感觉改。

用法：
    .\\toolkit\\python.exe dev_tools\\chess_econ_report.py <logfile> [...]
    .\\toolkit\\python.exe dev_tools\\chess_econ_report.py --json <logfile>

分段：优先按 `[ChessMatch] start/end`；老日志退回按 `Chess completed games` 锚点。
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dev_tools.chess_log_report import (  # noqa: E402
    COMPLETED_RE,
    RANK_RE,
    TIME_RE,
    detect_encoding,
)

LEVEL_RE = re.compile(r'\[LEVEL[^\]]*\]\s*\[([^\]]+)\]')
GOLD_RE = re.compile(r'\[GOLD[^\]]*\]\s*\[(\d+)\]')
MATCH_START_RE = re.compile(r'\[ChessMatch\] start index=(\d+)')
MATCH_END_RE = re.compile(r'\[ChessMatch\] end index=(\d+)')

COUNTERS = {
    'refresh': 'Refresh Chess shop completed',
    'experience': '@ C_EXPERIENCE',
    'buy': 'Buy Chess card',
    'sell': 'sale confirmed',
    'deploy': 'Chess deploy candidate',
    'grigri': 'Chess grigri option',
    'shop_scan': 'Chess shop recognized',
    'unknown_shop': '未识别',
}

LEVEL_VALUE = {
    '一阶': 1, '二阶': 2, '三阶': 3, '四阶': 4, '五阶': 5,
    '六阶': 6, '七阶': 7, '八阶': 8, '九阶': 9,
}


def new_game(index, timestamp):
    return {
        'index': index,
        'start': timestamp,
        'end': '',
        'rank': None,
        'rank_raw': '',
        'max_level': 0,
        'level_timeline': [],
        'gold_min': None,
        'gold_max': None,
        'gold_last': None,
        'counters': {name: 0 for name in COUNTERS},
    }


def finish_game(game, timestamp=''):
    if game is None:
        return
    if timestamp:
        game['end'] = timestamp
    game.pop('_open', None)


def parse(path):
    encoding = detect_encoding(path)
    games = []
    current = None
    last_completed_index = 0

    with open(path, encoding=encoding, errors='replace') as stream:
        for line in stream:
            time_match = TIME_RE.search(line)
            timestamp = time_match.group(1) if time_match else ''

            start_match = MATCH_START_RE.search(line)
            if start_match:
                if current is not None:
                    finish_game(current, timestamp)
                current = new_game(int(start_match.group(1)), timestamp)
                games.append(current)
                continue

            if current is None:
                # 老日志：第一次看到结算名次时开一局
                if RANK_RE.search(line):
                    current = new_game(last_completed_index + 1, timestamp)
                    current['_open'] = True
                    games.append(current)
                else:
                    continue

            rank_match = RANK_RE.search(line)
            if rank_match:
                current['rank_raw'] = rank_match.group(1)
                current['rank'] = int(rank_match.group(2))

            completed_match = COMPLETED_RE.search(line)
            if completed_match:
                last_completed_index = int(completed_match.group(1))
                current['index'] = last_completed_index

            level_match = LEVEL_RE.search(line)
            if level_match:
                text = level_match.group(1).strip()
                value = LEVEL_VALUE.get(text)
                if value is None:
                    digit = re.search(r'([1-9])', text)
                    value = int(digit.group(1)) if digit else None
                if value:
                    if value > current['max_level']:
                        current['max_level'] = value
                        current['level_timeline'].append(
                            {'time': timestamp[-8:], 'level': value}
                        )

            gold_match = GOLD_RE.search(line)
            if gold_match:
                gold = int(gold_match.group(1))
                current['gold_last'] = gold
                current['gold_min'] = (
                    gold if current['gold_min'] is None
                    else min(current['gold_min'], gold)
                )
                current['gold_max'] = (
                    gold if current['gold_max'] is None
                    else max(current['gold_max'], gold)
                )

            for name, marker in COUNTERS.items():
                if marker in line:
                    if name == 'unknown_shop':
                        current['counters'][name] += line.count(marker)
                    else:
                        current['counters'][name] += 1

            if MATCH_END_RE.search(line):
                finish_game(current, timestamp)

    if current is not None and current.get('_open') is None:
        finish_game(current)
    return {'path': str(path), 'encoding': encoding, 'games': games}


def report(payload):
    print(f"== {payload['path']}  (encoding={payload['encoding']})")
    if not payload['games']:
        print('   (没有可统计的对局)')
        return
    header = (
        f"   {'#':>2} {'start':<8} {'end':<8} {'rank':>4} {'lv':>3} "
        f"{'gold(min/max/last)':>18} {'refresh':>7} {'exp':>4} {'buy':>4} "
        f"{'sell':>4} {'deploy':>6} {'grigri':>6} {'shop':>5} {'unk':>4}"
    )
    print(header)
    for game in payload['games']:
        counters = game['counters']
        gold = (
            f"{game['gold_min']}/{game['gold_max']}/{game['gold_last']}"
            if game['gold_last'] is not None else '-'
        )
        print(
            f"   {game['index']:>2} {game['start'][-8:] or '-':<8} "
            f"{game['end'][-8:] or '-':<8} "
            f"{game['rank'] if game['rank'] is not None else '-':>4} "
            f"{game['max_level']:>3} {gold:>18} "
            f"{counters['refresh']:>7} {counters['experience']:>4} "
            f"{counters['buy']:>4} {counters['sell']:>4} "
            f"{counters['deploy']:>6} {counters['grigri']:>6} "
            f"{counters['shop_scan']:>5} {counters['unknown_shop']:>4}"
        )

    # 按名次分组看差异（只在同一日志内比较，样本很小，仅作观察）
    by_rank = {}
    for game in payload['games']:
        if game['rank'] is None:
            continue
        by_rank.setdefault(game['rank'], []).append(game)
    if by_rank:
        print('   --- 按名次平均（观察用，样本小）')
        for rank in sorted(by_rank):
            group = by_rank[rank]
            avg_level = sum(g['max_level'] for g in group) / len(group)
            avg_gold = sum(
                g['gold_last'] for g in group if g['gold_last'] is not None
            ) / max(1, len([g for g in group if g['gold_last'] is not None]))
            avg_refresh = sum(
                g['counters']['refresh'] for g in group
            ) / len(group)
            print(
                f"   第{rank}名 x{len(group)}: 平均最高阶={avg_level:.1f}, "
                f"收尾金币={avg_gold:.0f}, 平均刷新={avg_refresh:.1f}"
            )


def main() -> int:
    args = [arg for arg in sys.argv[1:]]
    as_json = '--json' in args
    args = [arg for arg in args if arg != '--json']
    if not args:
        print(__doc__)
        return 2
    payloads = []
    for name in args:
        path = Path(name)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            print(f'!! 日志不存在: {path}')
            continue
        payload = parse(path)
        payloads.append(payload)
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            report(payload)
            print()
    return 0 if payloads else 1


if __name__ == '__main__':
    sys.exit(main())
