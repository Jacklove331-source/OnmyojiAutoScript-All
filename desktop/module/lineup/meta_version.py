# This Python file uses the following encoding: utf-8

"""版本资料获取与缓存。

用途：AI 阵容计算前确认「当前版本 + 玩法主流配置」，避免依赖过时的训练数据。

规则：
- 优先使用缓存（``config/meta_cache.json``），超过 TTL 才重新抓取；
- 抓取失败时**明确返回 stale/offline 标记**，调用方必须把结论标成"基于旧资料"或直接不结论；
- 缓存里记录抓取时间与来源 URL，方便判断攻略是否过期。
"""

import json
import os
import time
import urllib.request

CACHE_PATH = 'config/meta_cache.json'
DEFAULT_TTL_HOURS = 24


def _now() -> float:
    return time.time()


def load_cache(path: str = CACHE_PATH) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as file:
            return json.load(file) or {}
    except (OSError, ValueError):
        return {}


def save_cache(data: dict, path: str = CACHE_PATH) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def cache_age_hours(entry: dict) -> float:
    fetched = float(entry.get('fetched_at') or 0)
    return (_now() - fetched) / 3600 if fetched else float('inf')


def fetch_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 OAS-lineup'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode('utf-8', errors='ignore')


def get_meta(key: str, url: str = '', ttl_hours: float = DEFAULT_TTL_HOURS,
             cache_path: str = CACHE_PATH, fetcher=None) -> dict:
    """取某个 key 的版本资料。

    返回 dict：{text, fetched_at, age_hours, stale, offline, source}
    - stale=True 表示缓存过期但沿用（抓取失败时）
    - offline=True 表示从未拿到过资料，调用方不得据此下"最新版本"结论
    """
    cache = load_cache(cache_path)
    entry = dict(cache.get(key) or {})
    age = cache_age_hours(entry)
    if entry and age <= ttl_hours:
        return {
            'text': entry.get('text', ''), 'fetched_at': entry.get('fetched_at'),
            'age_hours': round(age, 2), 'stale': False, 'offline': False,
            'source': entry.get('source', url),
        }

    if url:
        try:
            text = (fetcher or fetch_text)(url)
            entry = {'text': text, 'fetched_at': _now(), 'source': url}
            cache[key] = entry
            save_cache(cache, cache_path)
            return {'text': text, 'fetched_at': entry['fetched_at'], 'age_hours': 0.0,
                    'stale': False, 'offline': False, 'source': url}
        except Exception:  # noqa: BLE001 - 网络失败要降级而不是崩
            pass

    if entry:
        return {
            'text': entry.get('text', ''), 'fetched_at': entry.get('fetched_at'),
            'age_hours': round(age, 2), 'stale': True, 'offline': False,
            'source': entry.get('source', url),
        }
    return {'text': '', 'fetched_at': None, 'age_hours': None,
            'stale': False, 'offline': True, 'source': url}


def describe(meta: dict) -> str:
    """给日志用的简短状态描述。"""
    if meta.get('offline'):
        return '版本资料不可用（离线/未抓取）——不得声称基于最新版本'
    if meta.get('stale'):
        return f"版本资料已过期 {meta.get('age_hours')}h，沿用旧缓存——结论需标注"
    return f"版本资料 {meta.get('age_hours')}h 内，来源 {meta.get('source')}"
