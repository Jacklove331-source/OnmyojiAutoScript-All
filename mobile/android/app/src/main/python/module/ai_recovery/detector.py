# This Python file uses the following encoding: utf-8

"""异常检测：把「截图变化 + 操作记录 + 页面状态 + 任务类型」综合成一次异常事件。

关键约束（避免误判）：
- 只看截图相同**不**算卡死：战斗/加载/动画会持续变化；静止必须叠加
  「点击过但无效」或「未知页面/加载态超时」或「任务级超长超时」才成立。
- 每个任务有独立超时；战斗类页面额外放宽。
- 检测器只产生事件，不做任何设备操作。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from module.ai_recovery.types import Anomaly


@dataclass
class ClickRecord:
    ts: float
    x: int
    y: int
    name: str = ''
    page: str = ''
    frame_hash: str = ''

    def label(self) -> str:
        return f'{self.name or "click"}@({self.x},{self.y})'


@dataclass
class FrameRecord:
    ts: float
    hash: str
    page: str
    thumb: np.ndarray | None = field(default=None, repr=False)


class ProgressMonitor:
    """轻量进度监视器；`observe()` 每帧调用，开销 < 1ms。"""

    def __init__(self, cfg, task: str, screen_size: tuple[int, int] = (1280, 720),
                 clock=None):
        self.cfg = cfg
        self.task = task or 'Unknown'
        self.screen_size = screen_size
        self.clock = clock or time.time
        self.frames: deque[FrameRecord] = deque(maxlen=48)
        self.clicks: deque[ClickRecord] = deque(maxlen=32)
        self.page_history: deque[tuple[float, str]] = deque(maxlen=40)
        self.signals: dict[str, tuple[float, str]] = {}
        self.last_anomaly: Anomaly | None = None
        self._last_trigger: dict[str, float] = {}
        self._last_any: float = 0.0
        self._task_started = self.clock()
        self._last_page_change = self._task_started
        self._last_frame_change = self._task_started
        self._stable_hash = ''
        self._stable_since = self._task_started
        self._unknown_since: float | None = None

    # ---------- 输入 ----------

    def observe(self, image: np.ndarray | None, page: str | None = None, *, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        page = page or ''
        frame_hash = ''
        thumb = None
        if image is not None:
            try:
                from module.ai_recovery.imageutil import dhash, to_gray
                import cv2
                frame_hash = dhash(image)
                thumb = cv2.resize(to_gray(image), (32, 18), interpolation=cv2.INTER_AREA)
            except Exception:  # noqa: BLE001 - 图像异常不能影响任务
                frame_hash = ''
        record = FrameRecord(ts=now, hash=frame_hash, page=page, thumb=thumb)
        if self.frames and frame_hash and self.frames[-1].hash:
            if not self._same_screen(self.frames[-1], record):
                self._last_frame_change = now
        self.frames.append(record)

        if not self.page_history or self.page_history[-1][1] != page:
            self.page_history.append((now, page))
            self._last_page_change = now
        if page:
            self._unknown_since = None
        elif self._unknown_since is None:
            self._unknown_since = now

        if frame_hash:
            if frame_hash != self._stable_hash:
                self._stable_hash = frame_hash
                self._stable_since = now

    def note_click(self, x: int, y: int, name: str = '', page: str = '', *, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        current_hash = self.frames[-1].hash if self.frames else ''
        self.clicks.append(ClickRecord(ts=now, x=int(x), y=int(y), name=name, page=page,
                                       frame_hash=current_hash))

    def note_signal(self, kind: str, detail: str = '', *, now: float | None = None) -> None:
        """任务代码显式上报的信号：template_miss / ocr_fail / state_mismatch / ..."""
        self.signals[kind] = (self.clock() if now is None else now, detail[:300])

    def reset_progress(self) -> None:
        """恢复成功后调用，避免立刻再次触发。"""
        now = self.clock()
        self.frames.clear()
        self.clicks.clear()
        self.page_history.clear()
        self.signals.clear()
        self._last_frame_change = now
        self._last_page_change = now
        self._stable_since = now
        self._stable_hash = ''
        self._unknown_since = None
        self._last_any = 0.0

    # ---------- 判定 ----------

    def check(self, *, now: float | None = None) -> Anomaly | None:
        now = self.clock() if now is None else now
        cfg = self.cfg.detector
        # 任意异常触发后进入冷却：同一屏状态不再连续产出第二、第三种异常
        if self._last_any and (now - self._last_any) < cfg.cooldown_s:
            return None
        page = self.page_history[-1][1] if self.page_history else ''

        # 1) 未知页面持续过久（加载超时/新界面/弹窗）
        #    必须叠加「画面基本静止」：加载/战斗动画仍在变化时不算卡死，避免正常启动误调 AI。
        if self._unknown_since is not None and page == '':
            lasted = now - self._unknown_since
            static = now - self._last_frame_change
            static_enough = static >= cfg.unknown_page_s * 0.8
            if (lasted >= cfg.unknown_page_s and static_enough) or lasted >= cfg.unknown_page_s * 4:
                if self._cooldown_ok('unknown_page', now):
                    return self._make('unknown_page', page, now,
                                      detail=f'未识别页面持续 {lasted:.0f}s（静止 {static:.0f}s）',
                                      evidence={'unknown_s': round(lasted, 1),
                                                'static_s': round(static, 1),
                                                'clicks': self._recent_clicks(now)})

        # 2) 同一画面长时间无进展（必须有“点击无效”或“加载/登录态”佐证）
        stable = now - self._last_frame_change
        clicks_since = [click for click in self.clicks if click.ts >= self._last_frame_change - 0.5]
        timeout = self.cfg.timeout_for(self.task)
        if stable >= cfg.stale_frame_s and self._cooldown_ok('no_progress', now):
            if len(clicks_since) >= 2:
                return self._make('no_progress', page, now,
                                  detail=f'画面静止 {stable:.0f}s，期间 {len(clicks_since)} 次点击无变化',
                                  evidence={'stable_s': round(stable, 1),
                                            'clicks': [c.label() for c in clicks_since[-6:]]})
            if page.lower() in {'loading', 'login', 'unknown'} and stable >= cfg.unknown_page_s:
                return self._make('no_progress', page, now,
                                  detail=f'{page or "未知"}页静止 {stable:.0f}s',
                                  evidence={'stable_s': round(stable, 1), 'page': page})
            if stable >= max(timeout, cfg.stale_frame_s * 2):
                return self._make('no_progress', page, now,
                                  detail=f'任务 {self.task} 超过 {stable:.0f}s 无画面变化',
                                  evidence={'stable_s': round(stable, 1), 'task_timeout_s': timeout})

        # 3) 同一按钮连续点击无效果
        window_start = now - cfg.repeated_click_window_s
        recent = [click for click in self.clicks if click.ts >= window_start]
        if len(recent) >= cfg.repeated_click_n and self._cooldown_ok('click_no_effect', now):
            groups: dict[tuple[int, int], list[ClickRecord]] = {}
            for click in recent:
                key = (round(click.x / 12) * 12, round(click.y / 12) * 12)
                groups.setdefault(key, []).append(click)
            for point, group in groups.items():
                if len(group) < cfg.repeated_click_n:
                    continue
                first = group[0]
                hashes = {frame.hash for frame in self.frames if frame.ts >= first.ts}
                if len(hashes) <= 1:
                    return self._make('click_no_effect', page, now,
                                      detail=f'坐标 {point} 连续点击 {len(group)} 次，画面无变化',
                                      evidence={'point': list(point), 'clicks': [c.label() for c in group[-6:]],
                                                'names': sorted({c.name for c in group if c.name})})

        # 4) 两个页面来回跳（点击循环）
        pages = [(ts, key) for ts, key in self.page_history if ts >= now - cfg.page_loop_window_s and key]
        if len(pages) >= cfg.page_loop_n and self._cooldown_ok('click_loop', now):
            tail = [key for _, key in pages[-cfg.page_loop_n:]]
            if len(set(tail)) == 2 and all(tail[i] != tail[i + 1] for i in range(len(tail) - 1)):
                return self._make('click_loop', page, now,
                                  detail=f'页面在 {tail[0]} ↔ {tail[1]} 之间反复跳转',
                                  evidence={'pages': tail})

        # 5) 任务代码显式上报的信号
        for kind in ('recovery_failed', 'recognition_conflict', 'template_miss', 'ocr_fail',
                     'state_mismatch', 'load_timeout', 'screen_changed', 'popup'):
            if kind not in self.signals:
                continue
            ts, detail = self.signals[kind]
            if now - ts <= 120 and self._cooldown_ok(kind, now):
                return self._make(kind, page, now, detail=detail,
                                  evidence={'signal_age_s': round(now - ts, 1)})
        return None

    # ---------- 内部 ----------

    def _make(self, kind: str, page: str, now: float, *, detail: str, evidence: dict) -> Anomaly:
        self._last_trigger[kind] = now
        self._last_any = now
        if kind in self.signals:
            self.signals.pop(kind, None)
        evidence = dict(evidence)
        evidence['screen'] = list(self.screen_size)
        evidence['frames'] = len(self.frames)
        evidence['task_elapsed_s'] = round(now - self._task_started, 1)
        evidence['page_history'] = [key for _, key in list(self.page_history)[-8:]]
        anomaly = Anomaly(type=kind, task=self.task, page=page, detail=detail, evidence=evidence)
        self.last_anomaly = anomaly
        return anomaly

    def _cooldown_ok(self, kind: str, now: float) -> bool:
        last = self._last_trigger.get(kind)
        if not last:
            return True
        return (now - last) >= self.cfg.detector.cooldown_s

    def _recent_clicks(self, now: float) -> list[str]:
        return [click.label() for click in self.clicks if now - click.ts <= 60][-6:]

    def _same_screen(self, previous: FrameRecord, current: FrameRecord) -> bool:
        if previous.hash == current.hash:
            return True
        if previous.thumb is None or current.thumb is None:
            return False
        diff = float(np.abs(previous.thumb.astype(np.float32) - current.thumb.astype(np.float32)).mean() / 255.0)
        return diff < (1.0 - self.cfg.detector.similar_ratio)
