"""离线检查 DeepSeek 客户端：JSON 提取、配置读取、未配置报错、响应解析（不联网）。"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from module.lineup import deepseek  # noqa: E402

# 1) JSON 提取
samples = [
    '```json\n{"a": 1}\n```',
    '说明文字 {"name": "【AI】魂土-低配", "shikigami": []} 结尾',
    '{"x": {"y": 2}}',
]
for text in samples:
    parsed = json.loads(deepseek._extract_json(text))
    print('extract ok:', parsed)

# 2) 未配置密钥时报错
cfg = deepseek.load_config('config/__not_exists__.json')
cfg['api_key'] = ''
try:
    deepseek.chat_json([{'role': 'user', 'content': 'hi'}], config=cfg)
    print('ERROR: should have raised')
except RuntimeError as error:
    print('expected error:', str(error)[:40])

# 3) 模拟 HTTP 响应，验证解析与结构校验
class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_urlopen(request, timeout=None):
    content = json.dumps({
        'name': '【AI】魂土-低配速刷',
        'reason': '用现有式神',
        'shikigami': [{'name': '天火命铃彦姬', 'soul': '针女', 'position': 1}],
    }, ensure_ascii=False)
    return FakeResponse({'choices': [{'message': {'content': content}}]})


original = deepseek.urllib.request.urlopen
deepseek.urllib.request.urlopen = fake_urlopen
try:
    cfg = {'api_key': 'test', 'base_url': 'https://example.invalid', 'model': 'm',
           'timeout': 5, 'retries': 0, 'temperature': 0.2}
    data = deepseek.chat_json([{'role': 'user', 'content': 'x'}], config=cfg)
    print('chat_json ok:', data['name'])

    roster = [{'name': '天火命铃彦姬'}, {'name': '梦山白藏主'}]
    plan = deepseek.ask_lineup('Orochi', roster, {'dungeon': '八岐大蛇·神罚'}, config=cfg)
    print('ask_lineup ok:', plan['name'], len(plan['shikigami']))

    # 4) 模型胡编未拥有式神 → 必须报错
    def fake_bad(request, timeout=None):
        content = json.dumps({'name': '【AI】x', 'shikigami': [{'name': '不存在的式神'}]},
                             ensure_ascii=False)
        return FakeResponse({'choices': [{'message': {'content': content}}]})
    deepseek.urllib.request.urlopen = fake_bad
    try:
        deepseek.ask_lineup('Orochi', roster, {}, config=cfg)
        print('ERROR: should reject unowned unit')
    except ValueError as error:
        print('expected reject:', str(error)[:30])
finally:
    deepseek.urllib.request.urlopen = original
