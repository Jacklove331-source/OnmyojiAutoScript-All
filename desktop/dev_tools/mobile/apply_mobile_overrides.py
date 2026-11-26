"""重新应用「手机端对 OAS 文件的覆盖」（每次同步 module/tasks 到 APK 后跑一次）。

目前只有一处覆盖：
    tasks/GameUi/assets.py 的 I_CHECK_MAIN → 「探索」悬浮按钮模板（page_main_check_mobile.png）
原因：当前游戏庭院**没有底部导航栏**，OAS 原来的检查项（底部式神录图标）永远失配，
导致 `Page detect miss[goto_page]`，任务进不了页面图。

用法：
    python dev_tools/mobile/apply_mobile_overrides.py [--frame <庭院截图>]
不给 --frame 时用 log/mobile/wq_page.png（需是 1280x720 的庭院截图）。
"""

from __future__ import annotations

import argparse
import pathlib

import cv2

REPO = pathlib.Path(r'D:\3D\YYS2\OnmyojiAutoScript-easy-install-1')
APK_PY = pathlib.Path(r'D:\3D\YYS2\oasx_mobile\android\app\src\main\python')

CROP = (330, 25, 100, 105)          # 探索按钮（x, y, w, h）
ROI_FRONT = (330, 25, 100, 105)
ROI_BACK = (300, 5, 180, 150)
THRESHOLD = 0.75
TEMPLATE_NAME = 'page_main_check_mobile.png'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--frame', default=str(REPO / 'log' / 'mobile' / 'wq_page.png'))
    args = parser.parse_args()

    frame_path = pathlib.Path(args.frame)
    if not frame_path.exists():
        print(f'FAIL: frame not found: {frame_path}')
        return 1
    img = cv2.imread(str(frame_path))
    if img is None:
        print(f'FAIL: cannot read frame: {frame_path}')
        return 1
    x, y, w, h = CROP
    crop = img[y:y + h, x:x + w]
    template_path = APK_PY / 'tasks' / 'GameUi' / 'page' / TEMPLATE_NAME
    template_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(template_path), crop)
    res = cv2.matchTemplate(img, crop, cv2.TM_CCOEFF_NORMED)
    _, score, _, _ = cv2.minMaxLoc(res)
    print(f'template: {template_path.name} {crop.shape} self-score={score:.4f}')

    assets_py = APK_PY / 'tasks' / 'GameUi' / 'assets.py'
    lines = assets_py.read_text(encoding='utf-8').splitlines(keepends=True)
    patched = 0
    for index, line in enumerate(lines):
        stripped = line.lstrip('\t ')
        if stripped.startswith('I_CHECK_MAIN = RuleImage(') and 'mobile' not in stripped:
            indent = line[:len(line) - len(stripped)]
            lines[index] = (
                f"{indent}# [mobile-override] 庭院无底部导航，改用「探索」悬浮按钮作主界面特征\n"
                f"{indent}I_CHECK_MAIN = RuleImage(roi_front={ROI_FRONT}, roi_back={ROI_BACK}, "
                f'threshold={THRESHOLD}, method="Template matching", '
                f'file="./tasks/GameUi/page/{TEMPLATE_NAME}")\n'
            )
            patched += 1
            # 删掉可能残留在上一行的 mobile-override 注释，避免重复
            if index > 0 and '[mobile-override]' in lines[index - 1]:
                lines[index - 1] = ''
    assets_py.write_text(''.join(lines), encoding='utf-8', newline='')
    print(f'patched I_CHECK_MAIN lines: {patched}')

    import py_compile
    py_compile.compile(str(assets_py), doraise=True)
    print('assets.py compile OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
