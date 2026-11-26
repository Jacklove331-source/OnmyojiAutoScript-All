# This Python file uses the following encoding: utf-8

"""把 PR #1973 的新 UI 素材导入仓库（只导素材与 assets 元数据，不动我们的 script_task）。

策略：
- 覆盖 `tasks/WantedQuests/assets.py` + `wq/*.png|json` + `invite/*` + `tasks/GlobalGame/*`：
  这些是「新 UI 的坐标/模板」，与我们的 OCR 流程互不冲突（`ui_wq_v2.py` 自带 RuleOcr 常量）；
- **不覆盖** `tasks/WantedQuests/script_task.py`：我们保留自己的 v2 流程（已实测能跑）；
- 覆盖前把原文件备份到 `log/backup_pr1973/`，便于回滚。
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / 'dev_tools' / 'upstream_pr1973'
BACKUP = ROOT / 'log' / 'backup_pr1973'

TARGETS = [
    'tasks/GlobalGame/assets.py',
    'tasks/GlobalGame/gg/gg_g_accept.png',
    'tasks/GlobalGame/gg/gg_g_reject.png',
    'tasks/GlobalGame/gg/image.json',
    'tasks/WantedQuests/assets.py',
    'tasks/WantedQuests/invite/image.json',
    'tasks/WantedQuests/invite/invite_wq_invite_1.png',
    'tasks/WantedQuests/invite/invite_wq_invite_2.png',
    'tasks/WantedQuests/invite/invite_wq_invite_3.png',
    'tasks/WantedQuests/wq/image.json',
    'tasks/WantedQuests/wq/ocr.json',
    'tasks/WantedQuests/wq/click.json',
    'tasks/WantedQuests/wq/wq_goto_1.png',
    'tasks/WantedQuests/wq/wq_goto_2.png',
    'tasks/WantedQuests/wq/wq_goto_3.png',
    'tasks/WantedQuests/wq/wq_goto_4.png',
    'tasks/WantedQuests/wq/wq_trace_disable.png',
    'tasks/WantedQuests/wq/wq_trace_enable.png',
    'tasks/WantedQuests/wq/wq_trace_false.png',
    'tasks/WantedQuests/wq/wq_trace_one_check_opened.png',
    'tasks/WantedQuests/wq/wq_trace_one_disable.png',
    'tasks/WantedQuests/wq/wq_trace_one_enable.png',
    'tasks/WantedQuests/wq/wq_trace_one_realworld.png',
    'tasks/WantedQuests/wq/wq_trace_true.png',
]


def main() -> int:
    copied = 0
    missing = []
    for rel in TARGETS:
        src = STAGE / rel
        dst = ROOT / rel
        if not src.exists():
            missing.append(rel)
            print(f'SKIP {rel}（暂存缺失）')
            continue
        if dst.exists():
            backup = BACKUP / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
        print(f'COPY {rel}')
    print(f'\n导入 {copied} 个文件，缺失 {len(missing)}：{missing}')
    print(f'原文件备份在 {BACKUP}')
    return 0 if not missing else 1


if __name__ == '__main__':
    sys.exit(main())
