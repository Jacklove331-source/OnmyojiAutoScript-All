# This Python file uses the following encoding: utf-8

"""实战过程指标：把棋局日志变成可比较的数字（验收 AC13~AC17 的数据源）。

为什么不用名次：名次被随机对手淹没，3 局看不出好坏；这里只看**决策过程**
（前期买到/上阵了多少、购买确认率、御魂装备成败、识图健康度）。

数据来自仓库已有的 `dev_tools/chess_decision_audit.py --json`（不重复造解析器），
本模块只做「派生指标 + 汇总 + 基线对照」。

用法：
    .\\toolkit\\python.exe dev_tools\\dual_agent\\metrics.py <log> [<log> ...]
    .\\toolkit\\python.exe dev_tools\\dual_agent\\metrics.py --baseline
    .\\toolkit\\python.exe dev_tools\\dual_agent\\metrics.py --since "2026-10-28 10:30:00"
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path('D:/3D/YYS2/_recovery_070208')
EARLY_ROUNDS = 8

# 固定对照基线：稳定基线提交 806e0352 的连续三局（策略优化开始前，2026-10-27 12:48~13:59）。
# 写死在代码里，避免「基线跟着结果漂移」。
BASELINE_LOGS = ('chess_series3_run3_1248.log',)

AUDIT_SCRIPT = 'dev_tools/chess_decision_audit.py'


def _project_python(project_root: Path) -> str:
    candidate = project_root / 'toolkit' / 'python.exe'
    return str(candidate) if candidate.exists() else sys.executable


def audit_log(project_root: Path, log_path: Path, timeout: int = 600) -> list[dict]:
    """跑审计脚本拿 JSON；失败返回空列表（调用方据此判定证据不足）。"""
    try:
        completed = subprocess.run(
            [_project_python(project_root), AUDIT_SCRIPT, '--json', str(log_path)],
            cwd=str(project_root), capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    text = completed.stdout or ''
    start = text.find('{')
    if start < 0:
        return []
    try:
        data = json.loads(text[start:])
    except ValueError:
        return []
    games = data.get('games') if isinstance(data, dict) else None
    if not isinstance(games, list):
        return []
    for game in games:
        if isinstance(game, dict):
            game['_log'] = log_path.name
    return [game for game in games if isinstance(game, dict)]


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def derive(game: dict) -> dict:
    """单局 → 派生指标（全部是整数/小数，方便汇总）。"""
    events = game.get('events') or {}
    timeline = game.get('timeline') or {}
    early_buy = 0
    early_deploy = 0
    first_buy_round = None
    for key, row in timeline.items():
        round_no = _int(key)
        if not isinstance(row, dict):
            continue
        if _int(row.get('buy_ok')) and (first_buy_round is None or round_no < first_buy_round):
            first_buy_round = round_no
        if 1 <= round_no <= EARLY_ROUNDS:
            early_buy += _int(row.get('buy_ok'))
            early_deploy += _int(row.get('deploy'))
    rank = game.get('rank')
    return {
        'log': game.get('_log', ''),
        'index': game.get('index'),
        'start': game.get('start') or '',
        'rank': rank if isinstance(rank, int) else None,
        'completed': isinstance(rank, int),
        'max_round': _int(game.get('max_round')),
        'early_buy_ok': early_buy,
        'early_deploy': early_deploy,
        'first_buy_round': first_buy_round,
        'buy_attempt': _int(events.get('buy_attempt')),
        'buy_ok': _int(events.get('buy_ok')),
        'buy_unconfirmed': _int(events.get('buy_unconfirmed')),
        'transition_deployed': _int(events.get('transition_deployed')),
        'soul_equipped': _int(events.get('soul_equipped')),
        'soul_equip_failed': _int(events.get('soul_equip_failed')),
        'level_ocr_invalid': _int(events.get('level_ocr_invalid')),
        'unknown_cells': _int(game.get('unknown_cells')),
        'below_threshold': _int(events.get('below_threshold')),
        'screenshot_timeout': _int(events.get('screenshot_timeout')),
        'grigri_conflict': _int(events.get('grigri_conflict')),
        'grigri_decisions': _int(events.get('grigri_decision_icon'))
        + _int(events.get('grigri_decision_ocr')),
        'budget_samples': len(game.get('budget_samples') or []),
        'sell_lineup_card': _int(events.get('sell_lineup_card')),
    }


def collect(project_root: Path, logs: list[Path]) -> list[dict]:
    games: list[dict] = []
    for log in logs:
        for game in audit_log(project_root, log):
            games.append(derive(game))
    games.sort(key=lambda item: item['start'])
    return games


def summarize(games: list[dict]) -> dict:
    done = [game for game in games if game['completed']]
    count = len(done)

    def avg(key: str) -> float:
        if not count:
            return 0.0
        return round(sum(float(game[key] or 0) for game in done) / count, 2)

    attempts = sum(game['buy_attempt'] for game in done)
    oks = sum(game['buy_ok'] for game in done)
    equipped = sum(game['soul_equipped'] for game in done)
    failed = sum(game['soul_equip_failed'] for game in done)
    return {
        'games': len(games),
        'completed': count,
        'avg_rank': avg('rank'),
        'avg_early_buy_ok': avg('early_buy_ok'),
        'avg_early_deploy': avg('early_deploy'),
        'buy_confirm_rate': round(oks / attempts, 3) if attempts else 0.0,
        'avg_buy_unconfirmed': avg('buy_unconfirmed'),
        'games_with_transition': sum(1 for game in done if game['transition_deployed'] > 0),
        'soul_equipped': equipped,
        'soul_equip_failed': failed,
        'soul_success_rate': round(equipped / (equipped + failed), 3) if (equipped + failed) else 0.0,
        'avg_level_ocr_invalid': avg('level_ocr_invalid'),
        'avg_unknown_cells': avg('unknown_cells') + avg('below_threshold'),
        'avg_screenshot_timeout': avg('screenshot_timeout'),
        'grigri_conflict': sum(game['grigri_conflict'] for game in done),
        'grigri_decisions': sum(game['grigri_decisions'] for game in done),
        'games_with_budget': sum(1 for game in done if game['budget_samples'] > 0),
    }


def logs_since(since: str, log_dir: Path = LOG_DIR) -> list[Path]:
    """mtime 晚于 since 的棋局日志（评估窗口）；since 为空返回空列表。"""
    if not since:
        return []
    try:
        threshold = datetime.strptime(since[:19], '%Y-%m-%d %H:%M:%S').timestamp()
    except ValueError:
        return []
    picked = [
        path for path in log_dir.glob('chess_*.log')
        if path.stat().st_mtime >= threshold
    ]
    return sorted(picked, key=lambda path: path.stat().st_mtime)


def window_games(project_root: Path, since: str, start_floor: str = '') -> list[dict]:
    """评估窗口内**开局时间**不早于 start_floor 的对局（默认 = since）。"""
    floor = start_floor or since
    games = collect(project_root, logs_since(since))
    return [game for game in games if (game['start'] or '') >= floor[:19]]


def baseline_games(project_root: Path) -> list[dict]:
    return collect(project_root, [LOG_DIR / name for name in BASELINE_LOGS
                                  if (LOG_DIR / name).exists()])


def format_table(games: list[dict]) -> str:
    header = ('log', 'idx', 'start', 'rank', 'rnd', 'eBuy', 'eDep', 'buy', 'unconf',
              'trans', 'soulOK', 'soulX', 'lvlX', 'unk')
    lines = [' | '.join(header)]
    for game in games:
        lines.append(' | '.join(str(item) for item in (
            game['log'][-26:], game['index'], game['start'][5:16], game['rank'],
            game['max_round'], game['early_buy_ok'], game['early_deploy'],
            f"{game['buy_ok']}/{game['buy_attempt']}", game['buy_unconfirmed'],
            game['transition_deployed'], game['soul_equipped'],
            game['soul_equip_failed'], game['level_ocr_invalid'],
            game['unknown_cells'] + game['below_threshold'],
        )))
    return '\n'.join(lines)


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    root = Path(__file__).resolve().parents[2]
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == '--baseline':
        games = baseline_games(root)
    elif args[0] == '--since' and len(args) > 1:
        games = window_games(root, args[1])
    else:
        games = collect(root, [Path(arg) for arg in args])
    print(format_table(games))
    print(json.dumps(summarize(games), ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
