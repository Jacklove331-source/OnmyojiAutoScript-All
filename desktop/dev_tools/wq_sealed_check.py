# This Python file uses the following encoding: utf-8

"""离线复跑「全已封印」判据：把一张悬赏封印面板帧喂给 compute_sealed_verdict()。

为什么需要：WQ 面板四张卡全部封印后卡面没有 `a/b` 进度，wq2_cards() 一张卡都读不到，
旧逻辑判「判据可疑」→ success=False 延到次日。新判据只看 OCR 文本，可以离线对帧复核，
不用等下一次实跑。

用法：
    .\\toolkit\\python.exe dev_tools\\wq_sealed_check.py <frame.png> [frame2.png ...]

每个帧打印：cards 数量、title/sealed/claimable/progress 计数、每处命中（文本 + 坐标）、
以及生产函数 wq2_panel_all_sealed() 的结论。末行 SUMMARY 可被脚本断言。
本脚本**不复制**判据逻辑：计数与结论都来自 ui_wq_v2.compute_sealed_verdict()。
"""

import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.logger import logger  # noqa: E402
from tasks.WantedQuests.ui_wq_v2 import (  # noqa: E402
    O_WQ_PANEL_TITLE,
    WantedQuestsUiV2,
    compute_sealed_verdict,
)


class _FrameDevice:
    def __init__(self, image):
        self.image = image


class _FrameTask(WantedQuestsUiV2):
    """只用 device.image 的离线替身：不发 adb、不点屏幕。

    父类没有 __init__，这里显式给出 device；config/server 故意不设（父类也没有），
    将来判据若真的用到它们，会立刻抛 AttributeError 而不是被当成「未配置」静默走偏。
    """

    def __init__(self, image):
        self.device = _FrameDevice(image)


_OCR_ROI_X = O_WQ_PANEL_TITLE.roi[0]
_OCR_ROI_Y = O_WQ_PANEL_TITLE.roi[1]
_COLUMNS = (240, 510, 780, 1050)


class _FakeItem:
    """compute_sealed_verdict() 的自检用 OCR item（字段与 RuleOcr 结果一致）。"""

    def __init__(self, text, cx, cy):
        # box 是 ROI 局部坐标：左上/右下角，按中心点反推
        local_x, local_y = cx - _OCR_ROI_X, cy - _OCR_ROI_Y
        self.ocr_text = text
        self.box = ((local_x - 20, local_y - 10), (local_x + 20, local_y - 10),
                    (local_x + 20, local_y + 10), (local_x - 20, local_y + 10))


def _selftest() -> int:
    """负样本用例：结构化 items 直接喂判据，覆盖「还有进度 / 可领取 / 封印标记不足」。"""
    title = _FakeItem('悬赏封印', 320, 88)
    cases = [
        ('全封印三处标记 -> True',
         [title, _FakeItem('已封印', 242, 353), _FakeItem('封印', 510, 361),
          _FakeItem('封印', 1045, 371), _FakeItem('碎片', 250, 560)], True),
        ('有一张卡还剩 9/12 -> False',
         [title, _FakeItem('已封印', 242, 353), _FakeItem('封印', 510, 361),
          _FakeItem('9/12', 780, 481)], False),
        ('OCR 把 1/6 读成 176（同 PROGRESS_RE 的 /7 规则）-> False',
         [title, _FakeItem('已封印', 242, 353), _FakeItem('封印', 510, 361),
          _FakeItem('176', 240, 481)], False),
        ('存在「领取」-> False',
         [title, _FakeItem('已封印', 242, 353), _FakeItem('封印', 510, 361),
          _FakeItem('领取', 782, 405)], False),
        ('封印标记只有 1 处（OCR 漏字）-> False',
         [title, _FakeItem('已封印', 242, 353), _FakeItem('碎片', 510, 561)], False),
        ('没有标题（探索页误命中）-> False',
         [_FakeItem('已封印', 242, 353), _FakeItem('封印', 510, 361)], False),
        ('奖励数字 30/30000 不算进度 -> True',
         [title, _FakeItem('已封印', 242, 353), _FakeItem('封印', 510, 361),
          _FakeItem('30', 250, 560), _FakeItem('30000', 520, 560)], True),
    ]
    failed = 0
    for name, items, expected in cases:
        verdict = compute_sealed_verdict(items, _COLUMNS, _OCR_ROI_X, _OCR_ROI_Y)
        ok = verdict['completed'] is expected
        failed += 0 if ok else 1
        print(f"   [{'PASS' if ok else 'FAIL'}] {name} "
              f"(completed={verdict['completed']} sealed={verdict['sealed']} "
              f"claimable={verdict['claimable']} progress={verdict['progress']})")
    print(f'SELFTEST {"PASS" if failed == 0 else "FAIL"} ({len(cases) - failed}/{len(cases)})')
    return 0 if failed == 0 else 1


def probe(path: Path) -> dict:
    image = cv2.imread(str(path))
    if image is None:
        raise SystemExit(f'cannot read frame: {path}')
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    task = _FrameTask(image)

    cards = task.wq2_cards()
    items = O_WQ_PANEL_TITLE.detect_and_ocr(image) or []
    verdict = compute_sealed_verdict(items, task._CARD_COLUMNS,
                                     O_WQ_PANEL_TITLE.roi[0], O_WQ_PANEL_TITLE.roi[1])
    # 生产入口走同一条判据逻辑，这里只确认它没被别的东西改跑偏
    prod_verdict = task.wq2_panel_all_sealed()

    print(f'== {path.name}')
    print(f"   title={verdict['title']} sealed={verdict['sealed']} "
          f"claimable={verdict['claimable']} progress={verdict['progress']} "
          f"cards={len(cards)}")
    print(f"   sealed_hits={verdict['sealed_hits']}")
    print(f"   claim_hits={verdict['claim_hits']}")
    print(f"   progress_hits={verdict['progress_hits']}")
    for card in cards:
        print(f"   card[{card['index']}] [{card['text']}] "
              f"{card['current']}/{card['total']} @ {card['center']}")
    print(f"   compute_sealed_verdict.completed={verdict['completed']}")
    print(f'   wq2_panel_all_sealed()={prod_verdict}')
    return {'path': str(path), 'verdict': verdict, 'cards': len(cards),
            'prod': prod_verdict}


def main() -> int:
    args = [arg for arg in sys.argv[1:] if not arg.startswith('-')]
    if '--selftest' in sys.argv:
        return _selftest()
    if not args:
        print(__doc__)
        return 2
    results = []
    for arg in args:
        path = Path(arg)
        if not path.is_absolute():
            path = ROOT / path
        results.append(probe(path))
    print('SUMMARY ' + ' '.join(
        f"{Path(r['path']).name}:completed={r['verdict']['completed']}:"
        f"prod={r['prod']}:sealed={r['verdict']['sealed']}:"
        f"claimable={r['verdict']['claimable']}:progress={r['verdict']['progress']}:"
        f"cards={r['cards']}"
        for r in results))
    logger.info('wq sealed check done')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
