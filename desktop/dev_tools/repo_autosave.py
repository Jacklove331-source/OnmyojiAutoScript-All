# This Python file uses the following encoding: utf-8

"""仓库自动存档：防止 OAS 更新器 reset --hard 把未提交改动冲掉。

做两件事（可重复执行）：
1. 有未提交改动就自动提交到当前分支，提交信息带 ``[autosave]`` 前缀；
2. 当天没生成过 bundle 时，导出一份全量 bundle 到 ``D:\\3D\\YYS2\\_backups``。

由 Windows 计划任务每 20 分钟调用：``schtasks /Run /TN OAS-RepoAutosave``。
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKUP_DIR = Path('D:/3D/YYS2/_backups')


def git(*args: str) -> str:
    result = subprocess.run(
        ['git', *args], cwd=str(REPO), capture_output=True, text=True,
        encoding='utf-8', errors='ignore',
    )
    return (result.stdout or '') + (result.stderr or '')


def autosave() -> None:
    status = git('status', '--porcelain')
    if not status.strip():
        print('clean, nothing to commit')
        return
    git('add', '-A')
    # 敏感/易变内容不纳入自动提交：配置、日志、备份包、密钥。
    for pattern in ('config', 'log', '*.bundle', '*.key', '*.pem', '.credentials*'):
        git('reset', '-q', '--', pattern)
    staged = git('diff', '--cached', '--name-only')
    if not staged.strip():
        print('only ignored/sensitive changes, skip commit')
        return
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    out = git('commit', '-m', f'[autosave] {stamp}')
    print(out.strip()[:200])


def bundle() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"repo-{datetime.now().strftime('%Y%m%d')}.bundle"
    if target.exists():
        print(f'bundle exists: {target.name}')
        return
    out = git('bundle', 'create', str(target), '--all')
    print(out.strip()[:200] or f'bundle written: {target.name}')


def main() -> int:
    autosave()
    bundle()
    return 0


if __name__ == '__main__':
    sys.exit(main())
