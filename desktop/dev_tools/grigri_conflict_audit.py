# This Python file uses the following encoding: utf-8

"""符咒 OCR/图标冲突的复盘工具。

背景：`_recognize_grigri_options()` 遇到「文字识别的名字 ≠ 图标模板命中的名字」时
只打一条 warning，然后无条件采用 OCR 结果。到底是文字准还是图标准，日志里没有证据。

本工具两种模式：

1. 默认：把已落盘的冲突样本汇总成表（raw_text / ocr_name / text_score /
   icon_name / icon_score），看「原始 OCR 文本」是否本来就支持图标结果。
2. `--replay`：对**整帧样本**（`full_frame: true`）重新跑一遍
   图标模板匹配 + 名称 OCR，输出每个候选的分数，用于验证判定规则。

用法：
    .\\toolkit\\python.exe dev_tools\\grigri_conflict_audit.py
    .\\toolkit\\python.exe dev_tools\\grigri_conflict_audit.py --replay
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

SAMPLE_DIR = ROOT / 'log' / 'chess_ref' / 'grigri_conflict'


def load_samples():
    samples = []
    for path in sorted(SAMPLE_DIR.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        data['_json'] = str(path)
        data['_png'] = str(path.with_suffix('.png'))
        samples.append(data)
    return samples


def print_table(samples):
    print(f'samples: {len(samples)}')
    header = (
        f"{'quality':<7} {'opt':>3} {'raw_text':<16} {'ocr_name':<14} "
        f"{'text':>5} {'icon_name':<16} {'icon':>5}  推断"
    )
    print(header)
    icon_wins = 0
    ocr_wins = 0
    ambiguous = 0
    for data in samples:
        verdict = infer_verdict(data)
        if verdict == 'icon':
            icon_wins += 1
        elif verdict == 'ocr':
            ocr_wins += 1
        else:
            ambiguous += 1
        print(
            f"{str(data.get('quality')):<7} {data.get('option'):>3} "
            f"{str(data.get('raw_text')):<16} {str(data.get('ocr_name')):<14} "
            f"{data.get('text_score'):>5} {str(data.get('icon_name')):<16} "
            f"{data.get('icon_score'):>5}  {verdict}"
        )
    print()
    print(f'  推断：图标更可信 {icon_wins} / 文字更可信 {ocr_wins} / 无法判定 {ambiguous}')
    return icon_wins, ocr_wins, ambiguous


def _norm(text):
    text = ''.join(str(text or '').split())
    for ch in '·・.,，。':
        text = text.replace(ch, '')
    return text


def infer_verdict(data):
    """仅凭落盘记录推断哪一侧更可信（无图时的保守规则）。

    - 图标分数明显高、且原始 OCR 文本里含有图标名字的核心字 → 图标更可信；
    - OCR 文本与映射名完全一致且分数 ≥0.95 → 文字更可信；
    - 其余视为无法判定（保持现有「OCR 优先」）。
    """
    raw = _norm(data.get('raw_text'))
    ocr_name = _norm(data.get('ocr_name'))
    icon_name = _norm(data.get('icon_name'))
    icon_score = float(data.get('icon_score') or 0.0)
    text_score = float(data.get('text_score') or 0.0)

    if text_score >= 0.95 and raw and ocr_name and raw == ocr_name:
        return 'ocr'
    if icon_name and icon_name != ocr_name:
        # 图标名去掉品质后缀后的主名，是否出现在原始 OCR 文本里
        base = icon_name.split('·')[0]
        if base and base in raw and icon_score >= 0.80:
            return 'icon'
        # OCR 名的主名在原始文本里的覆盖度
        ocr_base = ocr_name.split('·')[0]
        if ocr_base and ocr_base in raw and text_score >= 0.90:
            return 'ocr'
    return 'ambiguous'


def replay(samples):
    """对已落盘样本重跑图标匹配（支持整帧或面板裁剪），输出 top-3 与 margin。"""
    from module.config.config import Config
    from module.device.device import Device
    from tasks.Chess.script_task import ScriptTask
    from tasks.Chess.strategy import grigri as G

    config = Config('oas3')
    device = Device(config)
    task = ScriptTask(config=config, device=device)
    rules = (
        task.C_GRIGRI_OPTION_1,
        task.C_GRIGRI_OPTION_2,
        task.C_GRIGRI_OPTION_3,
    )
    checked = 0
    decisive = 0
    ambiguous = 0
    for data in samples:
        png = Path(data.get('_png', ''))
        if not png.exists():
            continue
        image = cv2.imread(str(png))
        if image is None:
            continue
        full_frame = image.shape[0] == 720 and image.shape[1] == 1280
        if full_frame:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            panel_x, panel_y = 0, 0
        else:
            # 面板裁剪：JSON 里记录了它在大图里的绝对位置
            panel = data.get('panel_roi') or [0, 0, 0, 0]
            if image.shape[0] != int(panel[3]) or image.shape[1] != int(panel[2]):
                print(
                    f"   skip {png.name}: 面板尺寸与 panel_roi 不符 "
                    f"{image.shape[:2]} vs {panel}"
                )
                continue
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            panel_x, panel_y = int(panel[0]), int(panel[1])

        option = int(data.get('option') or 1)
        quality = data.get('quality') or None
        x, y, w, h = rules[option - 1].roi_front
        left = max(0, x - 24 - panel_x)
        top = max(0, y - 8 - panel_y)
        right = min(rgb.shape[1], x + w + 24 - panel_x)
        bottom = min(rgb.shape[0], y + h - panel_y)
        if right <= left or bottom <= top:
            print(f'   skip {png.name}: 选项区域越界')
            continue
        source = rgb[top:bottom, left:right]

        scored = []
        for name in G.grigri_names_for_quality(quality):
            template = task.grigri_icon_templates.get(name)
            if template is None:
                continue
            if template.shape[0] > source.shape[0] or template.shape[1] > source.shape[1]:
                continue
            result = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, _ = cv2.minMaxLoc(result)
            scored.append((float(score), name))
        scored.sort(reverse=True)
        checked += 1
        top3 = ', '.join(f'{name}:{score:.3f}' for score, name in scored[:3])
        margin = (
            scored[0][0] - scored[1][0] if len(scored) > 1 else 1.0
        )
        verdict = 'decisive' if margin >= 0.03 else 'ambiguous'
        if verdict == 'decisive':
            decisive += 1
        else:
            ambiguous += 1
        print(
            f"   {png.name}: opt={option} quality={quality} "
            f"ocr={data.get('ocr_name')}({data.get('text_score')}) "
            f"recorded_icon={data.get('icon_name')}({data.get('icon_score')}) "
            f"margin={margin:.3f} -> {verdict}\n      top3: {top3}"
        )
    print()
    print(
        f'   replay 样本 {checked}：图标判定清晰 {decisive} / 前二接近 {ambiguous}'
    )


def main() -> int:
    samples = load_samples()
    if not samples:
        print(f'没有样本：{SAMPLE_DIR}')
        return 1
    print_table(samples)
    if '--replay' in sys.argv:
        print()
        print('== replay（需要整帧样本）')
        replay(samples)
    return 0


if __name__ == '__main__':
    sys.exit(main())
