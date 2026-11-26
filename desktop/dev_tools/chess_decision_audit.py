# This Python file uses the following encoding: utf-8
"""百鬼棋局决策审计：把日志还原成「每局每回合做了什么、哪里浪费了」。

目的不是看名次，而是看**决策过程**：钱花在哪、刷新有没有回报、买卡为什么失败、
手牌为什么堵、有没有卖掉阵容牌、部署有没有停、识图失败多少次。

用法：
    .\\toolkit\\python.exe dev_tools\\chess_decision_audit.py <log> [<log> ...]
    .\\toolkit\\python.exe dev_tools\\chess_decision_audit.py --json <log>

分局：优先 `[ChessMatch] start/end`，老日志退回 `Chess game-over rank OCR` / `completed games`。

输出三段：
1. 每局总表（动作计数、失败计数、经济估算）
2. 异常清单（带时间戳和原始行，便于回查）
3. 每回合紧凑时间线（只列有事件的回合）
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

L_TIME = re.compile(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d+)')
L_GOLD = re.compile(r'\[GOLD[^\]]*\]\s*\[(\d+)\]')
L_LEVEL = re.compile(r'\[LEVEL[^\]]*\]\s*\[([^\]]+)\]')
L_ROUND = re.compile(r'\[ROUND(?:_2)?[^\]]*\]\s*\[(\d+)回目\]')
L_PHASE = re.compile(r'Chess phase detected: mode=(\S+), round=(\d+)')
L_RANK = re.compile(r'game-over rank OCR: \[(.*?)\] -> 第(\d+)名')
L_BUY = re.compile(
    r'Buy Chess card: (\S+) \(slot=(\d+), attempt=(\d+), source=(\w+), '
    r'identity_score=([\d.]+)\)'
)
L_UNCONF = re.compile(
    r'target purchase was not confirmed by avatar disappearance; slot=(\d+)'
)
L_REFRESH = re.compile(r'Refresh Chess shop completed')
L_REFRESH_CMP = re.compile(
    r'shop refresh slot comparison: attempt=(\d+), changed=\[([^\]]*)\], '
    r'changed_count=(\d+)/(\d+), before_names=\[([^\]]*)\], '
    r'after_names=\[([^\]]*)\]'
)
L_EXP = re.compile(r'@ C_EXPERIENCE')
L_MARKET = re.compile(r'@ C_MARKET')
L_DEPLOY = re.compile(r'Chess deploy candidate: (\S+), weight=(\d+), score=([\d.]+)')
L_SELL = re.compile(r'Sell Chess card: type=(\w+), name=(\S+), position=\((\d+), (\d+)\)')
L_SELL_LINEUP = re.compile(
    r'Sell Chess lineup card after hand remains full: name=(\S+), position=\((\d+), (\d+)\)'
)
L_SHOP = re.compile(r'Chess shop recognized \(left->right\): (.*)$')
L_PLAN = re.compile(r'Chess adaptive plan: primary=(\S+), secondary=(\S+),')
L_BELOW = re.compile(
    r'store portrait below threshold \(([\d.]+)\);(?: ocr_name=\[([^\]]*)\];)? '
    r'top candidates: (.*)$'
)
L_NO_CARD = re.compile(
    r'shop slot without detected card: roi=\(([^)]*)\), store_cards_detected=(\d+)'
)
L_GRIGRI = re.compile(
    r'Chess grigri option: option=(\d+), quality=(\S+), ocr=\[([^\]]*)\], '
    r'name=(\S+), category=(\S+), benefit=(\S+), icon=(\S+)\(([\d.]+)\)'
)
L_SOUL_EQUIP_FAIL = re.compile(r'Chess soul equip not confirmed')
L_CAPACITY = re.compile(r'lineup capacity')
L_LEVEL_INVALID = re.compile(r'Chess level OCR invalid')
L_BUY_CANCELLED = re.compile(
    r'Chess buy (\S+) cancelled after (\d+) attempts'
)
L_NO_CANDIDATE = re.compile(r'no deployable lineup hand card candidate')
L_SCREENSHOT_TIMEOUT = re.compile(r'screenshot\(\) call timeout')
L_FALLBACK = re.compile(r'Screenshot method fell back to ADB')
L_TRANSITION_DEPLOY = re.compile(
    r'Chess transition unit deployed: (\S+)\s*->\s*set (\d+)'
)
L_TRANSITION_RECALL = re.compile(
    r'Chess transition unit recalled to hand: (\S+), set=(\d+)'
)
L_DEPLOY_GAP = re.compile(
    r'no deployable lineup hand card candidate \(reason=(\w+)'
)
L_SHOP_SKIP_TRANSITION = re.compile(r'Chess shop skip transition duplicate')
L_SOUL_SUMMARY = re.compile(
    r'Chess soul equip summary: equipped=(\d+)'
)
L_SOUL_SKIP = re.compile(r'skip_reasons=(\{.*\})')
L_BUDGET_STOP = re.compile(r'remaining_time=\d+ < budget=([\d.]+)')

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


def clock_to_seconds(clock):
    """把 HH:MM:SS.mm 转成秒数；解析失败返回 None。"""
    if not clock:
        return None
    try:
        parts = clock.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, IndexError):
        return None


def stamp_to_seconds(stamp):
    """把 'YYYY-MM-DD HH:MM:SS' 转成当天秒数；解析失败返回 None。"""
    if not stamp or ' ' not in stamp:
        return None
    return clock_to_seconds(stamp.split(' ', 1)[1])


def new_game(index):
    return {
        'index': index,
        'start': '',
        'end': '',
        'rank': None,
        'rounds': [],
        'max_round': 0,
        'max_level': 0,
        'gold_samples': [],
        'events': {
            'buy_attempt': 0,
            'buy_ok': 0,
            'buy_unconfirmed': 0,
            'buy_cancelled': 0,
            'refresh': 0,
            'refresh_no_payoff': 0,
            'level_up': 0,
            'market_open': 0,
            'deploy': 0,
            'deploy_stopped_no_candidate': 0,
            'sell_card': 0,
            'sell_lineup_card': 0,
            'soul_equip_failed': 0,
            'capacity_unknown': 0,
            'level_ocr_invalid': 0,
            'no_card_detected': 0,
            'below_threshold': 0,
            'grigri_option': 0,
            'grigri_conflict': 0,
            'screenshot_timeout': 0,
            'fallback': 0,
            'transition_deployed': 0,
            'transition_recalled': 0,
            'soul_equipped': 0,
            'soul_skipped': 0,
            'budget_stops': 0,
            'shop_skip_transition': 0,
            'grigri_decision_icon': 0,
            'grigri_decision_ocr': 0,
        },
        'budget_samples': [],
        'unknown_cells': 0,
        'shop_scans': 0,
        'sold_names': {},
        'bought_names': {},
        'deployed_names': {},
        'anomalies': [],
        'timeline': {},
    }


def parse(path):
    encoding = detect_encoding(path)
    lines = open(path, encoding=encoding, errors='replace').read().splitlines()
    games = []
    current = None
    current_round = None
    pending_buy = None  # 等待确认结果的购买尝试

    def close_pending(ok):
        nonlocal pending_buy
        if pending_buy is None:
            return
        if ok:
            current['events']['buy_ok'] += 1
            # 回填发起购买时的回合（确认可能发生在后面的回合/下一笔购买时）
            rnd = pending_buy.get('round')
            if rnd is not None and rnd in current['timeline']:
                current['timeline'][rnd]['buy_ok'] += 1
            pending_buy = None
            return
        current['events']['buy_unconfirmed'] += 1
        # 与 buy_ok 同样回填到「发起购买」的回合：否则逐回合表里
        # 未确认购买永远是 0，看不出哪一回合在浪费钱。
        rnd = pending_buy.get('round')
        if rnd is not None and rnd in current['timeline']:
            current['timeline'][rnd]['buy_unconfirmed'] += 1
        current['anomalies'].append({
            'time': pending_buy['time'],
            'kind': 'purchase_unconfirmed',
            'detail': f"slot={pending_buy['slot']} name={pending_buy['name']}",
        })
        pending_buy = None

    for line in lines:
        time_match = L_TIME.search(line)
        stamp = time_match.group(1) if time_match else ''
        clock = time_match.group(2) if time_match else ''

        if '[ChessMatch] start index=' in line:
            idx = int(re.search(r'start index=(\d+)', line).group(1))
            current = new_game(idx)
            current['start'] = time_match.group(1) if time_match else ''
            games.append(current)
            pending_buy = None
            current_round = None
            continue
        if current is None:
            rank_match = L_RANK.search(line)
            if rank_match:
                current = new_game(len(games) + 1)
                current['start'] = stamp
                games.append(current)
            else:
                continue

        events = current['events']

        rank_match = L_RANK.search(line)
        if rank_match:
            current['rank'] = int(rank_match.group(2))
            current['end'] = stamp

        if '[ChessMatch] end' in line:
            current['end'] = stamp

        round_match = L_ROUND.search(line)
        if round_match:
            rnd = int(round_match.group(1))
            current['max_round'] = max(current['max_round'], rnd)

        phase_match = L_PHASE.search(line)
        if phase_match:
            # 保持「当前回合」跨行有效：等级/金币/买卡都不带回合号，
            # 之前每行重置成 None 导致时间线全空。
            current_round = int(phase_match.group(2))
            current['max_round'] = max(current['max_round'], current_round)
            row = current['timeline'].setdefault(current_round, {
                'modes': [], 'gold': [], 'level': 0, 'buy_ok': 0,
                'buy_unconfirmed': 0, 'refresh': 0, 'level_up': 0,
                'deploy': 0, 'sell': 0, 'sell_lineup': 0, 'notes': [],
            })
            mode = phase_match.group(1)
            if not row['modes'] or row['modes'][-1] != mode:
                row['modes'].append(mode)
        round_no = current_round

        gold_match = L_GOLD.search(line)
        if gold_match:
            gold = int(gold_match.group(1))
            current['gold_samples'].append(gold)
            if round_no is not None:
                current['timeline'][round_no]['gold'].append(gold)
            # 金币读数在「开/关商店」前后会不会跳变？读错金币会直接导致
            # 「买不起却去点」或「该买却不买」，是经济决策的隐形杀手。
            now_ts = stamp_to_seconds(stamp)
            last_gold = current.get('_last_gold')
            last_ts = current.get('_last_gold_ts')
            if (
                last_gold is not None
                and now_ts is not None
                and last_ts is not None
                and 0 <= now_ts - last_ts <= 3.0
                and gold >= last_gold + 20
            ):
                current['anomalies'].append({
                    'time': stamp,
                    'kind': 'gold_jump',
                    'detail': (
                        f'{last_gold} -> {gold} in '
                        f'{now_ts - last_ts:.1f}s'
                    ),
                })
            current['_last_gold'] = gold
            current['_last_gold_ts'] = now_ts

        level_match = L_LEVEL.search(line)
        if level_match:
            text = level_match.group(1).strip()
            value = LEVEL_VALUE.get(text)
            if value is None:
                digit = re.search(r'([1-9])', text)
                value = int(digit.group(1)) if digit else None
            if value:
                current['max_level'] = max(current['max_level'], value)
                if round_no is not None:
                    current['timeline'][round_no]['level'] = value

        if L_BUY_CANCELLED.search(line):
            events['buy_cancelled'] += 1
            current['anomalies'].append({
                'time': stamp, 'kind': 'buy_cancelled',
                'detail': L_BUY_CANCELLED.search(line).group(0)[:80],
            })

        buy_match = L_BUY.search(line)
        if buy_match:
            close_pending(ok=True)  # 上一次购买若没报失败，视为成功
            name, slot, attempt, source, score = buy_match.groups()
            events['buy_attempt'] += 1
            current['bought_names'][name] = current['bought_names'].get(name, 0) + 1
            pending_buy = {
                'time': stamp, 'name': name, 'slot': int(slot),
                'attempt': int(attempt), 'round': round_no,
            }

        unconf_match = L_UNCONF.search(line)
        if unconf_match:
            slot = int(unconf_match.group(1))
            if pending_buy is not None and pending_buy['slot'] == slot:
                pending_buy['failed'] = True
                close_pending(ok=False)

        if L_REFRESH.search(line):
            events['refresh'] += 1
            if round_no is not None:
                current['timeline'][round_no]['refresh'] += 1

        if L_EXP.search(line):
            events['level_up'] += 1
            if round_no is not None:
                current['timeline'][round_no]['level_up'] += 1

        if L_MARKET.search(line):
            events['market_open'] += 1

        deploy_match = L_DEPLOY.search(line)
        if deploy_match:
            name = deploy_match.group(1)
            events['deploy'] += 1
            current['deployed_names'][name] = current['deployed_names'].get(name, 0) + 1
            if round_no is not None:
                current['timeline'][round_no]['deploy'] += 1

        if L_NO_CANDIDATE.search(line):
            events['deploy_stopped_no_candidate'] += 1

        sell_match = L_SELL.search(line)
        if sell_match:
            events['sell_card'] += 1
            name = sell_match.group(2)
            current['sold_names'][name] = current['sold_names'].get(name, 0) + 1
            if round_no is not None:
                current['timeline'][round_no]['sell'] += 1

        sell_lineup = L_SELL_LINEUP.search(line)
        if sell_lineup:
            events['sell_lineup_card'] += 1
            if round_no is not None:
                current['timeline'][round_no]['sell_lineup'] += 1
            current['anomalies'].append({
                'time': stamp, 'kind': 'sell_lineup_card',
                'detail': f'name={sell_lineup.group(1)} pos=({sell_lineup.group(2)},{sell_lineup.group(3)})',
            })

        if L_SOUL_EQUIP_FAIL.search(line):
            events['soul_equip_failed'] += 1
        soul_summary = L_SOUL_SUMMARY.search(line)
        if soul_summary:
            events['soul_equipped'] += int(soul_summary.group(1))
            # skip_reasons 实盘日志会折行，只有同一行才统计 soul_skipped
            skip_match = L_SOUL_SKIP.search(line)
            if skip_match:
                try:
                    skip = json.loads(skip_match.group(1))
                except (ValueError, TypeError):
                    skip = {}
                events['soul_skipped'] += sum(
                    int(value) for value in skip.values()
                    if isinstance(value, (int, float))
                )
        if L_TRANSITION_DEPLOY.search(line):
            events['transition_deployed'] += 1
        gap = L_DEPLOY_GAP.search(line)
        if gap:
            reason = gap.group(1)
            events.setdefault('deploy_gap', {})
            events['deploy_gap'][reason] = events['deploy_gap'].get(reason, 0) + 1
        if L_SHOP_SKIP_TRANSITION.search(line):
            events['shop_skip_transition'] += 1
        recall = L_TRANSITION_RECALL.search(line)
        if recall:
            events['transition_recalled'] += 1
            current['anomalies'].append({
                'time': stamp, 'kind': 'transition_recalled',
                'detail': f'name={recall.group(1)} set={recall.group(2)}',
            })
        budget_stop = L_BUDGET_STOP.search(line)
        if budget_stop:
            events['budget_stops'] += 1
            current['budget_samples'].append(float(budget_stop.group(1)))
        if L_CAPACITY.search(line) and 'unavailable' in line:
            events['capacity_unknown'] += 1
        if L_LEVEL_INVALID.search(line):
            events['level_ocr_invalid'] += 1
        if L_NO_CARD.search(line):
            events['no_card_detected'] += 1
        below = L_BELOW.search(line)
        if below:
            events['below_threshold'] += 1
            current['anomalies'].append({
                'time': stamp, 'kind': 'portrait_below_threshold',
                'detail': f'threshold={below.group(1)} ocr={below.group(2)} top={below.group(3)[:70]}',
            })
        grigri = L_GRIGRI.search(line)
        if grigri:
            events['grigri_option'] += 1
            ocr_name = grigri.group(3).strip()
            icon_name = grigri.group(7).strip()
            if ocr_name and icon_name not in ('', '未命中') and ocr_name != icon_name:
                events['grigri_conflict'] += 1
                current['anomalies'].append({
                    'time': stamp, 'kind': 'grigri_conflict',
                    'detail': f'ocr={ocr_name} icon={icon_name}({grigri.group(8)})',
                })
            if 'decision=icon' in line:
                events['grigri_decision_icon'] += 1
            elif 'decision=ocr' in line:
                events['grigri_decision_ocr'] += 1
        shop = L_SHOP.search(line)
        if shop:
            cells = [cell.strip() for cell in shop.group(1).split('|')]
            current['shop_scans'] += 1
            unknown = sum(1 for cell in cells if cell == '未识别')
            current['unknown_cells'] += unknown
        if L_SCREENSHOT_TIMEOUT.search(line):
            events['screenshot_timeout'] += 1
        if L_FALLBACK.search(line):
            events['fallback'] += 1

    close_pending(ok=True)
    return {'path': str(path), 'encoding': encoding, 'games': games}


def audit(report):
    print(f"== {report['path']}  (encoding={report['encoding']})")
    for game in report['games']:
        events = game['events']
        golds = game['gold_samples']
        spent = events['level_up'] * 4 + events['refresh'] * 2
        gold_line = (
            f"{min(golds)}/{max(golds)}/{(golds[-1] if golds else 0)}"
            if golds else '-'
        )
        print(
            f"  [#{game['index']}] {game['start'][-8:] or '-':>8} → "
            f"{game['end'][-8:] or '-':>8}  rank={game['rank']} "
            f"rounds={game['max_round']} lv={game['max_level']} "
            f"gold(min/max/last)={gold_line}"
        )
        print(
            f"      buy: attempt={events['buy_attempt']} ok={events['buy_ok']} "
            f"unconfirmed={events['buy_unconfirmed']} cancelled={events['buy_cancelled']} | "
            f"refresh={events['refresh']} level_up={events['level_up']} "
            f"(spent≈{spent}) market_open={events['market_open']}"
        )
        print(
            f"      deploy={events['deploy']} no_candidate={events['deploy_stopped_no_candidate']} | "
            f"sell_card={events['sell_card']} sell_lineup={events['sell_lineup_card']} | "
            f"soul_equip_fail={events['soul_equip_failed']}"
        )
        print(
            f"      recognize: shop_scans={game['shop_scans']} unknown_cells={game['unknown_cells']} "
            f"no_card={events['no_card_detected']} below_thr={events['below_threshold']} | "
            f"capacity_unknown={events['capacity_unknown']} level_ocr_invalid={events['level_ocr_invalid']}"
        )
        print(
            f"      grigri={events['grigri_option']} conflict={events['grigri_conflict']} "
            f"(decision icon/ocr={events['grigri_decision_icon']}/"
            f"{events['grigri_decision_ocr']}) | "
            f"screenshot_timeout={events['screenshot_timeout']} "
            f"adb_fallback={events['fallback']}"
        )
        budgets = game.get('budget_samples') or []
        budget_text = (
            f"avg={sum(budgets) / len(budgets):.1f}s "
            f"min={min(budgets):.1f} max={max(budgets):.1f}"
            if budgets else '-'
        )
        print(
            f"      adapt: transition_deploy={events['transition_deployed']} "
            f"transition_recall={events['transition_recalled']} | "
            f"budget_stops={events['budget_stops']} ({budget_text}) | "
            f"soul equipped/skipped={events['soul_equipped']}/"
            f"{events['soul_skipped']} | "
            f"shop_skip_transition={events['shop_skip_transition']}"
        )
        gaps = events.get('deploy_gap') or {}
        if gaps:
            print(
                '      deploy_gap: '
                + ', '.join(f'{key}={value}' for key, value in sorted(gaps.items()))
            )
        if game['deployed_names']:
            print(f"      deployed={game['deployed_names']}")
        if game['sold_names']:
            print(f"      sold={game['sold_names']}")
        if game['bought_names']:
            print(f"      bought={game['bought_names']}")
        if game['anomalies']:
            print('      anomalies:')
            for item in game['anomalies'][:14]:
                print(f"        {item['time'][-8:]} {item['kind']}: {item['detail']}")
            if len(game['anomalies']) > 14:
                print(f"        ... {len(game['anomalies']) - 14} more")
        rows = game['timeline']
        if rows:
            print('      rounds: rnd modes | lv gold | buyOK buyUnconf refresh '
                  'lvUp deploy sell(sellLineup)')
            for rnd in sorted(rows):
                row = rows[rnd]
                gold = (
                    f"{min(row['gold'])}-{max(row['gold'])}" if row['gold'] else '-'
                )
                modes = ''.join(row['modes'])
                print(
                    f"        {rnd:>2} {modes:<8} | {row['level'] or '-':>2} {gold:<8} | "
                    f"{row['buy_ok']:>5} {row['buy_unconfirmed']:>3} "
                    f"{row['refresh']:>2} {row['level_up']:>2} "
                    f"{row['deploy']:>2} {row['sell']:>2}({row['sell_lineup']})"
                )
        print()


def main() -> int:
    args = [arg for arg in sys.argv[1:]]
    as_json = '--json' in args
    args = [arg for arg in args if arg != '--json']
    if not args:
        print(__doc__)
        return 2
    for name in args:
        path = Path(name)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            print(f'!! 日志不存在: {path}')
            continue
        report = parse(path)
        if as_json:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            audit(report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
