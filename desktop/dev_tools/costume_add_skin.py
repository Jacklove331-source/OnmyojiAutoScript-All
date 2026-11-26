# This Python file uses the following encoding: utf-8

"""用标注器给「庭院皮肤」新增一套模板（图像侧）。

只做图像与规则文件：建 `tasks/Component/Costume/main<N>/image.json` + 5 张 png，
并通过标注器触发 `tasks/Component/Costume/assets.py` 重新生成。
MainType / model / i18n 的代码注册由 `dev_tools/costume_register_skin.py` 负责。

前置：启用标注器服务（`dev_tools/annotator_standalone.py` 或 `启动素材标注器.bat`）

用法：
    .\\toolkit\\python.exe dev_tools\\costume_add_skin.py --id 18 --image log/_court18.png
    .\\toolkit\\python.exe dev_tools\\costume_add_skin.py --id 18 --image log/_court18.png \\
        --roi check=250,150,90,60 --roi town=706,249,61,57
    .\\toolkit\\python.exe dev_tools\\costume_add_skin.py --id 18 --image log/_court18.png --dry-run

进度同时写到 `log/_costume_add_skin.trace`（上一次 stdout 被吞掉排查了很久，留着备查）。
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
TRACE = ROOT / 'log' / '_costume_add_skin.trace'
TASK = 'Component'

# key -> (itemName, 说明, roiFront, roiBack, threshold)
DEFAULTS = {
    'check': ('check_main_{idx}', '庭院判定（该皮肤独有装饰）',
              (250, 150, 90, 60), (170, 90, 250, 180), 0.8),
    'exploration': ('main_goto_exploration_{idx}', '探索入口',
                    (657, 120, 45, 75), (600, 80, 160, 180), 0.55),
    'summon': ('main_goto_summon_{idx}', '召唤入口',
               (1074, 173, 57, 65), (1010, 120, 190, 170), 0.55),
    'town': ('main_goto_town_{idx}', '町中入口',
             (706, 249, 61, 57), (640, 190, 190, 180), 0.6),
    'pet_house': ('pet_house_{idx}', '宠物屋入口',
                  (1012, 414, 56, 25), (960, 380, 160, 110), 0.7),
}
ORDER = ['check', 'exploration', 'summon', 'town', 'pet_house']


def trace(line: str) -> None:
    print(line, flush=True)
    try:
        TRACE.parent.mkdir(parents=True, exist_ok=True)
        with TRACE.open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except OSError:
        pass


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
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode('utf-8', errors='replace')
            status, ctype = response.status, response.headers.get('Content-Type', '')
    except urllib.error.HTTPError as error:
        return error.code, {'raw': error.read().decode('utf-8', errors='replace')}
    except Exception as error:  # 连接被拒/超时也要看清楚，而不是静默退出
        return -1, {'error': f'{type(error).__name__}: {error}'}
    if 'json' in ctype.lower():
        try:
            return status, json.loads(body) if body else {}
        except ValueError:
            return status, {'raw': body}
    return status, {'raw': body}


def build_specs(idx: int, overrides: dict) -> list:
    specs = []
    for key in ORDER:
        item_tpl, desc_tpl, front, back, threshold = DEFAULTS[key]
        item_name = item_tpl.format(idx=idx)
        specs.append({
            'key': key,
            'itemName': item_name,
            'description': desc_tpl,
            'imageName': f'main{idx}_{item_name}.png',
            'roiFront': overrides.get(key, list(front)),
            'roiBack': list(back),
            'threshold': threshold,
        })
    return specs


def main() -> int:
    parser = argparse.ArgumentParser(description='用标注器新增庭院皮肤模板')
    parser.add_argument('--id', type=int, required=True)
    parser.add_argument('--image', required=True, help='庭院截图（相对仓库根）')
    parser.add_argument('--roi', action='append', default=[],
                        help='覆盖 roi：check=250,150,90,60，可重复')
    parser.add_argument('--port', type=int, default=23333)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    idx = args.id
    base = f'http://127.0.0.1:{args.port}'
    source = pathlib.Path(args.image)
    if not source.is_absolute():
        source = ROOT / source
    if not source.exists():
        trace(f'[FAIL] 截图不存在：{source}')
        return 1

    overrides = {}
    for item in args.roi:
        key, _, value = item.partition('=')
        key = key.strip()
        if key not in DEFAULTS:
            trace(f'[FAIL] 未知 roi 键 {key}，可选：{", ".join(ORDER)}')
            return 1
        overrides[key] = [int(v) for v in value.split(',')]

    specs = build_specs(idx, overrides)
    trace(f'皮肤 #{idx}：{len(specs)} 条规则，源图 {source.name}')
    for spec in specs:
        trace(f"  {spec['itemName']:26} roiFront={spec['roiFront']} "
              f"roiBack={spec['roiBack']} th={spec['threshold']}")
    if args.dry_run:
        trace('[dry-run] 未写入任何文件')
        return 0

    json_relpath = f'Costume/main{idx}/image.json'
    dir_path = f'Component/Costume/main{idx}'
    json_path = ROOT / 'tasks' / TASK / json_relpath
    json_path.parent.mkdir(parents=True, exist_ok=True)

    failures = [0]

    def report(name, ok, detail=''):
        if not ok:
            failures[0] += 1
        trace(f'[{"PASS" if ok else "FAIL"}] {name}{(": " + detail) if detail else ""}')
        return ok

    status, data = call(base, 'POST', '/tool/annotator/api/session')
    session_id = ((data or {}).get('session') or {}).get('session_id')
    if not session_id:
        trace(f'[FAIL] 标注器服务不可用 HTTP {status} {data}')
        return 1
    trace(f'会话 {session_id}')

    try:
        content = base64.b64encode(source.read_bytes()).decode('ascii')
        status, data = call(base, 'POST', '/tool/annotator/api/images/upload', {
            'session_id': session_id,
            'images': [{'name': source.name, 'content_base64': content}],
        })
        images = (data or {}).get('images') or []
        image_id = (images[0].get('image_id') or images[0].get('id')) if images else None
        if not report('上传庭院截图', bool(image_id), str(image_id)):
            return 1

        if json_path.exists():
            status, data = call(base, 'POST', '/tool/annotator/api/rules/source/delete', {
                'dir_path': dir_path, 'file_name': json_path.name,
            })
            report('删除旧 json', status == 200, f'HTTP {status}')
        status, data = call(base, 'POST', '/tool/annotator/api/rules/source/create', {
            'dir_path': dir_path, 'file_name': json_path.name,
        })
        if not report('新建 image.json', status == 200 and json_path.exists(), f'HTTP {status}'):
            return 1

        for spec in specs:
            roi_text = ','.join(str(v) for v in spec['roiFront'])
            status, data = call(base, 'POST', '/tool/annotator/api/images/crop-save', {
                'session_id': session_id, 'image_id': image_id,
                'task_name': TASK, 'json_relpath': json_relpath,
                'image_name': spec['imageName'], 'roi': roi_text,
            })
            target = json_path.parent / spec['imageName']
            report(f"裁剪 {spec['imageName']}", status == 200 and target.exists(),
                   f'{target.stat().st_size} bytes' if target.exists() else f'HTTP {status} {data}')

        rules = [{
            'itemName': spec['itemName'],
            'imageName': spec['imageName'],
            'method': 'Template matching',
            'threshold': spec['threshold'],
            'description': spec['description'],
            'roiFront': ','.join(str(v) for v in spec['roiFront']),
            'roiBack': ','.join(str(v) for v in spec['roiBack']),
        } for spec in specs]
        status, data = call(base, 'POST', '/tool/annotator/api/rules/save', {
            'session_id': session_id, 'task_name': TASK,
            'json_relpath': json_relpath, 'rule_type': 'image', 'rules': rules,
        })
        report('保存规则并重生成 assets.py',
               status == 200 and (data or {}).get('generate_status') == 'success',
               f"generate={(data or {}).get('generate_status')} "
               f"assets={(data or {}).get('assets_file')}")

        for spec, rule in zip(specs, rules):
            status, data = call(base, 'POST', '/tool/annotator/api/rules/test', {
                'session_id': session_id, 'image_id': image_id, 'task_name': TASK,
                'json_relpath': json_relpath, 'rule_type': 'image', 'rule': rule,
            })
            result = (data or {}).get('result') or data or {}
            similarity = float(result.get('similarity', -1))
            report(f"回测 {spec['itemName']}",
                   status == 200 and bool(result.get('matched')) and similarity >= spec['threshold'],
                   f"similarity={similarity:.3f} th={spec['threshold']} "
                   f"roiFront={result.get('roiFront')}")
    finally:
        call(base, 'DELETE', f'/tool/annotator/api/session/{session_id}',
             query={'reason': 'costume_add'})

    trace(f'\nCOSTUME SKIN {idx} IMAGES: '
          f'{"PASSED" if failures[0] == 0 else "FAILED"} (failures={failures[0]})')
    if failures[0] == 0:
        trace(f'下一步：.\\toolkit\\python.exe dev_tools\\costume_register_skin.py '
              f'--id {idx} --name "<皮肤名>"')
    return 0 if failures[0] == 0 else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.exit(2)
