# This Python file uses the following encoding: utf-8
"""chess_decision_audit 指标口径回归：合成日志验证逐回合 buy_ok 与御魂/转场计数。

覆盖两处实盘口径修复：
1. 购买确认成功时，把 buy_ok 记到「发起购买时的回合」，而不是确认发生的回合；
2. 御魂 summary 只要求 `Chess soul equip summary: equipped=(\\d+)`，
   同一行有 skip_reasons 才统计 soul_skipped（实盘日志会折行到下一行）。

不依赖 D:/3D/YYS2/_recovery_070208 下的真实日志。
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chess_decision_audit as audit

checks = []


def check(name, condition):
    checks.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")


def parse_text(text):
    """把合成日志写入临时文件，走 detect_encoding + parse 的真实路径。"""
    fd, path = tempfile.mkstemp(suffix='.log', prefix='chess_audit_check_')
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)
    try:
        return audit.parse(path)
    finally:
        os.remove(path)


# 场景 A：跨回合购买确认 + 折行御魂 summary + 转场部署
log_a = """2026-10-28 22:42:58.000 [ChessMatch] start index=1
2026-10-28 22:43:00.000 Chess phase detected: mode=battle, round=1
2026-10-28 22:43:01.000 Buy Chess card: shi (slot=2, attempt=1, source=shop, identity_score=0.95)
2026-10-28 22:43:02.000 Chess phase detected: mode=battle, round=2
2026-10-28 22:43:03.000 Buy Chess card: gou (slot=3, attempt=1, source=shop, identity_score=0.91)
2026-10-28 22:43:04.000 target purchase was not confirmed by avatar disappearance; slot=3
2026-10-28 22:43:05.000 Buy Chess card: tu (slot=4, attempt=1, source=shop, identity_score=0.93)
2026-10-28 22:43:06.000 Chess soul equip summary: equipped=1, souls_left=1, verified_targets=3,
2026-10-28 22:43:06.500 skip_reasons={"no_room": 1, "low_gold": 2}
2026-10-28 22:43:07.000 Chess soul equip summary: equipped=2, souls_left=0, verified_targets=2,
2026-10-28 22:43:08.000 Chess soul equip not confirmed
2026-10-28 22:43:09.000 Chess transition unit deployed: yao -> set 2
2026-10-28 22:43:10.000 Chess transition unit deployed: qin -> set 3
2026-10-28 22:43:11.000 Chess phase detected: mode=battle, round=3
2026-10-28 22:43:12.000 Chess soul equip summary: equipped=1, souls_left=2, verified_targets=1, skip_reasons={"no_room": 2}
2026-10-28 22:43:13.000 [ChessMatch] end
"""

report_a = parse_text(log_a)
games_a = report_a['games']
game_a = games_a[0]
ev_a = game_a['events']
tl_a = game_a['timeline']

check('A: 单局且 index=1', len(games_a) == 1 and game_a['index'] == 1)
check('A: events.buy_attempt == 3', ev_a['buy_attempt'] == 3)
check('A: events.buy_ok == 2', ev_a['buy_ok'] == 2)
check('A: events.buy_unconfirmed == 1', ev_a['buy_unconfirmed'] == 1)
check('A: timeline[1].buy_ok == 1（记到发起回合）', tl_a.get(1, {}).get('buy_ok') == 1)
check('A: timeline[2].buy_ok == 1（EOF 确认也回填）', tl_a.get(2, {}).get('buy_ok') == 1)
check('A: timeline[2].buy_unconfirmed == 1', tl_a.get(2, {}).get('buy_unconfirmed') == 1)
check('A: timeline[3].buy_ok == 0（回合3无购买）', tl_a.get(3, {}).get('buy_ok') == 0)
check(
    'A: 逐回合 buy_ok 之和 == events.buy_ok',
    sum(row.get('buy_ok', 0) for row in tl_a.values()) == ev_a['buy_ok'],
)
check('A: soul_equipped == 4（折行 1+2 与同行 1）', ev_a['soul_equipped'] == 4)
check('A: soul_skipped == 2（仅同行的 skip 计入）', ev_a['soul_skipped'] == 2)
check('A: soul_equip_failed == 1', ev_a['soul_equip_failed'] == 1)
check('A: transition_deployed == 2', ev_a['transition_deployed'] == 2)
try:
    json.dumps(report_a, ensure_ascii=False, default=str)
    check('A: --json 输出可序列化（结构兼容）', True)
except TypeError:
    check('A: --json 输出可序列化（结构兼容）', False)

# 场景 B：老日志（rank 回退成局），购买确认隔一个回合发生
log_b = """2026-10-28 22:44:00.000 game-over rank OCR: [七] -> 第8名
2026-10-28 22:44:01.000 Chess phase detected: mode=battle, round=1
2026-10-28 22:44:02.000 Buy Chess card: ma (slot=1, attempt=1, source=shop, identity_score=0.90)
2026-10-28 22:44:03.000 Chess phase detected: mode=battle, round=2
2026-10-28 22:44:04.000 Chess phase detected: mode=battle, round=3
2026-10-28 22:44:05.000 Buy Chess card: hou (slot=1, attempt=1, source=shop, identity_score=0.92)
"""

report_b = parse_text(log_b)
games_b = report_b['games']
game_b = games_b[0]
ev_b = game_b['events']
tl_b = game_b['timeline']

check('B: rank 回退也能成局且 rank=8', len(games_b) == 1 and game_b['rank'] == 8)
check('B: events.buy_ok == 2', ev_b['buy_ok'] == 2)
check('B: timeline[1].buy_ok == 1（跨回合确认记到发起回合）', tl_b.get(1, {}).get('buy_ok') == 1)
check('B: timeline[2].buy_ok == 0（中间回合不被误记）', tl_b.get(2, {}).get('buy_ok') == 0)
check('B: timeline[3].buy_ok == 1', tl_b.get(3, {}).get('buy_ok') == 1)
check(
    'B: 逐回合 buy_ok 之和 == events.buy_ok',
    sum(row.get('buy_ok', 0) for row in tl_b.values()) == ev_b['buy_ok'],
)

passed = sum(1 for _, ok in checks if ok)
failed = len(checks) - passed
print(f'TOTAL {len(checks)} PASS {passed} FAIL {failed}')
if failed:
    print('CHESS AUDIT METRICS: FAILED')
    sys.exit(1)
print('CHESS AUDIT METRICS: PASSED')
sys.exit(0)
