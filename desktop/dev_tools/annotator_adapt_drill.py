# This Python file uses the following encoding: utf-8

"""标注器「完整适配一轮」演练：建 json → 裁模板 → 存规则 → 自动重生成 assets.py → 清理。

这就是游戏更新/新活动适配时要走的动作序列，只是把前端操作换成同名 API。
跑通一次等于确认「以后靠这套流程改画面」是可行的，而不是只会测单条规则。

用一张已确认能命中的历史截图（`log/error/1794233126350/*.png`）当素材，
在 `tasks/GameUi/page_checks/` 下建**临时** json，做完立刻回滚（删 json/png + git 还原 assets.py）。

用法：
    .\\toolkit\\python.exe dev_tools\\annotator_standalone.py --no-open --port 23333   # 先起服务
    .\\toolkit\\python.exe dev_tools\\annotator_adapt_drill.py --port 23333
"""

import argparse
import base64
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLE = ROOT / 'log' / 'error' / '1794233126350' / '2026-11-09_22-05-26-290739.png'

TASK = 'GameUi'
JSON_RELPATH = 'page_checks/image_annotator_drill.json'
ITEM_NAME = 'annotator_drill'
IMAGE_NAME = 'annotator_drill.png'
# 错误快照里 check_demon_encounter 的真实位置（上一轮实测 roiFront=27,657,42,43）
ROI = '27,657,42,43'


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
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode('utf-8', errors='replace')
            status, content_type = response.status, response.headers.get('Content-Type', '')
    except urllib.error.HTTPError as error:
        return error.code, {'raw': error.read().decode('utf-8', errors='replace')}
    if 'json' in content_type.lower():
        try:
            return status, json.loads(body) if body else {}
        except ValueError:
            return status, {'raw': body}
    return status, {'raw': body}


def git(*args):
    return subprocess.run(['git', *args], cwd=str(ROOT), capture_output=True, text=True,
                          encoding='utf-8', errors='replace')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=23333)
    parser.add_argument('--keep', action='store_true', help='保留演练产物（默认清理）')
    args = parser.parse_args()
    base = f'http://127.0.0.1:{args.port}'
    failures = 0

    def check(name, ok, detail=''):
        nonlocal failures
        print(f'[{"PASS" if ok else "FAIL"}] {name}{(": " + detail) if detail else ""}')
        failures += 0 if ok else 1
        return ok

    if not SAMPLE.exists():
        print(f'[FAIL] 素材截图不存在：{SAMPLE}')
        return 1

    status, data = call(base, 'POST', '/tool/annotator/api/session')
    session = (data or {}).get('session') or {}
    session_id = session.get('session_id') if isinstance(session, dict) else None
    if not check('建会话', status == 200 and bool(session_id), str(session_id)):
        return 1

    json_path = ROOT / 'tasks' / TASK / JSON_RELPATH
    png_path = json_path.parent / IMAGE_NAME
    created_json = False
    try:
        # 1) 预清理：上一次演练可能留下文件
        call(base, 'POST', '/tool/annotator/api/rules/source/delete',
             {'dir_path': f'{TASK}/page_checks', 'file_name': JSON_RELPATH.split('/')[-1]})
        if json_path.exists():
            json_path.unlink()

        # 2) 上传截图
        content = base64.b64encode(SAMPLE.read_bytes()).decode('ascii')
        status, data = call(base, 'POST', '/tool/annotator/api/images/upload', {
            'session_id': session_id,
            'images': [{'name': SAMPLE.name, 'content_base64': content}],
        })
        images = (data or {}).get('images') or []
        image_id = (images[0].get('image_id') or images[0].get('id')) if images else None
        if not check('上传截图', bool(image_id), str(image_id)):
            return 1

        # 3) 新建规则 json（等价于前端「文件 → 新建」）
        status, data = call(base, 'POST', '/tool/annotator/api/rules/source/create',
                            {'dir_path': f'{TASK}/page_checks',
                             'file_name': JSON_RELPATH.split('/')[-1]})
        created_json = status == 200 and json_path.exists()
        check('新建 image json', created_json, f'HTTP {status} {json_path.name}')

        # 4) 从截图裁模板（等价于前端「在 ROI 画布上框选 → 保存为 imageName」）
        status, data = call(base, 'POST', '/tool/annotator/api/images/crop-save', {
            'session_id': session_id, 'image_id': image_id,
            'task_name': TASK, 'json_relpath': JSON_RELPATH,
            'image_name': IMAGE_NAME, 'roi': ROI,
        })
        png_ok = status == 200 and png_path.exists()
        check('裁剪并保存模板图', png_ok,
              f'{"HTTP " + str(status) if not png_ok else f"{png_path.stat().st_size} bytes"}')

        # 5) 保存规则 → 触发 assets.py 重生成
        rule = {
            'itemName': ITEM_NAME, 'imageName': IMAGE_NAME,
            'method': 'Template matching', 'threshold': 0.8,
            'description': '标注器演练临时规则', 'roiFront': ROI, 'roiBack': ROI,
        }
        status, data = call(base, 'POST', '/tool/annotator/api/rules/save', {
            'session_id': session_id, 'task_name': TASK, 'json_relpath': JSON_RELPATH,
            'rule_type': 'image', 'rules': [rule],
        })
        generate = (data or {}).get('generate_status')
        assets_file = (data or {}).get('assets_file') or ''
        check('保存规则并重生成 assets.py',
              status == 200 and generate == 'success' and bool(assets_file),
              f'HTTP {status} generate={generate} assets={assets_file}')

        # 6) 验证 json / png / assets.py 三处一致
        saved = json.loads(json_path.read_text(encoding='utf-8')) if json_path.exists() else []
        check('json 落盘', len(saved) == 1 and saved[0].get('itemName') == ITEM_NAME,
              f'{len(saved)} 条')
        assets_text = (ROOT / 'tasks' / TASK / 'assets.py').read_text(encoding='utf-8', errors='replace')
        attr = f'I_{ITEM_NAME.upper()}'
        check('assets.py 含新属性', attr in assets_text, attr)

        # 7) 用刚生成的模板回测同一帧，应接近 1.0（证明"裁出来的图能当模板用"）
        status, data = call(base, 'POST', '/tool/annotator/api/rules/test', {
            'session_id': session_id, 'image_id': image_id, 'task_name': TASK,
            'json_relpath': JSON_RELPATH, 'rule_type': 'image', 'rule': saved[0],
        })
        result = (data or {}).get('result') or data or {}
        similarity = float(result.get('similarity', -1))
        check('新模板回测', status == 200 and bool(result.get('matched')) and similarity > 0.95,
              f'similarity={similarity:.3f} roiFront={result.get("roiFront")}')
    finally:
        if not args.keep:
            # 回滚：删 json、删 png、还原 assets.py
            if created_json:
                call(base, 'POST', '/tool/annotator/api/rules/source/delete',
                     {'dir_path': f'{TASK}/page_checks',
                      'file_name': JSON_RELPATH.split('/')[-1]})
            if json_path.exists():
                json_path.unlink()
            if png_path.exists():
                png_path.unlink()
            git('checkout', '--', f'tasks/{TASK}/assets.py')
            dirty = git('status', '--porcelain', '--', f'tasks/{TASK}').stdout.strip()
            check('回滚干净', not dirty, dirty[:120] if dirty else 'tasks/GameUi 无残余改动')
        call(base, 'DELETE', f'/tool/annotator/api/session/{session_id}',
             query={'reason': 'drill'})

    print(f'\nANNOTATOR ADAPT DRILL: {"PASSED" if failures == 0 else "FAILED"} '
          f'(failures={failures})')
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
