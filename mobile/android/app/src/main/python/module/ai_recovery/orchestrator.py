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
8. 成功 → 交还 OAS；失败 → 记录并隔离，不反复接管。
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from module.ai_recovery.actions import ActionExecutor
from module.ai_recovery.budget import BudgetGuard
from module.ai_recovery.client import AiBadResponse, AiError, AiUnavailable, DeepSeekClient
from module.ai_recovery.config import AiRecoveryConfig
from module.ai_recovery.evidence import EvidenceStore
from module.ai_recovery.imageutil import content_hash, dhash, encode_jpeg
from module.ai_recovery.kb import KnowledgeBase, replay_plan
from module.ai_recovery.templates import TemplateLibrary
from module.ai_recovery.types import Anomaly, Diagnosis, Outcome, RecoveryResult

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

    # ---------- 主入口 ----------

    def handle(self, anomaly: Anomaly) -> RecoveryResult:
        if not self.cfg.enabled:
            return self._result(Outcome.SKIPPED, anomaly, 'AI 自愈未启用')
        if not isinstance(anomaly, Anomaly):
            anomaly = Anomaly.from_dict(dict(anomaly or {}))
        if not anomaly.task:
            anomaly.task = getattr(self.adapter, 'task_name', '') or 'Unknown'

        key = anomaly.key()
        state = _state_for(key)
        now = self.clock()
        if now - float(state['last_attempt']) < float(self.cfg.detector.cooldown_s):
            return self._result(Outcome.SKIPPED, anomaly, f'冷却中（{int(now - state["last_attempt"])}s）')
        if int(state['incidents']) >= int(self.cfg.detector.max_incidents_per_key):
            return self._result(Outcome.SKIPPED, anomaly,
                                f'同一异常已接管 {state["incidents"]} 次，停止以免循环')
        state['last_attempt'] = now
        state['incidents'] = int(state['incidents']) + 1
        STATS['incidents'] += 1

        folder = self.evidence.begin(anomaly.incident_id, anomaly)
        image = self._capture(folder)
        if image is None:
            return self._local_first(anomaly, folder, reason='获取截图失败')

        screen = image.shape[1], image.shape[0]
        api_image = image
        view_mode = 'full_screen'
        allow_crop = (not self.cfg.api.send_full_screen) or anomaly.type in {
            'template_miss', 'ocr_fail', 'click_no_effect', 'screen_changed', 'recognition_conflict'}
        if allow_crop:
            focus = (anomaly.evidence or {}).get('focus') or (anomaly.evidence or {}).get('target')
            if isinstance(focus, dict):
                try:
                    from module.ai_recovery.imageutil import crop_region
                    padded = _expand_region(focus, 0.15)
                    patch = crop_region(image, padded)
                    if patch.shape[0] >= 120 and patch.shape[1] >= 160:
                        api_image = patch
                        view_mode = 'target_crop'
                        anomaly.evidence['focus_region'] = padded
                except Exception:  # noqa: BLE001
                    api_image = image
        phash = dhash(image)
        resolution = (int(screen[0]), int(screen[1]))
        game_version = str(getattr(self.adapter, 'game_version', '') or '')
        self.evidence.save_png(folder, 'before.png', image)
        self.evidence.save_json(folder, 'context.json', self._build_context(anomaly, resolution))

        if anomaly.type in LOCAL_FIRST_TYPES:
            return self._local_first(anomaly, folder, image=image)

        # 知识库优先：可信方案直接重放，不花 token
        entry = self.kb.lookup(anomaly, phash, resolution, game_version, now=now)
        if entry is not None:
            STATS['kb_hits'] += 1
            result = self._replay_kb(anomaly, entry, image, folder)
            if result is not None:
                return result

        # 预算与截图去重
        guard = BudgetGuard(self.cfg, config_name=self.cfg.config_name, now=now)
        guard.start_incident()
        if self.kb.was_seen(content_hash(image), ttl_s=600, now=now) and int(state['incidents']) > 1:
            return self._result(Outcome.SKIPPED, anomaly, '相同截图近期已分析过，跳过重复调用')
        ok, reason = guard.reserve(model=self.cfg.api.model)
        if not ok:
            return self._result(Outcome.SKIPPED, anomaly, f'预算限制：{reason}')

        # 调用 DeepSeek
        context = self._build_context(anomaly, resolution)
        context['view'] = view_mode
        try:
            jpeg = encode_jpeg(api_image, max_side=self.cfg.api.image_max_side,
                               quality=self.cfg.api.image_quality,
                               redact_top_px=self.cfg.api.redact_top_px)
            analysis = self.client.analyze(jpeg, context)
        except AiUnavailable as error:
            STATS['unavailable'] += 1
            logger.warning(f'[AI] DeepSeek 不可用，退回本地恢复：{error}')
            self.evidence.save_json(folder, 'error.json', {'error': str(error)})
            return self._result(Outcome.UNAVAILABLE, anomaly, f'API 不可用：{error}')
        except AiBadResponse as error:
            STATS['unavailable'] += 1
            logger.warning(f'[AI] DeepSeek 返回非法结构：{error}')
            self.evidence.save_json(folder, 'error.json', {'bad_response': str(error)})
            return self._result(Outcome.UNAVAILABLE, anomaly, f'AI 返回非法：{error}')
        except AiError as error:
            STATS['unavailable'] += 1
            return self._result(Outcome.UNAVAILABLE, anomaly, f'AI 调用失败：{error}')
        except Exception as error:  # noqa: BLE001 - 自愈层失败必须退回本地，不影响主任务
            STATS['unavailable'] += 1
            logger.warning(f'[AI] 分析阶段异常：{type(error).__name__}: {error}')
            return self._result(Outcome.UNAVAILABLE, anomaly, f'分析失败：{type(error).__name__}')

        STATS['ai_calls'] += 1
        STATS['tokens'] += analysis.usage.total_tokens
        STATS['cost_cny'] += float(analysis.usage.cost_cny)
        guard.commit(analysis.usage)
        self.kb.mark_seen(content_hash(image), now=now)
        self.evidence.save_json(folder, 'diagnosis.json', analysis.diagnosis.to_dict())
        self.evidence.save_json(folder, 'usage.json', analysis.usage.to_dict())
        self.evidence.save_annotated(folder, 'planned.png', image,
                                     [action.to_dict() for action in analysis.diagnosis.actions])

        result = self._result(Outcome.OBSERVED, anomaly,
                              f'AI: {analysis.diagnosis.page} / {analysis.diagnosis.cause}',
                              diagnosis=analysis.diagnosis)
        result.tokens = analysis.usage.total_tokens
        result.cost_cny = analysis.usage.cost_cny
        result.ai_called = True
        result.evidence_dir = str(folder)

        diagnosis = analysis.diagnosis
        if diagnosis.needs_human or diagnosis.confidence < self.cfg.safety.min_confidence:
            STATS['needs_human'] += 1
            result.outcome = Outcome.NEEDS_HUMAN
            if diagnosis.needs_human:
                result.message = 'AI 判定需人工确认，已停止自动操作'
            else:
                result.message = f'置信度 {diagnosis.confidence:.2f} 低于阈值，已停止自动操作'
            self._finish(folder, result)
            return result
        if diagnosis.risk == 'high' and not self.cfg.safety.allow_high_risk:
            STATS['needs_human'] += 1
            result.outcome = Outcome.NEEDS_HUMAN
            result.message = '高风险操作，已停止并保存证据'
            self._finish(folder, result)
            return result

        # 模板候选（观察模式也允许生成/验证，不点游戏）
        template_note = self._maybe_template(diagnosis, image, folder)
        if template_note:
            result.actions_done.append({'template': template_note})

        if not self.cfg.auto:
            STATS['observed'] += 1
            self.evidence.save_json(folder, 'plan.json', {
                'mode': 'observe',
                'actions': [action.to_dict() for action in diagnosis.actions],
            })
            result.outcome = Outcome.OBSERVED
            self._finish(folder, result)
            return result

        # 自动模式：安全执行 + 逐动作验证
        if not diagnosis.actions:
            result.outcome = Outcome.STOPPED
            result.message = 'AI 未给出可执行动作，按失败处理'
            STATS['stopped'] += 1
            self._finish(folder, result)
            return result

        executor = ActionExecutor(self.cfg, self.adapter)
        exec_result = executor.execute(diagnosis.actions)
        result.actions_done.extend([record.to_dict() for record in exec_result.records])
        self.evidence.save_json(folder, 'actions.json', exec_result.to_dict())
        page_after = _refresh_page(self.adapter)
        verified = bool(exec_result.ok) and page_after not in ('', 'unknown', 'None')
        result.verified = verified
        if verified:
            try:
                after = self.adapter.screenshot()
                self.evidence.save_annotated(folder, 'after.png', after,
                                             [{'x': record.x / screen[0], 'y': record.y / screen[1]}
                                              for record in exec_result.records if record.x is not None])
            except Exception:  # noqa: BLE001
                pass
            self.kb.record(anomaly, diagnosis, [action.to_dict() for action in diagnosis.actions],
                           True, phash, resolution, game_version,
                           note=f'page_after={page_after}')
            STATS['recovered'] += 1
            result.outcome = Outcome.RECOVERED
            result.message = f'AI 恢复成功：{page_after}'
            self._finish(folder, result)
            self._notify(f'AI 已恢复：{anomaly.task}/{anomaly.type} → {page_after}')
            return result

        self.kb.record(anomaly, diagnosis, [action.to_dict() for action in diagnosis.actions],
                       False, phash, resolution, game_version,
                       note=exec_result.message[:200])
        STATS['stopped'] += 1
        result.outcome = Outcome.STOPPED
        result.message = f'恢复未通过验证：{exec_result.message}（页面={page_after or "未知"}）'
        self._finish(folder, result)
        self._notify(f'AI 无法安全恢复：{anomaly.task}/{anomaly.type}，已隔离当前任务')
        return result

    # ---------- 本地优先 ----------

    def _local_first(self, anomaly: Anomaly, folder: Path, image=None, reason: str = '') -> RecoveryResult:
        local = getattr(self.adapter, 'local_recovery', None)
        ok = False
        detail = reason
        if callable(local):
            try:
                ok = bool(local(anomaly.type))
                detail = detail or ('本地恢复成功' if ok else '本地恢复失败')
            except Exception as error:  # noqa: BLE001
                detail = f'本地恢复异常：{type(error).__name__}'
        if ok and not detail.startswith('本地恢复成功'):
            detail = '本地恢复成功' + (f'（{detail}）' if detail else '')
        result = self._result(Outcome.RECOVERED if ok else Outcome.UNAVAILABLE, anomaly,
                              detail or '设备类异常交回本地恢复')
        result.verified = ok
        result.ai_called = False
        self._finish(folder, result)
        return result

    # ---------- 知识库复用 ----------

    def _replay_kb(self, anomaly: Anomaly, entry, image, folder: Path) -> RecoveryResult | None:
        plan = replay_plan(entry)
        if not plan:
            return None
        self.evidence.save_json(folder, 'kb_plan.json', {
            'entry_id': entry.id, 'actions': [action.to_dict() for action in plan],
            'success': entry.success, 'fail': entry.fail,
        })
        if not self.cfg.auto:
            result = self._result(Outcome.OBSERVED, anomaly,
                                  f'知识库命中（{entry.success}/{entry.total} 成功），观察模式不执行')
            result.actions_done = [action.to_dict() for action in plan]
            result.evidence_dir = str(folder)
            self._finish(folder, result)
            return result

        executor = ActionExecutor(self.cfg, self.adapter)
        exec_result = executor.execute(plan)
        page_after = _refresh_page(self.adapter)
        verified = bool(exec_result.ok) and page_after not in ('', 'unknown', 'None')
        self.kb.mark_reuse(entry, verified, now=self.clock())
        result = self._result(Outcome.RECOVERED if verified else Outcome.STOPPED, anomaly,
                              '知识库方案生效' if verified else '知识库方案失效，准备重新分析')
        result.verified = verified
        result.actions_done = [record.to_dict() for record in exec_result.records]
        self.evidence.save_json(folder, 'kb_actions.json', exec_result.to_dict())
        if verified:
            STATS['recovered'] += 1
            self._finish(folder, result)
            return result
        # 失效：继续走 AI（预算允许），不永久相信历史坐标
        return None

    # ---------- 模板 ----------

    def _maybe_template(self, diagnosis: Diagnosis, image: np.ndarray, folder: Path) -> dict | None:
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
            if report.passed and self.cfg.auto:
                manifest = self.templates.promote(candidate, report,
                                                  game_version=str(getattr(self.adapter, 'game_version', '') or ''),
                                                  note=update.get('note', ''))
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
        attempted = list(_state_for(anomaly.key())['attempted'])[-5:]
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

    def _finish(self, folder: Path, result: RecoveryResult) -> None:
        result.evidence_dir = str(folder)
        self.evidence.finish(folder, result)


def _expand_region(region: dict, pad: float) -> dict:
    x = max(0.0, float(region.get('x', 0.0)) - pad)
    y = max(0.0, float(region.get('y', 0.0)) - pad)
    w = min(1.0 - x, float(region.get('w', 0.0)) + pad * 2)
    h = min(1.0 - y, float(region.get('h', 0.0)) + pad * 2)
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
