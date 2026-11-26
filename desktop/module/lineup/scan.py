# This file uses the following encoding: utf-8
"""式神录扫描：自动翻页、点卡片、OCR 名字，写入 roster.json。

坐标基于 1280x720（MuMu 默认分辨率）。
调参用的截图放在 log/lineup_capture/，可离线验证：
    .\\toolkit\\python.exe dev_tools\\roster_scan.py ocr log\\lineup_capture\\shrin_page.png
"""
import io
import json
import re
import time
from pathlib import Path

from module.logger import logger

# 庭院 / 式神录（1280x720）
SHRIN_ENTRY = (1140, 665)          # 庭院底栏「式神录」
RECORDS_BACK = (30, 30)            # 式神录左上返回箭头
TABS = {
    '全部': (62, 148),
    'SP': (160, 148),
    'SSR': (213, 148),
    'SR': (262, 148),
    'R': (302, 148),
    'N': (340, 148),
}
GRID_X = (98, 212, 326, 440)
# 卡片是竖版（约 110x170），一屏只有 3 行；y=265/435/605 是实测行中心
GRID_Y = (265, 435, 605)
# 竖排名牌：默认在式神右侧；打开「情报」面板后整列左移
NAME_ROIS = ((548, 88, 78, 392), (12, 88, 78, 392))
SWIPE_UP = ((250, 600), (250, 220))
SWIPE_DOWN = ((250, 220), (250, 600))
# 式神详情：点右侧折叠把手打开「式神情报」面板，同一屏里就有等级、速度、技能等级
DETAIL_HANDLE = (1200, 345)
DETAIL_TAB_INFO = (1210, 170)
DETAIL_BACK = (30, 30)
DETAIL_LEVEL_ROI = (715, 95, 240, 45)
DETAIL_SPEED_ROI = (1000, 295, 130, 35)
DETAIL_SKILL_ROI = (745, 490, 260, 80)
# 右侧竖排页签（情报/御魂/外观/羁绊）：出现即说明详情面板已打开
DETAIL_TAB_ROI = (1180, 120, 100, 420)

CJK = re.compile(r'[\u4e00-\u9fff]{2,}')
# OCR 偶尔会把 UI 上的固定文字一起框进来，直接丢掉
STOPWORDS = {'式神', '伤害', '增伤', '喜欢中', '展示', '摄影', '档案', '育成', '满级', '我的式神', '我的', '式神录', '情报'}


def _ocr_text(image) -> str:
    """对整块图做一次 Full OCR，按纵坐标（再横坐标）排序后拼接。

    注意 module.ocr.base_ocr.BoxedResult 的字段是 ``ocr_text``；
    早期实现读的是 ``.ocr``，永远拿到空字符串，扫描结果因此全空。
    """
    return ''.join(_ocr_groups(image))


def _ocr_groups(image) -> list:
    """把 OCR 结果按「行」分组：竖排名字会被拆成单字，行与行之间用间隙区分。"""
    from module.atom.ocr import RuleOcr

    h, w = image.shape[:2]
    if h < 8 or w < 8:
        return []
    ocr = RuleOcr(roi=(0, 0, w, h), area=(0, 0, w, h), mode='Full',
                  method='Default', keyword='', name='roster_name')
    try:
        results = ocr.detect_and_ocr(image, logDisplay=False)
    except Exception as e:
        logger.debug(f'[RosterScan] ocr error: {e}')
        return []
    boxes = []
    for r in results:
        text = getattr(r, 'ocr_text', '') or ''
        if not text:
            continue
        try:
            ys = [float(p[1]) for p in r.box]
            xs = [float(p[0]) for p in r.box]
        except (AttributeError, TypeError, ValueError, IndexError):
            return [getattr(r, 'ocr_text', '') or '' for r in results]
        boxes.append((min(ys), max(ys), min(xs), text))
    boxes.sort()

    groups = []
    for y0, y1, _x0, text in boxes:
        if groups and y0 - groups[-1][1] <= max(12.0, 0.45 * (y1 - y0)):
            groups[-1][0] += text
            groups[-1][1] = max(groups[-1][1], y1)
        else:
            groups.append([text, y1])
    return [g[0] for g in groups]


def _clean_name(text: str) -> str:
    """从 OCR 文本里取最长的中文片段，并过滤 UI 固定词。"""
    runs = [r for r in CJK.findall(text or '') if r not in STOPWORDS]
    if not runs:
        return ''
    return max(runs, key=len)


def _variants(crop):
    """竖排文字：原图 + 顺时针/逆时针旋转各试一次。"""
    yield crop
    try:
        import cv2
    except ImportError:
        return
    yield cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
    yield cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)


def _ocr_vertical(image, roi) -> str:
    x, y, w, h = roi
    img_h, img_w = image.shape[:2]
    x, y = max(0, int(x)), max(0, int(y))
    w, h = min(int(w), img_w - x), min(int(h), img_h - y)
    if w < 8 or h < 8:
        return ''
    crop = image[y:y + h, x:x + w]
    # 先读原图：竖排名字 PP-OCR 能直接按列识别，旋转反而会把旁边的标签粘进来
    for variant in _variants(crop):
        for text in _ocr_groups(variant):
            name = _clean_name(text)
            if len(name) >= 2:
                return name
    return ''


_KNOWN_NAMES = None


def _known_names() -> set:
    """已知式神名（棋子目录 + 阵容知识库），用于纠正 OCR 的单字错读。"""
    global _KNOWN_NAMES
    if _KNOWN_NAMES is not None:
        return _KNOWN_NAMES
    names = set()
    try:
        from tasks.Chess.strategy.shikigami_catalog import SHIKIGAMI_BY_CHINESE_NAME
        names.update(SHIKIGAMI_BY_CHINESE_NAME.keys())
    except Exception as e:
        logger.debug(f'[RosterScan] catalog unavailable: {e}')
    try:
        from module.lineup.meta import META_FILE, _load
        names.update((_load(META_FILE).get('shikigami') or {}).keys())
    except Exception as e:
        logger.debug(f'[RosterScan] meta unavailable: {e}')
    names.discard('')
    _KNOWN_NAMES = names
    return names


def _snap_name(name: str) -> str:
    """把 OCR 结果对到已知式神名。

    - 同长度、只差一个字且候选唯一：修正 千/干 这类误读；
    - 多出首尾一个字：竖排名字容易把下方图标一起框进来，去掉后能对上就用。
    """
    if not name:
        return name
    known = _known_names()
    if not known or name in known:
        return name
    close = [n for n in known
             if len(n) == len(name) and sum(a != b for a, b in zip(n, name)) == 1]
    if len(close) == 1:
        logger.info(f'[RosterScan] ocr "{name}" -> "{close[0]}"')
        return close[0]
    if len(name) > 2:
        for trimmed in (name[:-1], name[1:]):
            if trimmed in known:
                logger.info(f'[RosterScan] ocr "{name}" -> "{trimmed}"')
                return trimmed
    return name


def name_from_image(image, rois=NAME_ROIS) -> str:
    """从式神录截图里读当前选中式神的名字（找不到返回空串）。"""
    for roi in rois:
        name = _ocr_vertical(image, roi)
        if len(name) >= 2:
            return _snap_name(name)
    return ''


def _match(image, asset, threshold: float = None) -> bool:
    try:
        return bool(asset.match(image, threshold))
    except Exception as e:
        logger.debug(f'[RosterScan] match {getattr(asset, "name", "?")} failed: {e}')
        return False


def _on_courtyard(image) -> bool:
    """庭院判定用底栏入口（默认皮肤/换皮都能命中），I_CHECK_MAIN 换皮后会失配。"""
    from tasks.GameUi.assets import GameUiAssets
    return (_match(image, GameUiAssets.I_MAIN_GOTO_EXPLORATION, 0.75)
            or _match(image, GameUiAssets.I_MAIN_GOTO_SHIKIGAMI_RECORDS, 0.65))


_TITLE_ASSET = None


def _records_title_match(image) -> bool:
    """式神录左上角「式神录」标题：不受搜索/预设面板展开影响。"""
    global _TITLE_ASSET
    if _TITLE_ASSET is None:
        from module.atom.image import RuleImage
        _TITLE_ASSET = RuleImage(
            roi_front=(55, 5, 200, 55), roi_back=(55, 5, 200, 55), threshold=0.85,
            method='Template matching',
            file='./tasks/GameUi/page_checks/page_check_records_title.png')
    return _match(image, _TITLE_ASSET)


def _on_records(image) -> bool:
    from tasks.GameUi.page_check_assets import PageCheckAssets
    if _match(image, PageCheckAssets.I_CHECK_RECORDS):
        return True
    # 搜索/预设面板展开时 I_CHECK_RECORDS 会掉到 0.62，用标题兜底
    return _records_title_match(image)


def _load_roster(path: Path) -> dict:
    if not path.exists():
        return {'shikigami': []}
    try:
        with io.open(path, encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'shikigami': []}


def _save_roster(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _click(device, x: int, y: int, wait: float = 0.4) -> None:
    """带清空点击记录的点击，避免 OAS 的“重复点击”保护误报。"""
    device.click(x, y, control_check=False)
    time.sleep(wait)
    try:
        device.click_record_clear()
    except Exception:
        pass


def _open_records(device) -> bool:
    """从庭院进入式神录；进入失败返回 False。"""
    image = device.screenshot()
    if _on_records(image):
        return True
    if not _on_courtyard(image):
        # 不在庭院：点一次返回尝试回到庭院，再判断
        _click(device, *RECORDS_BACK, wait=1.0)
        image = device.screenshot()
        if not _on_courtyard(image):
            return False
    _click(device, *SHRIN_ENTRY, wait=2.5)
    return _on_records(device.screenshot())


def _ocr_roi_text(image, roi, scale: int = 4) -> list:
    """裁剪 + 放大后做一次 Full OCR，返回每段文本。"""
    from module.atom.ocr import RuleOcr

    x, y, w, h = roi
    w, h = min(w, image.shape[1] - x), min(h, image.shape[0] - y)
    if w < 6 or h < 6:
        return []
    crop = image[y:y + h, x:x + w]
    if scale and scale > 1:
        import cv2
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hh, ww = crop.shape[:2]
    ocr = RuleOcr(roi=(0, 0, ww, hh), area=(0, 0, ww, hh), mode='Full',
                  method='Default', keyword='', name='roster_detail')
    try:
        return [str(r.ocr_text) for r in ocr.detect_and_ocr(crop, logDisplay=False)]
    except Exception as e:
        logger.debug(f'[RosterScan] detail ocr failed: {e}')
        return []


def level_from_image(image):
    """式神情报面板顶部的 ``Lv. 40/40``。"""
    joined = ' '.join(_ocr_roi_text(image, DETAIL_LEVEL_ROI, 4)).upper().replace('LV.', 'LV')
    m = re.search(r'LV\s*(\d+)\s*/\s*(\d+)', joined) or re.search(r'(\d+)\s*/\s*(\d+)', joined)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def skills_from_image(image) -> list:
    """面板底部三个技能图标左上角的等级数字，按从左到右返回。"""
    from module.atom.ocr import RuleOcr

    x, y, w, h = DETAIL_SKILL_ROI
    w, h = min(w, image.shape[1] - x), min(h, image.shape[0] - y)
    if w < 8 or h < 8:
        return []
    import cv2
    best = []
    for scale in (8, 10, 6):
        crop = cv2.resize(image[y:y + h, x:x + w], None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)
        hh, ww = crop.shape[:2]
        ocr = RuleOcr(roi=(0, 0, ww, hh), area=(0, 0, ww, hh), mode='Full',
                      method='Default', keyword='', name='roster_skills')
        try:
            results = ocr.detect_and_ocr(crop, logDisplay=False)
        except Exception as e:
            logger.debug(f'[RosterScan] skill ocr failed: {e}')
            continue
        items = []
        for r in results:
            text = str(getattr(r, 'ocr_text', '') or '').strip()
            if not text.isdigit():
                continue
            try:
                items.append((min(float(p[0]) for p in r.box), int(text)))
            except (TypeError, ValueError, IndexError):
                continue
        items.sort()
        values = [value for _x, value in items[:3]]
        # 技能等级只可能是 1-5；出现 6/7 之类说明把徽章边框认成了数字，整组丢弃
        if not values or any(v < 1 or v > 5 for v in values):
            continue
        if len(values) > len(best):
            best = values
        if len(best) == 3:
            break
    return best


def speed_from_image(image):
    """面板里的速度 ``117+6``，返回合计值（读不到返回 None）。"""
    joined = ' '.join(_ocr_roi_text(image, DETAIL_SPEED_ROI, 4))
    m = re.search(r'(\d+)\s*\+\s*(\d+)', joined) or re.search(r'(\d+)', joined)
    if not m:
        return None
    try:
        return int(m.group(1)) + (int(m.group(2)) if m.lastindex and m.lastindex > 1 else 0)
    except (TypeError, ValueError):
        return None


def _save_debug(image, capture_dir, tag: str) -> None:
    """把现场截图落盘，方便事后查 OCR 为什么失败。"""
    try:
        import cv2
        path = Path(str(capture_dir))
        path.mkdir(parents=True, exist_ok=True)
        file = path / f'{tag}_{time.strftime("%H%M%S")}.png'
        cv2.imwrite(str(file), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        logger.info(f'[RosterScan] debug capture -> {file}')
    except Exception as e:
        logger.debug(f'[RosterScan] save debug failed: {e}')


def _on_detail(image) -> bool:
    """详情面板是否已打开：右侧竖排页签里出现 情报/御魂/外观/羁绊 任一即可。"""
    text = ' '.join(_ocr_roi_text(image, DETAIL_TAB_ROI, 2))
    return any(key in text for key in ('情报', '御魂', '外观', '羁绊'))


def _read_stats(device, capture_dir=None) -> dict:
    """打开式神情报面板，读等级/速度/技能，然后返回列表。"""
    stats = {'level': None, 'skills': [], 'speed': None}
    image = device.screenshot()
    for _attempt in range(2):
        if _on_detail(image):
            break
        _click(device, *DETAIL_HANDLE, wait=1.3)
        image = device.screenshot()
    if not _on_detail(image):
        logger.warning('[RosterScan] 情报面板没打开，跳过这张卡的等级/技能')
        return stats
    # 面板会记住上次停留的页签（皮肤页没有等级/技能），必须切回「情报」
    for _attempt in range(2):
        _click(device, *DETAIL_TAB_INFO, wait=0.9)
        image = device.screenshot()
        if '情报' in ' '.join(_ocr_roi_text(image, (1180, 130, 100, 120), 2)):
            break
    stats['level'] = level_from_image(image)
    stats['skills'] = skills_from_image(image)
    stats['speed'] = speed_from_image(image)
    if (stats['level'] is None or not stats['skills']) and capture_dir is not None:
        _save_debug(image, capture_dir, 'detail_miss')
    _click(device, *DETAIL_BACK, wait=1.3)
    if _on_detail(device.screenshot()):
        _click(device, *DETAIL_BACK, wait=1.0)
    return stats


def _scroll_top(device) -> None:
    """把式神列表滑回顶部（顶部再往下滑只是回弹，无副作用）。"""
    for _ in range(2):
        device.swipe(*SWIPE_DOWN, duration=0.3)
        time.sleep(0.45)


def _scroll_to(device, page: int) -> None:
    """回到顶部后再向下翻 page 屏，保证每张卡都在确定的滚动位置上处理。"""
    _scroll_top(device)
    for _ in range(max(0, int(page))):
        device.swipe(*SWIPE_UP, duration=0.5)
        time.sleep(1.0)


def _scan_page(device, progress, with_level: bool = False, capture_dir=None, page: int = 0) -> dict:
    """扫描当前页的网格，返回 {(名字): 等级或 None}。"""
    found = {}
    last_name = None
    if not with_level:
        _scroll_to(device, page)
    for gy in GRID_Y:
        for gx in GRID_X:
            name = ''
            for attempt in range(3):
                if with_level:
                    # 育成页返回后列表会自动滚动，先回到本页的确定位置
                    _scroll_to(device, page)
                _click(device, gx, gy, wait=0.7)
                # 点中的卡会有一小段选中/模型动画，名字不是立刻刷新，多等几拍
                for _ in range(4):
                    name = name_from_image(device.screenshot())
                    if name and name != last_name:
                        break
                    time.sleep(0.5)
                if name and name != last_name:
                    break
                # 点空了会保留上一张卡的选择，表现为名字和上一格一样 —— 重试点一次
                logger.debug(f'[RosterScan] retry click ({gx},{gy}) name={name!r}')
            if not name or name == last_name:
                logger.debug(f'[RosterScan] no new card at ({gx},{gy})')
                continue
            last_name = name
            if name in found and found[name].get('level') is not None:
                continue
            stats = _read_stats(device, capture_dir) if with_level else {'level': None, 'skills': [], 'speed': None}
            known = found.get(name) or {}
            found[name] = {
                'level': stats['level'] if stats['level'] is not None else known.get('level'),
                'skills': stats['skills'] or known.get('skills') or [],
            }
            if progress:
                progress(name)
            level = found[name]['level']
            skill_text = ''.join(str(s) for s in found[name]['skills'])
            logger.info(f'[RosterScan] + {name}'
                        + (f' Lv{level}' if level else '')
                        + (f' 技能{skill_text}' if skill_text else ''))
    return found


def run_scan(device, cfg, progress=None) -> dict:
    """执行一次扫描。cfg 为 RosterScan 配置对象。"""
    rarities = [r.strip() for r in str(getattr(cfg, 'rarities', 'SP,SSR,SR')).split(',') if r.strip()]
    pages = int(getattr(cfg, 'pages_per_rarity', 2))
    out_path = Path(str(getattr(cfg, 'output_file', 'config/roster.json')))
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path

    data = _load_roster(out_path)
    existing = {item.get('name'): item for item in data.get('shikigami', []) if item.get('name')}
    capture_dir = Path(str(getattr(cfg, 'capture_dir', 'log/roster_scan')))
    if not capture_dir.is_absolute():
        capture_dir = Path.cwd() / capture_dir
    result = {'count': len(existing), 'found': {}, 'path': str(out_path), 'skipped': False}

    if not _open_records(device):
        logger.warning('[RosterScan] 未能进入式神录（不在庭院或页面未识别），本次跳过')
        result['skipped'] = True
        return result

    found = {}
    levels = {}
    skills = {}
    speeds = {}
    pages_scanned = 0
    complete = False
    include_level = bool(getattr(cfg, 'include_level', True))
    include_skills = bool(getattr(cfg, 'include_skills', True))
    include_speed = bool(getattr(cfg, 'include_speed', False))
    read_stats = include_level or include_skills or include_speed
    try:
        for rarity in rarities:
            tab = TABS.get(rarity)
            if tab is None:
                logger.warning(f'[RosterScan] unknown rarity {rarity}, skip')
                continue
            _click(device, *tab, wait=1.2)
            _scroll_top(device)
            rarity_found = 0
            for page in range(pages):
                page_names = _scan_page(device, progress, read_stats, capture_dir, page)
                pages_scanned += 1
                if not page_names:
                    logger.warning(f'[RosterScan] {rarity} page {page + 1} 没有识别到名字，提前结束该稀有度')
                    break
                before = len(found)
                for name, stats in page_names.items():
                    if name not in found:
                        found[name] = rarity
                        rarity_found += 1
                    if stats.get('level') is not None and levels.get(name) is None:
                        levels[name] = stats['level']
                    if stats.get('skills') and not skills.get(name):
                        skills[name] = stats['skills']
                    if include_speed and stats.get('speed') is not None and speeds.get(name) is None:
                        speeds[name] = stats['speed']
                if len(found) - before <= 2 and page > 0:
                    logger.info(f'[RosterScan] {rarity} 本页只新增 {len(found) - before} 个，基本是重叠，结束该稀有度')
                    break
            # 各稀有度之间回到列表顶部，避免下一页从半截开始
            _scroll_top(device)
            logger.info(f'[RosterScan] {rarity}: {rarity_found} new names')
        complete = True
    except Exception as e:
        logger.warning(f'[RosterScan] 扫描中断: {e}')
    finally:
        # 返回庭院，避免把界面留在式神录
        _click(device, *RECORDS_BACK, wait=1.0)

    # 合并：保留已有条目的技能/御魂，扫描结果补名字、稀有度和等级
    merged = []
    for name, rarity in found.items():
        item = existing.get(name) or {
            'name': name,
            'level': 40,
            'skills': [5, 5, 5],
            'speed': None,
            'souls': [],
        }
        item['rarity'] = rarity
        item['source'] = 'scan'
        if levels.get(name) is not None:
            item['level'] = levels[name]
        if skills.get(name):
            item['skills'] = skills[name]
        if speeds.get(name) is not None:
            item['speed'] = speeds[name]
        merged.append(item)
    # 本次没扫到的手动条目保留；上次扫描写进来的条目如果这次没扫到，多半是 OCR 错字，
    # 只在整轮扫描正常跑完时才清理，避免中途出错把名单删掉。
    for name, item in existing.items():
        if name in found:
            continue
        if complete and item.get('source') == 'scan':
            logger.info(f'[RosterScan] drop stale "{name}"')
            continue
        merged.append(item)

    data['shikigami'] = merged
    data['updated_at'] = time.strftime('%Y-%m-%d')
    _save_roster(out_path, data)
    logger.info(f'[RosterScan] saved {len(merged)} shikigami -> {out_path} ({pages_scanned} pages)')

    result.update({'count': len(merged), 'found': found, 'pages': pages_scanned})
    return result
