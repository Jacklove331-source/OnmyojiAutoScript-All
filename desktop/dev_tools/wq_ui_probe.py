# This Python file uses the following encoding: utf-8

"""悬赏封印 UI 适配探针（不能看图时用来读屏/点文本）。

用途：
    .\\toolkit\\python.exe dev_tools\\wq_ui_probe.py shot            # 打印整屏文本(+坐标)并存图
    .\\toolkit\\python.exe dev_tools\\wq_ui_probe.py click-text 悬赏  # 点第一个含该文本的位置
    .\\toolkit\\python.exe dev_tools\\wq_ui_probe.py match            # 跑一遍现有 WQ 模板匹配，打印分数
    .\\toolkit\\python.exe dev_tools\\wq_ui_probe.py crop x y w h out.png  # 裁一块存图（做新模板用）

设计约束：同一时间只允许一个 OAS 设备进程；本工具跑完即退出，不常驻。
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.atom.ocr import RuleOcr  # noqa: E402
from module.config.config import Config  # noqa: E402
from module.device.device import Device  # noqa: E402


def ocr_full_text(device, image, config_name='oas3'):
    """整屏文本：按 128px 横带做 Single OCR，返回 [(text, band_top)]。"""
    height, width = image.shape[:2]
    results = []
    step = 128
    for top in range(0, height, step):
        h = min(step, height - top)
        rule = RuleOcr(
            roi=(0, top, width, h), area=(0, top, width, h),
            mode='Single', method='Default', keyword='', name=f'band_{top}',
        )
        try:
            text = rule.ocr(image)
        except Exception as error:  # noqa: BLE001
            text = f'<err {type(error).__name__}>'
        text = str(text or '').strip()
        if text:
            results.append((text, top))
    return results


def detect_boxes(device, image):
    """带坐标的文本块：mode='Full' 返回 BoxedResult 列表（含 box/ocr_text）。"""
    height, width = image.shape[:2]
    rule = RuleOcr(
        roi=(0, 0, width, height), area=(0, 0, width, height),
        mode='Full', method='Default', keyword='', name='full',
    )
    try:
        items = rule.detect_and_ocr(image)
    except Exception as error:  # noqa: BLE001
        print(f'detect_and_ocr failed: {error!r}')
        return []
    output = []
    for item in items or []:
        text = str(getattr(item, 'ocr_text', '') or '').strip()
        box = getattr(item, 'box', None)
        if not text or box is None:
            continue
        try:
            x1, y1 = int(box[0][0]), int(box[0][1])
            x2, y2 = int(box[2][0]), int(box[2][1])
        except (IndexError, TypeError):
            continue
        output.append({'text': text, 'x': x1, 'y': y1,
                       'w': x2 - x1, 'h': y2 - y1})
    return output


def make_device(config_name: str):
    return Device(Config(config_name))


def main() -> int:
    args = [item for item in sys.argv[1:] if item]
    if not args:
        print(__doc__)
        return 2
    command = args[0]
    config_name = 'oas3'
    device = make_device(config_name)
    image = device.screenshot()
    print(f'frame {getattr(image, "shape", None)}')
    dump_dir = ROOT / 'log' / 'wq_probe'
    dump_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%H%M%S')

    if command == 'shot':
        bands = ocr_full_text(device, image)
        print('== 横带文本 ==')
        for text, top in bands:
            print(f'  y={top:>3}: {text[:90]}')
        boxes = detect_boxes(device, image)
        print(f'== 文本块 {len(boxes)} ==')
        for item in boxes[:60]:
            print(f"  ({item['x']:>4},{item['y']:>3},{item['w']:>3},"
                  f"{item['h']:>2}) {item['text'][:40]}")
        out = dump_dir / f'{stamp}_shot.json'
        out.write_text(json.dumps({'bands': bands, 'boxes': boxes},
                                  ensure_ascii=False, indent=1),
                       encoding='utf-8')
        import cv2
        cv2.imwrite(str(dump_dir / f'{stamp}_shot.png'),
                    cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        print(f'saved {out.name}')
        return 0

    if command == 'click-text':
        if len(args) < 2:
            print('用法: click-text <文本>')
            return 2
        needle = args[1]
        boxes = detect_boxes(device, image)
        hit = next((item for item in boxes if needle in item['text']), None)
        if hit is None:
            print(f'没找到含「{needle}」的文本；现有文本: '
                  + ' | '.join(item['text'] for item in boxes[:20]))
            return 1
        x = hit['x'] + hit['w'] // 2
        y = hit['y'] + hit['h'] // 2
        print(f"click ({x},{y}) -> {hit['text']}")
        device.click(x, y, control_check=False)
        time.sleep(2)
        after = device.screenshot()
        bands = ocr_full_text(device, after)
        print('== 点击后横带文本 ==')
        for text, top in bands:
            print(f'  y={top:>3}: {text[:90]}')
        import cv2
        cv2.imwrite(str(dump_dir / f'{stamp}_after.png'),
                    cv2.cvtColor(after, cv2.COLOR_RGB2BGR))
        return 0

    if command == 'match':
        from tasks.WantedQuests.assets import WantedQuestsAssets  # noqa: F401
        from tasks.WantedQuests.script_task import ScriptTask

        class Probe(ScriptTask):
            def __init__(self, config, dev):  # 跳过 ScriptTask.__init__ 的设备检查
                self.config = config
                self.device = dev

        probe = Probe(Config(config_name), device)
        names = [name for name in dir(probe)
                 if name.startswith(('I_WQ', 'I_TRACE', 'O_WQ', 'C_'))]
        for name in sorted(names):
            rule = getattr(probe, name, None)
            if rule is None:
                continue
            try:
                if hasattr(rule, 'match_all_any'):
                    matches = rule.match_all_any(image, roi=list(rule.roi_back),
                                                 threshold=rule.threshold,
                                                 nms_threshold=0.3)
                    best = max((item[0] for item in matches), default=0.0)
                    where = f'matches={len(matches)} best={best:.3f}'
                elif hasattr(rule, 'ocr'):
                    value = rule.ocr(image)
                    where = f'ocr=[{str(value)[:40]}]'
                else:
                    where = 'skip'
            except Exception as error:  # noqa: BLE001
                where = f'err={type(error).__name__}'
            print(f'  {name:<34} thr={getattr(rule, "threshold", "-")} {where}')
        return 0

    if command == 'crop':
        if len(args) < 6:
            print('用法: crop x y w h out.png')
            return 2
        x, y, w, h = (int(args[1]), int(args[2]), int(args[3]), int(args[4]))
        out = Path(args[5])
        if not out.is_absolute():
            out = ROOT / out
        import cv2
        patch = image[y:y + h, x:x + w]
        cv2.imwrite(str(out), cv2.cvtColor(patch, cv2.COLOR_RGB2BGR))
        print(f'saved {out} from ({x},{y},{w},{h}) shape={patch.shape}')
        return 0

    print(f'未知命令: {command}')
    return 2


if __name__ == '__main__':
    sys.exit(main())
