"""Recognize the single-stroke Han suffix on compact role-card names."""
from __future__ import annotations

import cv2
import numpy as np

_REFERENCE_WIDTH, _REFERENCE_HEIGHT = 1600, 900
_CARD_COLUMNS = ((548, 885), (965, 1299))
_FIRST_NAME_ROW = 277
_CARD_ROW_PITCH = 155
_NAME_STRIP_HEIGHT = 29


def _has_level_badge(frame, x, y):
    # A nickname begins to the right of its brown level diamond.
    sample = frame[y - 5:y + 8, x - 28:x - 18].astype(np.int16)
    red, green, blue = np.moveaxis(sample, 2, 0)
    return np.mean((red >= green + 7) & (green >= blue + 5)) >= 0.72


def _mark_descends_right(mask, x, y, width, height):
    rows, columns = np.nonzero(mask[y:y + height, x:x + width])
    lower = rows >= height / 2
    return columns[lower].mean() - columns[~lower].mean() >= width / 5


def _compact_name(frame, left, top, right):
    strip = frame[top:top + _NAME_STRIP_HEIGHT, left:right]
    gray = cv2.cvtColor(strip, cv2.COLOR_RGB2GRAY)
    mask = ((gray >= 172) & ((strip.max(axis=2) - strip.min(axis=2)) <= 45)).astype(np.uint8)
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    components = sorted(tuple(int(value) for value in row) for row in stats[1:] if row[4] >= 5)
    if len(components) < 2:
        return None

    # The complete short nickname ends in these two glyphs. The level
    # digits and badge may create separate components to their left.
    x, y, width, height, _ = components[-2]
    mark_x, mark_y, mark_width, mark_height, _ = components[-1]
    if not (5 <= width <= 14 and 9 <= height <= 17
            and 4 <= mark_width <= 10 and 4 <= mark_height <= 9
            and 6 <= mark_x - x - width <= 15
            and -4 <= mark_y - y <= 1 and mark_y + mark_height <= y + height - 4):
        return None
    if not _has_level_badge(frame, left + x, top + y):
        return None
    if not _mark_descends_right(mask, mark_x, mark_y, mark_width, mark_height):
        return None

    # Read the whole nickname again. Padding keeps the tiny stroke visible
    # to OCR, which can spell the upper dot as an ideographic comma.
    left_edge = max(0, x - 5)
    right_edge = mark_x + mark_width + 8
    original = strip[2:27, left_edge:right_edge]
    gray_label = gray[2:27, left_edge:right_edge]
    _, letters = cv2.threshold(gray_label, 170, 255, cv2.THRESH_BINARY)
    bordered = cv2.copyMakeBorder(letters, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=0)
    variants = tuple(cv2.resize(patch, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
                     for patch in (original, bordered))
    # ML Kit can drop the tight upscaled name crop entirely, and the 5px left
    # margin can include the level badge, which it then reads as a leading ")".
    # The trailing stroke is already proven by the geometry above, so offer the
    # leading glyph alone on a black border as a fallback; the border is what
    # makes the detector accept a crop this small.
    letter = strip[max(0, y - 2):min(strip.shape[0], y + height + 3),
                   max(0, x - 3):min(strip.shape[1], x + width + 4)]
    letter_bordered = cv2.copyMakeBorder(letter, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=0)
    letter_upscaled = cv2.resize(letter, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    variants += (letter_bordered, letter_upscaled)
    center_x = left + (x + mark_x + mark_width) / 2
    center_y = top + y + height / 2
    return center_x, center_y, variants


def compact_dot_candidates(frame):
    """Return role-name crops with a full-width trailing stroke above the baseline."""
    height, width = frame.shape[:2]
    view = frame
    if (width, height) != (_REFERENCE_WIDTH, _REFERENCE_HEIGHT):
        view = cv2.resize(frame, (_REFERENCE_WIDTH, _REFERENCE_HEIGHT), interpolation=cv2.INTER_LINEAR)
    for top in range(_FIRST_NAME_ROW, 750, _CARD_ROW_PITCH):
        for left, right in _CARD_COLUMNS:
            candidate = _compact_name(view, left, top, right)
            if candidate is not None:
                x, y, crop = candidate
                yield (round(x * width / _REFERENCE_WIDTH),
                       round(y * height / _REFERENCE_HEIGHT)), crop
