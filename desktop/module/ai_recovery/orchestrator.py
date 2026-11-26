# This Python file uses the following encoding: utf-8

"""AI 自愈编排器：检测到异常后决定「本地恢复 / 查知识库 / 调 DeepSeek / 安全执行」。

关键顺序（省钱且可靠）：
1. 关闭时直接返回；
2. 冷却 / 同一异常接管次数 / 截图去重；
3. 设备类异常先走本地恢复（ADB 掉线、截图失败 → 不浪费 token）；
4. 知识库有可信方案 → 直接重放并验证（不调 API）；
5. 预算允许才调 DeepSeek；
6. 低置信/高风险/需人工 → 只存证据不动作；
7. 观察模式只分析；自动模式执行白名单动作，每步验证；
8. 成功 → 交还 OAS；失败 → 保存修复请求，交回原任务的错误处理。
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from module.ai_recovery.actions import ActionExecutor, ExecResult, _ocr_boxes
from module.ai_recovery.budget import BudgetGuard
from module.ai_recovery.client import AiBadResponse, AiError, AiUnavailable, DeepSeekClient
from module.ai_recovery.config import AiRecoveryConfig
from module.ai_recovery.evidence import EvidenceStore
from module.ai_recovery.imageutil import change_ratio, content_hash, dhash, encode_jpeg
from module.ai_recovery.kb import KnowledgeBase, replay_plan
from module.ai_recovery.templates import TemplateLibrary
from module.ai_recovery.types import Anomaly, Diagnosis, Outcome, RecoveryResult
from module.ai_recovery.imageutil import region_to_pixels
from module.ai_recovery.verification import fresh_page, verify_recovery
from module.ai_recovery.runtime import RecoveryLease, RecoveryStateStore
from module.ai_recovery.repair_queue import enqueue
from module.ai_recovery.learning import LearningMemory

try:
    from module.logger import logger
except Exception:  # pragma: no cover - 离线单测的兜底
    class _Logger:
        def info(self, *args, **kwargs):
            print('[AI]', *args)

        warning = error = info

    logger = _Logger()

# 这些异常先交给本地机制，AI 拿不到截图也帮不上忙
LOCAL_FIRST_TYPES = {
    'device_disconnected', 'adb_offline', 'adb_missing', 'screenshot_failed',
    'game_not_running', 'emulator_stopped', 'app_lost_login',
}

# 进程级共享状态：同一任务里可能有多个 AiRecovery 实例
_STATE: dict[str, dict] = {}
STATS = {
    'incidents': 0, 'ai_calls': 0, 'recovered': 0, 'observed': 0,
    'stopped': 0, 'needs_human': 0, 'skipped': 0, 'unavailable': 0,
    'tokens': 0, 'cost_cny': 0.0, 'kb_hits': 0,
}


def _state_for(key: str) -> dict:
    state = _STATE.get(key)
    if state is None:
        state = {'last_attempt': 0.0, 'incidents': 0, 'attempted': []}
        _STATE[key] = state
    return state


class AiRecovery:
    def __init__(self, cfg: AiRecoveryConfig, adapter, *,
                 client: DeepSeekClient | None = None,
                 kb: KnowledgeBase | None = None,
                 templates: TemplateLibrary | None = None,
                 evidence: EvidenceStore | None = None,
                 clock=None):
        self.cfg = cfg
        self.adapter = adapter
        self.clock = clock or time.time
        self.client = client or DeepSeekClient(cfg)
        self.kb = kb if kb is not None else KnowledgeBase(cfg, config_name=cfg.config_name)
        self.templates = templates if templates is not None else TemplateLibrary(cfg, config_name=cfg.config_name)
        self.evidence = evidence if evidence is not None else EvidenceStore(cfg.config_name)
        self.learning = LearningMemory(cfg, self.evidence.root, clock=self.clock)
        self._case_id = ''

    # ---------- 主入口 ----------

    def handle(self, anomaly: Anomaly) -> RecoveryResult:
        if not isinstance(anomaly, Anomaly):
            anomaly = Anomaly.from_dict(dict(anomaly or {}))
        if not anomaly.task:
            anomaly.task = getattr(self.adapter, 'task_name', '') or 'Unknown'
        if not self.cfg.enabled:
            return self._result(Outcome.SKIPPED, anomaly, 'AI 自愈未启用')
        folder = self.evidence.begin(anomaly.incident_id, anomaly)
        self._attempted = []
        self._case_id = ''
        try:
            device_key = str(getattr(self.adapter, 'device_id', '') or self.cfg.config_name)
            with RecoveryLease(self.evidence.root, device_key) as lease:
                if not lease.acquired:
                    result = self._result(Outcome.SKIPPED, anomaly, '同一设备已有自愈接管或接管锁不可用')
                else:
                    result = self._handle(anomaly, folder)
        except Exception as error:
            logger.warning(f'[AI] 自愈异常已拦截：{type(error).__name__}')
            result = self._result(Outcome.UNAVAILABLE, anomaly,
                                  f'自愈内部异常：{type(error).__name__}')
            self.evidence.save_json(folder, 'internal_error.json', {'type': type(error).__name__})
        result.evidence_dir = str(folder)
        try:
            self.learning.finish(anomaly, result)
        except Exception as error:
            logger.warning(f'[AI学习] 结果记忆写入失败：{type(error).__name__}')
        if result.outcome.value in STATS:
            STATS[result.outcome.value] += 1
        if result.outcome in {Outcome.STOPPED, Outcome.NEEDS_HUMAN, Outcome.UNAVAILABLE, Outcome.RECOVERED}:
            try:
                request = enqueue(self.evidence.root, self.cfg.config_name, anomaly, result)
                if request:
                    self.evidence.save_json(folder, 'repair_request.json', request)
            except Exception as error:
                logger.warning(f'[AI] 修复任务归档失败：{type(error).__name__}')
        self._finish(folder, result)
        return result

    def _handle(self, anomaly, folder):
        now = self.clock()
        admission = self.learning.begin(anomaly)
        self._case_id = admission['case_id']
        if not admission['allowed']:
            return self._result(Outcome.SKIPPED, anomaly,
                                f"重复错误已退避，{int(admission['retry_after_s']) + 1}秒后再试")
        state_key = f'{self.cfg.config_name}|{anomaly.key()}|{self._case_id}'
        state_store = RecoveryStateStore(self.evidence.root, self.cfg.config_name)
        state = state_store.load(state_key, now=now, window_s=self.cfg.detector.incident_window_s)
        _STATE[state_key] = state
        self._attempted = list(state['attempted'])
        if state['incidents'] and now - float(state['last_attempt']) < self.cfg.detector.cooldown_s:
            return self._result(Outcome.SKIPPED, anomaly, '同一异常仍在冷却')
        if state['incidents'] >= self.cfg.detector.max_incidents_per_key:
            return self._result(Outcome.SKIPPED, anomaly, '同一异常接管次数已达上限')
        state['last_attempt'] = now
        state['incidents'] += 1
        state_store.save(state_key, state)
        STATS['incidents'] += 1
        image = self._capture(folder)
        if image is None or anomaly.type in LOCAL_FIRST_TYPES:
            return self._local_first(anomaly, folder, image=image)
        self.evidence.save_png(folder, 'before.png', image)
        resolution = tuple(int(v) for v in image.shape[1::-1])
        game_version = str(getattr(self.adapter, 'game_version', '') or '')
        entry = self.kb.lookup(anomaly, dhash(image), resolution, game_version, now=now)
        if entry is not None:
            STATS['kb_hits'] += 1
            result = self._replay_kb(anomaly, entry, image, folder)
            if result is not None:
                return result
            # A replay may have changed the screen before failing its postcondition.
            image = self._capture(folder)
            if image is None:
                return self._result(Outcome.UNAVAILABLE, anomaly, '知识库方案失效后无法重新截图')
        if not self.learning.provider_allowed():
            return self._result(Outcome.SKIPPED, anomaly, '模型接口连续失败，暂缓调用；本地知识库仍可复用')
        guard = BudgetGuard(self.cfg, config_name=self.cfg.config_name)
        guard.start_incident(task=anomaly.task)
        if self.kb.was_seen(content_hash(image), ttl_s=600, now=now) and state['incidents'] > 1:
            return self._result(Outcome.SKIPPED, anomaly, '相同截图近期已分析，避免重复调用')
        result = None
        total_tokens, total_cost = 0, 0.0
        executor = ActionExecutor(self.cfg, self.adapter)
        actions_used = 0
        action_deadline = None
        max_attempts = 1 + int(self.cfg.safety.replan_attempts)
        if isinstance(self.client, DeepSeekClient):
            self.client.request_guard = guard
        for attempt in range(max_attempts):
            if actions_used >= self.cfg.safety.max_actions:
                result.message += '；本次接管动作已达上限'
                return result
            allowed, reason = (guard.check(model=self.cfg.api.model, task=anomaly.task)
                               if isinstance(self.client, DeepSeekClient)
                               else guard.reserve(model=self.cfg.api.model, task=anomaly.task))
            if not allowed:
                if result is not None:
                    result.message += f'；停止重试：{reason}'
                    return result
                return self._budget_skip(anomaly, reason)
            attempt_dir = folder / f'attempt_{attempt + 1:02d}'
            attempt_dir.mkdir(parents=True, exist_ok=True)
            # Always use fresh pixels, including when the previous plan failed.
            if attempt:
                image = self._capture(attempt_dir)
                if image is None:
                    result.message += '；重新截图失败'
                    return result
            resolution = tuple(int(v) for v in image.shape[1::-1])
            before_page = fresh_page(self.adapter) or anomaly.page
            # Detection can capture a newer frame. Pair the AI plan with that frame.
            image = self._capture(attempt_dir)
            if image is None:
                return self._result(Outcome.UNAVAILABLE, anomaly, '分析前无法取得新截图')
            resolution = tuple(int(v) for v in image.shape[1::-1])
            phash = dhash(image)
            context = self._build_context(anomaly, resolution)
            context['attempted_recoveries'] = self._attempted[-5:]
            context['attempt'] = attempt + 1
            context['failed_plans'] = self.learning.feedback(self._case_id)
            api_image, crop_pixels = self._prepare_view(image, anomaly, context)
            # 本地 OCR 辅助视觉上下文：对实际送模型的视图提取，保证坐标与 view 一致
            hints = self._extract_ocr_hints(api_image)
            if hints:
                context['ocr_elements'] = hints
            self.evidence.save_png(attempt_dir, 'before.png', image)
            self.evidence.save_json(attempt_dir, 'context.json', context)
            self.evidence.save_json(folder, 'context.json', context)
            try:
                jpeg = encode_jpeg(api_image, max_side=self.cfg.api.image_max_side,
                                   quality=self.cfg.api.image_quality,
                                   redact_top_px=max(0, self.cfg.api.redact_top_px
                                                     - (crop_pixels[1] if crop_pixels else 0)))
                analysis = self.client.analyze(jpeg, context)
                plan = self._map_plan(analysis.diagnosis, crop_pixels, resolution)
                if plan.dropped_actions:
                    # 单个动作字段非法不放弃整次诊断（2026-11 复盘 1794913297885）
                    logger.warning(
                        f'[AI] 丢弃 {len(plan.dropped_actions)} 个非法动作，保留 {len(plan.actions)} 个继续执行：'
                        f'{[item["reasons"][0] for item in plan.dropped_actions[:3]]}')
            except Exception as error:
                self.learning.provider_result(success=False, reason=type(error).__name__)
                message = str(error)[:250] if isinstance(error, AiError) else type(error).__name__
                self.evidence.save_json(attempt_dir, 'error.json', {'type': type(error).__name__})
                result = self._result(Outcome.UNAVAILABLE, anomaly, f'AI 分析不可用：{message}')
                result.tokens, result.cost_cny = total_tokens, total_cost
                result.ai_called = True
                return result
            self.learning.provider_result(success=True)
            STATS['ai_calls'] += 1
            STATS['tokens'] += analysis.usage.total_tokens
            STATS['cost_cny'] += float(analysis.usage.cost_cny)
            total_tokens += analysis.usage.total_tokens
            total_cost += analysis.usage.cost_cny
            guard.commit(analysis.usage)
            self.kb.mark_seen(content_hash(image), now=now)
            self.evidence.save_json(attempt_dir, 'diagnosis.json', plan.to_dict())
            self.evidence.save_json(folder, 'diagnosis.json', plan.to_dict())
            self.evidence.save_json(attempt_dir, 'usage.json', analysis.usage.to_dict())
            self.evidence.save_json(folder, 'usage.json', analysis.usage.to_dict())
            self.evidence.save_annotated(attempt_dir, 'planned.png', image,
                                         [a.to_dict() for a in plan.actions])
            self.evidence.save_annotated(folder, 'planned.png', image,
                                         [a.to_dict() for a in plan.actions])
            result = self._result(Outcome.OBSERVED, anomaly,
                                  f'AI: {plan.page} / {plan.cause}', diagnosis=plan)
            result.ai_called = True
            result.tokens, result.cost_cny = total_tokens, total_cost
            if plan.needs_human or plan.confidence < self.cfg.safety.min_confidence or (
                    plan.risk == 'high' and not self.cfg.safety.allow_high_risk):
                result.outcome = Outcome.NEEDS_HUMAN
                result.message = '诊断置信度不足、需人工处理或动作风险过高'
                return result
            if not self.cfg.auto:
                note = self._maybe_template(plan, image, attempt_dir)
                if note:
                    result.actions_done.append({'template': note})
                self.evidence.save_json(folder, 'plan.json', {
                    'mode': 'observe', 'actions': [a.to_dict() for a in plan.actions]})
                return result
            if not plan.actions:
                result.outcome = Outcome.STOPPED
                result.message = 'AI 未给出可执行动作'
                return result
            if not self.learning.plan_allowed(self._case_id, plan):
                result.outcome = Outcome.STOPPED
                result.message = '同一方案已连续失败，阻止重复执行'
                self._attempted.append('history: repeated failed plan blocked before input')
                state['attempted'] = self._attempted[-5:]
                state_store.save(state_key, state)
                continue
            remaining = self.cfg.safety.max_actions - actions_used
            if len(plan.actions) > remaining:
                result.outcome = Outcome.STOPPED
                result.message = '新方案超过本次接管剩余动作额度'
                return result
            if action_deadline is None:
                action_deadline = time.time() + self.cfg.safety.max_incident_seconds
            page_now = fresh_page(self.adapter)
            latest = self.adapter.screenshot()
            if (any(a.type != 'wait' for a in plan.actions)
                    and ((page_now and page_now.lower() not in ('unknown', 'none', 'loading')
                          and page_now != before_page)
                         or self._is_plan_frame_stale(image, latest, plan.actions,
                                                      self.cfg.safety.plan_stale_change_ratio))):
                executed = ExecResult(False, message='plan_frame_stale')
            else:
                executed = executor.execute(plan.actions, deadline=action_deadline)
            actions_used += len(executed.records)
            result.actions_done = [r.to_dict() for r in executed.records]
            self.evidence.save_json(attempt_dir, 'actions.json', executed.to_dict())
            self.evidence.save_json(folder, 'actions.json', executed.to_dict())
            proof = verify_recovery(self.cfg, self.adapter, anomaly, executed,
                                    diagnosis=plan, page_before=before_page,
                                    before_image=image)
            result.verification = proof.to_dict()
            result.verified = proof.verified
            self.evidence.save_json(attempt_dir, 'verification.json', proof.to_dict())
            try:
                after = self.adapter.screenshot()
                self.evidence.save_png(attempt_dir, 'after.png', after)
                self.evidence.save_png(folder, 'after.png', after)
            except Exception:
                pass
            if proof.verified:
                plan.expected_page = proof.page_after
                plan.actions = [replace(action, x=record.x / resolution[0], y=record.y / resolution[1])
                                if action.type == 'click' and record.x is not None else action
                                for action, record in zip(plan.actions, executed.records)]
                self.kb.record(anomaly, plan, [a.to_dict() for a in plan.actions], True,
                               phash, resolution, game_version, note=proof.reason)
                # Failed or wait-only plans never teach automatically clickable templates.
                note = self._maybe_template(plan, image, attempt_dir,
                                            page_before=before_page, verified=True, executed=executed)
                if note:
                    result.actions_done.append({'template': note})
                result.outcome = Outcome.RECOVERED
                result.message = f'恢复验证通过：{proof.page_after}'
                self._notify(f'AI 已恢复：{anomaly.task}/{anomaly.type} → {proof.page_after}')
                return result
            self.learning.plan_failed(self._case_id, plan, proof.reason)
            self.kb.record(anomaly, plan, [a.to_dict() for a in plan.actions], False,
                           phash, resolution, game_version, note=proof.reason)
            result.outcome = Outcome.STOPPED
            result.message = f'恢复未通过验证：{proof.reason}（{proof.frame_summary(6)}）'
            logger.warning(f'[AI] {anomaly.task}/{anomaly.type} 恢复未通过验证：'
                           f'{proof.reason}；用帧 {proof.frame_summary(8)}')
            attempt_summary = (
                f'attempt={attempt + 1}; actions={[a.type for a in plan.actions]}; '
                f'page={proof.page_after}; failed={proof.reason}')
            self._attempted.append(attempt_summary)
            state['attempted'] = (state['attempted'] + [attempt_summary])[-5:]
            state_store.save(state_key, state)
            if all(a.type == 'wait' for a in plan.actions):
                # Do not let a long, otherwise healthy battle extend endlessly through waits.
                break
        return result

    def _local_first(self, anomaly, folder, image=None, reason=''):
        if not self.cfg.auto:
            return self._result(Outcome.OBSERVED, anomaly, '观察模式：设备异常只记录，不执行恢复')
        local = getattr(self.adapter, 'local_recovery', None)
        try:
            ok = callable(local) and bool(local(anomaly.type))
        except Exception:
            ok = False
        result = self._result(Outcome.RECOVERED if ok else Outcome.UNAVAILABLE,
                              anomaly, '本地恢复成功' if ok else '本地恢复未成功')
        result.verified = ok
        result.verification = {'version': 1, 'kind': 'device_recovery', 'verified': ok}
        return result

    def _replay_kb(self, anomaly, entry, image, folder):
        plan = replay_plan(entry)
        if not plan:
            return None
        diagnosis = Diagnosis(page=entry.page, page_known=True, confidence=1.0,
                              cause='validated knowledge replay', actions=plan,
                              expected_page=entry.expected_page)
        if not self.learning.plan_allowed(self._case_id, diagnosis):
            self._attempted.append('knowledge: previously failed solution quarantined')
            return None
        if not self.cfg.auto:
            return self._result(Outcome.OBSERVED, anomaly, '知识库命中，观察模式不执行')
        before_page = fresh_page(self.adapter) or anomaly.page
        if entry.page and entry.page.lower() not in ('unknown', 'none') and entry.page != before_page:
            self.kb.mark_reuse(entry, False, now=self.clock())
            self._attempted.append(f'knowledge={entry.id}; failed=source_page_mismatch')
            return None
        executed = ActionExecutor(self.cfg, self.adapter).execute(plan)
        proof = verify_recovery(self.cfg, self.adapter, anomaly, executed,
                                page_before=before_page,
                                expected_page=getattr(entry, 'expected_page', ''),
                                before_image=image)
        self.kb.mark_reuse(entry, proof.verified, now=self.clock())
        self.evidence.save_json(folder, 'kb_actions.json', executed.to_dict())
        self.evidence.save_json(folder, 'kb_verification.json', proof.to_dict())
        if not proof.verified:
            self.learning.plan_failed(self._case_id, diagnosis, proof.reason)
            self._attempted.append(f'knowledge={entry.id}; failed={proof.reason}')
            return None
        result = self._result(Outcome.RECOVERED, anomaly, '知识库方案通过恢复验证')
        result.diagnosis = diagnosis
        result.verified = True
        result.verification = proof.to_dict()
        result.actions_done = [r.to_dict() for r in executed.records]
        return result

    def _prepare_view(self, image, anomaly, context):
        resolution = tuple(int(v) for v in image.shape[1::-1])
        context['view'] = 'full_screen'
        context['input_resolution'] = list(resolution)
        allow_crop = not self.cfg.api.send_full_screen or anomaly.type in {
            'template_miss', 'ocr_fail', 'click_no_effect', 'screen_changed', 'recognition_conflict'}
        focus = (anomaly.evidence or {}).get('focus') or (anomaly.evidence or {}).get('target')
        if allow_crop and isinstance(focus, dict):
            try:
                region = _expand_region(focus, .15)
                pixels = region_to_pixels(region, *resolution)
                x, y, w, h = pixels
                if w >= 160 and h >= 120:
                    context['view'] = 'target_crop'
                    context['focus_region'] = dict(zip(('x', 'y', 'w', 'h'),
                        (x / resolution[0], y / resolution[1], w / resolution[0], h / resolution[1])))
                    context['input_resolution'] = [w, h]
                    return image[y:y+h, x:x+w], pixels
            except (ValueError, TypeError, OverflowError):
                pass
        return image, None

    def _map_plan(self, diagnosis, crop, resolution):
        if crop is None:
            return diagnosis
        x, y, w, h = crop
        width, height = resolution
        actions = [replace(a, x=(x + float(a.x) * w) / width,
                           y=(y + float(a.y) * h) / height)
                   if a.x is not None and a.y is not None else replace(a)
                   for a in diagnosis.actions]
        template = dict(diagnosis.template_update or {})
        if isinstance(template.get('region'), dict):
            region = template['region']
            template['region'] = {'x': (x + region['x'] * w) / width,
                                  'y': (y + region['y'] * h) / height,
                                  'w': region['w'] * w / width, 'h': region['h'] * h / height}
        return replace(diagnosis, actions=actions, template_update=template)

    # ---------- 帧新鲜度 / OCR 辅助 ----------

    def _is_plan_frame_stale(self, before_image, latest_image, actions, threshold) -> bool:
        """方案是否已失效：优先看第一个点击目标局部区域的变化，避免庭院/战斗动画误丢方案。

        没有带归一化坐标的点击动作时退回全屏对比。
        """
        try:
            height, width = before_image.shape[:2]
            if latest_image is None or latest_image.shape[:2] != (height, width):
                return change_ratio(before_image, latest_image) > threshold
        except (AttributeError, ValueError):
            return change_ratio(before_image, latest_image) > threshold
        for action in actions or []:
            if getattr(action, 'type', '') != 'click':
                continue
            x, y = getattr(action, 'x', None), getattr(action, 'y', None)
            if x is None or y is None:
                continue
            try:
                cx = min(width - 1, max(0, int(round(float(x) * width))))
                cy = min(height - 1, max(0, int(round(float(y) * height))))
                x0 = max(0, cx - 90)
                y0 = max(0, cy - 60)
                x1 = min(width, x0 + 180)
                y1 = min(height, y0 + 120)
                if x1 <= x0 or y1 <= y0:
                    break
                patch_before = before_image[y0:y1, x0:x1]
                patch_latest = latest_image[y0:y1, x0:x1]
            except (TypeError, ValueError, IndexError, AttributeError):
                break
            return change_ratio(patch_before, patch_latest) > threshold
        return change_ratio(before_image, latest_image) > threshold

    def _extract_ocr_hints(self, image, limit: int = 20) -> list[dict]:
        """本地 OCR 辅助视觉上下文：画面文字 + 归一化中心坐标，最多 limit 条。

        只在 include_ocr_hints 开启且即将调模型前调用；图片过小/纯色/OCR 异常
        一律静默返回空列表，绝不拖慢或打断自愈流程。
        """
        if not self.cfg.api.include_ocr_hints:
            return []
        if image is None or not getattr(image, 'size', 0):
            return []
        try:
            height, width = image.shape[:2]
            if width < 100 or height < 100:
                return []
            if float(image.std()) < 3.0:  # 纯色测试图没有文字可提取
                return []
        except Exception:  # noqa: BLE001
            return []
        try:
            boxes = _ocr_boxes(image)
        except Exception:  # noqa: BLE001
            return []
        hints: list[dict] = []
        for box, text in boxes or []:
            text = str(text or '').strip()
            if not text:
                continue
            cx = (float(box[0]) + float(box[2])) / 2.0
            cy = (float(box[1]) + float(box[3])) / 2.0
            hints.append({'t': text[:20], 'x': round(cx / width, 3), 'y': round(cy / height, 3)})
            if len(hints) >= limit:
                break
        return hints

    # ---------- 模板 ----------

    def _maybe_template(self, diagnosis: Diagnosis, image: np.ndarray, folder: Path,
                        *, page_before: str = '', verified: bool = False,
                        executed=None) -> dict | None:
        if not self.cfg.templates.enabled:
            return None
        update = diagnosis.template_update or {}
        if not update.get('needed') or not isinstance(update.get('region'), dict):
            return None
        name = str(update.get('name') or '').strip()
        if not name:
            return None
        try:
            candidate = self.templates.create_candidate(
                image, update['region'], name, diagnosis_task(self.adapter),
                source='ai_observe' if not self.cfg.auto else 'ai_auto')
            negatives = self.evidence.negative_pool(limit=10)
            report = self.templates.validate(candidate, [image], negatives)
            self.evidence.save_json(folder, 'template_candidate.json', {
                'candidate': candidate.to_dict(), 'validation': report.to_dict(),
            })
            note = {'name': name, 'passed': report.passed, 'reason': report.reason,
                    'path': str(candidate.path)}
            if report.passed and self.cfg.auto and verified:
                replay = {}
                clicks = [a for a in diagnosis.actions if a.type != 'wait']
                records = [r for r in executed.records if r.action == 'click' and r.ok] if executed else []
                if (len(clicks) == len(records) == 1 and clicks[0].type == 'click'
                        and diagnosis.expected_page and diagnosis.expected_page != page_before):
                    region = update['region']
                    px, py = records[0].x / image.shape[1], records[0].y / image.shape[0]
                    if (region['x'] <= px <= region['x'] + region['w']
                            and region['y'] <= py <= region['y'] + region['h']):
                        replay = {'verified': True, 'source_page': page_before,
                                  'expected_page': diagnosis.expected_page,
                                  'action': clicks[0].to_dict(),
                                  'offset': [(px - region['x']) / region['w'],
                                             (py - region['y']) / region['h']]}
                manifest = self.templates.promote(candidate, report,
                                                  game_version=str(getattr(self.adapter, 'game_version', '') or ''),
                                                  note=update.get('note', ''), replay=replay)
                note['promoted_version'] = manifest.get('active_version')
                if self.cfg.templates.auto_apply_to_source:
                    source = find_source_asset(name)
                    if source:
                        apply_record = self.templates.apply_to_source(candidate.task, name, source)
                        note['applied_to'] = apply_record['source']
                        note['backup'] = apply_record['backup']
            logger.info(f'[AI] 模板候选 {name}: passed={report.passed} ({report.reason})')
            return note
        except Exception as error:  # noqa: BLE001 - 模板失败不影响恢复
            logger.warning(f'[AI] 模板候选生成失败：{error}')
            return {'name': name, 'passed': False, 'reason': str(error)[:200]}

    # ---------- 上下文 ----------

    def _build_context(self, anomaly: Anomaly, resolution: tuple[int, int]) -> dict:
        adapter = self.adapter
        recent = []
        try:
            recent = list(getattr(adapter, 'recent_actions', lambda: [])() or [])
        except Exception:  # noqa: BLE001
            recent = []
        frames = []
        monitor = getattr(adapter, 'monitor', None)
        if monitor is not None:
            try:
                frames = [f'{time.strftime("%H:%M:%S", time.localtime(item.ts))}:{item.page or "-"}'
                          for item in list(monitor.frames)[-6:]]
            except Exception:  # noqa: BLE001
                frames = []
        attempted = list(_state_for(f'{self.cfg.config_name}|{anomaly.key()}')['attempted'])[-5:]
        try:
            known_pages = list(getattr(adapter, 'known_pages', lambda: [])() or [])
        except Exception:  # noqa: BLE001
            known_pages = []
        try:
            state_machine = str(getattr(adapter, 'state_machine', lambda: '')() or '')
        except Exception:  # noqa: BLE001
            state_machine = ''
        return {
            'task': anomaly.task,
            'config': self.cfg.config_name,
            'mode': self.cfg.mode,
            'anomaly': anomaly.type,
            'anomaly_detail': anomaly.detail,
            'page': anomaly.page,
            'page_known_by_oas': bool(anomaly.page),
            'resolution': list(resolution),
            'game_version': str(getattr(adapter, 'game_version', '') or ''),
            'state_machine': state_machine,
            'recent_actions': recent[-8:],
            'frame_history': frames,
            'attempted_recoveries': attempted,
            'known_pages': known_pages[:24],
            'kb_hint': '',
            'error': anomaly.detail,
            'recovery_goal': (anomaly.evidence or {}).get('expected_page', ''),
            'view': 'full_screen',
        }

    # ---------- 其它 ----------

    def _capture(self, folder: Path) -> np.ndarray | None:
        for attempt in range(2):
            try:
                image = self.adapter.screenshot()
                if image is not None and getattr(image, 'size', 0):
                    return image
            except Exception as error:  # noqa: BLE001
                logger.warning(f'[AI] 截图失败（第 {attempt + 1} 次）：{type(error).__name__}')
                self.evidence.save_json(folder, 'screenshot_error.json',
                                        {'attempt': attempt + 1, 'error': type(error).__name__})
                try:
                    self.adapter.sleep(0.5)
                except Exception:  # noqa: BLE001
                    pass
        return None

    def _notify(self, text: str) -> None:
        try:
            notify = getattr(self.adapter, 'notify', None)
            if callable(notify):
                notify(text)
        except Exception:  # noqa: BLE001
            pass
        logger.info(f'[AI] {text}')

    def _result(self, outcome: Outcome, anomaly: Anomaly, message: str,
                diagnosis: Diagnosis | None = None) -> RecoveryResult:
        return RecoveryResult(outcome=outcome, incident_id=anomaly.incident_id,
                              message=message, diagnosis=diagnosis)

    def _budget_skip(self, anomaly: Anomaly, reason: str) -> RecoveryResult:
        """预算耗尽是「可恢复」的失败：明确原因 + 建议 + 建议重试时间，但不伪报成功。

        2026-11 复盘（log/error/1795179473096、1795092694155）：
        只打一行 `skipped: 预算限制：per_hour_calls=12`，上层看门狗照旧把任务判死，
        现场看不出「等一小时就自愈」。这里补上建议与 soft_fail 标记，交给上层缓一缓。
        """
        if 'per_task_hour_calls' in reason:
            advice, retry_after = '该任务本小时自愈次数已达上限，等待整点小时重置（≤60 分钟）；不影响其他任务的自愈', 3600.0
        elif 'per_hour_calls' in reason:
            advice, retry_after = '等待整点小时窗口重置（≤60 分钟）', 3600.0
        elif 'per_day' in reason:
            advice, retry_after = '等待次日额度重置', 86400.0
        elif 'per_incident_calls' in reason:
            advice = '提高 budget.per_incident_calls，或等下一次独立接管'
            retry_after = float(getattr(self.cfg.detector, 'cooldown_s', 180.0) or 180.0)
        else:
            advice, retry_after = '检查预算状态文件/log/ai_recovery/budget_*.json 与配置', 600.0
        message = (f'预算耗尽（{reason}）：本次没有调用 AI，也没有恢复页面；'
                   f'建议：{advice} / 提高 budget 上限 / 继续依赖本地知识库')
        result = self._result(Outcome.SKIPPED, anomaly, message)
        result.soft_fail = True
        result.retry_after_s = retry_after
        logger.warning(f'[AI] {anomaly.task}/{anomaly.type} {message}')
        return result

    def _finish(self, folder: Path, result: RecoveryResult) -> None:
        result.evidence_dir = str(folder)
        self.evidence.finish(folder, result)


def _expand_region(region: dict, pad: float) -> dict:
    x = max(0.0, float(region.get('x', 0.0)) - pad)
    y = max(0.0, float(region.get('y', 0.0)) - pad)
    w = max(0.0, min(1.0, float(region.get('x', 0.0)) + float(region.get('w', 0.0)) + pad) - x)
    h = max(0.0, min(1.0, float(region.get('y', 0.0)) + float(region.get('h', 0.0)) + pad) - y)
    return {'x': x, 'y': y, 'w': w, 'h': h}


def _refresh_page(adapter) -> str:
    refresh = getattr(adapter, 'refresh_page', None)
    if callable(refresh):
        try:
            return str(refresh() or '')
        except Exception:  # noqa: BLE001
            pass
    return str(getattr(adapter, 'current_page', lambda: '')() or '')


def diagnosis_task(adapter) -> str:
    return str(getattr(adapter, 'task_name', '') or 'Unknown')


def find_source_asset(name: str) -> str:
    """按模板名在 assets 里找同名原始 png（大小写不敏感）；唯一才返回。"""
    from module.ai_recovery.config import ROOT
    matches = []
    assets = ROOT / 'assets'
    if not assets.exists():
        return ''
    target = name.lower()
    for path in assets.rglob('*.png'):
        if path.stem.lower() == target:
            matches.append(path)
            if len(matches) > 1:
                return ''
    return str(matches[0]) if matches else ''
