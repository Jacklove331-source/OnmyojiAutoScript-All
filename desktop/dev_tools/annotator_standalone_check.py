# This Python file uses the following encoding: utf-8

"""端到端验证独立标注器服务：建会话 → 传图 → 读规则 → 测一条模板 → 关会话。

不碰模拟器（只上传离线截图），因此可以在 OAS 实例跑任务时安全执行。

用法：
    # 先起服务
    .\\toolkit\\python.exe dev_tools\\annotator_standalone.py --no-open --port 23333
    # 再验证
    .\\toolkit\\python.exe dev_tools\\annotator_standalone_check.py --port 23333

期望：check_demon_encounter 在 2026-11-09 的错误快照上 matched=True，similarity≈0.976。
"""

import argparse
import base64
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLE = ROOT / 'log' / 'error' / '1794233126350' / '2026-11-09_22-05-26-290739.png'
TASK = 'GameUi'
JSON_RELPATH = 'page_checks/image_DemonEncounter.json'
RULE_NAME = 'check_demon_encounter'


def call(base, method, path, payload=None, query=None):
    url = base + path
    if query:
        url += '?' + urllib.parse.urlencode(query)
    data = None
    headers = {'Accept': 'application/json'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode('utf-8', errors='replace')
            status = response.status
            content_type = response.headers.get('Content-Type', '')
    except urllib.error.HTTPError as error:
        body = error.read().decode('utf-8', errors='replace')
        return error.code, {'raw': body}
    # 页面是 HTML，接口才是 JSON；不要对 HTML 调 json.loads
    if 'json' in content_type.lower():
        try:
            return status, json.loads(body) if body else {}
        except ValueError:
            return status, {'raw': body}
    return status, {'raw': body}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=23333)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    base = f'http://{args.host}:{args.port}'
    failures = 0

    def check(name, ok, detail=''):
        nonlocal failures
        print(f'[{"PASS" if ok else "FAIL"}] {name}{(": " + detail) if detail else ""}')
        failures += 0 if ok else 1
        return ok

    status, page = call(base, 'GET', '/tool/annotator')
    check('页面可访问', status == 200, f'HTTP {status}')

    status, data = call(base, 'POST', '/tool/annotator/api/session')
    session = (data or {}).get('session') or {}
    session_id = session.get('session_id') if isinstance(session, dict) else None
    if not check('创建会话', status == 200 and bool(session_id), f'HTTP {status} {session_id}'):
        return 1

    try:
        status, data = call(base, 'GET', '/tool/annotator/api/tasks')
        tasks = (data or {}).get('tasks') or []
        check('任务列表', status == 200 and TASK in tasks, f'{len(tasks)} 个任务')

        status, data = call(base, 'GET', '/tool/annotator/api/rules/load',
                            query={'task_name': TASK, 'json_relpath': JSON_RELPATH})
        rules = (data or {}).get('rules') or []
        check('读取规则文件', status == 200 and bool(rules),
              f'{len(rules)} 条规则 / HTTP {status}')
        if rules:
            print(f'       规则字段: {sorted(rules[0].keys())}')

        def rule_name(item):
            for key in ('name', 'itemName', 'rule_name'):
                if item.get(key):
                    return item[key]
            return ''

        rule = next((r for r in rules if rule_name(r) == RULE_NAME), None)
        if not check(f'找到规则 {RULE_NAME}', rule is not None,
                     '已有: ' + ', '.join(rule_name(r) for r in rules)):
            return 1

        if not SAMPLE.exists():
            print(f'[SKIP] 样本截图不存在：{SAMPLE}')
            return 1
        content = base64.b64encode(SAMPLE.read_bytes()).decode('ascii')
        status, data = call(base, 'POST', '/tool/annotator/api/images/upload', {
            'session_id': session_id,
            'images': [{'name': SAMPLE.name, 'content_base64': content}],
        })
        images = (data or {}).get('images') or []
        if not check('上传截图', status == 200 and bool(images), f'HTTP {status}'):
            return 1
        image_id = images[0].get('image_id') or images[0].get('id')
        check('拿到 image_id', bool(image_id), str(image_id))

        status, data = call(base, 'POST', '/tool/annotator/api/rules/test', {
            'session_id': session_id,
            'image_id': image_id,
            'task_name': TASK,
            'json_relpath': JSON_RELPATH,
            'rule_type': 'image',
            'rule': rule,
        })
        result = (data or {}).get('result') or data or {}
        similarity = float(result.get('similarity', -1))
        matched = bool(result.get('matched'))
        check('模板测试命中', status == 200 and matched and similarity > 0.9,
              f'HTTP {status} matched={matched} similarity={similarity:.3f} '
              f'roiFront={result.get("roiFront")} msg={result.get("message")}')
    finally:
        call(base, 'DELETE', f'/tool/annotator/api/session/{session_id}',
             query={'reason': 'selftest'})

    print(f'\nANNOTATOR STANDALONE CHECK: {"PASSED" if failures == 0 else "FAILED"} '
          f'(failures={failures})')
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
