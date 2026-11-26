"""Chess runtime: round OCR, board occupancy, and state readers."""

# This Python file uses the following encoding: utf-8

import json
import random
import re
import time
from datetime import datetime
from functools import cached_property
from pathlib import Path

import cv2
import numpy as np
from module.logger import logger
from tasks.Chess.strategy.grigri import (
    grigri_category,
    grigri_file,
    grigri_keep_names,
    grigri_names_for_quality,
    grigri_score,
    grigri_selection_key,
    resolve_conflict,
    resolve_grigri_name,
)


class ChessRoundStateMixin:
    """Internal mixin; use through ``ScriptTask`` only."""

    @cached_property
    def grigri_icon_templates(self) -> dict[str, object]:
        """按中文名缓存全部符咒图标，供 OCR 失败时进行图像兜底。"""
        badge_dir = Path(__file__).resolve().parents[1] / 'resource' / 'badge'
        templates = {}
        for name in grigri_names_for_quality(None):
            file = badge_dir / grigri_file(name)
            image = cv2.imread(str(file), cv2.IMREAD_COLOR)
            if image is None:
                logger.warning(f'Chess grigri icon is missing: {file}')
                continue
            templates[name] = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        logger.debug(f'Loaded {len(templates)} Chess grigri icon templates')
        return templates

    def _detect_grigri_quality(self) -> str | None:
        for quality, rule in (
            ('gold', self.I_GRIGRI_GOLD),
            ('silver', self.I_GRIGRI_SILVER),
            ('copper', self.I_GRIGRI_COPPER),
        ):
            if self.appear(rule):
                self._grigri_quality_cache = quality
                return quality
        cached_quality = getattr(self, '_grigri_quality_cache', None)
        if cached_quality is not None:
            logger.warning(
                'Chess grigri quality was not recognized after refresh; '
                f'reuse current selection quality={cached_quality}'
            )
            return cached_quality
        logger.warning('Chess grigri quality was not recognized')
        return None

    def _match_grigri_icon(
        self,
        option_rule,
        quality: str | None,
    ) -> tuple[str | None, float, float]:
        """匹配符咒图标，返回 (名字, 最高分, 与次名的分差)。

        分差用于冲突裁决：`朱印/纹章` 两种模板分数几乎相同（实测差 0.000~0.010），
        此时图标不可信；前二差距大才说明图标真的认出来了。
        """
        x, y, width, height = option_rule.roi_front
        image_height, image_width = self.device.image.shape[:2]
        # 点击框略窄于 94px 图标，匹配区向左右各扩 24px。
        left = max(0, x - 24)
        top = max(0, y - 8)
        right = min(image_width, x + width + 24)
        bottom = min(image_height, y + height)
        source = self.device.image[top:bottom, left:right]
        scored: list[tuple[float, str]] = []
        for name in grigri_names_for_quality(quality):
            template = self.grigri_icon_templates.get(name)
            if (
                template is None
                or template.shape[0] > source.shape[0]
                or template.shape[1] > source.shape[1]
            ):
                continue
            result = cv2.matchTemplate(
                source,
                template,
                cv2.TM_CCOEFF_NORMED,
            )
            _, score, _, _ = cv2.minMaxLoc(result)
            scored.append((float(score), name))
        if not scored:
            return None, 0.0, 0.0
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_name = scored[0]
        margin = scored[0][0] - scored[1][0] if len(scored) > 1 else 1.0
        if best_score < self.GRIGRI_ICON_THRESHOLD:
            return None, best_score, margin
        return best_name, best_score, margin

    def _recognize_grigri_options(self) -> list[dict]:
        quality = self._detect_grigri_quality()
        lineup_strategy = self.get_lineup_strategy()
        option_rules = (
            self.C_GRIGRI_OPTION_1,
            self.C_GRIGRI_OPTION_2,
            self.C_GRIGRI_OPTION_3,
        )
        ocr_rules = (
            self.O_GRIGRI_OPTION_NAME_1,
            self.O_GRIGRI_OPTION_NAME_2,
            self.O_GRIGRI_OPTION_NAME_3,
        )
        result = []
        for index, (option_rule, ocr_rule) in enumerate(
            zip(option_rules, ocr_rules),
            start=1,
        ):
            raw = self._normalize_ocr_text(ocr_rule.ocr(self.device.image))
            ocr_name, text_score = resolve_grigri_name(raw, quality)
            icon_name, icon_score, icon_margin = self._match_grigri_icon(
                option_rule,
                quality,
            )
            # 冲突裁决按实测规则（见 strategy/grigri.resolve_conflict）：
            # 朱印/纹章 这类图标分不开的情况信文字；图标前二差距大且文字
            # 映射不够确定时才信图标。
            name, decision = resolve_conflict(
                ocr_name,
                text_score,
                icon_name,
                icon_score,
                icon_margin,
            )
            if ocr_name and icon_name and ocr_name != icon_name:
                logger.warning(
                    'Chess grigri OCR/icon conflict: '
                    f'option={index}, ocr={ocr_name}({text_score:.3f}), '
                    f'icon={icon_name}({icon_score:.3f}, '
                    f'margin={icon_margin:.3f}) -> decision={decision}, '
                    f'use={name}'
                )
                self._dump_grigri_conflict(
                    index=index,
                    quality=quality,
                    raw=raw,
                    ocr_name=ocr_name,
                    text_score=text_score,
                    icon_name=icon_name,
                    icon_score=icon_score,
                    icon_margin=icon_margin,
                    decision=decision,
                )
            category = grigri_category(name)
            score = grigri_score(name, lineup_strategy)
            selection_key = grigri_selection_key(name, lineup_strategy)
            item = {
                'index': index,
                'quality': quality,
                'raw': raw,
                'name': name,
                'category': category,
                'score': score,
                'selection_key': selection_key,
                'text_score': text_score,
                'icon_name': icon_name,
                'icon_score': icon_score,
                'rule': option_rule,
            }
            result.append(item)
            logger.info(
                'Chess grigri option: '
                f'option={index}, quality={quality or "unknown"}, '
                f'ocr=[{raw}], name={name or "未知"}, '
                f'category={category}, benefit={score:g}, '
                f'icon={icon_name or "未命中"}({icon_score:.3f})'
            )
        return result

    # OCR 名字与图标命中不一致时的样本上限
    GRIGRI_CONFLICT_DUMP_LIMIT = 40

    def _dump_grigri_conflict(
        self,
        index: int,
        quality: str | None,
        raw: str,
        ocr_name: str,
        text_score: float,
        icon_name: str,
        icon_score: float,
        icon_margin: float = 0.0,
        decision: str = '',
    ) -> None:
        """保存符咒冲突样本：整帧（供离线重跑 OCR/图标）+ 名称条裁剪。

        只存面板时无法重跑 OCR（名称文字的 ROI 在面板下方），所以改为存整帧；
        为了复盘方便再单独存一条「三张卡名称」的横条。
        """
        count = int(getattr(self, '_grigri_conflict_dumps', 0))
        if count >= self.GRIGRI_CONFLICT_DUMP_LIMIT:
            return
        try:
            image = self.device.image
            option_rules = (
                self.C_GRIGRI_OPTION_1,
                self.C_GRIGRI_OPTION_2,
                self.C_GRIGRI_OPTION_3,
            )
            xs, ys, xe, ye = [], [], [], []
            for rule in option_rules:
                x, y, w, h = rule.roi_front
                xs.append(x)
                ys.append(y)
                xe.append(x + w)
                ye.append(y + h)
            folder = Path('./log/chess_ref/grigri_conflict')
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%H%M%S_%f')[:-3]
            name = f'{stamp}_opt{index}'
            # 整帧：能离线重跑 图标 + OCR，是复现的前提
            cv2.imwrite(
                str(folder / f'{name}.png'),
                cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
            )
            # 名称横条：三张卡的名字都在这一条里，便于快速核对
            strip_top = max(0, int(max(ys) + 150))
            strip_bottom = min(image.shape[0], strip_top + 70)
            strip_left = max(0, min(xs) - 20)
            strip_right = min(image.shape[1], max(xe) + 20)
            if strip_bottom > strip_top and strip_right > strip_left:
                cv2.imwrite(
                    str(folder / f'{name}_names.png'),
                    cv2.cvtColor(
                        image[strip_top:strip_bottom, strip_left:strip_right],
                        cv2.COLOR_RGB2BGR,
                    ),
                )
            meta = {
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'round': getattr(self, '_current_round_no', None),
                'option': index,
                'quality': quality,
                'raw_text': raw,
                'ocr_name': ocr_name,
                'text_score': round(float(text_score), 4),
                'icon_name': icon_name,
                'icon_score': round(float(icon_score), 4),
                'icon_margin': round(float(icon_margin or 0.0), 4),
                'decision': decision,
                'full_frame': True,
                'names_roi': [strip_left, strip_top,
                              strip_right - strip_left,
                              strip_bottom - strip_top],
            }
            (folder / f'{name}.json').write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            self._grigri_conflict_dumps = count + 1
            logger.info(
                f'Chess grigri conflict sample saved: {name}.png '
                f'(decision={decision})'
            )
        except Exception as error:  # noqa: BLE001 - 存样本失败不能打断选符咒
            logger.debug(f'Chess grigri dump failed: {error!r}')

    @staticmethod
    def _best_grigri_option(options: list[dict]) -> dict:
        best_key = max(item['selection_key'] for item in options)
        return random.choice([
            item for item in options if item['selection_key'] == best_key
        ])

    def _refresh_grigri_option(self, option: dict) -> bool:
        index = option['index']
        remaining = self._grigri_refresh_remaining
        if remaining[index - 1] <= 0:
            logger.debug(
                f'Chess grigri option {index} has no refresh remaining'
            )
            return False
        logger.info(
            f'Refresh low-score Chess grigri: '
            f'option={index}, benefit={option["score"]:g}, '
            f'remaining={remaining}'
        )
        refresh_rule = getattr(self, f'C_GRIGRI_REFRESH_{index}')
        for click_index in range(1, 3):
            click_x, click_y = refresh_rule.coord()
            self.device.click(
                x=click_x,
                y=click_y,
                control_name=(
                    f'{refresh_rule.name}_{click_index}'
                ),
            )
            logger.debug(
                f'Chess grigri refresh click: '
                f'option={index}, click={click_index}/2'
            )
            time.sleep(self.FAST_OPERATION_INTERVAL)
        remaining[index - 1] -= 1
        logger.info(
            f'Chess grigri refresh recorded: '
            f'option={index}, remaining={remaining}'
        )
        # 两次刷新点击后等待卡面、品质标志和文字动画稳定，再重新识别。
        time.sleep(self.ACTION_SETTLE_INTERVAL)
        self.screenshot()
        return True

    def _continue_grigri_refresh_below_nine(self) -> bool:
        """读取是否启用“刷新收益排名非前40%选项”。"""
        chess_task_config = getattr(self.config, 'chess', None)
        chess_config = getattr(chess_task_config, 'chess_config', None)
        return bool(getattr(
            chess_config,
            'continue_grigri_refresh_below_nine',
            True,
        ))

    def _next_refreshable_grigri_option(
        self,
        options: list[dict],
    ) -> dict | None:
        """选择下一项要刷新的位置；优先刷新当前评分最低项。"""
        candidates = [
            item
            for item in options
            if self._grigri_refresh_remaining[item['index'] - 1] > 0
        ]
        if not candidates:
            return None
        if not self._continue_grigri_refresh_below_nine():
            return None
        if any(item['quality'] is None for item in candidates):
            logger.warning(
                'Skip Chess grigri refresh: quality is unknown, '
                'top-40-percent ranking cannot be calculated safely'
            )
            return None

        lineup_strategy = self.get_lineup_strategy()
        keep_names = {
            quality: grigri_keep_names(
                quality,
                lineup_strategy,
            )
            for quality in {item['quality'] for item in candidates}
        }
        logger.debug(
            'Chess grigri top-40-percent keep counts: '
            f'{ {quality: len(names) for quality, names in keep_names.items()} }'
        )
        # 以当前品质的图鉴总数为基数，严格按数量保留前40%；
        # 同收益只在最终选取时随机，排行榜边界使用固定图鉴顺序。
        candidates = [
            item
            for item in candidates
            if item['name'] not in keep_names[item['quality']]
        ]
        if not candidates:
            return None

        # 刷新标准只看实际收益，不受符咒类别影响。
        lowest_score = min(item['score'] for item in candidates)
        return random.choice([
            item
            for item in candidates
            if item['score'] == lowest_score
        ])

    def _recognize_grigri_options_with_retry(self) -> list[dict]:
        """三个选项全未知时，用下一张截图复核一次。"""
        options = self._recognize_grigri_options()
        if len(options) == 3 and all(not item['name'] for item in options):
            logger.info(
                'All Chess grigri options unknown; wait for next screenshot '
                'and recognize once more'
            )
            time.sleep(self.ACTION_SETTLE_INTERVAL)
            self.screenshot()
            self._grigri_quality_cache = None
            options = self._recognize_grigri_options()
            if len(options) == 3 and all(not item['name'] for item in options):
                logger.warning(
                    'All Chess grigri options still unknown after recheck; '
                    'allow random selection'
                )
        return options

    def select_grigri(self) -> bool:
        """识别、评分并选择符咒；低分时对目标选项刷新一次。"""
        if not self.appear(self.I_SELECT_GRIGRI):
            return False

        # 每次进入新的选符咒界面，三个位置各允许刷新一次。
        self._grigri_quality_cache = None
        self._grigri_refresh_remaining = [1, 1, 1]
        options = self._recognize_grigri_options_with_retry()
        for _ in range(self.GRIGRI_REFRESH_MAXIMUM):
            refresh_target = self._next_refreshable_grigri_option(
                options
            )
            if refresh_target is None:
                logger.info(
                    'Stop Chess grigri refresh: '
                    f'no refreshable position, '
                    f'remaining={self._grigri_refresh_remaining}'
                )
                break
            if not self._refresh_grigri_option(refresh_target):
                break
            options = self._recognize_grigri_options_with_retry()

        selected = self._best_grigri_option(options)
        selected_rule = selected['rule']
        deadline = time.monotonic() + self.GRIGRI_SELECT_TIMEOUT
        attempts = 0
        logger.debug(
            f'Chess grigri option locked: option={selected["index"]}, '
            f'name={selected["name"] or "未知"}, '
            f'category={selected["category"]}, '
            f'benefit={selected["score"]:g}; '
            'retry until selection panel closes'
        )
        while time.monotonic() < deadline:
            self.screenshot()
            if not self.appear(self.I_SELECT_GRIGRI):
                logger.debug(
                    'Chess grigri selection confirmed: '
                    f'option={selected["index"]}, attempts={attempts}'
                )
                return True
            attempts += 1
            self.click(selected_rule)
            time.sleep(self.ACTION_SETTLE_INTERVAL)

        self.screenshot()
        if not self.appear(self.I_SELECT_GRIGRI):
            return True
        logger.warning(
            'Chess grigri selection did not close after repeated clicks: '
            f'option={selected["index"]}, attempts={attempts}'
        )
        return False

    @staticmethod
    def _normalize_ocr_text(value) -> str:
        if value is None:
            return ''
        return ''.join(str(value).split())

    @classmethod
    def _fuzzy_text_match(
        cls,
        expected,
        current,
    ) -> tuple[bool, float, float]:
        """Chess 内部 OCR 编辑距离兜底，避免修改公共 RuleOcr。"""
        expected = cls._normalize_ocr_text(expected)
        current = cls._normalize_ocr_text(current)
        threshold = 0.75 if len(expected) <= 2 else 0.65
        if not expected or not current:
            return False, 0.0, threshold
        if expected == current:
            return True, 1.0, threshold

        # 仅保留较短字符串长度的一行动态规划状态。
        if len(expected) < len(current):
            expected, current = current, expected
        previous = list(range(len(current) + 1))
        for expected_index, expected_char in enumerate(expected, start=1):
            row = [expected_index]
            for current_index, current_char in enumerate(current, start=1):
                row.append(min(
                    row[current_index - 1] + 1,
                    previous[current_index] + 1,
                    previous[current_index - 1]
                    + (expected_char != current_char),
                ))
            previous = row
        similarity = 1.0 - previous[-1] / max(len(expected), len(current))
        return similarity >= threshold, similarity, threshold

    def _parse_round_number(self, ocr_rule) -> tuple[int | None, str]:
        raw = self._normalize_ocr_text(ocr_rule.ocr(self.device.image))
        matched = re.search(r'(\d+)(?=回)', raw)
        if matched is None:
            matched = re.search(r'\d+', raw)
        if matched is None:
            return None, raw
        value = int(matched.group(0))
        return (value if value > 0 else None), raw

    def _read_primary_round_layout(self) -> tuple[str, bool]:
        """以主 chess_mode 是否读到数字判断是否启用前三回合布局。"""
        frame_id = getattr(self.device, 'image_frame_id', None)
        cache = getattr(self, '_primary_round_layout_cache', None)
        if frame_id is not None and cache is not None and cache[0] == frame_id:
            return cache[1]

        raw = self._normalize_ocr_text(
            self.O_CHESS_MODE.ocr(self.device.image)
        )
        if raw:
            use_alternate_layout = bool(re.search(r'\d', raw))
            self._last_alternate_round_layout = use_alternate_layout
        else:
            # 动画帧偶发空识别时沿用上一帧，避免在两套区域间抖动。
            use_alternate_layout = getattr(
                self,
                '_last_alternate_round_layout',
                False,
            )
        result = raw, use_alternate_layout
        if frame_id is not None:
            self._primary_round_layout_cache = frame_id, result
        return result

    def _is_early_round_layout(self) -> bool:
        """第二套 round/mode 布局即前三回合，此阶段禁止任何卖卡。"""
        _, use_alternate_layout = self._read_primary_round_layout()
        return use_alternate_layout

    def _read_round_number(self) -> int | None:
        """主 chess_mode 读到数字时使用前三回合专用 round_2。"""
        mode_raw, use_alternate_layout = self._read_primary_round_layout()
        ocr_rule = self.O_ROUND_2 if use_alternate_layout else self.O_ROUND
        value, round_raw = self._parse_round_number(ocr_rule)
        if use_alternate_layout:
            logger.debug(
                f'Chess mode primary is numeric [{mode_raw}]; '
                f'use round_2 [{round_raw}] -> {value}'
            )
        return value

    # 特殊回合标志优先于文字 OCR；后续回合标志在此登记阶段。
    SPECIAL_STAGE_RULES = (('I_STAGE_HYAKKI', '鬼'),)
    BOARD_MARKER_DISABLED_RULES = ('I_STAGE_ORCHI', 'I_STAGE_DEMON')

    def _stage_marker_visible(self, rule) -> bool:
        """回合预告中的灰色图案也会模板命中，只接受金色激活图案。"""
        x, y, w, h = rule.roi_back
        region = self.device.image[y:y+h, x:x+w]
        template = rule.image
        th, tw = template.shape[:2]
        if region.shape[0] < th or region.shape[1] < tw:
            return False
        scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(scores >= rule.threshold)
        for px, py in zip(xs, ys):
            hsv = cv2.cvtColor(region[py:py+th, px:px+tw], cv2.COLOR_RGB2HSV)
            gold = ((hsv[:, :, 0] >= 10) & (hsv[:, :, 0] <= 45)
                    & (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 150))
            if float(np.mean(gold)) >= .10:
                return True
        return False

    def _special_chess_mode(self) -> str | None:
        for rule_name, mode in self.SPECIAL_STAGE_RULES:
            if self._stage_marker_visible(getattr(self, rule_name)):
                return mode
        return None

    def _read_chess_mode(self) -> str | None:
        """优先识别特殊回合标志，其余阶段读取文字。"""
        special_mode = self._special_chess_mode()
        if special_mode is not None:
            return special_mode
        primary_raw, use_alternate_layout = self._read_primary_round_layout()
        raw = (
            self._normalize_ocr_text(
                self.O_CHESS_MODE_2.ocr(self.device.image)
            )
            if use_alternate_layout
            else primary_raw
        )
        if raw in ('备', '战'):
            return raw
        # “待”等过渡文字虽然不触发阶段动作，但必须保留为有文本状态，
        # 不能和真正的 OCR 空结果混为一谈，否则会误累计结算空帧。
        return '待' if raw else None

    def _is_boss_challenge_round(self, round_no=None) -> bool:
        """大蛇、逢魔标志出现过的本回合不使用勾玉上阵检测。"""
        key = (self.chess_state.game_number,
               self._current_round_no if round_no is None else round_no)
        if any(self._stage_marker_visible(getattr(self, name))
               for name in self.BOARD_MARKER_DISABLED_RULES):
            self._board_marker_disabled_round = key
            return True
        return getattr(self, '_board_marker_disabled_round', None) == key

    def _tracked_board_positions(self) -> set[int]:
        """汇总无需依赖勾玉模板即可确认的本局站位。"""
        positions = {
            int(position)
            for position in getattr(
                self,
                '_player_deployed_positions',
                set(),
            )
        }
        actual_positions = getattr(self, '_board_actual_positions', {})
        for name in getattr(self, '_board_lineup_names', set()):
            position = actual_positions.get(
                name,
                self.shikigami_deploy_positions.get(name),
            )
            if position is not None:
                positions.add(int(position))
        for unit in getattr(self, '_board_special_units', {}).values():
            position = unit.get('position')
            if position is not None:
                positions.add(int(position))
        return positions

    def _board_set_has_shikigami(self, set_index: int) -> bool:
        """判断站位是否有人；首领回合仅采用本局动态站位记录。"""
        if self._is_boss_challenge_round():
            occupied = int(set_index) in self._tracked_board_positions()
            logger.debug(
                'Chess boss-round board occupancy by runtime state: '
                f'round={self._current_round_no}, set={set_index}, '
                f'occupied={occupied}'
            )
            return occupied

        return self._board_set_has_shikigami_visual(set_index)

    def _board_set_has_shikigami_visual(self, set_index: int) -> bool:
        """仅按当前截图中的勾玉标志判断指定站位是否有人。"""

        if self._is_boss_challenge_round():
            return int(set_index) in self._tracked_board_positions()

        x, y, width, height = self._set_jade_area(set_index)
        image_height, image_width = self.device.image.shape[:2]
        if (
            x < 0
            or y < 0
            or x >= image_width
            or y >= image_height
        ):
            logger.warning(
                f'Chess set jade area is outside screenshot: '
                f'set={set_index}, area={(x, y, width, height)}'
            )
            return False
        width = min(width, image_width - x)
        height = min(height, image_height - y)
        if width <= 0 or height <= 0:
            logger.warning(
                f'Chess set jade area is empty after clipping: '
                f'set={set_index}, area={(x, y, width, height)}'
            )
            return False

        roi = [x, y, width, height]
        return any(
            bool(rule.match_all_any(
                self.device.image,
                roi=roi,
                threshold=self.BOARD_OCCUPANCY_TEMPLATE_THRESHOLD,
                nms_threshold=0.3,
                frame_id=self.device.image_frame_id,
            ))
            for rule in self.board_occupancy_rules
        )

    def _reconcile_lineup_after_hyakki_round(
        self,
        next_round_no: int | None = None,
    ) -> list[str]:
        """百鬼夜行结束后移除已不在原站位上的式神上阵记录。"""
        if self._is_boss_challenge_round(round_no=next_round_no):
            logger.info(
                'Skip post-Hyakki lineup check during boss round: '
                f'round={next_round_no}'
            )
            return []
        deployed_names = set(
            getattr(self, '_board_lineup_names', set())
        )
        actual_positions = dict(
            getattr(self, '_board_actual_positions', {})
        )
        player_positions = set(
            getattr(self, '_player_deployed_positions', set())
        )
        removed = []
        for name in sorted(deployed_names):
            set_index = actual_positions.get(
                name,
                self.shikigami_deploy_positions.get(name),
            )
            if set_index is None:
                continue
            if self._board_set_has_shikigami_visual(int(set_index)):
                continue
            deployed_names.discard(name)
            actual_positions.pop(name, None)
            player_positions.discard(int(set_index))
            removed.append(name)
            logger.warning(
                'Chess shikigami missing after Hyakki round; '
                f'mark as undeployed: name={name}, set={set_index}'
            )

        self._board_lineup_names = deployed_names
        self._board_actual_positions = actual_positions
        self._player_deployed_positions = player_positions
        logger.info(
            'Chess post-Hyakki lineup check: '
            f'removed={removed}, deployed={sorted(deployed_names)}'
        )
        return removed

    def _read_board_position_count(self) -> dict:
        """统计 12 个站位勾玉区域中检测到图标的位置数量。"""
        if self._is_boss_challenge_round():
            occupied_positions = sorted(self._tracked_board_positions())
            count = len(occupied_positions)
            raw = ','.join(str(index) for index in occupied_positions)
            logger.debug(
                'Chess boss-round board position count by runtime state: '
                f'round={self._current_round_no}, count={count}/12, '
                f'occupied={occupied_positions}'
            )
            return {
                'current': count,
                'total': 12,
                'raw': raw,
                'occupied_positions': occupied_positions,
            }

        occupied_positions = [
            set_index
            for set_index in range(1, 13)
            if self._board_set_has_shikigami(set_index)
        ]
        count = len(occupied_positions)
        raw = ','.join(str(index) for index in occupied_positions)
        logger.debug(
            'Chess board position count by jade: '
            f'{count}/12, occupied={occupied_positions}'
        )
        return {
            'current': count,
            'total': 12,
            'raw': raw,
            'occupied_positions': occupied_positions,
        }

    def _read_shikigami_count(self) -> dict | None:
        """读取场上人数：统计 12 个站位勾玉区域是否有式神。"""
        return self._read_board_position_count()

    def _read_lineup_capacity_status(self) -> dict | None:
        """以本局已确认上阵名单统计人口；金鱼等特殊单位不计人口。"""
        level = self._read_level()
        if level is None:
            logger.warning(
                'Chess lineup capacity unavailable: '
                f'level={level}'
            )
            return None

        deployed_names = {
            name
            for name in getattr(self, '_board_lineup_names', set())
            if name in self.shikigami_deploy_positions
        }
        player_positions = set(
            getattr(self, '_player_deployed_positions', set())
        )
        # 人口必须按「场上实际单位」算：过渡位/已退出阵容的棋子还占着位置，
        # 若只数「当前阵容名单里的名字」，会误判还有空位而超编上阵。
        current = max(len(deployed_names), len(player_positions))
        capacity = level
        full = current >= capacity
        logger.debug(
            'Chess lineup capacity by runtime state: '
            f'population={current}, '
            f'capacity={capacity}, level={level}, '
            f'deployed={sorted(deployed_names)}, '
            f'player_positions={sorted(player_positions)}, '
            f'arakawa_goldfish='
            f'{getattr(self, "_arakawa_goldfish_current_position", None)}, '
            f'full={full}'
        )
        return {
            'current': current,
            'capacity': capacity,
            'level': level,
            'deployed_names': deployed_names,
            'player_positions': player_positions,
            'arakawa_goldfish': (
                getattr(self, '_arakawa_goldfish_current_position', None)
                is not None
            ),
            'full': full,
        }

    def _level_label_visible(self) -> bool:
        """等级文字与「买经验」按钮同在备战界面底部栏，以按钮模板判断
        底部栏当前是否可见。

        实战证据：`Chess level OCR invalid: []` 每 5 秒一次、紧邻
        deploy candidate，且同一时刻金币 OCR 也读空——整帧已不在备战
        界面（战斗/过渡帧）。此时不该尝试 OCR，也不该打 WARNING，
        静默复用缓存即可；「页面可见但 OCR 仍失败」才打 WARNING。
        """
        appear = getattr(self, 'appear', None)
        if appear is None:
            # 纯 mixin 假对象没有 appear（dev_tools 测试）：默认标签可见。
            return True
        return appear(self.I_EXPERIENCE)

    def _parse_level_text(self, raw: str) -> int | None:
        """从 OCR 文本解析等级：阿拉伯数字或中文“一阶”~“九阶”。

        只做解析，不写缓存、不打日志；写缓存统一由 `_read_level()` 完成。
        """
        digit = re.search(r'([1-9])', raw)
        if digit is not None:
            return int(digit.group(1))

        chinese_digits = {
            '一': 1,
            '二': 2,
            '三': 3,
            '四': 4,
            '五': 5,
            '六': 6,
            '七': 7,
            '八': 8,
            '九': 9,
        }
        for character, level in chinese_digits.items():
            if character in raw:
                return level
        return None

    def _read_level(self) -> int | None:
        """读取“一阶”至“九阶”，同时兼容阿拉伯数字显示。

        单帧 OCR 失败（`[]`）在实战里并不少见（升级动画、遮挡、渲染抖动）。
        上层用等级算人口上限，一旦返回 None 就会直接中止整轮上阵，代价太大。
        因此保留本局最后一次 OCR 读到的等级作为兜底：等级在局内只增不减，
        用旧值最多是低估人口（保守），不会导致超编。

        等级标签不可见时（战斗/过渡帧）直接静默复用缓存，不尝试 OCR、
        不打 WARNING；「标签可见但 OCR 失败」才是值得报警的真问题。
        """
        if not self._level_label_visible():
            cached = getattr(self, '_last_confirmed_level', None)
            if isinstance(cached, int) and 1 <= cached <= 9:
                if not getattr(self, '_level_label_visible_flag', False):
                    # 只在「可见→隐藏」切换时打一行 INFO（带缓存等级），
                    # 供实机确认空读确实发生在标签隐藏帧，而不是每帧都打。
                    logger.info(
                        'Chess level label became not visible; '
                        f'reuse last confirmed level={cached}'
                    )
                    self._level_label_visible_flag = False
                logger.debug(
                    'Chess level label is not visible; '
                    f'reuse last confirmed level={cached}'
                )
                return cached
            logger.debug(
                'Chess level label is not visible and level was '
                'never confirmed'
            )
            return None

        if getattr(self, '_level_label_visible_flag', True) is False:
            logger.info(
                'Chess level label visible again; level OCR resumes'
            )
        self._level_label_visible_flag = True

        raw = self._normalize_ocr_text(self.O_LEVEL.ocr(self.device.image))
        level = self._parse_level_text(raw)
        if level is not None:
            self._remember_level(level, raw)
            return level

        cached = getattr(self, '_last_confirmed_level', None)
        if isinstance(cached, int) and 1 <= cached <= 9:
            logger.warning(
                f'Chess level OCR invalid: [{raw}]; '
                f'reuse last confirmed level={cached}'
            )
            return cached
        logger.warning(f'Chess level OCR invalid: [{raw}]')
        return None

    def _remember_level(self, level: int, raw: str = '') -> None:
        """记录本局已确认的等级，供 OCR 失败时兜底。"""
        previous = getattr(self, '_last_confirmed_level', None)
        self._last_confirmed_level = int(level)
        if previous != level:
            logger.debug(f'Chess level: [{raw}] -> {level}')

    def _read_remaining_time(self) -> int | None:
        """读取剩余时间；第二套布局复用第一套阶段框中的数字。"""
        primary_raw, use_alternate_layout = (
            self._read_primary_round_layout()
        )
        raw = (
            primary_raw
            if use_alternate_layout
            else self._normalize_ocr_text(
                self.O_NOW_TIME.ocr(self.device.image)
            )
        )
        matched = re.search(r'\d+', raw)
        if matched is None:
            logger.warning(f'Chess remaining time OCR invalid: [{raw}]')
            return None
        remaining = int(matched.group(0))
        logger.debug(
            f'Chess remaining time: [{raw}] -> {remaining}, '
            f'alternate_layout={use_alternate_layout}'
        )
        return remaining

    def _read_game_rank(self) -> tuple[int | None, str]:
        """读取结算页“第几名”，兼容阿拉伯数字和中文数字。"""
        raw = self._normalize_ocr_text(self.O_RANK.ocr(self.device.image))
        matched = re.search(r'第?([1-8])名?', raw)
        if matched is not None:
            return int(matched.group(1)), raw

        chinese_digits = {
            '一': 1,
            '二': 2,
            '三': 3,
            '四': 4,
            '五': 5,
            '六': 6,
            '七': 7,
            '八': 8,
        }
        for character, rank in chinese_digits.items():
            if character in raw:
                return rank, raw
        return None, raw
