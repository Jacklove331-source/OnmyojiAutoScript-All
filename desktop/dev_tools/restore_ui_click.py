# This Python file uses the following encoding: utf-8

"""用历史快照的完整实现替换 BaseTask.ui_click_until_disappear。

当前（reset 后）版本只有 `(click, interval)`，而棋局按
`(click, click_target=, visible=, guard=, timeout=, confirm_frames=, max_clicks=)` 调用。
之前用 **kwargs 吞掉参数是错的（RuleClick 没有 match()），这里恢复真实实现。
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'tasks/base_task.py'
COMMIT = '0a6488cb'
NAME = 'ui_click_until_disappear'


def snapshot_source() -> str:
    result = subprocess.run(
        ['git', 'show', f'{COMMIT}:tasks/base_task.py'], cwd=str(ROOT),
        capture_output=True, text=True, encoding='utf-8', errors='ignore')
    return result.stdout or ''


def extract(source: str) -> str:
    lines = source.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith(f'def {NAME}('):
            start = index
            break
    if start is None:
        return ''
    body = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith('    def ') or line.startswith('    @'):
            break
        body.append(line)
    while body and not body[-1].strip():
        body.pop()
    return '\n'.join(body)


def main() -> None:
    snapshot = extract(snapshot_source())
    if not snapshot:
        print('snapshot method not found')
        return
    lines = TARGET.read_text(encoding='utf-8').splitlines()
    out, skipping = [], False
    for line in lines:
        if line.lstrip().startswith(f'def {NAME}('):
            out.extend(snapshot.splitlines())
            skipping = True
            continue
        if skipping:
            if line.startswith('    def ') or line.startswith('    @'):
                skipping = False
                out.append(line)
            continue
        out.append(line)
    TARGET.write_text('\n'.join(out) + '\n', encoding='utf-8')
    print('restored full ui_click_until_disappear')


if __name__ == '__main__':
    main()
