# This Python file uses the following encoding: utf-8

"""更新守卫回归测试（在临时仓库里跑，不碰真实项目）。

验证 deploy/git.py 的 `_update_guard`：
1. 当前分支与目标分支不一致 → 拒绝
2. 工作区有未跟踪/未提交改动 → 拒绝（keep_changes=True 时同样拒绝）
3. 干净的同名分支 → 放行
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy.git import GitManager  # noqa: E402


def git(*args: str) -> None:
    subprocess.run(['git', *args], check=True, capture_output=True, text=True)


def make_manager() -> GitManager:
    manager = object.__new__(GitManager)
    manager.__dict__['git'] = 'git'
    return manager


def run_checks() -> None:
    git('init', '-b', 'master')
    git('config', 'user.email', 'test@example.com')
    git('config', 'user.name', 'test')
    Path('a.txt').write_text('a', encoding='utf-8')
    git('add', '.')
    git('commit', '-m', 'init')

    manager = make_manager()

    # 1) 分支不一致 → 拒绝
    git('checkout', '-b', 'port/xylolit-vendor')
    assert manager._update_guard('origin', 'master', False) is False, '分支不一致应拒绝'
    print('[ok] 分支不一致 → 拒绝更新')

    # 2) 未跟踪文件 → 拒绝（keep_changes=True 也不能放行）
    git('checkout', 'master')
    Path('b.txt').write_text('b', encoding='utf-8')
    assert manager._update_guard('origin', 'master', False) is False, '未跟踪文件应拒绝'
    print('[ok] 未跟踪文件 → 拒绝更新')
    assert manager._update_guard('origin', 'master', True) is False, 'keep_changes=True 也应拒绝'
    print('[ok] keep_changes=True + 未跟踪文件 → 仍拒绝')

    # 3) 移出工作区后变干净 → 放行
    # 目标文件可能是上次运行的残留（Windows 的 rename 不能覆盖已存在文件，
    # 之前重复跑本测试会 FileExistsError），先清理。
    parked = Path(os.environ['TEMP']) / 'oas_guard_b.txt'
    if parked.exists():
        parked.unlink()
    Path('b.txt').rename(parked)
    assert manager._update_guard('origin', 'master', False) is True, '干净仓库应放行'
    print('[ok] 干净的同名分支 → 允许更新')


def main() -> int:
    tmp = tempfile.mkdtemp(prefix='oas_guard_')
    try:
        os.chdir(tmp)
        run_checks()
    finally:
        os.chdir(ROOT)
    print('UPDATER GUARD TEST PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
