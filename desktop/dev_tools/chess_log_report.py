# This Python file uses the following encoding: utf-8

"""百鬼棋局日志验收报告（可重复执行）。

把一个 chess 运行日志解析成「逐局证据表」，用验收标准自动判定每一局是否
真正完成，避免人工扫几十万行日志、也避免把「回合数上升」当成「对局完成」。

每局必须具备的锚点：
    Chess phase detected / ROUND ...            回目推进
    Chess game-over rank OCR: [...] -> 第N名    结算名次 OCR
    Chess game ended: 第N名                     任务层确认
    Click ... @ PAGE_CHESS_EXIT_TO_LOBBY(_2)    退出结算页
    Click ... @ PAGE_CHESS_RANK_GOTO_LOBBY      返回棋局大厅
    Chess completed games: X/Y                  完成计数递增

用法：
    .\\toolkit\\python.exe dev_tools\\chess_log_report.py <logfile> [<logfile> ...]
    .\\toolkit\\python.exe dev_tools\\chess_log_report.py --json <logfile>

退出码：0 = 全部局都有完整证据；1 = 存在证据缺失/异常。
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TIME_RE = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+')
RANK_RE = re.compile(r'game-over rank OCR: \[(.*?)\] -> 第(\d+)名')
ROUND_RE = re.compile(r'ROUND(?:_2)?\s+[\d.]+s\]\s+\[(\d+)回目\]')
COMPLETED_RE = re.compile(r'Chess completed games: (\d+)/(\d+|-?1)')
MATCH_START_RE = re.compile(r'\[ChessMatch\] start index=(\d+)')
MATCH_END_RE = re.compile(r'\[ChessMatch\] end index=(\d+)')
OUTCOME_RE = re.compile(r'\[ChessOutcome\] status=(\S+) reason=(.*)')
LEVEL_INVALID_RE = re.compile(r'Chess level OCR invalid')
CAPACITY_RE = re.compile(r'lineup capacity could not be confirmed')


def detect_encoding(path):
    """识别日志编码。

    坑：脱离 shell 的运行用 PowerShell/cmd 重定向时，日志是 **UTF-16LE（带 BOM）**，
    按 UTF-8 读会得到一堆替换字符，所有关键字都匹配不到（表现为"0 条记录"）。
    """
    with open(path, 'rb') as stream:
        head = stream.read(4)
    if head.startswith(b'\xff\xfe'):
        return 'utf-16'
    if head.startswith(b'\xfe\xff'):
        return 'utf-16-be'
    if head.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    try:
        with open(path, encoding='utf-8') as stream:
            stream.read(4096)
        return 'utf-8'
    except UnicodeDecodeError:
        return 'gb18030'


def parse(path):
    games = []
    current = None
    encoding = detect_encoding(path)
    stats = {
        'encoding': encoding,
        'lines': 0,
        'rank_ocr': 0,
        'exit_click': 0,
        'lobby_click': 0,
        'completed_markers': 0,
        'errors': 0,
        'tracebacks': 0,
        'level_invalid': 0,
        'capacity_blocked': 0,
        'shop_scans': 0,
        'shop_unknown': 0,
        'match_records': 0,
        'legacy_records': 0,
    }
    outcome = None

    # 老日志（没有 [ChessMatch] 打点）用锚点累加器还原逐局证据：
    # 名次 OCR → 退出点击 → 返回大厅点击 → completed games 标记为一局。
    legacy = None
    legacy_round = 0

    def flush_legacy(reason=''):
        nonlocal legacy
        if legacy is not None:
            legacy['reason'] = reason
            games.append(legacy)
            legacy = None

    with open(path, encoding=encoding, errors='replace') as stream:
        for line in stream:
            stats['lines'] += 1
            if 'ERROR' in line:
                stats['errors'] += 1
            if 'Traceback' in line:
                stats['tracebacks'] += 1
            if LEVEL_INVALID_RE.search(line):
                stats['level_invalid'] += 1
            if CAPACITY_RE.search(line):
                stats['capacity_blocked'] += 1

            time_match = TIME_RE.search(line)
            timestamp = time_match.group(1) if time_match else ''
            round_match = ROUND_RE.search(line)
            if round_match:
                legacy_round = max(legacy_round, int(round_match.group(1)))

            start_match = MATCH_START_RE.search(line)
            if start_match:
                current = {
                    'index': int(start_match.group(1)),
                    'source': 'ChessMatch',
                    'start_time': timestamp,
                    'max_round': 0,
                    'rank': None,
                    'rank_raw': '',
                    'game_ended': False,
                    'exit_click': False,
                    'lobby_click': False,
                    'completed_marker': None,
                    'end_time': '',
                }
                games.append(current)
                stats['match_records'] += 1

            rank_match = RANK_RE.search(line)
            if rank_match:
                stats['rank_ocr'] += 1
                # 又出现一次结算名次 = 上一局的锚点段落已经结束
                if legacy is not None and legacy['rank'] is not None:
                    flush_legacy('next rank OCR')
                if legacy is None:
                    legacy = {
                        'index': 0,
                        'source': 'legacy',
                        'start_time': timestamp,
                        'max_round': 0,
                        'rank': None,
                        'rank_raw': '',
                        'game_ended': False,
                        'exit_click': False,
                        'lobby_click': False,
                        'completed_marker': None,
                        'end_time': '',
                        'reason': '',
                    }
                    stats['legacy_records'] += 1
                legacy['rank_raw'] = rank_match.group(1)
                legacy['rank'] = int(rank_match.group(2))
                legacy['rank_time'] = timestamp

            if 'PAGE_CHESS_EXIT_TO_LOBBY' in line and 'Click' in line:
                stats['exit_click'] += 1
                if legacy is not None:
                    legacy['exit_click'] = True
            if 'PAGE_CHESS_RANK_GOTO_LOBBY' in line and 'Click' in line:
                stats['lobby_click'] += 1
                if legacy is not None:
                    legacy['lobby_click'] = True

            completed_match = COMPLETED_RE.search(line)
            if completed_match:
                stats['completed_markers'] += 1
                marker = (int(completed_match.group(1)), completed_match.group(2))
                if legacy is None:
                    legacy = {
                        'index': marker[0],
                        'source': 'legacy',
                        'start_time': timestamp,
                        'max_round': 0,
                        'rank': None,
                        'rank_raw': '',
                        'game_ended': False,
                        'exit_click': False,
                        'lobby_click': False,
                        'completed_marker': None,
                        'end_time': '',
                        'reason': '',
                    }
                    stats['legacy_records'] += 1
                # 日志顺序是「名次 OCR → game ended → completed games → 退出点击
                # → 返回大厅点击」，所以这里只补字段，不能直接 flush。
                legacy['index'] = marker[0]
                legacy['completed_marker'] = marker
                legacy['max_round'] = max(legacy['max_round'], legacy_round)
                legacy['end_time'] = timestamp
                if legacy['rank'] is not None:
                    legacy['game_ended'] = True

            if 'Chess game ended:' in line and current is not None:
                current['game_ended'] = True

            if 'Chess shop recognized' in line:
                stats['shop_scans'] += 1
                stats['shop_unknown'] += line.count('未识别')

            outcome_match = OUTCOME_RE.search(line)
            if outcome_match:
                outcome = {
                    'status': outcome_match.group(1),
                    'reason': outcome_match.group(2).strip(),
                    'time': timestamp,
                }

            # [ChessMatch] 明细字段（新代码才有的日志）
            if current is None:
                continue
            if round_match:
                current['max_round'] = max(
                    current['max_round'], int(round_match.group(1))
                )
            if rank_match:
                current['rank_raw'] = rank_match.group(1)
                current['rank'] = int(rank_match.group(2))
            if 'Chess game ended:' in line:
                current['game_ended'] = True
            if 'PAGE_CHESS_EXIT_TO_LOBBY' in line and 'Click' in line:
                current['exit_click'] = True
            if 'PAGE_CHESS_RANK_GOTO_LOBBY' in line and 'Click' in line:
                current['lobby_click'] = True
            if completed_match:
                current['completed_marker'] = (
                    int(completed_match.group(1)),
                    completed_match.group(2),
                )
                current['end_time'] = timestamp
            if MATCH_END_RE.search(line):
                current['end_time'] = timestamp

    # 末尾未闭合的锚点（例如进程被杀）也输出，标记为未完成
    if legacy is not None:
        legacy['max_round'] = max(legacy['max_round'], legacy_round)
        flush_legacy('unclosed at end of log')

    # 新日志同时有 [ChessMatch] 打点和锚点累加器，会得出重复的同一局；
    # 有精确记录时丢掉锚点版本，只保留逐局记录。
    if any(game.get('source') == 'ChessMatch' for game in games):
        games = [game for game in games if game.get('source') == 'ChessMatch']

    return {
        'path': str(path),
        'games': games,
        'stats': stats,
        'outcome': outcome,
    }


def evaluate(report):
    """按验收标准判定，返回 (per_game_rows, problems)。"""
    rows = []
    problems = []
    for game in report['games']:
        verified = (
            game['rank'] is not None
            and game['game_ended']
            and game['exit_click']
            and game['lobby_click']
        )
        row = dict(game)
        row['verified'] = verified
        rows.append(row)
        if not verified:
            missing = []
            if game['rank'] is None:
                missing.append('结算名次OCR')
            if not game['game_ended']:
                missing.append('game ended')
            if not game['exit_click']:
                missing.append('退出结算页点击')
            if not game['lobby_click']:
                missing.append('返回大厅点击')
            problems.append(
                f"第{game['index']}局 缺证据: {', '.join(missing)}"
            )
    return rows, problems


def print_report(report):
    stats = report['stats']
    print(f"== {report['path']}")
    print(
        f"   lines={stats['lines']} matches={len(report['games'])} "
        f"rank_ocr={stats['rank_ocr']} errors={stats['errors']} "
        f"tracebacks={stats['tracebacks']}"
    )
    print(
        f"   level_ocr_invalid={stats['level_invalid']} "
        f"capacity_blocked={stats['capacity_blocked']} "
        f"shop_scans={stats['shop_scans']} shop_unknown={stats['shop_unknown']}"
    )
    rows, problems = evaluate(report)
    if rows:
        header = (
            f"   {'#':>2} {'start':<8} {'end':<8} {'round':>5} "
            f"{'rank':>4} {'ended':>5} {'exit':>5} {'lobby':>5} {'count':>6}  verified"
        )
        print(header)
        for row in rows:
            count = row['completed_marker']
            count_text = f'{count[0]}/{count[1]}' if count else '-'
            print(
                f"   {row['index']:>2} {row['start_time'][-8:]:<8} "
                f"{row['end_time'][-8:] or '-':<8} {row['max_round']:>5} "
                f"{row['rank'] if row['rank'] is not None else '-':>4} "
                f"{'Y' if row['game_ended'] else 'N':>5} "
                f"{'Y' if row['exit_click'] else 'N':>5} "
                f"{'Y' if row['lobby_click'] else 'N':>5} "
                f"{count_text:>6}  {'YES' if row['verified'] else 'NO'}"
            )
    else:
        print('   (未发现 [ChessMatch] 记录——旧代码日志或任务未进入对局)')

    if report['outcome']:
        print(
            f"   outcome: status={report['outcome']['status']} "
            f"reason={report['outcome']['reason']}"
        )
    else:
        print('   outcome: (无 [ChessOutcome] 记录——进程可能仍在运行或被杀)')

    for problem in problems:
        print(f'   PROBLEM: {problem}')
    return problems


def main() -> int:
    args = [arg for arg in sys.argv[1:]]
    as_json = '--json' in args
    args = [arg for arg in args if arg != '--json']
    if not args:
        print(__doc__)
        return 2

    all_problems = []
    payload = []
    for name in args:
        path = Path(name)
        if not path.is_absolute():
            path = (Path.cwd() / path)
        if not path.exists():
            print(f'!! 日志不存在: {path}')
            all_problems.append(f'{path} 不存在')
            continue
        report = parse(path)
        payload.append(report)
        if as_json:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            all_problems.extend(print_report(report))
            print()

    if as_json:
        return 0
    print(
        'CHESS REPORT: '
        + ('PASSED' if not all_problems else f'{len(all_problems)} PROBLEM(S)')
    )
    return 0 if not all_problems else 1


if __name__ == '__main__':
    sys.exit(main())
