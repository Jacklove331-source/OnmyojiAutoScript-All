from __future__ import annotations

import random
from module.base.decorator import run_once
from tasks.Exploration.assets import ExplorationAssets
from tasks.GlobalGame.assets import GlobalGameAssets

"""GameUi 导航运行时。"""

import heapq
import inspect
import time
from pathlib import Path
from time import sleep

import numpy as np

from module.atom.click import RuleClick
from module.atom.gif import RuleGif
from module.atom.image import RuleImage
from module.atom.list import RuleList
from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.exception import GamePageUnknownError, GameNotRunningError
from module.logger import logger
from module.ocr.base_ocr import OcrMode
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets
from tasks.Component.GeneralBuff.assets import GeneralBuffAssets
from tasks.GameUi.action import ActionSequence, ConditionalAction
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.common import infer_tasks_category_from_parts, infer_tasks_category_from_path
from tasks.GameUi.matcher import all_of, not_, collect_rule_images
from tasks.Component.preset_list import foreground_rule
from tasks.GameUi.chess_battle import ChessBattleNavigationMixin
from tasks.GameUi.page_definition import Page, Transition, sort_pages_by_priority
from tasks.GameUi.registry import PageRegistry
from tasks.GameUi.session import NavigatorSession
from tasks.SixRealms.assets import SixRealmsAssets
from tasks.Restart.assets import RestartAssets
from tasks.base_task import BaseTask


class StrictKeywordOcr(RuleOcr):
    """只接受「单个文本框包含关键词」的 Full 模式 OCR。

    基线 `Full.ocr_full` 在整帧拼接文本里没有关键词时，会退化成逐字匹配：
    只要 ROI 里任意一个文本框含关键词中的某一个汉字，就算命中。实测日志
    （11-17 11:36、11-19 19:57、11-20 21:03）里 `['町中']`、`['15']`、`['切换']`、
    `[]` 这些帧都被判成「退出游戏确认框」，随后点到屏幕中间的取消按钮把页面点乱。

    这里去掉逐字兜底：空结果、短文本（`15`）、不相关文本（`町中`）一律返回
    `(0, 0, 0, 0)`，只有真正含关键词的文本框才算命中。
    """

    def ocr(self, image, keyword: str | None = None, exact: bool = False):
        if self.mode != OcrMode.FULL:
            return super().ocr(image, keyword=keyword, exact=exact)
        return self._strict_full_ocr(image, keyword=keyword, exact=exact)

    def _strict_full_ocr(self, image, keyword: str | None = None, exact: bool = False):
        target = keyword or self.keyword
        if not target:
            return 0, 0, 0, 0
        boxed_results = self.detect_and_ocr(image)
        if not boxed_results:
            logger.info(f'OCR [{self.name}] no text in ROI, treat as absent')
            return 0, 0, 0, 0
        texts = [result.ocr_text for result in boxed_results]
        for result in boxed_results:
            text = (result.ocr_text or '').strip()
            if not text:
                continue
            if exact:
                if text != target:
                    continue
            elif target not in text:
                continue
            box = result.box
            x1, y1 = int(box[0, 0]), int(box[0, 1])
            x2, y2 = int(box[2, 0]), int(box[2, 1])
            self.area = [x1 + self.roi[0], y1 + self.roi[1], x2 - x1, y2 - y1]
            logger.info(f'OCR [{self.name}] strict keyword "{target}" matched "{text}" at {self.area}')
            return self.area
        logger.info(f'OCR [{self.name}] strict keyword "{target}" not matched in {texts}')
        return 0, 0, 0, 0


class GameUi(ChessBattleNavigationMixin, BaseTask, GameUiAssets):
    """页面识别、导航与未知页恢复的统一入口。"""

    REPEATED_TRANSITION_FAILURE_THRESHOLD = 3

    # 全局未知页关闭动作，所有任务共享。
    DEFAULT_UNKNOWN_CLOSERS = [
        GlobalGameAssets.I_UI_BACK_RED,
        GlobalGameAssets.I_CHAT_CLOSE_BUTTON,
        ActivityShikigamiAssets.I_SKIP_BUTTON,
        GlobalGameAssets.I_UI_CONFIRM_SAMLL,
        GlobalGameAssets.I_UI_CONFIRM,
        ExplorationAssets.I_E_EXIT_CONFIRM,
        GameUiAssets.I_BACK_DAILY,
        GlobalGameAssets.I_UI_BACK_YELLOW,
        SixRealmsAssets.I_EXIT_SIXREALMS,
        GlobalGameAssets.I_UI_BACK_BLUE,
        GlobalGameAssets.I_UI_BACK_CIRCLE,
    ]

    # 「退出游戏」确认框：庭院或任务页误触返回键时弹出；必须点「取消」，
    # 点「确认」会直接退出游戏，所以放在未知页关闭动作里优先处理。
    # 判据必须是「单框含关键词」，不能用 Full 模式的逐字兜底（见 StrictKeywordOcr）。
    O_EXIT_GAME_CONFIRM = StrictKeywordOcr(
        roi=(280, 220, 720, 240),
        area=(0, 0, 1280, 720),
        mode="Full",
        method="Default",
        keyword="退出游戏",
        name="exit_game_confirm",
    )
    O_EXIT_GAME_CANCEL = StrictKeywordOcr(
        roi=(280, 350, 720, 180),
        area=(0, 0, 1280, 720),
        mode="Full",
        method="Default",
        keyword="取消",
        name="exit_game_cancel",
    )
    C_EXIT_GAME_CANCEL = RuleClick(
        roi_front=(760, 425, 100, 40),
        roi_back=(760, 425, 100, 40),
        name="exit_game_cancel",
    )
    EXIT_GAME_CANCEL_ROI = (760, 425, 100, 40)
    EXIT_GAME_CONFIRM_MAX_CLICKS = 2

    # 全屏浮层（「悬赏封印」「活动」这类盖满整屏的二级界面）压在页面上时，
    # 所有页面模板都匹配不到，老逻辑会直接判 Game page unknown。
    # 关闭判据有两组，都不需要新增模板 png：
    # 1) 标题带 OCR 关键词（单框包含，见 StrictKeywordOcr）；
    # 2) 右上角红色关闭叉的颜色几何（实地样本 log/error/1795179872096 中心约 (1210, 109)）。
    OVERLAY_CLOSE_ATTEMPTS = 3
    OVERLAY_CLOSE_CROSS_ROI = (1170, 60, 110, 100)
    OVERLAY_CLOSE_CROSS_MIN_PIXELS = 120
    O_OVERLAY_TITLE_BOUNTY = StrictKeywordOcr(
        roi=(500, 30, 300, 70),
        area=(0, 0, 1280, 720),
        mode="Full",
        method="Default",
        keyword="悬赏封印",
        name="overlay_title_bounty",
    )
    OVERLAY_TITLE_RULES = (O_OVERLAY_TITLE_BOUNTY,)
    C_OVERLAY_CLOSE_X = RuleClick(
        roi_front=(1204, 95, 18, 18),
        roi_back=(1204, 95, 18, 18),
        name="overlay_close_x",
    )
    # 未知页关闭全部失败后，再给几轮「退回可用页面并复判」的机会，不直接判未知页。
    PAGE_RECOVERY_ROUNDS = 2
    # 返回键总量上限：避免「按了也没用」时被上层当成一直在进展（每次 reset 超时）
    # 而无限按下去；一旦重新识别到主界面/町中，这个预算会重置。
    MAX_UNKNOWN_PAGE_BACK_PRESSES = 4

    def __init__(self, config, device):
        """初始化 GameUi 运行时。

        Args:
            config: 当前任务配置对象。
            device: 当前设备对象。
        """

        super().__init__(config, device)
        PageRegistry.load_all_pages()
        # 当前任务的导航 session，持有页面快照和运行期状态。
        self.navigator = NavigatorSession(task_category=self._infer_task_category())
        self.navigator.bootstrap(PageRegistry.all())
        # Town shares the bottom bar with the courtyard. Its own marker
        # disambiguates the two without requiring a floating exploration
        # lantern to match at exactly the same moment on animated costumes.
        courtyard = self.navigator.pages.get('page_main')
        if courtyard is not None:
            courtyard.recognizer = all_of(
                courtyard.recognizer, not_(self.I_CHECK_TOWN),
                lambda task: foreground_rule(task, task.I_CHECK_MAIN),
            )

    def _infer_task_category(self) -> str:
        """根据当前任务模块路径推断任务分类。

        Returns:
            当前任务所属分类名。
        """

        module_parts = self.__class__.__module__.split(".")
        if "tasks" in module_parts:
            return infer_tasks_category_from_parts(
                module_parts,
                module_parts.index("tasks"),
                component_use_child=True,
            )

        # 调度器通过固定模块名 `script_task` 动态加载任务时，__module__ 无法反映真实路径，
        # 因此这里回退到类定义文件路径推断任务分类。
        try:
            class_file = Path(inspect.getfile(self.__class__)).resolve()
        except (TypeError, OSError):
            return "global"

        return infer_tasks_category_from_path(class_file, component_use_child=True)

    def _default_detect_categories(self) -> set[str]:
        """获取普通当前页识别时允许扫描的分类集合。"""

        return {"global", self.navigator.task_category}

    def _navigation_detect_categories(self, destination: Page) -> set[str]:
        """获取导航时用于识别当前页的分类集合。

        Args:
            destination: 目标页面。

        Returns:
            当前导航允许识别的页面分类集合。
        """

        return {"global", self.navigator.task_category, destination.category}

    def _navigation_graph_categories(self, destination: Page, current: Page | None) -> set[str]:
        """获取导航时允许参与建图的分类集合。

        Args:
            destination: 目标页面。
            current: 当前页面。

        Returns:
            当前导航允许参与路径规划的页面分类集合。
        """

        categories = {"global", destination.category}
        if current is not None:
            categories.add(current.category)
        return categories

    def _invoke_callable(self, target):
        """调用用户自定义动作函数。

        Args:
            target: 自定义可调用对象。

        Returns:
            调用结果。
        """

        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError):
            return target(self)
        if len(signature.parameters) == 0:
            return target()
        return target(self)

    def match_page_once(self, page: Page) -> bool:
        """执行一次页面识别。

        Args:
            page: 待识别页面。

        Returns:
            单次识别是否成功。
        """

        if page.recognizer is None:
            return False
        return bool(page.recognizer.evaluate(self))

    def confirm_page(self, page: Page, skip_first_screenshot: bool = True) -> bool:
        """使用两帧稳定判定确认页面。

        Args:
            page: 待确认页面。
            skip_first_screenshot: 是否复用当前截图。

        Returns:
            两帧连续识别是否成功。
        """

        self.maybe_screenshot(skip_first_screenshot)
        if not self.match_page_once(page):
            return False
        self.screenshot()
        return self.match_page_once(page)

    def _detect_current_page(
        self,
        *,
        skip_first_screenshot: bool = True,
        categories: set[str] = None,
    ) -> Page | None:
        """在给定分类集合内识别当前页面。

        Args:
            skip_first_screenshot: 是否复用当前截图。
            categories: 允许参与识别的页面分类集合。

        Returns:
            稳定识别到的当前页面；识别失败时返回 `None`。
        """

        pages = self.navigator.all_pages(categories)
        return GameUi._detect_pages(self, pages, skip_first_screenshot=skip_first_screenshot)

    def _detect_current_page_with_fallback(
        self,
        *,
        skip_first_screenshot: bool = True,
        categories: set[str] = None,
        context: str = "current_page",
    ) -> Page | None:
        """先按作用域识别当前页面，失败后再回退到 session 全量页面。

        Args:
            skip_first_screenshot: 是否复用当前截图。
            categories: 第一轮识别允许参与的页面分类集合。
            context: 当前识别调用场景，用于日志输出。

        Returns:
            稳定识别到的当前页面；两轮识别都失败时返回 `None`。
        """

        page = self._detect_current_page(
            skip_first_screenshot=skip_first_screenshot,
            categories=categories,
        )
        if page is not None or not categories:
            return page

        sorted_categories = sorted(categories)
        page = GameUi._detect_pages(
            self,
            self.navigator.all_pages(),
            skip_first_screenshot=True,
        )
        if page is not None:
            logger.attr("UI", page.name)
            return page

        logger.warning(f"Page detect miss[{context}]: scoped={sorted_categories}")
        return None

    def _detect_pages(
        self,
        pages: list[Page],
        *,
        skip_first_screenshot: bool = True,
    ) -> Page | None:
        """在给定页面集合内识别当前页面。"""

        if not pages:
            return None

        self.maybe_screenshot(skip_first_screenshot)
        GameUi._prepare_page_rule_image_cache(self, pages)
        indexed_candidates = [(index, page) for index, page in enumerate(pages) if self.match_page_once(page)]
        if not indexed_candidates:
            return None

        self.screenshot()
        GameUi._prepare_page_rule_image_cache(self, [page for _, page in indexed_candidates])
        for page in sort_pages_by_priority(indexed_candidates):
            if self.match_page_once(page):
                self.navigator.current_page = page
                logger.attr("UI", page.name)
                return page
        return None

    def _prepare_page_rule_image_cache(self, pages: list[Page]) -> None:
        targets = []
        seen = set()
        for page in pages:
            if page.recognizer is None:
                continue
            for target in collect_rule_images(page.recognizer):
                cache_key = id(target)
                if cache_key in seen:
                    continue
                seen.add(cache_key)
                targets.append(target)
        if targets and hasattr(self, "prepare_appear_cache"):
            self.prepare_appear_cache(targets)

    @staticmethod
    def _action_name(action) -> str:
        """将动作对象转换为便于日志输出的名称。

        Args:
            action: 目标动作对象。

        Returns:
            动作名称字符串。
        """

        if isinstance(action, (RuleImage, RuleGif, RuleOcr, RuleList, RuleClick)):
            return action.name
        if isinstance(action, ConditionalAction):
            return f"ConditionalAction({GameUi._action_name(action.action)})"
        if isinstance(action, ActionSequence):
            return f"ActionSequence[{len(action.actions)}]"
        if callable(action):
            return str(getattr(action, "__name__", action.__class__.__name__))
        return repr(action)

    def _execute_action(self, action, *, interval: float | None = None, skip_first_screenshot: bool = True) -> bool:
        """统一执行动作对象。

        Args:
            action: 待执行动作。
            interval: 点击间隔。
            skip_first_screenshot: 是否复用当前截图。

        Returns:
            动作是否成功执行。
        """

        self.maybe_screenshot(skip_first_screenshot)

        if isinstance(action, ConditionalAction):
            if not action.condition.evaluate(self):
                return False
            return self._execute_action(action.action, interval=interval, skip_first_screenshot=False)

        if isinstance(action, ActionSequence):
            if not action.actions:
                return False
            results = [
                self._execute_action(item, interval=interval, skip_first_screenshot=(index == 0 and skip_first_screenshot))
                for index, item in enumerate(action.actions)
            ]
            if action.success_index < len(results):
                return results[action.success_index]
            return any(results)

        if isinstance(action, (list, tuple)):
            return self._execute_action(
                ActionSequence(tuple(action)),
                interval=interval,
                skip_first_screenshot=skip_first_screenshot,
            )

        if callable(action) and not isinstance(action, (RuleImage, RuleGif, RuleOcr, RuleList, RuleClick)):
            result = self._invoke_callable(action)
            if isinstance(result, bool):
                return result
            if result is None:
                return False
            return self._execute_action(result, interval=interval, skip_first_screenshot=False)

        if isinstance(action, RuleList):
            return self.list_appear_click(action, interval=interval or 0.8)
        if isinstance(action, (RuleImage, RuleGif)):
            return self.appear_then_click(action, interval=interval)
        if isinstance(action, RuleOcr):
            return self.ocr_appear_click(action, interval=interval)
        if isinstance(action, RuleClick):
            if interval is not None:
                return self.click(action, interval=interval)
            x, y = action.coord()
            self.device.click(x=x, y=y, control_name=action.name)
            return True
        return False

    def _run_hooks(self, hooks: list, *, interval: float = 0.6) -> None:
        """按顺序执行 hook 列表。

        Args:
            hooks: 待执行的 hook 列表。
            interval: hook 点击间隔。
        """

        for index, hook in enumerate(hooks):
            self._execute_action(hook, interval=interval, skip_first_screenshot=(index == 0))

    def _mark_page_entered(self, page: Page) -> None:
        """记录最近一次已触发进入成功 hook 的页面。

        Args:
            page: 当前已进入的页面。
        """

        self.navigator.last_enter_success_page_key = page.key

    def _run_enter_success_hooks_if_needed(self, page: Page) -> None:
        """仅在页面变化时触发进入成功 hook。

        Args:
            page: 当前页面。
        """

        if self.navigator.last_enter_success_page_key == page.key:
            return
        self._run_hooks(page.on_enter_success)
        self._mark_page_entered(page)

    def _allowed_pages_for_navigation(self, destination: Page, current: Page | None) -> dict[str, Page]:
        """构建本轮导航允许访问的页面集合。

        Args:
            destination: 目标页面。
            current: 当前页面。

        Returns:
            以页面 key 为索引的允许页面字典。
        """
        categories = self._navigation_graph_categories(destination, current)
        categories.add(self.navigator.task_category)
        pages = {page.key: page for page in self.navigator.all_pages(categories)}
        if current is not None:
            pages[current.key] = current
        pages[destination.key] = destination
        return pages

    def _build_path(self, current: Page, destination: Page) -> list[Transition]:
        """在允许页面集合内构建最小代价路径。

        Args:
            current: 当前页面。
            destination: 目标页面。

        Returns:
            最优路径的边列表；无法构建路径时返回 `None`。
        """

        if current == destination:
            return []

        allowed_pages = self._allowed_pages_for_navigation(destination, current)
        if destination.key not in allowed_pages:
            return None

        queue: list[tuple[float, int, str]] = [(0.0, 0, current.key)]
        best_cost: dict[str, float] = {current.key: 0.0}
        previous: dict[str, tuple[str, Transition]] = {}
        sequence = 0

        while queue:
            cost, _, page_key = heapq.heappop(queue)
            if cost > best_cost.get(page_key, float("inf")):
                continue
            if page_key == destination.key:
                break

            page = allowed_pages[page_key]
            for transition in page.transitions:
                next_page = transition.destination
                if next_page.key not in allowed_pages:
                    continue
                next_cost = cost + next_page.cost + transition.cost + self.navigator.penalty_of(transition)
                if next_cost >= best_cost.get(next_page.key, float("inf")):
                    continue
                best_cost[next_page.key] = next_cost
                previous[next_page.key] = (page_key, transition)
                sequence += 1
                heapq.heappush(queue, (next_cost, sequence, next_page.key))

        if destination.key not in previous:
            return None

        path: list[Transition] = []
        node_key = destination.key
        while node_key != current.key:
            prev_key, transition = previous[node_key]
            path.append(transition)
            node_key = prev_key
        path.reverse()
        return path

    def _refresh_current_page(self, destination: Page, skip_first_screenshot: bool) -> Page | None:
        """优先确认缓存当前页，失败后再执行作用域识别与全量兜底。

        Args:
            destination: 当前导航目标页面。
            skip_first_screenshot: 是否复用当前截图。

        Returns:
            当前稳定页面；无法识别时返回 `None`。
        """

        current = self.navigator.current_page
        if current is not None and self.confirm_page(current, skip_first_screenshot=skip_first_screenshot):
            return current
        return self._detect_current_page_with_fallback(
            skip_first_screenshot=skip_first_screenshot,
            categories=self._navigation_detect_categories(destination),
            context="goto_page",
        )

    def _wait_for_destination(self, destination: Page, timeout: float = 10.0) -> bool:
        """等待下一页面稳定出现。

        Args:
            destination: 目标页面。
            timeout: 等待超时时间。

        Returns:
            是否在超时前稳定识别到目标页面。
        """

        timer = Timer(timeout).start()
        while not timer.reached():
            if self.confirm_page(destination, skip_first_screenshot=False):
                self.navigator.current_page = destination
                return True
        return False

    def _execute_transition(self, transition: Transition) -> bool:
        """执行一条导航边。

        Args:
            transition: 待执行的页面边。

        Returns:
            是否成功到达边的终点页面。
        """

        source = transition.source
        destination = transition.destination

        logger.info(f"Page switch: {source} -> {destination}")

        action_timer = Timer(6.0).start()
        action_done = False
        while not action_timer.reached():
            if self._execute_action(transition.action, interval=0.8, skip_first_screenshot=False):
                action_done = True
                break

        if (not action_done and source.key == 'page_main'
                and self._expand_collapsed_courtyard_scroll()):
            action_done = self._execute_action(
                transition.action, interval=0.8, skip_first_screenshot=False)

        # 町中入口图标可能因动画或皮肤变化无法通过模板识别。只有在
        # 重新确认当前页仍为 town，且目标动作具有固定图标区域时，
        # 才机械点击一次 roi_front 中心作为保底。
        if (
            not action_done
            and source.key == "page_town"
            and isinstance(transition.action, (RuleImage, RuleGif))
            and self.confirm_page(source, skip_first_screenshot=False)
        ):
            x, y, width, height = transition.action.roi_front
            click_x = x + width // 2
            click_y = y + height // 2
            logger.warning(
                "Town target was not recognized; use one fixed-position "
                f"fallback click: action={self._action_name(transition.action)}, "
                f"position=({click_x}, {click_y})"
            )
            self.device.click(
                x=click_x,
                y=click_y,
                control_name=(
                    f"TOWN_FALLBACK_{self._action_name(transition.action)}"
                ),
            )
            action_done = True

        if not action_done:
            self._run_hooks(source.on_leave_failure)
            self._run_hooks(transition.on_leave_failure)
            self._run_hooks(destination.on_enter_failure)
            self._run_hooks(transition.on_enter_failure)
            penalty = self.navigator.add_penalty(transition)
            logger.warning(f"Transition failed before leaving {source}: {transition.key}, penalty={penalty:.1f}")
            self.navigator.current_page = self._detect_current_page_with_fallback(
                skip_first_screenshot=False,
                categories=self._navigation_detect_categories(destination),
            )
            return False

        if self._wait_for_destination(destination):
            self._run_hooks(source.on_leave_success)
            self._run_hooks(transition.on_leave_success)
            self._run_hooks(destination.on_enter_success)
            self._run_hooks(transition.on_enter_success)
            self._mark_page_entered(destination)
            logger.info(f"Page arrived {destination}")
            return True

        self._run_hooks(source.on_leave_failure)
        self._run_hooks(transition.on_leave_failure)
        self._run_hooks(destination.on_enter_failure)
        self._run_hooks(transition.on_enter_failure)
        penalty = self.navigator.add_penalty(transition)
        logger.warning(f"Transition cannot reach {destination}: {transition.key}, penalty={penalty:.1f}")
        self.navigator.current_page = self._detect_current_page_with_fallback(
            skip_first_screenshot=False,
            categories=self._navigation_detect_categories(destination),
        )
        return False

    def _collect_page_check_results(self, destination: Page) -> list[str]:
        """收集当前截图下所有命中的页面名称。

        Args:
            destination: 当前导航目标页面。

        Returns:
            当前截图命中的页面名称列表。
        """

        pages = self.navigator.all_pages(self._navigation_detect_categories(destination))
        self.screenshot()
        return [page.name for page in pages if self.match_page_once(page)]

    def _record_unknown_close_event(self, message: str) -> None:
        """记录未知页关闭历史。

        Args:
            message: 本次关闭尝试的结果描述。
        """

        self.navigator.unknown_close_history.append(message)
        self.navigator.unknown_close_history = self.navigator.unknown_close_history[-10:]

    def _log_navigation_timeout(
        self,
        destination: Page,
        last_path_signature: tuple[str, str] | None = None,
        repeated_failure_transition_key: str | None = None,
        repeated_failure_count: int = 0,
        last_repeated_failure_close_result: str | None = None,
    ) -> None:
        """输出导航超时诊断日志。

        Args:
            destination: 当前导航目标页面。
            last_path_signature: 最近一次成功构建的路径签名。
            repeated_failure_transition_key: 最近一次连续失败的边 key。
            repeated_failure_count: 当前连续失败次数。
            last_repeated_failure_close_result: 最近一次“三连失败后主动关闭”的结果摘要。
        """

        current = self.navigator.current_page.name if self.navigator.current_page else "None"
        allowed_categories = sorted(self._navigation_graph_categories(destination, self.navigator.current_page))
        penalties = {
            key: value
            for key, value in sorted(self.navigator.edge_penalties.items(), key=lambda item: item[0])
            if value > 0
        }
        check_results = self._collect_page_check_results(destination)
        logger.warning("Unknown ui page or navigation stalled")
        logger.warning(f"Current page: {current}")
        logger.warning(f"Target page: {destination.name}")
        logger.warning(f"Allowed categories: {allowed_categories}")
        logger.warning(f"Current task category: {self.navigator.task_category}")
        logger.warning(f"Current page checks: {check_results}")
        logger.warning(f"Last path signature: {last_path_signature}")
        logger.warning(
            "Repeated transition failure: "
            f"key={repeated_failure_transition_key}, count={repeated_failure_count}"
        )
        logger.warning(f"Last repeated-failure close result: {last_repeated_failure_close_result}")
        logger.warning(f"Edge penalties: {penalties}")
        logger.warning(f"Unknown close history: {self.navigator.unknown_close_history}")
        raise GamePageUnknownError(f"Cannot goto page[{destination}]")

    def get_current_page(self, skip_first_screenshot: bool = True, fallback: bool = False) -> Page | None:
        """获取当前稳定页面。

        Args:
            skip_first_screenshot: 是否复用当前截图。
            fallback: 是否回退至识别全部注册页面

        Returns:
            当前稳定识别到的页面；识别失败时返回 `None`。
        """

        if not fallback:
            return self._detect_current_page(
                skip_first_screenshot=skip_first_screenshot,
                categories=self._default_detect_categories()
            )
        return self._detect_current_page_with_fallback(
            skip_first_screenshot=skip_first_screenshot,
            categories=self._default_detect_categories(),
            context="get_current_page",
        )

    def pages_in_category(self, category: str) -> list[Page]:
        """读取当前 session 中指定分类下的所有页面副本。"""

        return self.navigator.all_pages({category})

    def detect_page_in(self, *targets: str | Page, include_global: bool = True) -> Page | None:
        """在指定分类或显式页面集合内识别当前页面。"""

        category_set = {target for target in targets if isinstance(target, str)}
        explicit_pages: list[Page] = []
        for target in targets:
            if not isinstance(target, Page):
                continue
            page = self.navigator.resolve_page(target)
            if page is None:
                continue
            explicit_pages.append(page)

        pages: list[Page] = []
        if category_set:
            if include_global:
                category_set.add("global")
            pages.extend(self.navigator.all_pages(category_set))
        elif not explicit_pages and include_global:
            pages.extend(self.navigator.all_pages({"global"}))

        if explicit_pages:
            existed = {page.key for page in pages}
            for page in explicit_pages:
                if page.key in existed:
                    continue
                pages.append(page)
                existed.add(page.key)

        return GameUi._detect_pages(self, pages, skip_first_screenshot=True)

    def _protected_page_visible(self) -> bool:
        """庭院 / 町中这类主界面是否可见。

        这两类页面上右上角是真实的主界面控件、返回键会弹「退出游戏」确认框，
        所以未知页关闭逻辑在这类页面上不许点右上角、不许按返回键。
        """

        if self.appear(self.I_CHECK_MAIN):
            # 回到可用页面了，返回键预算重置，下一次未知页还能重新退
            self._unknown_page_back_presses = 0
            return True
        if self.appear(self.I_CHECK_TOWN):
            self._unknown_page_back_presses = 0
            return True
        return False

    def _press_back_key(self, reason: str) -> bool:
        """按一次 Android 返回键（带总量上限和点击记录清理）。

        Args:
            reason: 本次按键的理由，写进日志便于复盘。

        Returns:
            是否真的发出了返回键。
        """

        pressed = getattr(self, '_unknown_page_back_presses', 0)
        if pressed >= self.MAX_UNKNOWN_PAGE_BACK_PRESSES:
            logger.warning(f'Back key budget exhausted ({pressed} pressed), stop pressing BACK')
            return False
        self._unknown_page_back_presses = pressed + 1
        # 返回键也计入点击记录，按之前同样清一次，避免熔断误伤
        self.device.click_record_clear()
        try:
            self.device.adb_shell(['input', 'keyevent', '4'])
        except Exception as error:  # noqa: BLE001
            logger.warning(f'Back key failed: {error}')
            return False
        logger.info(f'Press BACK to leave the unknown screen ({reason})')
        return True

    def _detect_overlay_close_cross(self, image=None) -> tuple[int, int] | None:
        """用颜色几何定位全屏浮层右上角的红色关闭叉。

        不新增模板 png：样本 log/error/1795179872096 里关闭叉是纯红色块，
        在 ROI 内取红色像素主体重心即可，比写死坐标更耐受分辨率/位置抖动。

        Args:
            image: 待检测截图，默认取设备当前截图。

        Returns:
            关闭叉中心坐标；红色主体不够大时返回 None。
        """

        image = self.device.image if image is None else image
        if image is None:
            return None
        x, y, width, height = self.OVERLAY_CLOSE_CROSS_ROI
        crop = image[y:y + height, x:x + width]
        if crop is None or crop.size == 0 or crop.ndim != 3:
            return None
        blue = crop[:, :, 0].astype('int16')
        green = crop[:, :, 1].astype('int16')
        red = crop[:, :, 2].astype('int16')
        mask = (red > 120) & (red - green > 50) & (red - blue > 50)
        if int(mask.sum()) < self.OVERLAY_CLOSE_CROSS_MIN_PIXELS:
            return None
        ys, xs = np.nonzero(mask)
        # 只保留主体（中位数 ±20px），避免零星红点把重心拉偏
        center_x, center_y = float(np.median(xs)), float(np.median(ys))
        near = (np.abs(xs - center_x) <= 20) & (np.abs(ys - center_y) <= 20)
        if not bool(near.any()):
            return None
        close_x = float(xs[near].mean()) + x
        close_y = float(ys[near].mean()) + y
        return int(round(close_x)), int(round(close_y))

    def _detect_fullscreen_overlay(self, skip_first_screenshot: bool = False):
        """识别遮挡主界面的全屏浮层。

        Args:
            skip_first_screenshot: 是否复用当前截图。

        Returns:
            `(判据, 关闭坐标)`，判据为 `'ocr'` 或 `'x'`；没有浮层时返回 `None`。
        """

        self.maybe_screenshot(skip_first_screenshot)
        if self._protected_page_visible():
            # 主界面/町中可见就不要动右上角：那里是正常控件，点错会打开侧栏或菜单
            logger.info('Fullscreen overlay check skipped: main page or town is visible')
            return None
        for rule in self.OVERLAY_TITLE_RULES:
            if self.ocr_appear(rule):
                logger.info(f'Fullscreen overlay detected by OCR keyword "{rule.keyword}"')
                return 'ocr', self._detect_overlay_close_cross()
        point = self._detect_overlay_close_cross()
        if point is not None:
            logger.info(f'Fullscreen overlay detected by geometry: red close cross at {point}')
            return 'x', point
        return None

    def _close_fullscreen_overlay(self) -> bool:
        """识别并关闭遮挡主界面的全屏浮层。

        每次点击关闭按钮后都必须重新截图复判：浮层有进出动画，用旧帧判定
        会在没关掉的浮层上反复点同一个坐标。

        Returns:
            是否已发出关闭动作（并在复判中确认浮层不再遮挡）。
        """

        for attempt in range(1, self.OVERLAY_CLOSE_ATTEMPTS + 1):
            # 每次尝试前清空点击记录：同一关闭按钮被反复点击不应把整局打成
            # GameTooManyClickError（10 次同按钮 / 6+6 次两按钮）
            self.device.click_record_clear()
            detected = self._detect_fullscreen_overlay()
            if detected is None:
                if attempt == 1:
                    logger.info('No fullscreen overlay detected, fall back to the legacy unknown-page closers')
                return False
            source, point = detected
            if point is None:
                point = self._detect_overlay_close_cross() or self.C_OVERLAY_CLOSE_X.coord()
            close_x, close_y = int(point[0]), int(point[1])
            logger.info(f'Close fullscreen overlay [{attempt}/{self.OVERLAY_CLOSE_ATTEMPTS}] '
                        f'source={source} click=({close_x}, {close_y})')
            self.device.click(close_x, close_y, control_check=False, control_name='OVERLAY_CLOSE')
            time.sleep(0.8)
            # 关闭动作后必须重新截图复判，不能沿用点击前的帧
            self.screenshot()
            if self._protected_page_visible():
                logger.info(f'Fullscreen overlay dismissed by {source}; main page is back')
                self._record_unknown_close_event('overlay_ocr' if source == 'ocr' else 'overlay_x')
                return True
            if self._detect_fullscreen_overlay(skip_first_screenshot=True) is None:
                logger.info(f'Fullscreen overlay no longer matches after {source} click, but the main page '
                            'is not confirmed yet; leave the recovery to the caller')
                self._record_unknown_close_event('overlay_ocr' if source == 'ocr' else 'overlay_x')
                return True
            logger.warning(f'Fullscreen overlay still on screen after {source} click '
                           f'[{attempt}/{self.OVERLAY_CLOSE_ATTEMPTS}]')
        logger.warning(f'Fullscreen overlay close failed after {self.OVERLAY_CLOSE_ATTEMPTS} attempts')
        return False

    def _exit_confirm_button_panel_visible(self, image=None) -> bool:
        """几何复核：「退出游戏」确认框的取消按钮面板是否比四周亮。

        关键词判据（StrictKeywordOcr）是决定性的：它已经排除了 `['町中']`、
        `['15']`、`[]` 这类误报帧。几何只做交叉验证，用来把「确认框」和
        「页面上恰好有一行含『退出游戏』的文字」区分开并写进日志。

        注意：不能把几何做成「不满足就不点取消」的硬门禁——真确认框上万一
        几何判据失效，其它关闭模板（如 I_UI_CONFIRM）可能点到「确认」把游戏退掉。
        """

        image = self.device.image if image is None else image
        if image is None:
            return False
        x, y, width, height = self.EXIT_GAME_CANCEL_ROI
        top, left = max(y - 25, 0), max(x - 25, 0)
        panel = image[max(y, 0):y + height, max(x, 0):x + width]
        surround = image[top:y + height + 25, left:x + width + 25]
        if panel.size == 0 or surround.size == 0:
            return False
        return float(panel.mean()) - float(surround.mean()) >= 4

    def _handle_exit_game_confirm(self) -> bool:
        """处理「退出游戏」确认框：只点「取消」，绝不点「确认」。

        Returns:
            是否已处理（点过取消并在复判中确认确认框消失）。
        """

        if not self.appear(self.O_EXIT_GAME_CONFIRM):
            return False
        if self._exit_confirm_button_panel_visible():
            logger.info('Exit-game confirm dialog detected (keyword + button panel geometry)')
        else:
            logger.warning('Exit-game confirm keyword matched but button panel geometry missing; '
                           'click cancel only (cancel is the safe button)')
        for attempt in range(1, self.EXIT_GAME_CONFIRM_MAX_CLICKS + 1):
            click_x, click_y = self.C_EXIT_GAME_CANCEL.coord()
            try:
                box = self.O_EXIT_GAME_CANCEL.ocr(self.device.image)
                if isinstance(box, (list, tuple)) and len(box) == 4 and box[2] > 0 and box[3] > 0:
                    click_x = int(box[0] + box[2] // 2)
                    click_y = int(box[1] + box[3] // 2)
            except Exception:  # noqa: BLE001
                pass
            logger.info(f'Exit-game confirm dialog: click cancel [{attempt}/'
                        f'{self.EXIT_GAME_CONFIRM_MAX_CLICKS}] at ({click_x}, {click_y})')
            self.device.click(click_x, click_y, control_check=False, control_name='EXIT_GAME_CANCEL')
            self._record_unknown_close_event('exit_game_cancel')
            time.sleep(0.8)
            # 点完必须重新截图复判，不能只点一次就当作已关闭
            self.screenshot()
            if not self.appear(self.O_EXIT_GAME_CONFIRM):
                logger.info('Exit-game confirm dialog dismissed by cancel')
                return True
        logger.warning('Exit-game confirm dialog still on screen after cancel clicks; '
                       'stop clicking and let the caller decide')
        return False

    def _recover_to_usable_page(self) -> bool:
        """未知页关闭动作全部失败后的兜底：先退回可用页面，再复判。

        直接判 `Game page unknown` 会整局失败，而这类现场通常是「浮层没关掉」或
        「页面在动画中间」，退一步（返回键 / 重新识别主界面）就能回到可导航状态。
        庭院/町中保护不变：识别到主界面就不再按返回键。

        Returns:
            是否在复判中重新看到了可识别的页面。
        """

        for round_index in range(1, self.PAGE_RECOVERY_ROUNDS + 1):
            self.device.click_record_clear()
            self.maybe_screenshot(True)
            if self._handle_exit_game_confirm():
                return True
            if self._protected_page_visible():
                logger.info(f'Page recovery [{round_index}/{self.PAGE_RECOVERY_ROUNDS}]: '
                            'main page or town recognized, stop recovery')
                return True
            if self._close_fullscreen_overlay():
                return True
            if self.navigator.unknown_close_history and self.navigator.unknown_close_history[-1] == 'exit_game_cancel':
                logger.warning('Page recovery skipped BACK right after cancelling exit-game confirm dialog')
                return False
            logger.info(f'Page recovery [{round_index}/{self.PAGE_RECOVERY_ROUNDS}]: no page recognized; '
                        'press BACK to leave the unknown screen (courtyard keeps its BACK protection)')
            if not self._press_back_key('page_recovery'):
                return False
            self._record_unknown_close_event('recover_back')
            time.sleep(0.8)
            self.screenshot()
            if self._handle_exit_game_confirm():
                return True
            if self._protected_page_visible():
                logger.info(f'Page recovery [{round_index}/{self.PAGE_RECOVERY_ROUNDS}]: '
                            'page recognized after BACK')
                return True
        logger.warning(f'Page recovery failed after {self.PAGE_RECOVERY_ROUNDS} rounds')
        return False

    def _expand_collapsed_courtyard_scroll(self) -> bool:
        # The main-page recognizer lives in the bottom scroll. After collecting
        # mail that scroll can be folded; confirm two foreground courtyard
        # markers before using the existing login scroll action.
        # Several costumes recognize the courtyard above the bottom scroll,
        # so I_CHECK_MAIN does not imply the navigation menu is expanded.
        if self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS):
            return False
        markers = (GeneralBuffAssets.I_BUFF_1, self.I_MAIN_GOTO_EXPLORATION)
        for rule in markers:
            if not self.appear(rule):
                return False
            x, y, width, height = map(int, rule.roi_front)
            crop = self.device.image[y:y + height, x:x + width]
            if (crop.shape != rule.image.shape or not crop.size
                    or float(crop.mean()) < float(rule.image.mean()) * .78):
                return False
        if not self.click(RestartAssets.C_LOGIN_SCROLL_CLOSE_AREA, interval=2):
            return False
        logger.info('Confirmed courtyard with folded scroll; expand navigation menu')
        self._record_unknown_close_event('courtyard_scroll_expand')
        time.sleep(.6)
        return True

    def close_unknown_pages(self, skip_first_screenshot: bool = True, allow_ai: bool = True) -> bool:
        """尝试关闭未知页面。

        处理顺序（每一步都会写日志说明判定与理由）：
        1. 客户端掉回登录态 → 交给 Restart；
        2. 「退出游戏」确认框 → 只点「取消」并复判（绝不点「确认」）；
        3. 庭院底部折叠的导航条 → 展开；
        4. 全屏浮层（悬赏封印这类盖满整屏的二级界面）→ 按 OCR 关键词/红色关闭叉几何点掉并复判；
        5. 传统关闭模板（每次尝试前清空点击记录，避免连点触发 GameTooManyClickError）；
        6. 返回键兜底（庭院/町中禁止按返回键）；
        7. 以上全失败 → 给 1~2 轮「退回可用页面再复判」的机会，仍失败才交回上层按超时处理。

        Args:
            skip_first_screenshot: 是否复用当前截图。
            allow_ai: 本地关闭动作全部失败时，是否允许 AI 自愈层最后尝试一次。

        Returns:
            是否成功触发某个关闭动作或把页面恢复到可识别状态。
        """

        self.maybe_screenshot(skip_first_screenshot)

        # 客户端掉回登录态（掉线/被顶号）时页面上的标志全部不可信，点关闭动作
        # 没有意义，交回 Restart 处理（登录流程会关掉弹窗）
        if self.device.app_lost_login():
            raise GameNotRunningError('Client dropped back to login')

        # 「退出游戏」确认框优先处理：其它关闭模板可能误点「确认」把游戏退掉
        if self._handle_exit_game_confirm():
            return True

        if self._expand_collapsed_courtyard_scroll():
            return True

        # 全屏浮层压在页面上时所有页面模板都匹配不到，老逻辑会一路走到
        # 「Try switch to a supported page」然后 CRITICAL Game page unknown。
        if self._close_fullscreen_overlay():
            return True

        logger.warning("Try switch to a supported page")
        for action in [*self.navigator.local_unknown_closers, *self.DEFAULT_UNKNOWN_CLOSERS]:
            action_name = self._action_name(action)
            # 若最后3次执行的都是该动作，则跳过该动作尝试其他动作
            if len(self.navigator.unknown_close_history) >= 3 and \
                    self.navigator.unknown_close_history[-3:] == [action_name] * 3:
                continue
            # 每次尝试前清空点击记录：同一关闭按钮（如 UI_UI_BACK_YELLOW）被反复点击
            # 累计到 10 次会抛 GameTooManyClickError 整局失败，这里的动作次数由
            # 模板列表长度和下面的返回键上限约束，不需要游戏自己的连点熔断来兜底。
            self.device.click_record_clear()
            if self._execute_action(action, interval=1.5, skip_first_screenshot=False):
                self._record_unknown_close_event(f"{action_name}")
                # 关掉未知界面后等待页面变化, 防止多次识别到未知界面
                time.sleep(random.randrange(8, 16, 1) / 10)
                return True
        # 兜底：大部分弹窗只有 Android 返回键能关；但庭院页按返回键会弹「退出游戏」，
        # 所以先确认当前不是庭院页/町中，且上一步不是刚取消完「退出游戏」框，再按返回键。
        if self.navigator.unknown_close_history and self.navigator.unknown_close_history[-1] == 'exit_game_cancel':
            logger.warning('Back key fallback skipped right after cancelling exit-game dialog')
        elif not self._protected_page_visible():
            if self._press_back_key('unknown_page_close'):
                self._record_unknown_close_event('back_key')
                time.sleep(1.0)
                return True
        else:
            logger.warning('Back key fallback skipped on page_main (would open exit dialog)')
        self._record_unknown_close_event(f"None")

        # 本地关闭动作全部失败后，先给 1~2 轮「退回可用页面再复判」的机会，
        # 不要立刻让上层判 Game page unknown。
        if self._recover_to_usable_page():
            return True

        # 本地关闭动作全部失败：先试已通过验证的 AI 图色模板（不花 token），
        # 再交给 AI 自愈层最后尝试一次（默认关闭，零调用）。
        if allow_ai and self.ai_try_learned_template():
            self._record_unknown_close_event('ai_template')
            time.sleep(0.5)
            return True
        if allow_ai and self.ai_recover_unknown_page():
            self._record_unknown_close_event('ai_recovery')
            time.sleep(0.5)
            return True

        @run_once
        def app_check():
            if not self.device.app_is_running():
                raise GameNotRunningError("Game not running")

        @run_once
        def minicap_check():
            if self.config.script.device.control_method == "uiautomator2":
                self.device.uninstall_minicap()

        @run_once
        def rotation_check():
            self.device.get_orientation()

        app_check()
        minicap_check()
        rotation_check()
        return False

    def _finalize_arrival(self, destination: Page, confirm_wait: float, start_time: float) -> bool:
        """统一处理到达目标页后的收尾动作。

        到达判定已由调用方完成（跳转边到达等待的两帧确认，或当前页刷新返回的两帧确认结果），
        本方法不再重复确认，只负责收尾：刷新当前页缓存、幂等触发进入成功钩子、按需等待、输出到达日志。

        Args:
            destination: 已确认到达的目标页面。
            confirm_wait: 到达后额外等待的确认时间。
            start_time: 本次导航开始时间戳，用于输出耗时。

        Returns:
            固定返回 True，表示已到达目标页面。
        """

        self.navigator.current_page = destination
        self._run_enter_success_hooks_if_needed(destination)
        if confirm_wait > 0:
            Timer(confirm_wait, count=int(confirm_wait // 0.5)).start().wait()
        logger.attr(f'{time.time() - start_time:.1f}s', f"Page arrived {destination}")
        return True

    def finish_task_navigation(self) -> bool:
        """任务收尾导航：兼容 xylolit-mu 分支的任务代码，默认回到庭院。"""
        if getattr(self.config, 'defer_task_courtyard_return', False):
            logger.info('Keep current page for scheduler handoff')
            return True
        from tasks.GameUi.default_pages import page_main
        return self.goto_page(page_main)

    def goto_page(self, destination: Page, confirm_wait: float = 0, skip_first_screenshot: bool = True,
                  timeout: int = 30, *, accepted_pages: tuple[Page, ...] = ()) -> bool | None:
        """导航到目标页面。

        Args:
            destination: 目标页面。
            confirm_wait: 到达目标页面后额外等待的确认时间。
            skip_first_screenshot: 是否复用当前截图。
            timeout: 无有效进展超时时间，单位秒。
            accepted_pages: 可直接继续任务的其他页面，识别到后无需转往目标页。

        Returns:
            是否成功到达目标页面。

        Raises:
            GamePageUnknownError: 无法恢复到可导航状态或无法推进到目标页面时抛出。
        """

        destination = self.navigator.resolve_page(destination) or self.navigator.add_page(destination)
        logger.hr(f"UI goto {destination}")
        start_time = time.time()
        progress_timer = Timer(timeout).start()
        last_progress_signature: tuple[str, str] | None = None
        last_detected_page_key: str | None = None
        repeated_failure_transition_key: str | None = None
        repeated_failure_count = 0
        last_repeated_failure_close_result: str | None = None

        def reset_repeated_transition_failures() -> None:
            nonlocal repeated_failure_transition_key, repeated_failure_count
            repeated_failure_transition_key = None
            repeated_failure_count = 0

        while True:
            current = self._refresh_current_page(destination, skip_first_screenshot)
            skip_first_screenshot = False

            if current is None:
                if self.close_unknown_pages(skip_first_screenshot=False):
                    progress_timer.reset()
                    last_progress_signature = ("close_unknown", destination.key)
                    last_detected_page_key = None
                    reset_repeated_transition_failures()
                elif progress_timer.reached():
                    self._log_navigation_timeout(
                        destination,
                        last_path_signature=last_progress_signature,
                        repeated_failure_transition_key=repeated_failure_transition_key,
                        repeated_failure_count=repeated_failure_count,
                        last_repeated_failure_close_result=last_repeated_failure_close_result,
                    )
                    raise GamePageUnknownError(f"Cannot goto page[{destination}]")
                continue

            if current.key != last_detected_page_key:
                self._run_enter_success_hooks_if_needed(current)
                progress_timer.reset()
                last_detected_page_key = current.key
                reset_repeated_transition_failures()
                # Login and other entry hooks can navigate to another page.
                # Refresh before routing, instead of recovering an obsolete
                # source such as the edge-less login page on a courtyard frame.
                if current.on_enter_success:
                    self.screenshot()
                    if not self.match_page_once(current):
                        continue

            if current == destination or any(current.key == page.key for page in accepted_pages):
                # current 来自 _refresh_current_page，其返回已是两帧稳定确认的结果，无需再次 confirm。
                return self._finalize_arrival(current, confirm_wait, start_time)

            path = self._build_path(current, destination)
            if not path:
                allowed = self._allowed_pages_for_navigation(destination, current)
                logger.warning(f'Navigation route missing: {current.key} -> {destination.key}; '
                               f'pages={len(allowed)}, current_edges='
                               f'{[(edge.key, edge.destination.key) for edge in current.transitions]}, '
                               f'has_destination={destination.key in allowed}')
                if self.close_unknown_pages(skip_first_screenshot=False):
                    progress_timer.reset()
                    last_progress_signature = ("close_unknown", current.key)
                    reset_repeated_transition_failures()
                    continue
                if progress_timer.reached():
                    self._log_navigation_timeout(
                        destination,
                        last_path_signature=last_progress_signature,
                        repeated_failure_transition_key=repeated_failure_transition_key,
                        repeated_failure_count=repeated_failure_count,
                        last_repeated_failure_close_result=last_repeated_failure_close_result,
                    )
                continue

            path_signature = (current.key, " -> ".join(transition.key for transition in path))
            if path_signature != last_progress_signature:
                progress_timer.reset()
                last_progress_signature = path_signature
            logger.info(f"Current page: {current}. Following path:")
            logger.info(" -> ".join([current.name, *[transition.destination.name for transition in path]]))

            advanced = True
            for transition in path:
                if not self._execute_transition(transition):
                    advanced = False
                    if repeated_failure_transition_key == transition.key:
                        repeated_failure_count += 1
                    else:
                        repeated_failure_transition_key = transition.key
                        repeated_failure_count = 1
                    if repeated_failure_count % self.REPEATED_TRANSITION_FAILURE_THRESHOLD == 0:
                        close_success = self.close_unknown_pages(skip_first_screenshot=False)
                        logger.warning(f"Transition {transition.key} repeated failure close result: {close_success}")
                        if close_success:
                            progress_timer.reset()
                            last_progress_signature = ("close_unknown_after_repeated_failure", transition.key)
                            reset_repeated_transition_failures()
                    break
                progress_timer.reset()
                last_detected_page_key = transition.destination.key
                last_repeated_failure_close_result = None
                reset_repeated_transition_failures()

            if advanced and self.navigator.current_page == destination:
                # _execute_transition 的到达等待已对目标页完成两帧确认，直接收尾返回，避免回环重复确认。
                return self._finalize_arrival(destination, confirm_wait, start_time)
            if progress_timer.reached():
                self._log_navigation_timeout(
                    destination,
                    last_path_signature=last_progress_signature,
                    repeated_failure_transition_key=repeated_failure_transition_key,
                    repeated_failure_count=repeated_failure_count,
                    last_repeated_failure_close_result=last_repeated_failure_close_result,
                )
                raise GamePageUnknownError(f"Cannot goto page[{destination}]")
