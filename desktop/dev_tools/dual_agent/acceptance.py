# This Python file uses the following encoding: utf-8

"""验收项：把「最终完成判定标准」转成机器可检查的检查项。

每项 AC 都有明确的检查方式（跑脚本 / 查 DB / 查日志 / 查 git），
调度器在每轮结束后刷新一遍，只有**全部机器项通过**才允许标记 COMPLETED。
人工判断项单独标记，不阻塞状态机，但会写进最终报告。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from config import Config
from safety import run_guarded

SCRIPT_AC = {
    'AC05': (
        'OAS 行为级回归',
        [
            'dev_tools/ui_click_regression.py',
            'dev_tools/adb_recovery_unit.py',
            'dev_tools/chess_level_cache_check.py',
            'dev_tools/click_record_check.py',
            'dev_tools/gold_and_record_check.py',
            'dev_tools/screenshot_fallback_check.py',
            'dev_tools/test_indexerror_boundary.py',
        ],
    ),
    'AC08': (
        '策略修复有回归测试',
        [
            'dev_tools/chess_transition_check.py',
            'dev_tools/chess_phase_budget_check.py',
            # 过渡位限购 + 上阵缺口分类（2026-10-28 新增逻辑，必须被机器验证覆盖）
            'dev_tools/chess_buy_deploy_guard_test.py',
        ],
    ),
    'AC09': (
        '符咒冲突与御魂操作有验证',
        [
            'dev_tools/grigri_conflict_check.py',
            'dev_tools/chess_soul_target_skip_test.py',
        ],
    ),
    'AC10': (
        '赛季数据核对',
        ['dev_tools/season_data_check.py'],
    ),
    # v2：实战指标的「尺子」本身要有测试（逐回合购买归属、折行日志、御魂成败统计）
    'AC19': (
        '棋局模块自检（tasks/Chess/selftest.py）',
        ['tasks/Chess/selftest.py'],
    ),
    'AC17T': (
        '实战指标统计正确性测试',
        ['dev_tools/chess_audit_metrics_check.py'],
    ),
}

STATIC_AC = {
    'AC05S': ('静态检查无问题', [
        ('dev_tools/contract_scan.py', ['Chess']),
        ('dev_tools/missing_method_check.py', []),
    ]),
}

HUMAN_REVIEW_ITEMS = (
    ('H1', '策略是否真的更强（名次/回合/资源利用趋势，不能只凭 1~2 局）'),
    ('H2', '双 Agent 的任务选择与审核质量是否合理'),
    ('H3', '是否存在脚本无法发现的游戏规则误判'),
)


@dataclass
class ACResult:
    ac_id: str
    title: str
    ok: bool
    detail: str = ''
    human: bool = False


def _tail(text: str, lines: int = 3, limit: int = 600) -> str:
    """末尾几行 + 所有失败行（审核方不用再去推演「到底哪条没过」，省 token）。"""
    all_lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    failures = [line for line in all_lines
                if ('FAIL' in line or 'Traceback' in line or 'Error' in line)
                and 'FAIL 0' not in line][:5]
    picked = failures + [line for line in all_lines[-lines:] if line not in failures]
    return ' | '.join(picked)[:limit + 400 * bool(failures)]


def _run_script(config: Config, script: str, extra: list[str] | None = None,
                timeout: int = 1800) -> tuple[bool, str]:
    path = config.project_root / script
    if not path.exists():
        return False, f'缺少脚本 {script}'
    args = [str(config.python), script, *(extra or [])]
    code, out = run_guarded(config, args, timeout=timeout)
    ok = code == 0
    return ok, f'{script} rc={code} :: {_tail(out)}'


def check_scripts(config: Config, ac_id: str, extra: list[str] | None = None
                  ) -> ACResult:
    title, scripts = SCRIPT_AC[ac_id]
    failures = []
    details = []
    for script in scripts:
        ok, detail = _run_script(config, script, extra)
        details.append(detail)
        if not ok:
            failures.append(detail)
    return ACResult(
        ac_id, title, not failures,
        '; '.join(failures) if failures else _tail('; '.join(details), 2, 900),
    )


def check_static(config: Config, ac_id: str = 'AC05S') -> ACResult:
    title, commands = STATIC_AC[ac_id]
    problems = []
    for script, extra in commands:
        args = [str(config.python), script, *extra]
        code, out = run_guarded(config, args, timeout=900)
        text = out or ''
        ok = code == 0 and 'problems 0' in text
        if not ok:
            problems.append(f'{script} rc={code} :: {_tail(text, 2)}')
    return ACResult(ac_id, title, not problems,
                    '; '.join(problems) if problems else 'contract/method scan ok')


def check_dual_agent_tests(config: Config) -> ACResult:
    ok, detail = _run_script(
        config, 'dev_tools/dual_agent/tests/run_all.py', timeout=1800
    )
    return ACResult('AC01', '双 Agent 调度器自测', ok, detail)


def check_dual_agent_roundtrip(config: Config, state) -> ACResult:
    """v2：审核方必须是**真 Claude**（provider=anthropic），执行方是 DeepSeek。

    v1 的 runs 全部走 claude.CMD → DeepSeek，不能再算作「Claude 审核」。
    """
    rows = state.conn.execute(
        "SELECT role, COALESCE(provider,'deepseek') AS provider, ok, COUNT(*) AS c "
        'FROM runs GROUP BY role, provider, ok'
    ).fetchall()
    stats = {(row['role'], row['provider'], row['ok']): row['c'] for row in rows}
    a_claude = stats.get(('agent_a', 'anthropic', 1), 0)
    b_deepseek = stats.get(('agent_b', 'deepseek', 1), 0)
    passed_by_claude = state.conn.execute(
        "SELECT COUNT(DISTINCT r.task_key) AS c FROM reviews r JOIN tasks t "
        "ON t.task_key = r.task_key WHERE r.verdict='PASS' AND r.provider='anthropic' "
        "AND t.phase='accepted'"
    ).fetchone()['c']
    ok = a_claude >= 1 and b_deepseek >= 1 and passed_by_claude >= 1
    return ACResult(
        'AC02', 'Claude 规划/审核 → DeepSeek 执行 真实协作（非模拟、非人工转发）', ok,
        f'claude_ok_calls={a_claude} deepseek_ok_calls={b_deepseek} '
        f'tasks_passed_by_claude={passed_by_claude}',
    )


def check_rework_cycle(config: Config, state) -> ACResult:
    """返工闭环：同一任务先被 Claude 判 REWORK、之后又被 Claude 判 PASS。"""
    rows = state.conn.execute(
        "SELECT task_key, verdict, id FROM reviews WHERE provider='anthropic' "
        'ORDER BY task_key, id'
    ).fetchall()
    seen_rework: set = set()
    closed = []
    for row in rows:
        if row['verdict'] == 'REWORK':
            seen_rework.add(row['task_key'])
        elif row['verdict'] == 'PASS' and row['task_key'] in seen_rework:
            closed.append(row['task_key'])
    return ACResult(
        'AC02R', '规划→执行→审核→返工→再审核通过 的完整闭环', bool(closed),
        f'closed_rework_cycles={sorted(set(closed))[:5]}' if closed
        else f'claude 判过 REWORK 的任务 {sorted(seen_rework)[:5]}，尚无返工后通过',
    )


def check_persistence(config: Config) -> ACResult:
    ok, detail = _run_script(config, 'dev_tools/dual_agent/tests/test_persistence.py')
    return ACResult('AC03', '任务持久化与恢复', ok, detail)


def check_budget_guard(config: Config) -> ACResult:
    ok, detail = _run_script(config, 'dev_tools/dual_agent/tests/test_budget.py')
    return ACResult('AC04', 'Token/费用预算与安全停止', ok, detail)


def check_chess_series(config: Config, state) -> ACResult:
    """会话开始后是否至少完成一局（完整流程到结算+退出）。

    注意：`chess_matches.jsonl` 的 `time` 是**开局**时间，一局要跑 20~30 分钟，
    所以不能用「time >= session_start」筛（会漏掉会话开始时正在打的那局）。
    这里改用行号基线：调度器启动时记下当时已有多少行，之后追加的行才算本会话。
    """
    matches = config.project_root / 'log' / 'chess_matches.jsonl'
    baseline = int(state.get_meta('matches_baseline_lines', 0) or 0)
    need = int(config.safety.get('chess_series_games', 3) or 3)
    completed = []
    best_streak = 0
    streak = 0
    total = 0
    if matches.exists():
        lines = matches.read_text(
            encoding='utf-8', errors='replace').splitlines()
        total = len([line for line in lines if line.strip()])
        for index, line in enumerate(lines):
            line = line.strip()
            if not line or index < baseline:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            good = bool(record.get('match_completed') and record.get('exit_confirmed')
                        and isinstance(record.get('final_rank'), int))
            if good:
                completed.append(
                    f"{record.get('time')} rank={record.get('final_rank')} "
                    f"rounds={record.get('round_reached')}"
                )
                # 同一个连测序列内的连续完成（index 从 1 重新开始 = 新序列）
                if int(record.get('index') or 0) == 1:
                    streak = 1
                else:
                    streak += 1
                best_streak = max(best_streak, streak)
            else:
                streak = 0
    ok = best_streak >= need
    detail = (
        f'基线后完成 {len(completed)} 局，最长连续 {best_streak}/{need} '
        f'（基线行 {baseline}/{total}）: ' + '; '.join(completed[-3:])
    )
    return ACResult('AC06', f'百鬼棋局连续 {need} 局完整流程（进局→结算→退出）',
                    ok, detail)


def check_recovery_evidence(config: Config, state) -> ACResult:
    log = config.project_root / 'log' / 'device_connection.jsonl'
    recovered = 0
    if log.exists():
        for line in log.read_text(encoding='utf-8', errors='replace').splitlines():
            if '"recovered": true' in line.replace(' ', ''):
                recovered += 1
    ok, detail = _run_script(config, 'dev_tools/adb_recovery_unit.py')
    ok2, detail2 = _run_script(config, 'dev_tools/screenshot_fallback_check.py')
    return ACResult(
        'AC07', 'ADB/截图异常恢复机制', ok and ok2,
        f'device_connection_recovered={recovered}; {detail}; {detail2}',
    )


def check_stable_docs(config: Config) -> ACResult:
    handoff = config.project_root / 'docs' / 'AI_HANDOFF.md'
    notes = config.project_root / 'tasks' / 'Chess' / 'DEV_NOTES.md'
    handoff_text = handoff.read_text(encoding='utf-8', errors='replace')[:200000] if handoff.exists() else ''
    notes_text = notes.read_text(encoding='utf-8', errors='replace')[:200000] if notes.exists() else ''
    tags = subprocess.run(
        ['git', 'tag', '--list', 'stable-*'],
        cwd=str(config.project_root), capture_output=True, text=True,
        encoding='utf-8', errors='replace',
    ).stdout.strip()
    has_handoff = '双 Agent' in handoff_text and '调度器 v2' in handoff_text
    has_notes = '十四、策略与识图优化' in notes_text
    ok = bool(tags) and has_handoff and has_notes
    return ACResult(
        'AC11', '稳定版本与文档（含调度器 v2 交接）', ok,
        f'tags={tags.splitlines() if tags else "无"} handoff(v2)={has_handoff} notes={has_notes}',
    )


# ----------------------------------------------------------------------
# v2：实战过程指标验收（数据源 metrics.py；阈值写死在这里，不随结果漂移）
# 评估窗口 = 调度器 v2 启动（meta v2_started_at）之后**开局**的已完成对局，取最近 3 局。
METRIC_GAMES = 3
THRESHOLDS = {
    'AC13': {'avg_early_buy_ok': 2.0, 'avg_early_deploy': 3.0, 'games_with_transition': 2},
    'AC14': {'buy_confirm_rate': 0.90, 'avg_buy_unconfirmed': 2.0,
             'avg_screenshot_timeout': 2.0},
    'AC15': {'soul_equipped': 3, 'soul_success_rate': 0.75},
    'AC16': {'avg_level_ocr_invalid': 5.0, 'avg_unknown_cells': 3.0},
}


def _metric_window(config: Config, state) -> tuple[list[dict], str]:
    import metrics  # 延迟导入：只有指标验收用得到

    since = str(state.get_meta('v2_started_at', '') or '')
    if not since:
        return [], ''
    games = [game for game in metrics.window_games(config.project_root, since)
             if game['completed']]
    return games[-METRIC_GAMES:], since


def check_metrics(config: Config, state) -> list[ACResult]:
    import metrics

    games, since = _metric_window(config, state)
    summary = metrics.summarize(games) if games else {}
    enough = len(games) >= METRIC_GAMES
    prefix = (f'窗口(≥{since or "未设置"}) 完成局 {len(games)}/{METRIC_GAMES}'
              + ('' if enough else '（证据不足）'))
    results = []

    # 用户 2026-10-28 指令：取消指标门限——成功标准只看「连续 3 局不报错」。
    # 指标仍然采集并展示，只作参考，不参与 PASS/FAIL。
    info_ok = bool(games)
    results.append(ACResult(
        'AC13', '参考指标：前期过渡/购买/动态上阵（不设门限）', info_ok,
        prefix + (f" | early_buy={summary.get('avg_early_buy_ok')}"
                  f" early_deploy={summary.get('avg_early_deploy')}"
                  f" transition_games={summary.get('games_with_transition')}"
                  if summary else '')))
    results.append(ACResult(
        'AC14', '参考指标：经济/时间预算（不设门限）', info_ok,
        prefix + (f" | confirm={summary.get('buy_confirm_rate')}"
                  f" unconfirmed={summary.get('avg_buy_unconfirmed')}"
                  f" shot_timeout={summary.get('avg_screenshot_timeout')}"
                  if summary else '')))
    results.append(ACResult(
        'AC15', '参考指标：御魂装备（不设门限）', info_ok,
        prefix + (f" | equipped={summary.get('soul_equipped')}"
                  f" failed={summary.get('soul_equip_failed')}"
                  f" rate={summary.get('soul_success_rate')}" if summary else '')))
    results.append(ACResult(
        'AC16', '参考指标：识图健康度（不设门限）', info_ok,
        prefix + (f" | level_ocr_invalid={summary.get('avg_level_ocr_invalid')}"
                  f" unknown={summary.get('avg_unknown_cells')}"
                  f" grigri={summary.get('grigri_conflict')}/{summary.get('grigri_decisions')}"
                  if summary else '')))
    results.append(check_strategy_compare(config, state, games, summary))
    return results


def check_strategy_compare(config: Config, state, games: list[dict],
                           summary: dict) -> ACResult:
    """对照：评估窗口 vs 固定基线（稳定版连续三局），过程指标必须全面变好。"""
    import metrics

    base_games = metrics.baseline_games(config.project_root)
    base = metrics.summarize(base_games) if base_games else {}
    enough = len(games) >= METRIC_GAMES and base.get('completed', 0) >= 3
    keys = ('avg_early_buy_ok', 'avg_early_deploy', 'buy_confirm_rate')
    # 只作参考：有数据即记录，不再要求「全面变好」
    better = bool(games)
    report = Path(getattr(state, 'dir', config.state_dir)) / 'strategy_compare.md'
    lines = ['# 策略对照（自动生成，`acceptance.check_strategy_compare`）', '',
             f'- 基线: {", ".join(metrics.BASELINE_LOGS)}（{base.get("completed", 0)} 局）',
             f'- 评估窗口: v2 启动后最近 {len(games)} 局', '',
             '| 指标 | 基线 | 窗口 |', '| --- | --- | --- |']
    for key in ('avg_rank', 'avg_early_buy_ok', 'avg_early_deploy', 'buy_confirm_rate',
                'avg_buy_unconfirmed', 'soul_equipped', 'soul_success_rate',
                'avg_level_ocr_invalid', 'avg_unknown_cells'):
        lines.append(f'| {key} | {base.get(key, "-")} | {summary.get(key, "-")} |')
    lines += ['', '## 窗口逐局', '', '```', metrics.format_table(games), '```']
    try:
        report.write_text('\n'.join(lines), encoding='utf-8')
    except OSError:
        pass
    return ACResult(
        'AC17', '参考：策略对照证据（不设门限）', better,
        f'base={ {key: base.get(key) for key in keys} } '
        f'window={ {key: summary.get(key) for key in keys} } report={report.name}',
    )


def check_season_research(config: Config) -> ACResult:
    """当前赛季关键数据已核实：研究记录需有来源、日期在 14 天内、有实质内容。"""
    import re
    from datetime import timedelta

    path = config.project_root / 'docs' / 'research' / 'season_verification.md'
    if not path.exists():
        return ACResult('AC10R', '当前赛季数据联网核实记录', False,
                        f'缺少 {path.relative_to(config.project_root)}')
    text = path.read_text(encoding='utf-8', errors='replace')
    links = set(re.findall(r'https?://[^\s)>\]]+', text))
    dates = re.findall(r'核实日期[:：]\s*(\d{4}-\d{2}-\d{2})', text)
    fresh = False
    for value in dates:
        try:
            fresh = fresh or (datetime.now() - datetime.strptime(value, '%Y-%m-%d')
                              <= timedelta(days=14))
        except ValueError:
            continue
    ok = len(links) >= 3 and fresh and len(text) >= 1500
    return ACResult('AC10R', '当前赛季数据联网核实记录', ok,
                    f'links={len(links)} dates={dates[:3]} chars={len(text)}')


def check_clean_series(config: Config, state, need: int = 3) -> ACResult:
    """门限（用户 2026-10-28 指定）：连续 need 局完整跑完且不报错。

    「完整」= 结算 OCR 成功 + 退出回大厅确认；「不报错」= 该局记录 error_kind 为空，
    且在这几局的时间窗内没有新的崩溃目录 `log/error/<ts>` 产生。
    这条替代原来的 AC06（连续局数）与所有指标门限。
    """
    matches = config.project_root / 'log' / 'chess_matches.jsonl'
    if not matches.exists():
        return ACResult('AC18', f'连续 {need} 局完整且不报错', False,
                        '缺少 chess_matches.jsonl')
    records = []
    for line in matches.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    if not records:
        return ACResult('AC18', f'连续 {need} 局完整且不报错', False, '无逐局记录')
    tail = records[-need:]
    problems = []
    for index, record in enumerate(tail, start=1):
        issues = []
        if not record.get('match_completed'):
            issues.append('match_completed=False')
        if not record.get('exit_confirmed'):
            issues.append('exit_confirmed=False')
        if not record.get('game_over_detected'):
            issues.append('game_over_detected=False')
        if record.get('error_kind'):
            issues.append(f"error_kind={record['error_kind']}")
        if record.get('final_rank') is None:
            issues.append('final_rank=None')
        if not record.get('round_reached'):
            issues.append('round_reached=0')
        if issues:
            problems.append(
                f"{record.get('time')}: " + ','.join(issues))
    # 这几局时间窗内是否产生崩溃目录
    error_dirs = []
    if tail and tail[0].get('time'):
        try:
            from datetime import datetime
            start = datetime.strptime(str(tail[0]['time']),
                                      '%Y-%m-%d %H:%M:%S').timestamp()
        except ValueError:
            start = 0.0
        error_root = config.project_root / 'log' / 'error'
        if error_root.exists():
            for child in error_root.iterdir():
                try:
                    if child.is_dir() and child.stat().st_mtime >= start:
                        error_dirs.append(child.name)
                except OSError:
                    continue
    ok = len(tail) >= need and not problems and not error_dirs
    detail = (f'最近 {len(tail)} 局: ' + '; '.join(
        f"{record.get('time')} rank={record.get('final_rank')} "
        f"rounds={record.get('round_reached')}" for record in tail)
        + (f' | 崩溃目录={error_dirs[:3]}' if error_dirs else '')
        + (f' | 问题={problems}' if problems else ''))
    return ACResult('AC18', f'连续 {need} 局完整且不报错', ok, detail)


def check_final_report(config: Config, state) -> ACResult:
    report = Path(getattr(state, 'dir', config.state_dir)) / 'FINAL_REPORT.md'
    ok = report.exists() and report.stat().st_size > 1200
    detail = f'{report.name} size={report.stat().st_size if report.exists() else 0}'
    return ACResult('AC12', '最终报告（含证据与剩余限制）', ok, detail)


def run_all(config: Config, state) -> list[ACResult]:
    results = [
        check_dual_agent_tests(config),
        check_dual_agent_roundtrip(config, state),
        check_rework_cycle(config, state),
        check_persistence(config),
        check_budget_guard(config),
        check_recovery_evidence(config, state),
        check_scripts(config, 'AC05'),
        check_static(config),
        check_scripts(config, 'AC08'),
        check_scripts(config, 'AC09'),
        check_scripts(config, 'AC10', [str(log) for log in _log_args(config)]),
        check_scripts(config, 'AC17T'),
        check_scripts(config, 'AC19', ['--quick']),
        check_season_research(config),
        check_chess_series(config, state),
        check_clean_series(config, state),
        check_stable_docs(config),
        *check_metrics(config, state),
        check_final_report(config, state),
    ]
    for result in results:
        state.record_acceptance(result.ac_id, result.title, result.ok,
                                result.detail)
    return results


def _log_args(config: Config) -> list[Path]:
    logs = sorted(
        Path('D:/3D/YYS2/_recovery_070208').glob('chess_*.log'),
        key=lambda path: path.stat().st_mtime,
    )
    return logs[-3:]


def snapshot(config: Config, state, quick: bool = True) -> list[ACResult]:
    """quick=True 只查状态类（不跑测试），避免每轮都花几分钟。"""
    if quick:
        results = [
            check_dual_agent_roundtrip(config, state),
            check_rework_cycle(config, state),
            check_chess_series(config, state),
            check_clean_series(config, state),
            check_stable_docs(config),
            check_season_research(config),
            check_final_report(config, state),
        ]
        # 指标验收要跑审计脚本（几秒~几十秒），最多每 10 分钟刷新一次
        import time as _time
        last = float(state.get_meta('metrics_checked_epoch', 0) or 0)
        if _time.time() - last >= 600:
            try:
                results.extend(check_metrics(config, state))
            except Exception as error:  # noqa: BLE001 - 指标失败不能拖垮调度
                results.append(ACResult('AC13', '前期低费过渡/购买/动态上阵（实战 1~8 回合）',
                                        False, f'metrics error: {error}'))
            state.set_meta('metrics_checked_epoch', _time.time())
        for result in results:
            state.record_acceptance(result.ac_id, result.title, result.ok,
                                    result.detail)
        return results
    return run_all(config, state)


# AC12 是 finish() 的产物而非前置条件：若把它列进门槛，
# 就会形成「报告要求先完成、完成又要求报告」的死锁。
# 2026-10-28 用户指令：取消指标门限，成功标准 = 连续 3 局完整且不报错（AC18）。
# AC13~AC17 降为参考指标、AC10R（赛季文档）不再作为完成前置。
REQUIRED_AC = (
    'AC01', 'AC02', 'AC02R', 'AC03', 'AC04', 'AC05', 'AC05S',
    'AC07', 'AC08', 'AC09', 'AC10', 'AC11', 'AC17T', 'AC18', 'AC19',
)


def all_machine_passed(config: Config, state) -> bool:
    rows = state.acceptance_rows()
    passed = {row['ac_id'] for row in rows if row['ok']}
    return set(REQUIRED_AC) <= passed


def final_gate(config: Config, state) -> tuple[bool, list[str]]:
    """完成前**重新全量**跑一遍（不信任旧的 PASS 记录），返回 (通过?, 未过项)。"""
    results = run_all(config, state)
    passed = {result.ac_id for result in results if result.ok}
    missing = [ac for ac in REQUIRED_AC if ac not in passed]
    return not missing, missing


def acceptance_spec() -> list[dict]:
    """验收清单（给提示词/报告用）：编号 + 标题 + 是否强制。"""
    titles = {
        'AC01': '双 Agent 调度器自测（tests/run_all.py）',
        'AC02': 'Claude 规划/审核 → DeepSeek 执行 真实协作',
        'AC02R': '返工闭环（Claude 判 REWORK → 返工 → Claude 判 PASS）',
        'AC03': '任务持久化与恢复', 'AC04': 'Token/费用预算与安全停止',
        'AC05': 'OAS 行为级回归（无已知回归）', 'AC05S': '静态检查（契约/缺失方法）',
        'AC06': '百鬼棋局连续 3 局完整流程', 'AC07': 'ADB/截图异常恢复',
        'AC08': '策略修复回归测试', 'AC09': '符咒冲突与御魂操作测试',
        'AC10': '赛季数据自洽 + 实机日志全命中', 'AC10R': '当前赛季联网核实记录',
        'AC11': '稳定 tag + 交接文档（含调度器 v2）',
        'AC18': '连续 3 局完整对局且不报错（唯一实战门限）',
        'AC19': '棋局模块自检（tasks/Chess/selftest.py --quick）',
        'AC13': f"参考指标：前期过渡（早期买均值{THRESHOLDS['AC13']['avg_early_buy_ok']}），"
                f"早期上阵≥{THRESHOLDS['AC13']['avg_early_deploy']}/局，过渡位≥2/3 局）",
        'AC14': '经济/时间预算（购买确认≥90%，动态预算每局生效）',
        'AC15': '御魂装备（≥3 次成功且成功率≥75%）',
        'AC16': '识图健康（等级 OCR 无效≤5/局，未识别≤3/局，符咒冲突全裁决）',
        'AC17': '策略对照：过程指标全面优于稳定基线（3 局 vs 3 局）',
        'AC17T': '实战指标统计正确性测试（dev_tools/chess_audit_metrics_check.py）',
    }
    return [{'id': ac, 'title': titles.get(ac, ac), 'required': True} for ac in REQUIRED_AC]


def human_pending() -> list[dict]:
    return [{'id': item[0], 'title': item[1]} for item in HUMAN_REVIEW_ITEMS]
