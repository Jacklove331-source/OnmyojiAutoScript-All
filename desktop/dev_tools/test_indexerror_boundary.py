# This Python file uses the following encoding: utf-8
"""回归测试：OCR 文本解析遇到空串/纯空白/无数字文本时不得抛 IndexError。"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from module.atom.ocr import RuleOcr
from module.ocr.base_ocr import OcrMode
from module.ocr.sub_ocr import Quantity

TOTAL = 0
PASS = 0
FAIL = 0


def check(name, fn):
    global TOTAL, PASS, FAIL
    TOTAL += 1
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - 回归脚本需要收集所有失败
        FAIL += 1
        print(f'FAIL {name}: {type(exc).__name__}: {exc}')
    else:
        PASS += 1
        print(f'PASS {name}')


def ocr_stub(mode):
    obj = object.__new__(RuleOcr)
    obj.mode = mode
    obj.name = 'indexerror_regression'
    return obj


def quantity_stub():
    obj = object.__new__(Quantity)
    obj.name = 'indexerror_regression'
    return obj


def test_quantity_empty():
    assert RuleOcr.after_process(ocr_stub(OcrMode.QUANTITY), '') == 0


def test_quantity_blank():
    assert RuleOcr.after_process(ocr_stub(OcrMode.QUANTITY), '   ') == 0


def test_quantity_no_digit():
    assert RuleOcr.after_process(ocr_stub(OcrMode.QUANTITY), '+') == 0


def test_quantity_normal():
    assert RuleOcr.after_process(ocr_stub(OcrMode.QUANTITY), '123') == 123


def test_sub_ocr_empty():
    assert Quantity.after_process(quantity_stub(), '') == 0


check('quantity_empty', test_quantity_empty)
check('quantity_blank', test_quantity_blank)
check('quantity_no_digit', test_quantity_no_digit)
check('quantity_normal', test_quantity_normal)
check('sub_ocr_empty', test_sub_ocr_empty)

print(f'TOTAL {TOTAL} PASS {PASS} FAIL {FAIL}')
if FAIL == 0:
    print('INDEXERROR_BOUNDARY: PASSED')
else:
    print('INDEXERROR_BOUNDARY: FAILED')
    sys.exit(1)
