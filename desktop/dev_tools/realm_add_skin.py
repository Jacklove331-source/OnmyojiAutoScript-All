# This Python file uses the following encoding: utf-8

"""用标注器新增一套「结界皮肤」模板（图像侧）。

结界界面（阴阳寮 → 结界）有 4 个可点元素，OAS 靠它们决定结界挂卡/蹭卡能做什么：
    I_SHI_GROWN   式神育成牌坊
    I_SHI_CARD    结界卡牌坊（皮肤里可能写成「结缘禅房」）
    I_SHI_DEFENSE 结界防守牌坊
    I_REALM_SHIN  结界的皮肤入口（默认皮肤是鸟居）

本脚本从一张结界界面截图裁出这 4 个模板，写到
`tasks/Component/Costume/realm<N>/image.json` + 对应 png，并让标注器重生成
`tasks/Component/Costume/assets.py`（生成 I_SHI_GROWN_<N> 等属性）。

代码注册（RealmType / realm_costume_model / check_costume_realm）由
`dev_tools/realm_register_skin.py` 负责。

用法：
    .\\toolkit\\python.exe dev_tools\\realm_add_skin.py --id 6 --image log/_realm_clear.png
    .\\toolkit\\python.exe dev_tools\\realm_add_skin.py --id 6 --image log/_realm_clear.png \\
        --roi shin=362,486,56,60 --roi defense=758,496,34,76
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
TRACE = ROOT / 'log' / '_realm_add_skin.trace'
TASK = 'Component'

# key -> (itemName, 说明, roiFront, roiBack, threshold)
DEFAULTS = {
    'shin': ('realm_shin_{idx}', '结界的皮肤入口（鸟居）',
             (362, 486, 56, 60), (300, 430, 180, 170), 0.85),
    'card': ('shi_card_{idx}', '结界卡入口',
             (468, 496, 36, 76), (410, 440, 150, 170), 0.6),
    'grown': ('shi_grown_{idx}', '式神育成入口',
              (632, 496, 34, 76), (580, 440, 150, 170), 0.6),
    'defense': ('shi_defense_{idx}', '结界防守入口',
                (758, 496, 34, 76), (700, 440, 150, 170), 0.7),
}
ORDER = ['shin', 'card', 'grown', 'defense']


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
    except Exception as error:
        return -1, {'error': f'{type(error).__name__}: {error}'}
    if 'json' in ctype.lower():
        try:
            return status, json.loads(body) if body else {}
        except ValueError:
            return status, {'raw': body}
    return status, {'raw': body}


def main() -> int:
    parser = argparse.ArgumentParser(description='用标注器新增结界皮肤模板')
    parser.add_argument('--id', type=int, required=True)
    parser.add_argument('--image', required=True, help='结界界面截图（相对仓库根）')
    parser.add_argument('--roi', action='append', default=[],
                        help='覆盖 roiFront：shin=362,486,56,60，可重复')
    parser.add_argument('--roi-back', action='append', default=[],
                        help='覆盖 roiBack：card=700,440,150,170；不给则按新的 roiFront 自动外扩')
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

    back_overrides = {}
    for item in args.roi_back:
        key, _, value = item.partition('=')
        key = key.strip()
        if key not in DEFAULTS:
            trace(f'[FAIL] 未知 roi-back 键 {key}')
            return 1
        back_overrides[key] = [int(v) for v in value.split(',')]

    specs = []
    for key in ORDER:
        item_tpl, desc, front, back, threshold = DEFAULTS[key]
        item_name = item_tpl.format(idx=idx)
        roi_front = overrides.get(key, list(front))
        if key in back_overrides:
            roi_back = back_overrides[key]
        elif key in overrides:
            # 改了 roiFront 但没给 roiBack 时，按新位置自动外扩，否则搜索范围不含新位置
            cx = roi_front[0] + roi_front[2] // 2
            cy = roi_front[1] + roi_front[3] // 2
            roi_back = [max(0, cx - 75), max(0, cy - 85), 150, 170]
        else:
            roi_back = list(back)
        specs.append({
            'key': key,
            'itemName': item_name,
            'description': desc,
            'imageName': f'realm{idx}_{item_name}.png',
            'roiFront': roi_front,
            'roiBack': roi_back,
            'threshold': threshold,
        })

    trace(f'结界皮肤 #{idx}：{len(specs)} 条规则，源图 {source.name}')
    for spec in specs:
        trace(f"  {spec['itemName']:22} roiFront={spec['roiFront']} "
              f"roiBack={spec['roiBack']} th={spec['threshold']}")
    if args.dry_run:
        trace('[dry-run] 未写入任何文件')
        return 0

    json_relpath = f'Costume/realm{idx}/image.json'
    dir_path = f'Component/Costume/realm{idx}'
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

    try:
        content = base64.b64encode(source.read_bytes()).decode('ascii')
        status, data = call(base, 'POST', '/tool/annotator/api/images/upload', {
            'session_id': session_id,
            'images': [{'name': source.name, 'content_base64': content}],
        })
        images = (data or {}).get('images') or []
        image_id = (images[0].get('image_id') or images[0].get('id')) if images else None
        if not report('上传结界界面截图', bool(image_id), str(image_id)):
            return 1

        if json_path.exists():
            status, _ = call(base, 'POST', '/tool/annotator/api/rules/source/delete', {
                'dir_path': dir_path, 'file_name': json_path.name})
            report('删除旧 json', status == 200, f'HTTP {status}')
        status, _ = call(base, 'POST', '/tool/annotator/api/rules/source/create', {
            'dir_path': dir_path, 'file_name': json_path.name})
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
             query={'reason': 'realm_add'})

    trace(f'\nREALM SKIN {idx} IMAGES: '
          f'{"PASSED" if failures[0] == 0 else "FAILED"} (failures={failures[0]})')
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
