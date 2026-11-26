"""离线检查版本资料模块：缓存命中、过期降级、完全离线标记（不联网）。"""

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from module.lineup import meta_version as mv  # noqa: E402

cache = os.path.join(tempfile.gettempdir(), 'oas_meta_cache_test.json')
if os.path.exists(cache):
    os.remove(cache)

# 1) 完全离线：从未抓取过
meta = mv.get_meta('patch', url='', cache_path=cache)
print('offline:', meta['offline'], '|', mv.describe(meta))
assert meta['offline'] is True

# 2) 抓取失败（模拟网络错误）→ 仍然 offline
def boom(url):
    raise OSError('network down')
meta = mv.get_meta('patch', url='https://example.invalid', cache_path=cache, fetcher=boom)
print('fetch fail:', meta['offline'], '|', mv.describe(meta))
assert meta['offline'] is True

# 3) 抓取成功并写缓存
calls = {'n': 0}
def fake(url):
    calls['n'] += 1
    return 'v1.7 版本公告：御魂改动...'
meta = mv.get_meta('patch', url='https://example.invalid', cache_path=cache, fetcher=fake)
print('fetch ok:', meta['age_hours'], '|', mv.describe(meta), '| calls', calls['n'])
assert meta['text'].startswith('v1.7') and calls['n'] == 1

# 4) 再次调用命中缓存，不再抓取
meta = mv.get_meta('patch', url='https://example.invalid', cache_path=cache, fetcher=fake)
print('cache hit: calls', calls['n'])
assert calls['n'] == 1

# 5) 过期 + 抓取失败 → stale 沿用旧缓存（必须标记）
cache_data = mv.load_cache(cache)
cache_data['patch']['fetched_at'] = time.time() - 3600 * 48
mv.save_cache(cache_data, cache)
meta = mv.get_meta('patch', url='https://example.invalid', ttl_hours=1,
                   cache_path=cache, fetcher=boom)
print('stale:', meta['stale'], '|', mv.describe(meta))
assert meta['stale'] is True and meta['text']

print('meta check ok')
