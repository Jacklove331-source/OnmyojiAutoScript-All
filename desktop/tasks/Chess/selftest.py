# This Python file uses the following encoding: utf-8

"""百鬼棋局模块自检：一条命令跑完属于本模块的回归与静态检查。

为什么放在模块里（`tasks/Chess/selftest.py`）而不是散在 `dev_tools/`：
棋局的回归脚本很多，改棋局代码的人应该能从**模块内**找到并跑完「本模块的验收」，
不需要记住十几个 dev_tools 文件名。这里只做编排，具体断言仍在各脚本里。

用法：
    .\\toolkit\\python.exe tasks\\Chess\\selftest.py          # 全部（含静态检查）
    .\\toolkit\\python.exe tasks\\Chess\\selftest.py --quick  # 跳过跑得慢的实机相关项

输出结尾固定为 `CHESS SELFTEST: PASSED` / `FAILED` 与 `TOTAL n PASS n FAIL n`，
便于 CI/调度器解析；退出码 0 表示全过。
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (脚本相对路径, 说明, 是否 --quick 也跑)
SUITES = (
    ('dev_tools/chess_transition_check.py', '前期过渡位/站位/淘汰', True),
    ('dev_tools/chess_phase_budget_check.py', '阶段动态时间预算', True),
    ('dev_tools/chess_buy_deploy_guard_test.py', '过渡位限购 + 上阵缺口分类', True),
    ('dev_tools/chess_soul_target_skip_test.py', '御魂装配目标跳过/重试', True),
    ('dev_tools/grigri_conflict_check.py', '符咒 OCR/图标冲突裁决', True),
    ('dev_tools/chess_audit_metrics_check.py', '决策审计统计正确性', True),
    ('dev_tools/season_data_check.py', '赛季数据自洽 + 实机日志覆盖', True),
    ('dev_tools/chess_level_cache_check.py', '等级 OCR 缓存兜底', True),
    ('dev_tools/click_record_check.py', '点击记录（GameTooManyClick）', True),
    ('dev_tools/gold_and_record_check.py', '金币两帧确认 + 逐局记录', True),
    ('dev_tools/test_indexerror_boundary.py', 'OCR 空文本边界', True),
    ('dev_tools/chess_adaptive_check.py', '自适应阵容选人/站位', False),
)

STATIC = (
    ('dev_tools/contract_scan.py', ['Chess'], '公共接口契约扫描'),
    ('dev_tools/missing_method_check.py', [], '缺失方法扫描（全任务）'),
)


def _decode(raw: bytes) -> str:
    """输出去容错：子进程输出先按 UTF-8 解码，失败再按 GBK（中文 Windows 常见），
    都失败就替换字符。只影响打印出来的文字，不影响任何判定。"""
    for encoding in ('utf-8', 'gbk'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def run(args: list[str], timeout: int = 1800) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            [sys.executable, *args],
            cwd=str(ROOT), capture_output=True, timeout=timeout,
        )
        return completed.returncode, _decode(completed.stdout or b'') + _decode(
            completed.stderr or b'')
    except subprocess.TimeoutExpired:
        return 124, f'<timeout {timeout}s>'
    except OSError as error:
        return 127, f'<exec failed: {error}>'


def tail(text: str, limit: int = 200) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return ' | '.join(lines[-2:])[:limit]


def console_safe(text: str) -> str:
    """只做输出容错：控制台编码（中文 Windows 是 GBK）编不出来的字符先替换掉。

    子进程输出按 utf-8 解码、解不出来的字节已经是 U+FFFD；U+FFFD 在 GBK 控制台
    编不出来，直接 print 会抛 UnicodeEncodeError 把整轮自检打断（--quick 裸命令
    100% 复现）。这里只影响显示，不影响任何判定（判定仍看 returncode 与
    'problems 0' 这类 ASCII 文本）。
    """
    encoding = (getattr(sys.stdout, 'encoding', '') or 'utf-8')
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, 'replace').decode(encoding, 'replace')


def main() -> int:
    quick = '--quick' in sys.argv[1:]
    started = time.time()
    failures = []
    total = 0

    for script, label, runs_in_quick in SUITES:
        if quick and not runs_in_quick:
            continue
        total += 1
        path = ROOT / script
        if not path.exists():
            failures.append(f'{script}（缺失）')
            print(f'[SKIP] {label}: 缺少 {script}')
            continue
        code, output = run([script])
        ok = code == 0
        if not ok:
            failures.append(f'{script} rc={code}')
        print(console_safe(f'[{"PASS" if ok else "FAIL"}] {label}: {tail(output)}'))

    for script, extra, label in STATIC:
        total += 1
        code, output = run([script, *extra], timeout=900)
        ok = code == 0 and 'problems 0' in output
        if not ok:
            failures.append(f'{script} rc={code}')
        print(console_safe(f'[{"PASS" if ok else "FAIL"}] {label}: {tail(output)}'))

    elapsed = time.time() - started
    print()
    print(f'TOTAL {total}  PASS {total - len(failures)}  '
          f'FAIL {len(failures)}  ({elapsed:.1f}s)')
    if failures:
        print(console_safe('CHESS SELFTEST: FAILED -> ' + ', '.join(failures)))
        return 1
    print('CHESS SELFTEST: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
