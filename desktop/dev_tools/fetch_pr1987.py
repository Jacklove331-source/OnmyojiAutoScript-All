# This Python file uses the following encoding: utf-8

"""把 GitHub PR #1987（Ghostuel:feat/xinyu-mingxiao-theme）的改动抓下来学习。

只下载到 dev_tools/upstream_pr1987/，不改仓库文件。
PR #1987 = 「适配新语明霄主题、月海游星结界及新版悬赏封印界面」（Draft, 62 文件 +1380/-383）

用法：.\\toolkit\\python.exe dev_tools\\fetch_pr1987.py [--only-prefix tasks/WantedQuests]
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / 'dev_tools' / 'upstream_pr1987'
REPO = 'Ghostuel/OnmyojiAutoScript'
BRANCH = 'feat/xinyu-mingxiao-theme'
PR = 1987
API = f'https://api.github.com/repos/runhey/OnmyojiAutoScript/pulls/{PR}/files?per_page=100'


def fetch(url: str, retries: int = 3, timeout: int = 90):
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'oas-learn'})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as error:  # noqa: BLE001
            print(f'  retry {attempt + 1}/{retries}: {type(error).__name__}: {error}')
            time.sleep(3)
    return None


def main() -> int:
    only = None
    if '--only-prefix' in sys.argv:
        only = sys.argv[sys.argv.index('--only-prefix') + 1]

    raw = fetch(API)
    if raw is None:
        print('取文件清单失败')
        return 1
    files = json.loads(raw.decode('utf-8'))
    print(f'PR #{PR} 文件 {len(files)} 个')

    STAGE.mkdir(parents=True, exist_ok=True)
    ok = skipped = failed = 0
    for item in files:
        path = item['filename']
        status = item['status']
        if status == 'removed':
            skipped += 1
            continue
        if only and not path.startswith(only):
            skipped += 1
            continue
        url = f'https://raw.githubusercontent.com/{REPO}/{BRANCH}/{path}'
        data = fetch(url)
        if data is None:
            failed += 1
            print(f'FAIL {path}')
            continue
        target = STAGE / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        ok += 1
        print(f'OK   {path} ({len(data)}B)')

    print(f'\n下载 {ok}，跳过 {skipped}，失败 {failed} → {STAGE}')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
