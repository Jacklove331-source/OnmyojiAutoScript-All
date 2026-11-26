# This Python file uses the following encoding: utf-8

"""从历史快照恢复丢失的公共方法（本次 OAS 重置事故后的修复工具）。

用法：编辑 TARGETS 后执行 ``python dev_tools/restore_lost_helpers.py``。
已存在的同名方法会跳过，重复执行安全。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (来源 commit, 来源文件, 方法名, 目标文件)
TARGETS = [
    ('0a6488cb', 'tasks/Component/GeneralInvite/general_invite.py',
     'room_check_can_fire', 'tasks/Component/GeneralInvite/general_invite.py'),
    ('0a6488cb', 'tasks/Component/BaseActivity/base_activity.py',
     'verify_zero_ticket', 'tasks/Component/BaseActivity/base_activity.py'),
    ('0a6488cb', 'tasks/base_task.py',
     'ui_click_until_appear', 'tasks/base_task.py'),
    ('0a6488cb', 'tasks/base_task.py',
     'ui_click_until_appear_or_timeout', 'tasks/base_task.py'),
]


def git_show(commit: str, path: str) -> str:
    result = subprocess.run(['git', 'show', f'{commit}:{path}'], cwd=str(ROOT),
                            capture_output=True, text=True, encoding='utf-8',
                            errors='ignore')
    return result.stdout or ''


def extract(source: str, name: str) -> str:
    lines = source.splitlines()
    start = None
    indent = 0
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(f'def {name}('):
            start = index
            indent = len(line) - len(stripped)
            break
    if start is None:
        return ''
    body = [lines[start]]
    for line in lines[start + 1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        body.append(line)
    while body and not body[-1].strip():
        body.pop()
    return '\n'.join(body)


def main() -> int:
    for commit, src, name, dst in TARGETS:
        target = ROOT / dst
        current = target.read_text(encoding='utf-8', errors='ignore')
        if f'def {name}(' in current:
            print(f'skip {dst}: {name} 已存在')
            continue
        block = extract(git_show(commit, src), name)
        if not block:
            print(f'fail {dst}: 在 {commit}:{src} 找不到 {name}')
            continue
        target.write_text(current.rstrip() + '\n\n' + block + '\n', encoding='utf-8')
        print(f'restored {dst}: {name} ({len(block.splitlines())} 行)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
