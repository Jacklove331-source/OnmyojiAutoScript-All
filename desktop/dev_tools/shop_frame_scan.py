# This Python file uses the following encoding: utf-8

"""离线扫商店卡面：拿一张截图，跑真实识别链路，输出每格结果与失败原因。

用途（对应「商店识图失败分类统计」）：不用开游戏就能复现识别结果——
把实机帧丢进来，看 5 个格子分别被识别成什么、分数多少、OCR 文本是什么，
并把每个格子/每张卡裁剪出来存盘作为**可复现样本**。

用法：
    .\\toolkit\\python.exe dev_tools\\shop_frame_scan.py <png...> [--out log\\chess_ref\\shop_scan]

输出（每帧）：
- 检测到的商店卡：name / score / price / bonds / raw_text / position / roi
- 5 个格子的归属：哪个格子没有卡、卡落在哪个坐标
- 裁剪图：`--out/<frame>/card_<i>.png`、`slot_<n>.png`
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402


def load_frame(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f'无法解码: {path}')
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def build_task(config_name):
    from module.config.config import Config
    from module.device.device import Device
    from tasks.Chess.script_task import ScriptTask

    config = Config(config_name)
    device = Device(config)
    task = ScriptTask(config=config, device=device)
    return task, device


def scan(task, image, out_dir=None, tag='frame'):
    """把 image 当作 device 当前帧，跑一次真实的商店卡面识别。"""
    device = task.device
    device.image = image
    # 帧号推进，避免命中上一帧的缓存
    try:
        device._bump_frame_id()
    except Exception:  # noqa: BLE001
        pass
    cards = task._frame_card_scan('store')

    slots = []
    for index, click_rule in task._shop_slots():
        x, y, w, h = click_rule.roi_back
        matched = [
            card for card in cards
            if card['name'] and x <= card['position'][0] < x + w
            and y <= card['position'][1] < y + h
        ]
        best = max(matched, key=lambda card: card['score']) if matched else None
        # 有卡（含识别不出名字的卡框）与空格子的区分
        try:
            has_card = bool(task._shop_slot_has_card(click_rule))
        except Exception as error:  # noqa: BLE001
            has_card = f'error: {error!r}'
        slots.append({
            'slot': index,
            'roi': [x, y, w, h],
            'recognized': best['name'] if best else None,
            'score': round(best['score'], 4) if best else None,
            'raw_text': best['raw_text'] if best else None,
            'cards_in_slot': len(matched),
            'has_card': has_card,
        })

    # 每张已识别卡 + 每个格子存图
    saved = []
    if out_dir is not None:
        folder = Path(out_dir) / tag
        folder.mkdir(parents=True, exist_ok=True)
        for i, card in enumerate(cards):
            cx, cy, cw, ch = card['roi']
            pad = 6
            crop = image[
                max(0, cy - pad):cy + ch + pad,
                max(0, cx - pad):cx + cw + pad,
            ]
            path = folder / f"card_{i}_{card['name'] or 'unknown'}.png"
            cv2.imwrite(str(path), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            saved.append(str(path))
        for slot in slots:
            x, y, w, h = slot['roi']
            crop = image[max(0, y):y + h, max(0, x):x + w]
            path = folder / f"slot_{slot['slot']}.png"
            cv2.imwrite(str(path), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            saved.append(str(path))

    return {
        'detected_cards': len(cards),
        'cards': [
            {
                'name': card['name'],
                'score': round(card['score'], 4),
                'price': card.get('price'),
                'bonds': card.get('bonds'),
                'raw_text': card.get('raw_text'),
                'position': card['position'],
                'roi': card['roi'],
            }
            for card in cards
        ],
        'slots': slots,
        'saved': saved,
    }


def main() -> int:
    args = [a for a in sys.argv[1:]]
    out_dir = None
    if '--out' in args:
        idx = args.index('--out')
        out_dir = args[idx + 1]
        del args[idx:idx + 2]
    summary = '--summary' in args
    if summary:
        args.remove('--summary')
    if not args:
        print(__doc__)
        return 2
    config_name = 'oas3'
    frames = [a for a in args if not a.startswith('--')]

    task, device = build_task(config_name)
    print(f'device serial={device.serial} '
          f'screenshot_method={device.config.script.device.screenshot_method}')

    totals = {'EMPTY': 0, 'card_but_unknown': 0, 'card+recognized': 0}
    unknown_frames = []

    for name in frames:
        path = Path(name)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_dir():
            paths = sorted(path.glob('*.png'))
        elif path.exists():
            paths = [path]
        else:
            print(f'!! 不存在: {path}')
            continue
        for frame_path in paths:
            image = load_frame(frame_path)
            result = scan(task, image, out_dir=out_dir, tag=frame_path.stem[:40])
            states = []
            for slot in result['slots']:
                state = (
                    'card+recognized' if slot['recognized']
                    else ('card_but_unknown' if slot['has_card'] else 'EMPTY')
                )
                states.append(state)
                totals[state] += 1
            unknown_slots = [
                slot['slot'] for slot, state in zip(result['slots'], states)
                if state == 'card_but_unknown'
            ]
            if unknown_slots:
                unknown_frames.append((frame_path.name, unknown_slots))
            if summary:
                print(f"== {frame_path.name} detected={result['detected_cards']} "
                      f"states={states}")
                continue
            print(f'== {frame_path.name}  shape={image.shape}  '
                  f'detected_cards={result["detected_cards"]}')
            for slot in result['slots']:
                state = (
                    'card+recognized' if slot['recognized']
                    else ('card_but_unknown' if slot['has_card'] else 'EMPTY')
                )
                print(
                    f"   slot {slot['slot']} roi={slot['roi']} -> "
                    f"{slot['recognized'] or ('未识别' if slot['has_card'] else '空')} "
                    f"score={slot['score']} cards_in_slot={slot['cards_in_slot']} "
                    f"has_card={slot['has_card']} state={state}"
                )
            for card in result['cards']:
                print(
                    f"      card {card['name'] or '<unknown>'} "
                    f"score={card['score']} price={card['price']} "
                    f"bonds={card['bonds']} raw_text={card['raw_text']} "
                    f"pos={card['position']}"
                )
            if out_dir:
                print(f'   saved {len(result["saved"])} crops -> {out_dir}')
            print()

    if summary or len(frames) > 1:
        total = sum(totals.values()) or 1
        print('== 汇总（按格子统计）')
        print(f"   EMPTY(空格子)      {totals['EMPTY']:5d}  "
              f"({totals['EMPTY'] / total * 100:.1f}%)")
        print(f"   card+recognized    {totals['card+recognized']:5d}  "
              f"({totals['card+recognized'] / total * 100:.1f}%)")
        print(f"   card_but_unknown   {totals['card_but_unknown']:5d}  "
              f"({totals['card_but_unknown'] / total * 100:.1f}%)")
        real_fail = totals['card_but_unknown']
        card_total = real_fail + totals['card+recognized']
        if card_total:
            print(f"   真实识别失败率（有卡但认不出）: "
                  f"{real_fail / card_total * 100:.2f}%")
        for name, slots in unknown_frames:
            print(f'   real-unknown: {name} slots={slots}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
