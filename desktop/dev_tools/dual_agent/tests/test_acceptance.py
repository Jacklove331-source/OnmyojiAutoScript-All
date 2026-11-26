# This Python file uses the following encoding: utf-8

"""验收项自测：机器项必须全部通过才能标记 COMPLETED。"""

import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parents[1]))

import acceptance  # noqa: E402
from config import load_config  # noqa: E402
from state import State  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


# 2026-10-28 用户指令：取消指标门限，实战门限改为 AC18（连续 3 局完整且不报错）。
# AC06（基线窗口内的连续局数）由 AC18 取代，故不再强制；
# 其余基础设施/回归项必须全部保留——调整口径不等于降低质量门禁。
REQUIRED = set(acceptance.REQUIRED_AC)
assert {
    'AC01', 'AC02', 'AC03', 'AC04', 'AC05', 'AC05S',
    'AC07', 'AC08', 'AC09', 'AC10', 'AC11',
} <= REQUIRED
assert 'AC18' in REQUIRED, '实战门限必须是 AC18（连续 3 局不报错）'


def test_required_items_declared():
    declared = set(acceptance.SCRIPT_AC) | {'AC05S'}
    check('脚本类验收项覆盖策略/识图/赛季',
          {'AC05', 'AC08', 'AC09', 'AC10'} <= declared, str(sorted(declared)))
    check('人工复核项已声明', len(acceptance.human_pending()) >= 3)


def test_gate_blocks_when_incomplete():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config = load_config()
        state = State(Path(folder), config.project_root)
        check('空状态不能判定完成',
              not acceptance.all_machine_passed(config, state))
        for ac_id in REQUIRED:
            state.record_acceptance(ac_id, 't', True, 'ok')
        check('全部机器项通过后允许 COMPLETED',
              acceptance.all_machine_passed(config, state))
        # 翻转一个**仍在强制清单里**的项（AC18 实战门限），验证门禁真的会挡
        state.record_acceptance('AC18', '连续 3 局不报错', False, 'no clean series')
        check('任一项回退则不再允许 COMPLETED',
              not acceptance.all_machine_passed(config, state))
        check('AC18 被列为强制项（实战门限不可绕过）',
              'AC18' in acceptance.REQUIRED_AC, str(acceptance.REQUIRED_AC))
        state.close()


def test_chess_check_reads_new_entries_only():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config = load_config()
        state = State(Path(folder), config.project_root)
        state.set_meta('session_start', '2026-10-27 23:00:00')
        matches = config.project_root / 'log' / 'chess_matches.jsonl'
        lines = 0
        if matches.exists():
            lines = len([line for line in matches.read_text(
                encoding='utf-8', errors='replace').splitlines() if line.strip()])
        state.set_meta('matches_baseline_lines', lines)
        result = acceptance.check_chess_series(config, state)
        check('会话开始后没有对局 → 未通过', not result.ok, result.detail[:160])
        state.set_meta('matches_baseline_lines', 0)
        result2 = acceptance.check_chess_series(config, state)
        check('基线设为 0 时会把历史完整对局算进来',
              result2.ok or '基线行 0' in result2.detail, result2.detail[:160])
        state.close()


def test_report_requirement():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config = load_config()
        state = State(Path(folder), config.project_root)
        result_before = acceptance.check_final_report(config, state)
        check('没有最终报告 → AC12 未通过', not result_before.ok)
        (Path(folder) / 'FINAL_REPORT.md').write_text(
            '# report\n' + 'evidence line\n' * 200, encoding='utf-8'
        )
        result_after = acceptance.check_final_report(config, state)
        check('报告存在且足够长 → AC12 通过', result_after.ok, result_after.detail)
        state.close()


def test_final_report_not_a_gate():
    check('AC12 不在完成前置集合中', 'AC12' not in REQUIRED)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config = load_config()
        state = State(Path(folder), config.project_root)
        for ac_id in REQUIRED:
            state.record_acceptance(ac_id, 't', True, 'ok')
        check('AC12 未有记录时仍允许 COMPLETED',
              acceptance.all_machine_passed(config, state))
        state.record_acceptance('AC12', '最终报告', False, '待生成')
        check('AC12=FAIL 也不阻塞 COMPLETED',
              acceptance.all_machine_passed(config, state))
        state.record_acceptance('AC11', '稳定版本与文档', False, 'missing tag')
        check('仅放宽 AC12：AC11 回退仍禁止 COMPLETED',
              not acceptance.all_machine_passed(config, state))
        state.close()


def main():
    for case in (test_required_items_declared, test_gate_blocks_when_incomplete,
                 test_chess_check_reads_new_entries_only, test_report_requirement,
                 test_final_report_not_a_gate):
        print(f'--- {case.__name__}')
        case()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('DUAL AGENT ACCEPTANCE: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
