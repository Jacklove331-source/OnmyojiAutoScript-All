# This Python file uses the following encoding: utf-8

"""从 GitHub PR #1973（LGG686/OnmyojiAutoScript:WantedQuests）拉取「新 UI 适配」文件到暂存目录。

只下载、不覆盖仓库文件；先比对/试匹配，确认可用再替换。

用法：.\\toolkit\\python.exe dev_tools\\fetch_pr1973.py
产物：dev_tools\\upstream_pr1973\\<原始相对路径>
"""

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / 'dev_tools' / 'upstream_pr1973'
BRANCH = 'WantedQuests'
REPO = 'LGG686/OnmyojiAutoScript'

FILES = [
    'tasks/GlobalGame/assets.py',
    'tasks/GlobalGame/gg/gg_g_accept.png',
    'tasks/GlobalGame/gg/gg_g_reject.png',
    'tasks/GlobalGame/gg/image.json',
    'tasks/WantedQuests/assets.py',
    'tasks/WantedQuests/invite/image.json',
    'tasks/WantedQuests/invite/invite_wq_invite_1.png',
    'tasks/WantedQuests/invite/invite_wq_invite_2.png',
    'tasks/WantedQuests/invite/invite_wq_invite_3.png',
    'tasks/WantedQuests/script_task.py',
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
    STAGE.mkdir(parents=True, exist_ok=True)
    ok = 0
    for rel in FILES:
        url = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/{rel}'
        target = STAGE / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'oas-fetch'})
            with urllib.request.urlopen(req, timeout=60) as response:
                data = response.read()
            target.write_bytes(data)
            ok += 1
            print(f'OK   {rel}  ({len(data)} bytes)')
        except Exception as error:  # noqa: BLE001
            print(f'FAIL {rel}: {type(error).__name__}: {error}')
    print(f'\n下载完成 {ok}/{len(FILES)} → {STAGE}')
    return 0 if ok == len(FILES) else 1


if __name__ == '__main__':
    sys.exit(main())
