"""Recognize the gift room even when an event replaces its default tab."""
from module.atom.ocr import RuleOcr
from tasks.DailyTrifles.assets import DailyTriflesAssets


class _GiftTabOcr(RuleOcr):
    def detect_and_ocr(self, image, logDisplay=True):
        # Padding this tall sidebar into a black square can merge its vertical
        # tabs or drop the bottom tab. Preserve the captured sidebar geometry.
        x, y, width, height = self.roi
        rows = self.model.detect_and_ocr(image[y:y + height, x:x + width])
        return [row for row in rows if row.score >= self.score]


DAILY_GIFT_TAB = _GiftTabOcr(
    name='STORE_DAILY_GIFT_TAB', mode='Full', method='Default',
    roi=(1160, 75, 105, 540), area=(1160, 75, 105, 540), keyword='日常',
)
DAILY_GIFT_TAB.score = 0.85


def is_current_gift_room(task) -> bool:
    return (task.appear(DailyTriflesAssets.I_ROOM_GIFT)
            and task.ocr_appear(DAILY_GIFT_TAB, exact=True))


def select_daily_gifts(task) -> bool:
    if is_current_gift_room(task):
        return task.ocr_appear_click(DAILY_GIFT_TAB, interval=0.8, exact=True)
    return task.appear_then_click(DailyTriflesAssets.I_GIFT_RECOMMEND, interval=1)
