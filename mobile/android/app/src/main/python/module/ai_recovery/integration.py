# This Python file uses the following encoding: utf-8

"""OAS 接入层：BaseTask/GameUi 的 mixin 与设备适配器。

- ``AiRecoveryMixin`` 只加方法，不覆盖 BaseTask 既有行为；
- 每帧 ``screenshot()`` 里做一次极轻量的进度采样，命中异常才进入 AI；
- 关闭 AI 时只有一次实例内 config 查询 + 一个布尔判断，零 API、零点击；
- 所有异常都被吞掉并记日志，绝不让自愈层把原任务带崩。
"""

from __future__ import annotations

import time
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

        warning = error = info

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
        return TaskAdapter.current_page(self)

    def known_pages(self) -> list[str]:
        try:
            from tasks.GameUi.registry import PageRegistry
            PageRegistry.load_all_pages()
            keys = [page.key for page in PageRegistry.all()]
            # 优先当前任务目录的页面 + 全局页面
            return keys[:120]
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

    def ai_tick(self) -> None:
        try:
            cfg = getattr(self, '_ai_cfg', None)
            if cfg is None:
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
        """未知页兜底：先试已通过验证的 AI 图色模板（比调用 API 便宜且已验证）。"""
        try:
            cfg = self.ai_config()
            if not (cfg.enabled and cfg.auto and cfg.templates.enabled):
                return False
            from module.ai_recovery.templates import TemplateLibrary
            from module.ai_recovery.imageutil import change_ratio
            image = getattr(self.device, 'image', None)
            if image is None:
                return False
            lib = TemplateLibrary(cfg, config_name=cfg.config_name)
            hit = lib.match_active(image)
            if not hit:
                return False
            x, y = hit['center']
            self.device.click(x, y, control_check=False, control_name=f'AI_TPL_{hit["name"]}')
            time.sleep(0.8)
            self.device.screenshot()
            getter = getattr(self, 'get_current_page', None)
            page = None
            if callable(getter):
                try:
                    page = getter(skip_first_screenshot=True)
                except Exception:  # noqa: BLE001
                    page = None
            changed = change_ratio(image, self.device.image) >= cfg.safety.verify_change_ratio
            logger.info(f'[AI] 已学模板命中 {hit["name"]} score={hit["score"]:.3f} '
                        f'center={(x, y)} page={page} changed={changed}')
            return bool(page is not None or changed)
        except Exception as error:  # noqa: BLE001
            logger.debug(f'[AI] 已学模板尝试失败：{type(error).__name__}: {error}')
            return False

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
            monitor.note_click(x, y, name=name, page=TaskAdapter.current_page(self))
        except Exception:  # noqa: BLE001
            pass

