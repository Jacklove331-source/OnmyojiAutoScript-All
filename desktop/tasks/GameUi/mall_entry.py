"""Courtyard mall entry when the decorative label differs from its template."""
from module.atom.ocr import RuleOcr
from tasks.GameUi.assets import GameUiAssets


MALL_ENTRY_TEXT = RuleOcr(
    name='MAIN_MALL_ENTRY_TEXT', mode='Full', method='Default',
    roi=(605, 615, 160, 90), area=(605, 615, 160, 90), keyword='商店',
)
MALL_ENTRY_TEXT.score = 0.85


def enter_mall_from_main(task) -> bool:
    # Import at call time: default_pages registers this action while loading.
    from tasks.GameUi.default_pages import page_main

    if not task.confirm_page(page_main, skip_first_screenshot=False):
        return False
    image = task.device.image
    if image is None or image.shape[:2] != (720, 1280):
        return False
    if task.appear_then_click(GameUiAssets.I_MAIN_GOTO_MALL, interval=0.8):
        return True
    # Exact, high-confidence text inside the mall slot; no fixed-position click.
    return task.ocr_appear_click(MALL_ENTRY_TEXT, interval=0.8, exact=True)
