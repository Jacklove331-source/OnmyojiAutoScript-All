# This Python file uses the following encoding: utf-8

"""检查中文本地化覆盖率：任务名 / 配置描述 / 枚举值。"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

I18N = ROOT / 'module/config/i18n/zh-CN.json'
MENU = ROOT / 'module/config/config_menu.py'


def main() -> None:
    data = json.loads(I18N.read_text(encoding='utf-8'))
    print('模块 i18n 键数:', len(data))

    menu = MENU.read_text(encoding='utf-8')
    names = sorted(set(re.findall(r"'([A-Z][A-Za-z]+)'", menu)))
    missing = [name for name in names if name not in data]
    print('菜单任务数:', len(names))
    print('缺中文任务名:', missing)

    dd = sorted(k for k in data if 'draft_duel' in k or 'matchmaking_timeout' in k)
    print('DraftDuel 已有键:', dd)

    # 配置模型里出现的 description 键，检查是否都有译文
    desc_keys = set()
    for path in list((ROOT / 'tasks').rglob('config*.py')):
        text = path.read_text(encoding='utf-8', errors='ignore')
        desc_keys.update(re.findall(r"description='([a-z0-9_]+)'", text))
    missing_desc = sorted(k for k in desc_keys if k not in data)
    print('配置描述键:', len(desc_keys), '| 缺译文:', len(missing_desc))
    print('示例缺失:', missing_desc[:15])


if __name__ == '__main__':
    main()
