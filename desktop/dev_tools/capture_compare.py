# This Python file uses the following encoding: utf-8

"""截图方式对照：同一屏幕下 nemu_ipc vs ADB 的质量与识别一致性。

回答的问题（用户要求、仅凭名次无法回答）：
- 两种方式在同一画面上的耗时、清晰度、颜色是否一致
- 同一批模板（商店/刷新/市场等标志）的匹配分数是否一致，
  会不会因为截图方式差异把「商店已关」误判成「商店还开着」
- OCR（回合/模式/等级/金币）读取结果是否一致

用法：
    .\\toolkit\\python.exe dev_tools\\capture_compare.py oas3 [--repeat 5] [--save]
    .\\toolkit\\python.exe dev_tools\\capture_compare.py --file log\\error\\<ts>\\xxx.png

说明：只读截图，不点击、不改配置；`--save` 会把两种方式的帧存到
`log/chess_ref/capture_compare/` 便于人工核对。
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

OUT_DIR = ROOT / 'log' / 'chess_ref' / 'capture_compare'

# 与商店状态机相关的模板：这些误判会直接卡住上卡/清手牌
MARKERS = (
    'I_REFRESH',
    'I_REFRESH_NOT_GOLD',
    'I_MARKET',
    'I_CHECK_MARKET',
    'I_OPEN_LINEUP',
    'I_QUESTION_CHECK',
    'I_SELECT_GRIGRI',
    'I_CLOSE_RED',
)


def sharpness(image):
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def color_mean(image):
    return [round(float(value), 2) for value in np.mean(image.reshape(-1, 3), axis=0)]


def marker_scores(task, image):
    scores = {}
    for name in MARKERS:
        rule = getattr(task, name, None)
        if rule is None:
            scores[name] = None
            continue
        try:
            score = float(rule.match(image, threshold=0.0))
        except Exception as error:  # noqa: BLE001
            scores[name] = f'error: {error!r}'
            continue
        scores[name] = round(score, 4)
    return scores


def marker_detail(task, image):
    """每个标志：分数 + 阈值 + 是否会被判为出现。"""
    detail = {}
    for name in MARKERS:
        rule = getattr(task, name, None)
        if rule is None:
            continue
        threshold = getattr(rule, 'threshold', None)
        try:
            result = rule.match(image)
            score = getattr(rule, 'last_score', None)
            if score is None:
                score = float(rule.match(image, threshold=0.0))
        except Exception as error:  # noqa: BLE001
            detail[name] = {'error': repr(error)}
            continue
        detail[name] = {
            'threshold': threshold,
            'score': round(float(score), 4) if isinstance(score, (int, float)) else None,
            'appear': bool(result),
        }
    return detail


def ocr_snapshot(task, image):
    values = {}
    for name, attr in (
        ('round', 'O_ROUND'),
        ('round_2', 'O_ROUND_2'),
        ('mode', 'O_CHESS_MODE'),
        ('mode_2', 'O_CHESS_MODE_2'),
        ('level', 'O_LEVEL'),
        ('gold', 'O_GOLD'),
        ('now_time', 'O_NOW_TIME'),
    ):
        rule = getattr(task, attr, None)
        if rule is None:
            continue
        try:
            values[name] = rule.ocr(image)
        except Exception as error:  # noqa: BLE001
            values[name] = f'error: {error!r}'
    return values


def capture(device, method, repeat, save_tag=None):
    """用指定方式连续抓 repeat 帧，返回统计。"""
    original_flag = getattr(device, '_screenshot_force_adb', False)
    device._screenshot_force_adb = (method == 'adb')
    timings = []
    last_image = None
    try:
        for index in range(repeat):
            started = time.monotonic()
            image = device.screenshot()
            elapsed = (time.monotonic() - started) * 1000
            timings.append(elapsed)
            last_image = np.array(image, copy=True)
            if save_tag:
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                path = OUT_DIR / f'{save_tag}_{method}_{index}.png'
                cv2.imwrite(str(path), cv2.cvtColor(last_image, cv2.COLOR_RGB2BGR))
    finally:
        device._screenshot_force_adb = original_flag
    return {
        'method': method,
        'frames': len(timings),
        'ms_avg': round(sum(timings) / len(timings), 1) if timings else None,
        'ms_min': round(min(timings), 1) if timings else None,
        'ms_max': round(max(timings), 1) if timings else None,
        'image': last_image,
    }


def build_task(config_name):
    """只为了拿模板/OCR 规则，不跑任务逻辑。"""
    from module.config.config import Config
    from module.device.device import Device
    from tasks.Chess.script_task import ScriptTask

    config = Config(config_name)
    device = Device(config)
    task = ScriptTask(config=config, device=device)
    return task, device


def main() -> int:
    args = sys.argv[1:]
    config_name = args[0] if args and not args[0].startswith('--') else 'oas3'
    repeat = 5
    if '--repeat' in args:
        repeat = int(args[args.index('--repeat') + 1])
    save = '--save' in args

    if '--file' in args:
        path = Path(args[args.index('--file') + 1])
        if not path.is_absolute():
            path = ROOT / path
        image = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
        print(f'file: {path}  shape={image.shape}')
        print(f'  sharpness={sharpness(image):.1f} mean_rgb={color_mean(image)}')
        print('  (单帧样本：无法比较两种截图方式，只做质量基线)')
        return 0

    task, device = build_task(config_name)
    mode = device.config.script.device.screenshot_method
    print(f'config={config_name} configured_method={mode} repeat={repeat}')
    print(f'before: force_adb={getattr(device, "_screenshot_force_adb", None)}')

    nemu = capture(device, 'nemu', repeat, 'cmp' if save else None)
    adb = capture(device, 'adb', repeat, 'cmp' if save else None)

    print()
    print('== timing (ms)')
    for result in (nemu, adb):
        print(f"  {result['method']:<5} frames={result['frames']} "
              f"avg={result['ms_avg']} min={result['ms_min']} max={result['ms_max']}")

    ne, ad = nemu['image'], adb['image']
    if ne is not None and ad is not None and ne.shape == ad.shape:
        diff = np.abs(ne.astype('int16') - ad.astype('int16'))
        print()
        print('== image quality')
        print(f"  nemu sharpness={sharpness(ne):.1f} mean_rgb={color_mean(ne)}")
        print(f"  adb  sharpness={sharpness(ad):.1f} mean_rgb={color_mean(ad)}")
        print(f"  mean_abs_diff={float(diff.mean()):.3f} max={int(diff.max())} "
              f"pct_pixels_diff>8={float((diff.max(axis=2) > 8).mean()) * 100:.2f}%")
        cv2.imwrite(
            str((OUT_DIR / 'diff_nemu_vs_adb.png')),
            cv2.cvtColor(
                np.clip(diff, 0, 255).astype('uint8'), cv2.COLOR_RGB2BGR
            ),
        )

    print()
    print('== marker match (同一屏幕，阈值见括号)')
    nemu_detail = marker_detail(task, ne)
    adb_detail = marker_detail(task, ad)
    for name in MARKERS:
        if name not in nemu_detail and name not in adb_detail:
            continue
        left = nemu_detail.get(name, {})
        right = adb_detail.get(name, {})
        print(
            f"  {name:<18} nemu score={left.get('score')} appear={left.get('appear')} | "
            f"adb score={right.get('score')} appear={right.get('appear')} | "
            f"thr={left.get('threshold')}"
        )

    print()
    print('== OCR（同一屏幕）')
    nemu_ocr = ocr_snapshot(task, ne)
    adb_ocr = ocr_snapshot(task, ad)
    for key in nemu_ocr:
        same = 'same' if nemu_ocr.get(key) == adb_ocr.get(key) else 'DIFF'
        print(f"  {key:<10} nemu={nemu_ocr.get(key)!r:<18} adb={adb_ocr.get(key)!r:<18} {same}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
