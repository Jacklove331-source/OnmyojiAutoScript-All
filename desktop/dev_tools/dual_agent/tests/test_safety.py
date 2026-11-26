# This Python file uses the following encoding: utf-8

"""安全边界测试：危险命令/受保护路径/单实机进程约束。"""

import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parents[1]))

import safety  # noqa: E402
from config import load_config  # noqa: E402
from state import State  # noqa: E402

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f'[{"PASS" if condition else "FAIL"}] {name}'
          + (f'  ({detail})' if detail and not condition else ''))


def expect_safety_error(fn, *args):
    try:
        fn(*args)
        return False
    except safety.SafetyError:
        return True


def test_forbidden_git():
    config = load_config()
    check('禁止 git reset --hard',
          expect_safety_error(safety.check_git_argv, config, ['reset', '--hard']))
    check('禁止 git clean',
          expect_safety_error(safety.check_git_argv, config, ['clean', '-fdx']))
    check('允许 git status',
          not expect_safety_error(safety.check_git_argv, config, ['status']))


def test_forbidden_shell():
    config = load_config()
    check('禁止 rm -rf', expect_safety_error(safety.check_shell, 'rm -rf /tmp', config))
    check('禁止 shutdown', expect_safety_error(safety.check_shell, 'shutdown /s', config))
    check('允许 python 测试命令',
          not expect_safety_error(
              safety.check_shell,
              r'.\toolkit\python.exe dev_tools\season_data_check.py', config))


def test_protected_paths():
    config = load_config()
    check('禁止写 .credentials',
          expect_safety_error(safety.check_write_path,
                              config, Path('C:/Users/x/.credentials.yaml')))
    check('禁止写 config/oas3.json',
          expect_safety_error(safety.check_write_path,
                              config, Path('D:/repo/config/oas3.json')))
    check('允许写 tasks/Chess',
          not expect_safety_error(safety.check_write_path,
                                  config, Path('D:/repo/tasks/Chess/strategy/adaptive.py')))


def test_guarded_run_blocks():
    config = load_config()
    code, out = safety.run_guarded(config, ['git', 'reset', '--hard'])
    check('run_guarded 拦截危险 git（未执行）',
          code == 126 and 'blocked' in out, f'{code} {out[:160]}')


def test_chess_slot():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config = load_config()
        config.raw['safety']['max_chess_runs_per_session'] = 1
        state = State(Path(folder), config.project_root)
        original = safety.chess_running
        safety.chess_running = lambda: []
        try:
            first = safety.claim_chess_slot(config, state)
            second = safety.claim_chess_slot(config, state)
        finally:
            safety.chess_running = original
        check('第一次申请实机名额成功', first)
        check('超过次数上限后拒绝', not second)
        check('次数记录在 meta', state.get_meta('chess_runs_started') == 1)
        state.close()


def test_chess_running_detection():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config = load_config()
        state = State(Path(folder), config.project_root)
        original = safety.chess_running
        safety.chess_running = lambda: [{'pid': 1234, 'cmd': 'chess_series_test'}]
        try:
            allowed = safety.claim_chess_slot(config, state)
        finally:
            safety.chess_running = original
        check('已有实机进程时拒绝新名额', not allowed)
        state.close()


def test_git_checkpoint_creates_state():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        config = load_config()
        state = State(Path(folder), config.project_root)
        original = safety.run_guarded
        original_subprocess_run = safety.subprocess.run
        calls = []

        class FakeStatus:
            stdout = ' M tasks/Chess/strategy/adaptive.py\n'
            stderr = ''
            returncode = 0

        safety.subprocess.run = lambda *a, **k: FakeStatus()

        def fake_run(cfg, args, timeout=600, cwd=None):
            calls.append(args)
            if args[:2] == ['git', 'rev-parse']:
                return 0, 'abcdef123456\n'
            if args[:2] == ['git', 'add']:
                return 0, ''
            if args[:2] == ['git', 'commit']:
                return 0, '[main abc] msg'
            if args[:3] == ['git', 'tag', '-f']:
                return 0, ''
            return 0, ''

        safety.run_guarded = fake_run
        try:
            info = safety.git_checkpoint(config, state, 'checkpoint test',
                                         tag='stable-test')
        finally:
            safety.run_guarded = original
        safety.subprocess.run = original_subprocess_run
        check('检查点记录 sha',
              info['sha'] == 'abcdef123456', str(info))
        check('检查点打 tag', info['tag'] == 'stable-test')
        check('有未提交改动时会 git add + commit',
              any(cmd[:2] == ['git', 'add'] for cmd in calls)
              and any(cmd[:2] == ['git', 'commit'] for cmd in calls), str(calls))
        state.close()


def main():
    for case in (test_forbidden_git, test_forbidden_shell, test_protected_paths,
                 test_guarded_run_blocks, test_chess_slot,
                 test_chess_running_detection, test_git_checkpoint_creates_state):
        print(f'--- {case.__name__}')
        case()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print()
    print(f'TOTAL {len(RESULTS)}  PASS {len(RESULTS) - len(failed)}  FAIL {len(failed)}')
    if failed:
        for name in failed:
            print(f'  FAILED: {name}')
        return 1
    print('DUAL AGENT SAFETY: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
