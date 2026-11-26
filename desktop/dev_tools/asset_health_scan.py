# This Python file uses the following encoding: utf-8

"""画面适配审计:把「当前这一帧」和项目里所有页面/功能模板对一遍，找出失配的模板。

用途：
- 游戏版本更新后，一眼看出哪些画面判定模板塌了（分数掉到 threshold 以下）
- 判断「现在停在哪一屏」（每张页面模板的最高分）
- 失配项的 ROI 直接从当前帧重新裁出来，作为替换候选（--recrop-out）

只读，不改动任何模板。默认读取 log/live/<config>.jpg（实例子进程写出的实时帧），
不抢占设备；确实需要全新帧时才用 --live（会创建 Device，要求没有其它进程在用模拟器）。

用法：
    .\\toolkit\\python.exe dev_tools\\asset_health_scan.py                     # 用最新 live 帧扫页面模板
    .\\toolkit\\python.exe dev_tools\\asset_health_scan.py --only-fail
    .\\toolkit\\python.exe dev_tools\\asset_health_scan.py --scope all --top 40
    .\\toolkit\\python.exe dev_tools\\asset_health_scan.py --screen shot.png --recrop-out log/recrop
    .\\toolkit\\python.exe dev_tools\\asset_health_scan.py --json-out log/asset_health.json
"""

import argparse
import json
import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_SCOPES = {
    'page_checks': ROOT / 'tasks' / 'GameUi' / 'page_checks',
    'page': ROOT / 'tasks' / 'GameUi' / 'page',
    'additional': ROOT / 'tasks' / 'GameUi' / 'additional',
}
ALL_SCOPE = {'GameUi': ROOT / 'tasks' / 'GameUi'}


def imread(path: pathlib.Path):
    """中文/空格路径也能读（cv2.imread 在 Windows 上会失败）。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite(path: pathlib.Path, img) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(path.suffix or '.png', img)
    if not ok:
        return False
    buf.tofile(str(path))
    return True


def parse_roi(text, fallback=None):
    try:
        values = [int(float(v)) for v in str(text).split(',')]
    except (TypeError, ValueError):
        return fallback
    if len(values) != 4:
        return fallback
    return values


def load_rules(folder: pathlib.Path, recursive: bool = False):
    """读目录下所有 image_*.json，返回规则列表。"""
    rules = []
    pattern = '**/image_*.json' if recursive else 'image_*.json'
    for path in sorted(folder.glob(pattern)):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            print(f'[WARN] 解析失败 {path}: {error}')
            continue
        for item in data:
            if str(item.get('method', 'Template matching')) != 'Template matching':
                continue
            image_name = item.get('imageName')
            if not image_name:
                continue
            image_path = path.parent / image_name
            rules.append({
                'name': str(item.get('itemName') or path.stem + '_' + pathlib.Path(image_name).stem),
                'group': path.stem.replace('image_', ''),
                'json': str(path.relative_to(ROOT)),
                'file': image_path,
                'roi_front': parse_roi(item.get('roiFront')),
                'roi_back': parse_roi(item.get('roiBack')),
                'threshold': float(item.get('threshold', 0.8)),
                'description': str(item.get('description') or ''),
            })
    return rules


def score_rule(rule, screen, screen_gray=None):
    """在 roi_back 内做模板匹配，返回 (score, hit_rect)。

    与 OAS `RuleImage.match` 的判定一致：模板图 + roi_back 搜索区域 + CCOEFF_NORMED 最大值。
    """
    template = imread(rule['file'])
    if template is None:
        return None, None, f'模板文件缺失: {rule["file"]}'
    th, tw = template.shape[:2]
    sh, sw = screen.shape[:2]
    roi = rule['roi_back'] or rule['roi_front'] or [0, 0, sw, sh]
    x, y, w, h = roi
    # roi_back 是搜索范围；ROI 尺寸小于模板时以模板为准外扩
    x = max(0, min(x, sw - 1))
    y = max(0, min(y, sh - 1))
    w = max(w, tw) if w > 0 else tw
    h = max(h, th) if h > 0 else th
    x2 = min(sw, x + w)
    y2 = min(sh, y + h)
    if x2 - x < tw or y2 - y < th:
        return None, None, f'搜索区域 {x},{y},{w},{h} 放不下模板 {tw}x{th}'
    search = screen[y:y2, x:x2]
    result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    rect = (x + max_loc[0], y + max_loc[1], tw, th)
    return float(max_val), rect, None


def resolve_screen(args):
    if args.screen:
        path = pathlib.Path(args.screen)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise SystemExit(f'截图不存在: {path}')
        return imread(path), str(path)
    if args.live:
        sys.path.insert(0, str(ROOT))
        import os
        os.chdir(str(ROOT))
        from module.config.config import Config
        from module.device.device import Device
        config = Config(args.live)
        device = Device(config)
        device.sleep(0.2)
        image = device.screenshot()
        out = ROOT / 'log' / 'live' / f'{args.live}_asset_scan.png'
        imwrite(out, image)
        return image, str(out)
    live_dir = ROOT / 'log' / 'live'
    candidates = []
    if live_dir.exists():
        candidates = sorted((p for p in live_dir.glob(f'{args.config}*.jpg')),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            candidates = sorted(live_dir.glob('*.jpg'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit('没有可用的 live 帧，请用 --screen 指定截图，或 --live <config> 抓新帧')
    latest = candidates[0]
    return imread(latest), str(latest)


def main() -> int:
    parser = argparse.ArgumentParser(description='OAS 画面模板适配审计')
    parser.add_argument('--screen', help='离线截图路径（相对 repo 根）')
    parser.add_argument('--live', help='实时抓帧的配置名，如 oas1（会占用设备，需独占）')
    parser.add_argument('--config', default='oas1', help='从 log/live/<config>.jpg 取帧，默认 oas1')
    parser.add_argument('--scope', default='page_checks',
                        choices=['page_checks', 'page', 'additional', 'all'],
                        help='扫描范围，默认 page_checks（页面判定模板）')
    parser.add_argument('--only-fail', action='store_true', help='只显示低于 threshold 的项')
    parser.add_argument('--top', type=int, default=0, help='按分数升序只显示前 N 项')
    parser.add_argument('--recrop-out', help='把失配项按 ROI 从当前帧裁出，存成替换候选')
    parser.add_argument('--json-out', help='完整结果写入 json')
    args = parser.parse_args()

    screen, source = resolve_screen(args)
    if screen is None:
        raise SystemExit(f'截图读取失败: {source}')
    height, width = screen.shape[:2]
    print(f'帧来源: {source}  尺寸 {width}x{height}')
    if (width, height) != (1280, 720):
        print(f'[WARN] 不是 1280x720，模板坐标会整体偏移；若是竖屏 webview 请先退出再扫')

    folders = {}
    if args.scope == 'all':
        folders = {'GameUi': ALL_SCOPE['GameUi']}
        rules = load_rules(ALL_SCOPE['GameUi'], recursive=True)
    else:
        rules = load_rules(DEFAULT_SCOPES[args.scope])
    # 去重（同一 itemName 在多个 json 里出现时保留阈值最高的）
    unique = {}
    for rule in rules:
        key = rule['name']
        if key not in unique or rule['threshold'] > unique[key]['threshold']:
            unique[key] = rule
    rules = sorted(unique.values(), key=lambda r: r['name'])
    if not rules:
        raise SystemExit(f'扫描范围 {args.scope} 没找到模板规则')

    rows = []
    errors = []
    for rule in rules:
        score, rect, error = score_rule(rule, screen)
        if error:
            errors.append((rule['name'], error))
            rows.append({'name': rule['name'], 'group': rule['group'], 'score': None,
                         'threshold': rule['threshold'], 'rect': None, 'hit': False,
                         'json': rule['json'], 'description': rule['description'],
                         'error': error})
            continue
        rows.append({'name': rule['name'], 'group': rule['group'], 'score': score,
                     'threshold': rule['threshold'], 'rect': list(rect),
                     'hit': score >= rule['threshold'], 'json': rule['json'],
                     'description': rule['description'], 'error': None})

    scored = [row for row in rows if row['score'] is not None]
    scored.sort(key=lambda r: r['score'])
    hits = [row for row in scored if row['hit']]
    print(f'模板 {len(rows)} 项（可评分 {len(scored)}），命中 {len(hits)}，'
          f'失配 {len(scored) - len(hits)}，坏图 {len(errors)}')
    if hits:
        print(f'最高分 {hits[-1]["score"]:.3f} → {hits[-1]["name"]}')

    # 页面级：每个 group 的最高分，用来判断「现在停在哪一屏」
    by_group = {}
    for row in scored:
        by_group.setdefault(row['group'], []).append(row)
    page_hits = sorted(((max(r['score'] for r in group), name)
                        for name, group in by_group.items()), reverse=True)
    print('\n== 页面分组最高分（判定当前界面） ==')
    for score, name in page_hits[:8]:
        print(f'  {name:<22} {score:.3f}')

    display = [row for row in scored if (not args.only_fail) or (not row['hit'])]
    if args.top > 0:
        display = display[:args.top]
    print('\n== 模板明细（按分数升序） ==')
    print(f'{"score":>6}  {"th":>4}  {"状态":<4} {"模板":<46} ROI/说明')
    for row in display:
        state = 'OK' if row['hit'] else 'FAIL'
        rect = f'{row["rect"]}' if row['rect'] else '-'
        print(f'{row["score"]:>6.3f}  {row["threshold"]:>4.2f}  {state:<4} '
              f'{row["name"][:46]:<46} {rect}  {row["description"][:24]}')

    if args.recrop_out:
        out_dir = pathlib.Path(args.recrop_out)
        if not out_dir.is_absolute():
            out_dir = ROOT / out_dir
        manifest = []
        for row in scored:
            if row['hit']:
                continue
            rule = unique[row['name']]
            template = imread(rule['file'])
            if template is None:
                continue
            th, tw = template.shape[:2]
            roi = rule['roi_front'] or [0, 0, tw, th]
            x, y, _, _ = roi
            x = max(0, min(x, width - tw))
            y = max(0, min(y, height - th))
            crop = screen[y:y + th, x:x + tw]
            if crop.shape[:2] != (th, tw):
                continue
            target = out_dir / f'candidate_{pathlib.Path(rule["file"]).name}'
            imwrite(target, crop)
            manifest.append({
                'rule': row['name'], 'old_file': str(rule['file']),
                'candidate': str(target), 'roi_front': [x, y, tw, th],
                'old_score': round(row['score'], 4), 'desc': rule['description'],
            })
        (out_dir / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\n替换候选已裁出 {len(manifest)} 张 → {out_dir}')
        print('核对后把 candidate_xxx.png 覆盖对应模板，再跑一次本脚本确认分数回升')

    if args.json_out:
        out = pathlib.Path(args.json_out)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            'source': source, 'screen_size': [width, height],
            'total': len(rows), 'hits': len(hits),
            'page_scores': [{'group': n, 'score': round(s, 4)} for s, n in page_hits],
            'rows': rows,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'结果写入 {out}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
