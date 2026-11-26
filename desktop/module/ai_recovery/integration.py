# This Python file uses the following encoding: utf-8

"""OAS 接入层：BaseTask/GameUi 的 mixin 与设备适配器。

- ``AiRecoveryMixin`` 只加方法，不覆盖 BaseTask 既有行为；
- 每帧 ``screenshot()`` 里做一次极轻量的进度采样，命中异常才进入 AI；
- 关闭 AI 时只有一次实例内 config 查询 + 一个布尔判断，零 API、零点击；
- 所有异常都被吞掉并记日志，绝不让自愈层把原任务带崩。
"""

from __future__ import annotations

import time
import re
from typing import Any

from module.ai_recovery.anomaly_builder import build_anomaly
from module.ai_recovery.config import AiRecoveryConfig, load_config
from module.ai_recovery.detector import ProgressMonitor
from module.ai_recovery.types import Anomaly, Outcome, RecoveryResult

try:
    from module.logger import logger
except Exception:  # pragma: no cover
    class _Logger:
        def info(self, *args, **kwargs):
            print('[AI]', *args)

        warning = error = debug = info

    logger = _Logger()


class TaskAdapter:
    """把 OAS 的 BaseTask/GameUi 包装成自愈层需要的窄接口。"""

    def __init__(self, task, monitor: ProgressMonitor | None = None):
        self.task = task
        self.monitor = monitor
        self.config_name = str(getattr(getattr(task, 'config', None), 'config_name', 'oas'))
        self._click_index = 0
        self._page_cache = ''

    # ---------- 基本属性 ----------

    @property
    def task_name(self) -> str:
        try:
            return str(self.task.get_task_name())
        except Exception:  # noqa: BLE001
            return type(self.task).__name__

    @property
    def device_id(self) -> str:
        script = getattr(getattr(self.task, 'config', None), 'script', None)
        serial = getattr(getattr(script, 'device', None), 'serial', '')
        return str(serial or self.config_name)

    @property
    def screen_size(self) -> tuple[int, int]:
        image = getattr(self.task.device, 'image', None)
        if image is not None:
            return int(image.shape[1]), int(image.shape[0])
        return 1280, 720

    @property
    def game_version(self) -> str:
        version = getattr(self.task, '_ai_game_version', '')
        return str(version or '')

    # ---------- 设备动作 ----------

    def screenshot(self):
        self.task.device.screenshot()
        return self.task.device.image

    def click(self, x: int, y: int) -> None:
        self._click_index += 1
        name = f'AI_RECOVERY_{self._click_index}'
        try:
            # 用唯一 control_name 避免污染原任务的点击记录/次数保护
            self.task.device.click(x, y, control_check=False, control_name=name)
        except TypeError:
            self.task.device.click(x, y, control_check=False)

    def back(self) -> None:
        self.task.device.adb_shell(['input', 'keyevent', '4'])

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, float(seconds)))

    def close_popup_local(self) -> bool:
        """只跑原有未知页关闭动作，不再触发 AI，避免递归。"""
        closer = getattr(self.task, 'close_unknown_pages', None)
        if not callable(closer):
            return False
        try:
            return bool(closer(skip_first_screenshot=False, allow_ai=False))
        except Exception:  # noqa: BLE001
            return False

    def goto_page(self, key: str) -> bool:
        goto = getattr(self.task, 'goto_page', None)
        if not callable(goto) or not key:
            return False
        try:
            from tasks.GameUi.registry import PageRegistry
            PageRegistry.load_all_pages()
            page = PageRegistry.get(key)
            if page is None:
                return False
            return bool(goto(page))
        except Exception as error:  # noqa: BLE001
            logger.warning(f'[AI] goto_page({key}) 失败：{type(error).__name__}')
            return False

    def current_page(self) -> str:
        navigator = getattr(self.task, 'navigator', None)
        page = getattr(navigator, 'current_page', None) if navigator is not None else None
        if page is not None:
            return str(getattr(page, 'key', page) or '')
        return self._page_cache

    def is_main_page(self) -> bool:
        """庭院页禁止返回键：会弹出「退出游戏」确认框。"""
        return TaskAdapter.current_page(self) == 'page_main'

    def refresh_page(self) -> str:
        """AI 动作后强制刷新页面识别（不复用旧缓存），用于判断是否真的恢复。"""
        getter = getattr(self.task, 'get_current_page', None)
        if callable(getter):
            try:
                page = getter(skip_first_screenshot=False)
                if page is not None:
                    key = str(getattr(page, 'key', page) or '')
                    self._page_cache = key
                    return key
            except Exception:  # noqa: BLE001
                pass
            self._page_cache = ''
            return ''
        return TaskAdapter.current_page(self)

    def known_pages(self) -> list[str]:
        try:
            from tasks.GameUi.registry import PageRegistry
            PageRegistry.load_all_pages()
            keys = [page.key for page in PageRegistry.all()]
            task_key = re.sub(r'[^a-z0-9]', '', self.task_name.lower())
            matching = [key for key in keys if task_key and
                        task_key in re.sub(r'[^a-z0-9]', '', key.lower())]
            priority = [self.current_page(), 'page_main', *matching, *keys]
            return list(dict.fromkeys(key for key in priority if key))[:120]
        except Exception:  # noqa: BLE001
            return []

    def recent_actions(self) -> list[str]:
        actions: list[str] = []
        try:
            record = getattr(self.task.device, 'click_record', None)
            if record:
                actions.extend([str(item) for item in list(record)[-6:]])
        except Exception:  # noqa: BLE001
            pass
        if self.monitor is not None:
            try:
                actions.extend([click.label() for click in list(self.monitor.clicks)[-4:]])
            except Exception:  # noqa: BLE001
                pass
        return actions[-8:]

    def state_machine(self) -> str:
        page = TaskAdapter.current_page(self) or '未知'
        return f'task={self.task_name}; page={page}'

    def notify(self, text: str) -> None:
        logger.warning(f'[AI] {text}')

    def verify_task_progress(self, anomaly, diagnosis) -> bool:
        checker = getattr(self.task, 'ai_verify_recovery_progress', None)
        return callable(checker) and checker(anomaly, diagnosis) is True

    # ---------- 本地恢复 ----------

    def local_recovery(self, kind: str) -> bool:
        device = self.task.device
        if kind in {'device_disconnected', 'adb_offline', 'adb_missing'}:
            recover = getattr(device, 'recover_connection', None)
            if callable(recover):
                try:
                    return bool(recover(stage='ai_recovery'))
                except Exception:  # noqa: BLE001
                    return False
            return False
        if kind == 'screenshot_failed':
            fallback = getattr(device, '_fallback_to_adb_screenshot', None)
            if callable(fallback):
                try:
                    if fallback('ai_recovery'):
                        return True
                except Exception:  # noqa: BLE001
                    pass
            try:
                device.screenshot()
                return True
            except Exception:  # noqa: BLE001
                return False
        if kind == 'game_not_running':
            start = getattr(device, 'app_start', None)
            if callable(start):
                try:
                    start()
                    return bool(device.app_is_running())
                except Exception:  # noqa: BLE001
                    return False
        return False


class AiRecoveryMixin:
    """BaseTask 的 AI 自愈能力（惰性初始化，默认关闭）。"""

    _ai_cfg: AiRecoveryConfig | None = None
    _ai_cfg_at: float = 0.0
    _ai_monitor: ProgressMonitor | None = None
    _ai_busy: bool = False
    _ai_last_result = None
    _ai_game_version: str = ''

    # ---------- 配置 ----------

    def ai_config(self, *, force: bool = False) -> AiRecoveryConfig:
        now = time.time()
        cached = getattr(self, '_ai_cfg', None)
        if cached is not None and not force and now - float(getattr(self, '_ai_cfg_at', 0.0)) < 60.0:
            return cached
        try:
            config_name = str(getattr(getattr(self, 'config', None), 'config_name', 'oas'))
        except Exception:  # noqa: BLE001
            config_name = 'oas'
        cfg = load_config(config_name=config_name, force=force)
        self._ai_cfg = cfg
        self._ai_cfg_at = now
        return cfg

    def ai_monitor(self) -> ProgressMonitor:
        monitor = getattr(self, '_ai_monitor', None)
        if monitor is None:
            cfg = self.ai_config()
            image = getattr(self.device, 'image', None)
            size = (int(image.shape[1]), int(image.shape[0])) if image is not None else (1280, 720)
            monitor = ProgressMonitor(cfg, self.get_task_name(), size)
            self._ai_monitor = monitor
        return monitor

    # ---------- 每帧轻量采样 ----------

    def ai_record_task_outcome(self, success: bool) -> None:
        """A successful page transition alone does not prove the task recovered."""
        try:
            cfg = self.ai_config()
            if cfg.enabled and cfg.code_repair.enabled:
                from module.ai_recovery.repair_runtime import task_feedback
                task_feedback(cfg.config_name,self.get_task_name(),bool(success))
            if not (cfg.enabled and cfg.learning.enabled):
                return
            from module.ai_recovery.evidence import EvidenceStore
            from module.ai_recovery.learning import LearningMemory
            LearningMemory(cfg, EvidenceStore(cfg.config_name).root).task_finished(
                self.get_task_name(), success=bool(success))
        except Exception as error:
            logger.warning(f'[AI学习] 任务反馈失败：{type(error).__name__}')

    def ai_handle_task_error(self, error: Exception) -> dict | None:
        """Recover a UI ScriptError, or quarantine an unsupported code error."""
        cfg = self.ai_config()
        if not (cfg.enabled and cfg.auto and cfg.learning.enabled):
            return None
        import hashlib
        import traceback
        from pathlib import Path
        from module.ai_recovery.evidence import EvidenceStore
        from module.ai_recovery.learning import LearningMemory
        from module.ai_recovery.repair_queue import enqueue
        adapter = TaskAdapter(self)
        source, revision, source_path = '', '', ''
        frames = traceback.extract_tb(error.__traceback__)
        from module.ai_recovery.config import ROOT
        from module.ai_recovery.repair_context import source_path as _valid_source_path
        trace_frames = []
        for item in frames[-10:]:
            try:
                relative = Path(item.filename).resolve().relative_to(ROOT.resolve()).as_posix()
                trace_frames.append({'path':relative,'function':item.name,'line':item.lineno})
            except ValueError:
                continue
        if frames:
            # 从最内层往回找第一个合法业务源码帧（tasks/ 或 module/ 且非受保护路径），
            # 避免 toolkit/Lib/site-packages/... 被误选为维修目标；找不到再退回最内层帧。
            chosen_frame, chosen_relative = None, ''
            for item in reversed(frames):
                try:
                    relative = Path(item.filename).resolve().relative_to(ROOT.resolve()).as_posix()
                    _valid_source_path(ROOT, relative)
                    chosen_frame, chosen_relative = item, relative
                    break
                except (ValueError, OSError):
                    continue
            if chosen_frame is None:
                chosen_frame = frames[-1]
                try:
                    chosen_relative = Path(chosen_frame.filename).resolve().relative_to(ROOT.resolve()).as_posix()
                except ValueError:
                    chosen_relative = ''
            frame = chosen_frame
            source = Path(frame.filename).name + ':' + frame.name
            try:
                revision = hashlib.sha256(Path(frame.filename).read_bytes()).hexdigest()[:16]
            except OSError:
                pass
            source_path = chosen_relative
        ui_error = type(error).__name__ == 'ScriptError'
        target = 'page_main' if ui_error and 'page_main' in adapter.known_pages() else ''
        anomaly = build_anomaly('script_error' if ui_error else 'task_exception', self.get_task_name(),
                                adapter.current_page(), detail=str(error), evidence={
                                    'exception_type':type(error).__name__, 'source':source,
                                    'source_revision':revision, 'expected_page':target,
                                    'source_path':source_path,'frames':trace_frames})
        evidence = EvidenceStore(cfg.config_name)
        memory = LearningMemory(cfg,evidence.root)
        if ui_error:
            result = self.ai_recover(anomaly)
            if result is None:
                return None
        else:
            admission = memory.begin(anomaly)
            result = RecoveryResult(Outcome.STOPPED if admission['allowed'] else Outcome.SKIPPED,
                                    anomaly.incident_id, message='代码异常已记录，延期当前任务，等待修复')
            folder = evidence.begin(anomaly.incident_id,anomaly)
            result.evidence_dir = str(folder)
            memory.finish(anomaly,result)
            enqueue(evidence.root,cfg.config_name,anomaly,result)
            evidence.finish(folder,result)
        return {'retry_after_s':memory.retry_after(anomaly), 'message':result.message,
                'outcome':result.outcome.value, 'verified':result.verified}

    def ai_tick(self) -> None:
        try:
            cfg = self.ai_config()
            if not cfg.enabled:
                return
            if getattr(self, '_ai_busy', False):
                return
            monitor = self.ai_monitor()
            monitor.cfg = cfg
            image = getattr(self.device, 'image', None)
            page = ''
            navigator = getattr(self, 'navigator', None)
            current = getattr(navigator, 'current_page', None) if navigator is not None else None
            if current is not None:
                page = str(getattr(current, 'key', current) or '')
            monitor.observe(image, page)
            anomaly = monitor.check()
            if anomaly is None:
                return
            self.ai_recover(anomaly)
        except Exception as error:  # noqa: BLE001 - 自愈层绝不能影响主任务
            logger.debug(f'[AI] tick 异常已忽略：{type(error).__name__}: {error}')

    # ---------- 接管 ----------

    def ai_recover(self, anomaly: Anomaly | None = None, *, kind: str = '', detail: str = '') -> RecoveryResult | None:
        if getattr(self, '_ai_busy', False):
            return None
        cfg = self.ai_config()
        if not cfg.enabled:
            return None
        self._ai_busy = True
        try:
            monitor = self.ai_monitor()
            if anomaly is None:
                page = ''
                navigator = getattr(self, 'navigator', None)
                current = getattr(navigator, 'current_page', None) if navigator is not None else None
                if current is not None:
                    page = str(getattr(current, 'key', current) or '')
                anomaly = build_anomaly(kind or 'unknown_page', self.get_task_name(), page,
                                        detail=detail, evidence={'source': 'explicit'})
            from module.ai_recovery.orchestrator import AiRecovery
            adapter = TaskAdapter(self, monitor=monitor)
            recovery = AiRecovery(cfg, adapter)
            result = recovery.handle(anomaly)
            self._ai_last_result = result
            logger.info(f'[AI] {anomaly.task}/{anomaly.type} -> {result.outcome.value}: {result.message}')
            if result.outcome == Outcome.RECOVERED:
                monitor.reset_progress()
            return result
        except Exception as error:  # noqa: BLE001
            logger.warning(f'[AI] 自愈流程异常（已忽略，交回原任务）：{type(error).__name__}: {error}')
            return None
        finally:
            self._ai_busy = False

    def ai_try_learned_template(self) -> bool:
        """Reuse only a verified, scoped click plan through the normal action executor."""
        if getattr(self, '_ai_busy', False):
            return False
        busy_before = getattr(self, '_ai_busy', False)
        try:
            cfg = self.ai_config()
            if not (cfg.enabled and cfg.auto and cfg.templates.enabled):
                return False
            self._ai_busy = True
            from module.ai_recovery.actions import ActionExecutor
            from module.ai_recovery.evidence import EvidenceStore
            from module.ai_recovery.runtime import RecoveryLease
            from module.ai_recovery.templates import TemplateLibrary
            from module.ai_recovery.types import Action, Diagnosis
            from module.ai_recovery.learning import LearningMemory
            from module.ai_recovery.verification import fresh_page, verify_recovery
            adapter = TaskAdapter(self)
            evidence = EvidenceStore(cfg.config_name)
            with RecoveryLease(evidence.root, adapter.device_id) as lease:
                if not lease.acquired:
                    return False
                before_page = fresh_page(adapter)
                image = adapter.screenshot()
                if image is None:
                    return False
                lib = TemplateLibrary(cfg, config_name=cfg.config_name)
                hit = lib.match_active(image, task=adapter.task_name,
                                       game_version=adapter.game_version, for_execution=True)
                if not hit:
                    return False
                replay = hit.get('replay') or {}
                if (not replay.get('verified') or replay.get('disabled')
                        or not replay.get('expected_page') or hit.get('task') != adapter.task_name):
                    return False
                source_page = replay.get('source_page')
                if source_page and source_page not in ('unknown', 'None') and source_page != before_page:
                    return False
                action = Action.from_dict(replay.get('action') or {})
                if action.type != 'click':
                    return False
                offset = replay.get('offset')
                if not isinstance(offset, list) or len(offset) != 2:
                    return False
                x, y, w, h = hit['rect']
                action.x = (x + float(offset[0]) * w) / image.shape[1]
                action.y = (y + float(offset[1]) * h) / image.shape[0]
                anomaly = Anomaly(type='template_replay', task=adapter.task_name, page=before_page,
                                  detail=str(hit.get('name') or ''),
                                  evidence={'expected_page':replay['expected_page']})
                memory = LearningMemory(cfg,evidence.root)
                admission = memory.begin(anomaly)
                diagnosis = Diagnosis(page=before_page,page_known=True,confidence=1.0,
                                      cause='validated template replay', actions=[action],
                                      expected_page=replay['expected_page'])
                if not admission['allowed'] or not memory.plan_allowed(admission['case_id'],diagnosis):
                    return False
                folder = evidence.begin(anomaly.incident_id, anomaly)
                evidence.save_png(folder, 'before.png', image)
                executed = ActionExecutor(cfg, adapter).execute([action])
                proof = verify_recovery(cfg, adapter, anomaly, executed, page_before=before_page,
                                        expected_page=replay['expected_page'])
                lib.mark_replay(hit['task'], hit['name'], proof.verified)
                result = RecoveryResult(
                    Outcome.RECOVERED if proof.verified else Outcome.STOPPED,
                    anomaly.incident_id, message=f'模板复用：{proof.reason}',
                    verified=proof.verified, verification=proof.to_dict(),
                    diagnosis=diagnosis,
                    actions_done=[record.to_dict() for record in executed.records],
                    evidence_dir=str(folder))
                if not proof.verified:
                    memory.plan_failed(admission['case_id'],diagnosis,proof.reason)
                memory.finish(anomaly,result)
                evidence.finish(folder, result)
                return proof.verified
        except Exception as error:  # noqa: BLE001
            logger.debug(f'[AI] 已学模板尝试失败：{type(error).__name__}: {error}')
            return False
        finally:
            self._ai_busy = busy_before

    def _ai_on_device_stuck(self) -> bool:
        """Device.stuck_record_check() 在抛 GameStuckError 前的最后一道 AI 兜底。

        OAS 的 60s 卡死计时器比异常检测器的 no_progress（90s/需点击证据）更早触发；
        不接这个点，Duel 这类“已知页 + 零点击等待”会直接失败 3 次退出。
        """
        try:
            cfg = self.ai_config()
            if not (cfg.enabled and cfg.auto):
                return False
            result = self.ai_recover(kind='device_stuck',
                                     detail='OAS 60s 卡死计时器触发，本地恢复未解决')
            if result is not None and getattr(result, 'soft_fail', False):
                # 预算/冷却这类可恢复失败：明确说明「没有修好，只是这个小时没额度了」
                logger.warning(f'[AI] 自愈暂时不可用（soft-fail，建议 {result.retry_after_s:.0f}s 后重试）：'
                               f'{result.message}')
            return bool(result and result.outcome == Outcome.RECOVERED)
        except Exception as error:  # noqa: BLE001
            logger.warning(f'[AI] device stuck 兜底异常：{type(error).__name__}: {error}')
            return False

    def ai_recover_unknown_page(self) -> bool:
        """未知页本地动作全部失败后调用；只有真正恢复才返回 True。"""
        try:
            result = self.ai_recover(kind='unknown_page',
                                     detail='本地未知页关闭动作全部失败，页面仍未识别')
            return bool(result and result.outcome == Outcome.RECOVERED)
        except Exception:  # noqa: BLE001
            return False

    # ---------- 信号上报（供任务/导航调用） ----------

    def ai_signal(self, kind: str, detail: str = '') -> None:
        try:
            cfg = getattr(self, '_ai_cfg', None) or self.ai_config()
            if not cfg.enabled:
                return
            self.ai_monitor().note_signal(kind, detail)
        except Exception:  # noqa: BLE001
            pass

    def ai_note_click(self, x: int, y: int, name: str = '') -> None:
        try:
            cfg = getattr(self, '_ai_cfg', None) or self.ai_config()
            if not cfg.enabled:
                return
            monitor = self.ai_monitor()
            monitor.note_click(x, y, name=name, page=TaskAdapter(self).current_page())
        except Exception:  # noqa: BLE001
            pass

