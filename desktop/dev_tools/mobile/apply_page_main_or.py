"""扩展手机端覆盖：page_main 用「多特征 OR」判定（探索 + 町中）。

原因：单一「探索」模板在庭院某些状态（弹窗/切换）下会失配，导致 goto_page 立刻放弃。
Page 支持传入可迭代的多个识别条件（任一命中即算该页）。
"""

import pathlib

import cv2

REPO = pathlib.Path(r'D:\3D\YYS2\OnmyojiAutoScript-easy-install-1')
APK_PY = pathlib.Path(r'D:\3D\YYS2\oasx_mobile\android\app\src\main\python')
frame = REPO / 'log' / 'mobile' / 'wq_page.png'

img = cv2.imread(str(frame))
print('frame:', img.shape)

# 1) 裁「町中」文字（当前庭院中央偏上）
crop = img[320:400, 690:800]
tpl = APK_PY / 'tasks' / 'GameUi' / 'page' / 'page_main_check_town.png'
cv2.imwrite(str(tpl), crop)
res = cv2.matchTemplate(img, crop, cv2.TM_CCOEFF_NORMED)
_, score, _, loc = cv2.minMaxLoc(res)
print(f'town template {crop.shape} self-score={score:.4f} loc={loc}')

# 2) 在 assets.py 里登记这个检查项（TAB 缩进）
assets = APK_PY / 'tasks' / 'GameUi' / 'assets.py'
text = assets.read_text(encoding='utf-8')
if 'I_CHECK_MAIN_TOWN' not in text:
    anchor = '\tI_CHECK_MAIN = RuleImage('
    idx = text.find(anchor)
    assert idx >= 0, 'I_CHECK_MAIN anchor'
    line = ('\t# [mobile-override] 庭院「町中」文字作为第二个主界面特征\n'
            '\tI_CHECK_MAIN_TOWN = RuleImage(roi_front=(690,320,110,80), roi_back=(650,290,200,140), '
            'threshold=0.75, method="Template matching", '
            'file="./tasks/GameUi/page/page_main_check_town.png")\n')
    text = text[:idx] + line + text[idx:]
    assets.write_text(text, encoding='utf-8', newline='')
    print('assets.py: I_CHECK_MAIN_TOWN 已加')

# 3) default_pages.py：page_main 改成多条件 OR
pages = APK_PY / 'tasks' / 'GameUi' / 'default_pages.py'
ptext = pages.read_text(encoding='utf-8')
old = 'page_main = Page(GameUiAssets.I_CHECK_MAIN, category="global")'
new = ('# [mobile-override] 庭院特征不稳定 → 多条件 OR（探索 / 町中 任一命中即算主界面）\n'
       'page_main = Page([GameUiAssets.I_CHECK_MAIN, GameUiAssets.I_CHECK_MAIN_TOWN], category="global")')
if new in ptext:
    print('default_pages: 已是多条件版本')
elif old in ptext:
    pages.write_text(ptext.replace(old, new, 1), encoding='utf-8', newline='')
    print('default_pages: page_main 已改为多条件 OR')
else:
    print('WARNING: 未找到 page_main 定义行')

import py_compile
for path in (assets, pages):
    py_compile.compile(str(path), doraise=True)
print('compile OK')
