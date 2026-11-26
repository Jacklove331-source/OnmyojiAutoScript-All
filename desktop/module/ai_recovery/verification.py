"""Verify observable recovery postconditions rather than animation alone.

2026-11 复盘（log/error/1795180871522 Duel、log/error/1795092647822 EvoZone）：
页面确实已经恢复，但被记成 ``page_not_freshly_recognized`` —— 旧实现只看
「恢复动作之后的第一帧」，把过渡态（page_battle_prepare → page_battle →
page_reward，页面本就在动）和一次读页面失败都当成失败，而且沿用了恢复前的旧帧。

这里改为：
- 每帧都重新截图 + 重新识别（``refresh_page``，不复用缓存），识别不到就在原地重试一次；
- 判定用「恢复动作之后」的连续 2 帧：到达预期页 / 连续两帧同一已知页 / 已知页在推进，
  都算恢复成功；过渡态不再直接判死；
- 只有「所有帧都没识别出任何已知页」才算 ``page_not_freshly_recognized``；
- 页面没变且异常本身是卡死类时仍然要求真实进度证据（不放宽成「无条件成功」）；
- 结果里带 ``frames`` 明细（帧号/时间/来源/页面），日志可据此对账。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from module.ai_recovery.imageutil import change_ratio
from module.logger import logger


UNKNOWN_PAGES = {'', 'unknown', 'none', 'loading'}
STALLED_TYPES = {
    'device_stuck', 'no_progress', 'click_no_effect', 'click_loop',
    'template_miss', 'ocr_fail', 'state_mismatch', 'load_timeout',
    'recovery_failed', 'recognition_conflict', 'screen_changed',
    'script_error', 'task_exception',
}


@dataclass
class Verification:
    verified: bool = False
    reason: str = ''
    page_before: str = ''
    page_after: str = ''
    expected_page: str = ''
    observations: int = 0
    version: int = 2
    frames: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    def frame_summary(self, limit: int = 4) -> str:
        """给日志用的一行摘要：帧号@时间/来源/页面。"""
        items = [f"#{frame.get('frame')}@{frame.get('at')}"
                 f"({frame.get('source')}:{frame.get('page') or '?'}/{frame.get('status')})"
                 for frame in self.frames[-limit:]]
        return ' '.join(items) if items else 'no-frame'


def fresh_page(adapter) -> str:
    refresh = getattr(adapter, 'refresh_page', None)
    try:
        # An explicit failed refresh must never fall back to a stale page.
        return str((refresh() if callable(refresh) else adapter.current_page()) or '')
    except Exception:
        return ''


def _read_page(adapter, *, attempts: int = 2) -> tuple[str, str]:
    """读一帧页面；第一次读不到就原地等 0.3s 再读一次（过渡态常见）。

    返回 (page, source)。绝不回退到导航器的旧缓存页面。
    """
    page, source = '', 'refresh_page'
    for attempt in range(max(1, attempts)):
        page = fresh_page(adapter)
        if page and page.lower() not in UNKNOWN_PAGES:
            return page, source
        if attempt + 1 < attempts:
            try:
                adapter.sleep(0.3)
            except Exception:  # noqa: BLE001 - 适配器没有 sleep 时退化为不等待
                pass
    return page, source


def _run_progress_check(adapter, anomaly, diagnosis) -> bool:
    checker = getattr(adapter, 'verify_task_progress', None)
    try:
        return callable(checker) and checker(anomaly, diagnosis) is True
    except Exception:  # noqa: BLE001 - 任务侧校验抛错按「没有进度证据」处理
        return False


def _unknown_page_visual_progress(proof: Verification, anomaly, diagnosis, executed,
                                  page_before: str, target: str, before_image,
                                  frame_images: list) -> bool:
    """未知页（Restart/弹窗/登录类）没有识别出已知页时的真实视觉推进验证。

    修复 ``page_not_freshly_recognized`` 误杀：重启/关弹窗后画面可能一直停
    在未知页（例如登录过渡、加载），但动作前后画面发生了明显且趋于稳定的
    变化，说明动作确实推动了界面，按视觉进度证据通过。
    """
    if target:
        return False
    if before_image is None or not frame_images:
        return False
    if all(getattr(record, 'action', '') == 'wait' for record in executed.records):
        return False  # 纯等待不算真实推进
    if (page_before or anomaly.page or '').lower() not in UNKNOWN_PAGES:
        return False
    diagnosis_category = str(getattr(diagnosis, 'page_category', '') or '')
    if not (anomaly.task == 'Restart' or diagnosis_category in {'login', 'dialog', 'loading'}):
        return False
    try:
        progressed = change_ratio(before_image, frame_images[-1]) >= 0.10
        stable = (len(frame_images) < 2
                  or change_ratio(frame_images[-2], frame_images[-1]) <= 0.06)
    except Exception:  # noqa: BLE001 - 图像比较异常按「没有视觉证据」处理
        return False
    if not (progressed and stable):
        return False
    proof.verified = True
    proof.reason = 'unknown_page_visual_progress'
    proof.page_after = page_before or 'unknown'
    logger.info(f'[AI] 恢复验证用帧 {proof.frame_summary()} → {proof.reason}')
    return True


def verify_recovery(cfg, adapter, anomaly, executed, *, diagnosis=None,
                    page_before: str = '', expected_page: str = '',
                    before_image=None) -> Verification:
    # The task's recovery contract outranks a model's or old lesson's guess.
    target = (anomaly.evidence or {}).get('expected_page', '') or expected_page or getattr(diagnosis, 'expected_page', '')
    if not target:
        target = next((record.page_after for record in reversed(executed.records)
                       if record.action == 'goto_page' and record.ok), '')
    proof = Verification(page_before=page_before or anomaly.page or '', expected_page=target)
    if not executed.ok or not executed.records:
        proof.reason = executed.message or 'action_failed'
        return proof
    try:
        known = set(adapter.known_pages())
    except Exception:
        known = set()
    if target and known and target not in known:
        proof.reason = 'unknown_expected_page'
        return proof

    frames = max(3, int(getattr(cfg.safety, 'verification_frames', 2)) + 1)
    previous = None
    moved = False
    frame_images: list = []
    for index in range(frames):
        if index:
            adapter.sleep(float(cfg.safety.verify_wait_s))
        try:
            image = adapter.screenshot()
        except Exception:  # noqa: BLE001 - 截图异常统一记成「取不到验证帧」
            image = None
        if image is None or not getattr(image, 'size', 0):
            proof.reason = 'verification_screenshot_unavailable'
            return proof
        frame_images.append(image)
        page, source = _read_page(adapter)
        frame = {
            'frame': index,
            'at': time.strftime('%H:%M:%S', time.localtime()),
            'source': source,
            'page': page,
            'status': 'unrecognized',
        }
        proof.frames.append(frame)
        proof.observations += 1
        proof.page_after = page
        if not page or page.lower() in UNKNOWN_PAGES or (known and page not in known):
            # 过渡态/识别失败：记录后继续看下一帧，不再像旧实现那样立即判死
            previous = None
            continue
        if target:
            if page == target:
                frame['status'] = 'target'
                proof.verified = True
                proof.reason = 'expected_page_reached'
                logger.info(f'[AI] 恢复验证用帧 {proof.frame_summary()} → {proof.reason}')
                return proof
            moved = moved or page != (page_before or anomaly.page or '')
            frame['status'] = 'transition'
            continue
        moved = moved or page != (page_before or anomaly.page or '')
        if previous == page and page != (page_before or anomaly.page or ''):
            # 只有「页面确实发生了变化并稳定下来」才算恢复证据；
            # 停在原页面（哪怕识别得很稳）不能算成功，避免把卡死判成恢复。
            frame['status'] = 'stable'
            proof.verified = True
            proof.reason = 'stable_page_transition'
            logger.info(f'[AI] 恢复验证用帧 {proof.frame_summary()} → {proof.reason}')
            return proof
        frame['status'] = 'stable' if previous == page else 'transition'
        previous = page

    recognized = [frame for frame in proof.frames if frame['status'] != 'unrecognized']
    if not recognized:
        if _unknown_page_visual_progress(proof, anomaly, diagnosis, executed,
                                         page_before, target, before_image, frame_images):
            return proof
        proof.reason = 'page_not_freshly_recognized'
        logger.warning(f'[AI] 恢复验证所有帧都未识别出已知页：{proof.frame_summary(8)}')
        return proof
    if target:
        proof.reason = 'expected_page_mismatch'
        logger.warning(f'[AI] 恢复验证未到达预期页 {target}：{proof.frame_summary()}')
        return proof
    if moved:
        # 已知页在动：说明恢复动作确实推动了页面（战斗过渡等），按进度证据通过
        proof.verified = True
        proof.reason = 'page_advancing'
        logger.info(f'[AI] 恢复验证用帧 {proof.frame_summary()} → {proof.reason}')
        return proof

    only_waited = all(record.action == 'wait' for record in executed.records)
    if only_waited or anomaly.type in STALLED_TYPES:
        if not _run_progress_check(adapter, anomaly, diagnosis):
            proof.reason = 'wait_without_progress' if only_waited else 'same_page_still_stalled'
            logger.warning(f'[AI] 恢复验证页面未变化且无进度证据：{proof.frame_summary()}')
            return proof
    proof.verified = True
    proof.reason = 'stable_page'
    logger.info(f'[AI] 恢复验证用帧 {proof.frame_summary()} → {proof.reason}')
    return proof
