# This Python file uses the following encoding: utf-8

"""安全动作执行器。

AI 永远拿不到设备控制权，只能提交「动作计划」，由这里逐条审核并执行：
- 动作类型白名单（click/wait/back/close_popup/goto_page）；
- 坐标边界、屏幕边缘安全距、禁点矩形；
- 目标附近 OCR 危险文字扫描（充值/购买/删除/退出…）；
- 同一坐标点击次数上限、单次异常动作上限与总耗时上限；
- 每个点击后重新截图验证画面/页面是否按预期变化，不生效就停手，绝不盲连点。
"""

from __future__ import annotations

import time
import math
from dataclasses import dataclass, field

import numpy as np

from module.ai_recovery.imageutil import change_ratio, crop_pixels
from module.ai_recovery.types import Action, ActionType


@dataclass
class ActionRecord:
    action: str
    ok: bool
    x: int | None = None
    y: int | None = None
    text: str = ''
    reason: str = ''
    change: float = 0.0
    page_before: str = ''
    page_after: str = ''
    blocked: str = ''

    def to_dict(self) -> dict:
        return {
            'action': self.action, 'ok': self.ok, 'x': self.x, 'y': self.y,
            'text': self.text, 'reason': self.reason, 'change': round(self.change, 4),
            'page_before': self.page_before, 'page_after': self.page_after,
            'blocked': self.blocked,
        }


@dataclass
class ExecResult:
    ok: bool
    records: list[ActionRecord] = field(default_factory=list)
    message: str = ''
    recoveries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {'ok': self.ok, 'message': self.message,
                'records': [item.to_dict() for item in self.records],
                'recoveries': self.recoveries}


class ActionExecutor:
    def __init__(self, cfg, adapter, *, ocr_factory=None):
        self.cfg = cfg
        self.adapter = adapter
        self._ocr_factory = ocr_factory
        self._click_counts: dict[tuple[int, int], int] = {}

    def execute(self, actions: list[Action], *, deadline: float | None = None) -> ExecResult:
        records: list[ActionRecord] = []
        started = time.time()
        max_seconds = float(self.cfg.safety.max_incident_seconds)
        executed = 0
        for action in actions:
            if executed >= int(self.cfg.safety.max_actions):
                records.append(ActionRecord(action=action.type, ok=False,
                                            blocked=f'max_actions={self.cfg.safety.max_actions}'))
                break
            if time.time() - started > max_seconds:
                records.append(ActionRecord(action=action.type, ok=False, blocked='incident_timeout'))
                break
            if deadline is not None and time.time() > deadline:
                records.append(ActionRecord(action=action.type, ok=False, blocked='deadline'))
                break
            record = self._execute_one(action)
            records.append(record)
            executed += 1
            if not record.ok:
                # 任何一步没有按预期生效就停手，交给调用方重新分析/隔离
                return ExecResult(ok=False, records=records,
                                  message=f'动作未生效: {record.action} {record.blocked or record.reason}')
        return ExecResult(ok=bool(records) and all(item.ok for item in records), records=records,
                          message='执行完成')

    # ---------- 单个动作 ----------

    def _execute_one(self, action: Action) -> ActionRecord:
        action_type = (action.type or '').lower()
        page_before = self.adapter.current_page() or ''
        record = ActionRecord(action=action_type, ok=False, text=action.text,
                              reason=action.reason, page_before=page_before)
        if action_type not in ActionType.values():
            record.blocked = f'action_not_allowed:{action_type}'
            return record

        try:
            before = self.adapter.screenshot()
        except Exception as error:  # noqa: BLE001
            record.blocked = f'screenshot_failed:{type(error).__name__}'
            return record

        if action_type == ActionType.WAIT.value:
            # 等待动作本身就是“让动画/加载走完”，画面没有变化才是正常的；
            # 它只负责睡够并刷新截图，不能用“画面必须改变”来判定失败。
            wait_ms = max(200, min(int(action.wait_ms or 1500), 15000))
            self.adapter.sleep(wait_ms / 1000.0)
            try:
                after = self.adapter.screenshot()
                record.change = change_ratio(before, after)
                record.page_after = self.adapter.current_page() or ''
                record.ok = True
            except Exception as error:  # noqa: BLE001
                record.blocked = f'screenshot_failed:{type(error).__name__}'
            return record

        if action_type == ActionType.BACK.value:
            if _at_main_page(self.adapter):
                record.blocked = 'back_on_main_page'
                return record
            self.adapter.back()
            return self._verify(record, before, expected_change=True)

        if action_type == ActionType.CLOSE_POPUP.value:
            acted = False
            try:
                acted = bool(self.adapter.close_popup_local())
            except Exception:  # noqa: BLE001
                acted = False
            if not acted:
                if _at_main_page(self.adapter):
                    record.blocked = 'back_on_main_page'
                    return record
                self.adapter.back()
            return self._verify(record, before, expected_change=True)

        if action_type == ActionType.GOTO_PAGE.value:
            if not self.cfg.safety.allow_goto_page:
                record.blocked = 'goto_page_disabled'
                return record
            key = (action.page_key or '').strip()
            if not key or key not in set(self.adapter.known_pages()):
                record.blocked = f'unknown_page_key:{key}'
                return record
            ok = False
            try:
                ok = bool(self.adapter.goto_page(key))
            except Exception as error:  # noqa: BLE001
                record.reason = f'goto error: {type(error).__name__}'
            after = self.adapter.screenshot()
            record.change = change_ratio(before, after)
            record.page_after = self.adapter.current_page() or ''
            record.ok = bool(ok) and record.page_after == key
            if not record.ok:
                record.reason = record.reason or f'未能到达 {key}'
            return record

        # click
        if action.x is None or action.y is None:
            record.blocked = 'missing_coordinates'
            return record
        width, height = self.adapter.screen_size
        if not all(math.isfinite(float(value)) and 0 <= float(value) <= 1
                   for value in (action.x, action.y)):
            record.blocked = 'invalid_coordinates'
            return record
        x = int(round(float(action.x) * width))
        y = int(round(float(action.y) * height))
        # 语义锚点优先：AI 常把归一化坐标算偏，但它给的按钮文字可由 OCR 精确定位。
        # 只有找到与 AI 点距离合理的同名文本时才替换，避免点到屏幕上别处的同名文字。
        if action.text:
            anchor = _find_text_anchor(before, action.text, (x, y),
                                       max_distance=width * 0.35, ocr_factory=self._ocr_factory)
            if anchor is not None:
                x, y = anchor
                record.reason = (record.reason + ' [OCR锚点]').strip()
        record.x, record.y = x, y
        margin = int(self.cfg.safety.edge_margin_px)
        if x < margin or y < margin or x > width - margin or y > height - margin:
            record.blocked = f'out_of_bounds:{x},{y}'
            return record
        if self._in_forbidden_rect(x / width, y / height):
            record.blocked = 'forbidden_zone'
            return record
        if _contains_forbidden(action.text, self.cfg.safety.forbidden_keywords):
            record.blocked = 'forbidden_button_text'
            return record
        # 目标附近 OCR 危险文字扫描
        danger = self._danger_text(before, x, y)
        if danger and not _contains_forbidden(action.text, self.cfg.safety.safe_keywords):
            record.blocked = f'forbidden_nearby_text:{danger}'
            return record

        point = (round(x / 10) * 10, round(y / 10) * 10)
        used = self._click_counts.get(point, 0)
        if used >= int(self.cfg.safety.max_click_per_point):
            record.blocked = f'max_click_per_point={self.cfg.safety.max_click_per_point}'
            return record
        self._click_counts[point] = used + 1

        try:
            self.adapter.click(x, y)
        except Exception as error:  # noqa: BLE001
            record.blocked = f'click_failed:{type(error).__name__}'
            return record
        return self._verify(record, before, expected_change=True)

    # ---------- 验证 ----------

    def _verify(self, record: ActionRecord, before: np.ndarray, *, expected_change: bool) -> ActionRecord:
        self.adapter.sleep(float(self.cfg.safety.verify_wait_s))
        after = None
        for _ in range(max(1, int(self.cfg.safety.verify_retries))):
            try:
                after = self.adapter.screenshot()
            except Exception:  # noqa: BLE001
                after = None
            if after is not None:
                record.change = change_ratio(before, after)
                record.page_after = self.adapter.current_page() or ''
                if not expected_change or record.change >= self.cfg.safety.verify_change_ratio:
                    record.ok = True
                    return record
                if record.page_after and record.page_after != record.page_before:
                    record.ok = True
                    return record
            self.adapter.sleep(0.6)
        if after is not None:
            record.change = change_ratio(before, after)
            record.page_after = self.adapter.current_page() or ''
        record.ok = False
        record.reason = record.reason or '点击后画面/页面无预期变化'
        return record

    def _in_forbidden_rect(self, x: float, y: float) -> bool:
        for rect in self.cfg.safety.forbidden_rects or []:
            try:
                rx, ry, rw, rh = [float(value) for value in rect[:4]]
            except (TypeError, ValueError):
                continue
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return True
        return False

    def _danger_text(self, image: np.ndarray, x: int, y: int) -> str:
        """只裁目标附近小图做一次 OCR；识别失败不阻塞（文字白名单检查已做过）。"""
        radius = int(self.cfg.safety.danger_scan_radius_px)
        if radius <= 0 or image is None:
            return ''
        try:
            patch = crop_pixels(image, x - radius, y - radius, radius * 2, radius * 2)
            texts = _ocr_texts(patch, self._ocr_factory)
        except Exception:  # noqa: BLE001 - OCR 不可用时不阻塞安全流程的其它检查
            return ''
        for text in texts:
            hit = _contains_forbidden(text, self.cfg.safety.forbidden_keywords)
            if hit:
                return hit
        return ''


def _at_main_page(adapter) -> bool:
    check = getattr(adapter, 'is_main_page', None)
    if callable(check):
        try:
            return bool(check())
        except Exception:  # noqa: BLE001
            return False
    return False


def _find_text_anchor(image: np.ndarray, text: str, near: tuple[int, int], *,
                      max_distance: float = 400, ocr_factory=None):
    """在整屏 OCR 结果里找与 action.text 相同的文字框，返回框中心（离 near 最近）。"""
    target = str(text or '').strip()
    if not target or image is None:
        return None
    try:
        results = _ocr_boxes(image, ocr_factory)
    except Exception:  # noqa: BLE001
        return None
    best = None
    best_distance = float(max_distance)
    for box, value in results:
        if not value:
            continue
        if target not in value and value not in target:
            continue
        center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        distance = ((center[0] - near[0]) ** 2 + (center[1] - near[1]) ** 2) ** 0.5
        if distance < best_distance:
            best_distance = distance
            best = (int(center[0]), int(center[1]))
    return best


def _ocr_boxes(image: np.ndarray, ocr_factory=None) -> list[tuple[tuple[int, int, int, int], str]]:
    if ocr_factory is not None:
        return ocr_factory(image)
    from module.atom.ocr import RuleOcr
    height, width = image.shape[:2]
    rule = RuleOcr(roi=(0, 0, width, height), area=(0, 0, width, height),
                   mode='Full', method='Default', keyword='', name='AI_TEXT_ANCHOR')
    results = rule.detect_and_ocr(image, logDisplay=False)
    boxes: list[tuple[tuple[int, int, int, int], str]] = []
    for item in results or []:
        box = getattr(item, 'box', None)
        text = getattr(item, 'ocr_text', None) or getattr(item, 'text', None) or ''
        if box is None or not text:
            continue
        try:
            arr = box.tolist() if hasattr(box, 'tolist') else list(box)
            xs = [float(point[0]) for point in arr]
            ys = [float(point[1]) for point in arr]
            boxes.append(((int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))), str(text)))
        except (TypeError, ValueError, IndexError):
            continue
    return boxes


def _contains_forbidden(text: str, keywords: list[str]) -> str:
    if not text:
        return ''
    filled = [str(item) for item in keywords or [] if str(item)]
    for keyword in filled:
        if keyword in text:
            return keyword
    return ''


def _ocr_texts(image: np.ndarray, ocr_factory=None) -> list[str]:
    """对一小块图做 Full OCR，返回文本列表；兼容不同版本的 BoxedResult。"""
    if ocr_factory is not None:
        return ocr_factory(image)
    from module.atom.ocr import RuleOcr
    height, width = image.shape[:2]
    rule = RuleOcr(roi=(0, 0, width, height), area=(0, 0, width, height),
                   mode='Full', method='Default', keyword='', name='AI_DANGER_SCAN')
    results = rule.detect_and_ocr(image, logDisplay=False)
    texts: list[str] = []
    for item in results or []:
        text = getattr(item, 'ocr_text', None) or getattr(item, 'text', None) or ''
        if text:
            texts.append(str(text))
    return texts
