"""Chess runtime: shop access, purchasing, and atomic economy operations."""

# This Python file uses the following encoding: utf-8

import json
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from module.atom.click import RuleClick
from module.atom.image import RuleImage
from module.logger import logger
from tasks.Chess.strategy.shikigami_catalog import SHIKIGAMI_BY_ROMAJI


class ChessEconomyMixin:
    """Internal mixin; use through ``ScriptTask`` only."""

    def _shop_slots(self) -> list[tuple[int, RuleClick]]:
        """返回商店五个卡面点击区；编号按原资源定义从右向左。"""
        return [
            (
                index,
                getattr(self, f'C_SHIKIGAMI_{index}'),
            )
            for index in range(1, 6)
        ]

    def _is_shop_open(self) -> bool:
        """使用刷新按钮判断商店是否展开；禁止在此读取价格 OCR。

        ``check_market`` 是开店确认的辅助标志，不能用于“商店仍展开”
        的通用判断，否则商店关闭后仍可能命中该图标，导致上卡阶段被
        误判为商店未关闭。
        """
        visible = self._shop_refresh_marker_visible()
        if visible:
            self._shop_assumed_open = True
            return True
        # 战斗技能会遮住右侧刷新按钮。经济续跑期间以脚本自己记录的
        # 商店状态为准，避免因“看不见刷新”而反复开关商店。
        return bool(
            getattr(self, '_economy_battle_mode', False)
            and getattr(self, '_shop_assumed_open', False)
        )

    def _shop_refresh_marker_visible(self) -> bool:
        """刷新/金币不足刷新任一出现，表示商店明确展开。"""
        return self.appear(self.I_REFRESH) or self.appear(self.I_REFRESH_NOT_GOLD)

    def _shop_open_confirm_marker_visible(self) -> bool:
        """开店确认：非战阶段只看 refresh；战阶段追加 check_market。"""
        if self._shop_refresh_marker_visible():
            return True
        return self._read_chess_mode() == '战' and self.appear(
            self.I_CHECK_MARKET
        )

    def _is_preparation_mode(self) -> bool:
        """只有无符咒弹窗的“备”可继续操作；弹窗出现立即中断。"""
        if self._read_chess_mode() != '备':
            return False
        if self.appear(self.I_SELECT_GRIGRI):
            logger.debug(
                'Interrupt Chess preparation immediately: grigri selection '
                'panel detected'
            )
            return False
        return True

    def _is_purchase_allowed(self) -> bool:
        """商店动作遇到“鬼”必停；其余阶段由外层状态机调度。"""
        mode = self._read_chess_mode()
        if mode == '鬼':
            return False
        return True

    def _read_shop_gold(self) -> int | None:
        """读取当前金币；OCR 异常时返回 None，避免误停购买流程。"""
        value = self.O_GOLD.ocr(self.device.image)
        if not isinstance(value, int) or value < 0:
            logger.warning(f'Chess gold OCR invalid: [{value}]')
            return None
        return self._stabilize_gold_reading(value)

    # 金币读数保护：3 秒内凭空多出 20+ 不可能是真实收益，只可能是 UI 切换
    # （开/关商店）时同一 ROI 读到了别的数字。实测样例：8->98、36->86、
    # 43->89、11->99（chess7 与三局连跑都出现）。误读会直接干扰
    # 「买不买得起 / 要不要吃利息 / 该不该升级」的判断。
    GOLD_JUMP_REJECT_DELTA = 20
    GOLD_JUMP_REJECT_WINDOW = 3.0

    def _stabilize_gold_reading(self, value: int) -> int:
        """拒绝不可能的跳升，返回可信金币值（并在首次误读时留证据图）。

        实测金币 ROI 会间隔读到另一个数字（8→96→2 这种来回跳，间隔 1~16 秒都有）。
        单靠「3 秒内 +20 就拒绝」挡不住「16 秒后才出现的 96」——那会被当成收入。
        因此改法：**突然 +20 以上的读数必须连续两帧一致才承认**，否则只当噪声。
        真实收入会一直保持在高位，两帧必然一致；瞬时误读下一帧就回落到真值。
        """
        now = time.monotonic()
        last = getattr(self, '_gold_last_value', None)
        last_at = float(getattr(self, '_gold_last_at', 0.0) or 0.0)
        candidate = getattr(self, '_gold_candidate', None)
        candidate_at = float(getattr(self, '_gold_candidate_at', 0.0) or 0.0)

        if isinstance(last, int) and value >= last + self.GOLD_JUMP_REJECT_DELTA:
            # 候选过期（超过 10 秒没再出现同一数值）就丢掉
            if candidate is not None and now - candidate_at > 10.0:
                candidate = None
            if candidate is not None and abs(value - candidate) <= 5:
                logger.info(
                    'Chess gold confirmed as real increase: '
                    f'{last} -> {value} (two consistent readings)'
                )
                self._gold_candidate = None
            else:
                logger.warning(
                    'Chess gold OCR rejected as misread: '
                    f'{last} -> {value}; waiting for a second reading'
                )
                self._dump_gold_misread(last, value)
                self._gold_candidate = value
                self._gold_candidate_at = now
                return last
        else:
            self._gold_candidate = None

        self._gold_last_value = value
        self._gold_last_at = now
        return value

    def _dump_gold_misread(self, previous: int, reading: int) -> None:
        """把误读那一帧的金币区域存下来，便于离线核对 ROI。"""
        try:
            x, y, w, h = self.O_GOLD.roi_front
            image = self.device.image
            crop = image[max(0, y - 10):y + h + 10, max(0, x - 40):x + w + 10]
            folder = Path('./log/chess_ref/gold_misread')
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%H%M%S_%f')[:-3]
            target = folder / f'{stamp}_{previous}_{reading}.png'
            cv2.imwrite(
                str(target),
                cv2.cvtColor(crop, cv2.COLOR_RGB2BGR),
            )
            logger.info(f'Chess gold misread crop saved: {target}')
        except Exception as error:  # noqa: BLE001 - 证据保存失败不影响任务
            logger.debug(f'Chess gold misread dump failed: {error!r}')

    @staticmethod
    def _parse_coin_text(raw_text: str) -> dict | None:
        """解析鼬乐币 m/600，并恢复斜杠丢失或误识别为 1/I 的结果。"""
        raw = ''.join(str(raw_text or '').split())
        if not raw:
            return None

        # 标准斜杠以及被识别为 I/l/竖线的分隔符。
        explicit = re.search(r'(\d{1,3})[/／Iil|](600)$', raw)
        recovered = False
        if explicit is not None:
            current = int(explicit.group(1))
            recovered = '/' not in raw and '／' not in raw
        else:
            digits = ''.join(re.findall(r'\d', raw))
            if not digits.endswith('600'):
                return None
            prefix = digits[:-3]
            # 3441600 表示 344/600，其中额外的 1 是斜杠误识别。
            if len(prefix) == 4 and prefix.endswith('1'):
                prefix = prefix[:-1]
                recovered = True
            elif prefix:
                recovered = True
            if not prefix:
                return None
            current = int(prefix)

        if not 0 <= current <= 600:
            return None
        return {
            'current': current,
            'remaining': 600 - current,
            'total': 600,
            'raw': raw,
            'recovered': recovered,
        }

    def _read_coin(self) -> dict | None:
        """读取棋局大厅鼬乐币，优先消费 DigitCounter 三元组。"""
        result = self.O_COIN.ocr(self.device.image)
        coin = None
        if isinstance(result, (tuple, list)) and len(result) == 3:
            current, remaining, total = result
            if (
                all(isinstance(value, int) for value in result)
                and current >= 0
                and remaining >= 0
                and total == 600
                and current + remaining == total
            ):
                coin = {
                    'current': current,
                    'remaining': remaining,
                    'total': total,
                    'raw': f'{current}/{total}',
                    'recovered': False,
                }
        else:
            # 兼容尚未重新生成资源、仍返回文本的旧版 OCR 配置。
            raw = self._normalize_ocr_text(result)
            coin = self._parse_coin_text(raw)

        if coin is None:
            logger.warning(f'Chess coin OCR invalid: [{result}]')
            return None
        if coin['recovered']:
            logger.debug(
                f'Chess coin OCR recovered: [{coin["raw"]}] -> '
                f'{coin["current"]}/{coin["total"]}'
            )
        else:
            logger.debug(
                f'Chess coin: {coin["current"]}/{coin["total"]}'
            )
        return coin

    def _coin_is_full(self) -> bool:
        """最多复查三帧，仅 600/600 才视为鼬乐币已满。"""
        for attempt in range(1, 4):
            coin = self._read_coin()
            if coin is not None:
                full = coin['current'] == coin['total'] == 600
                if full:
                    logger.info('Chess coin is full: 600/600')
                return full
            if attempt < 3:
                time.sleep(self.SCREENSHOT_INTERVAL)
                self.screenshot()
        return False

    def _can_afford_shop_shikigami(
        self,
        slot_index: int,
        known_price: int,
    ) -> bool:
        """判断购买能力；身份已确定时优先使用目录费用。"""
        if slot_index not in range(1, 6):
            logger.warning(f'Invalid Chess shop slot index: {slot_index}')
            return False

        gold = self._read_shop_gold()
        raw_price = ''
        price = known_price
        if gold is None or price is None:
            logger.warning(
                'Skip Chess shop purchase because affordability OCR is '
                f'unavailable: slot={slot_index}, gold={gold}, '
                f'price_raw=[{raw_price}]'
            )
            return False

        affordable = gold >= price
        logger.debug(
            'Chess shop affordability: '
            f'slot={slot_index}, gold={gold}, price={price}, '
            f'affordable={affordable}'
        )
        return affordable


    def _ensure_shop_open(self) -> bool:
        """必要时点击商店图标，并等待刷新按钮确认商店已经展开。"""
        if not self._is_purchase_allowed():
            logger.debug('Stop opening Chess shop: Hyakki mode detected')
            return False
        if getattr(self, '_economy_battle_mode', False):
            return self._ensure_battle_economy_shop_open()
        if self._is_shop_open():
            logger.debug('Chess shop is already open')
            self._shop_assumed_open = True
            return True

        logger.debug('Chess shop is closed, click market to open it')
        # C_MARKET 是跨回合反复使用的合法开关；每次新状态转换单独计数。
        self.device.click_record_remove_rule(self.I_MARKET)
        deadline = time.monotonic() + self.SHOP_OPEN_TIMEOUT
        attempts = 0
        while time.monotonic() < deadline:
            if not self._is_purchase_allowed():
                logger.debug('Stop opening Chess shop: Hyakki mode detected')
                return False
            attempts += 1
            self.click(self.I_MARKET)
            attempt_deadline = min(
                deadline,
                time.monotonic() + 2 * self.SLOW_POLL_INTERVAL,
            )
            while time.monotonic() < attempt_deadline:
                time.sleep(self.SCREENSHOT_INTERVAL)
                self.screenshot()
                if not self._is_purchase_allowed():
                    logger.debug('Stop opening Chess shop: Hyakki mode detected')
                    return False
                if self._shop_open_confirm_marker_visible():
                    logger.debug(
                        f'Chess shop opened successfully, attempts={attempts}'
                    )
                    self._shop_assumed_open = True
                    return True

        logger.warning('Chess shop failed to open before timeout')
        return False

    def _ensure_battle_economy_shop_open(self) -> bool:
        """战斗中只开一次商店，不以可能被技能遮挡的刷新图标反复切换。"""
        if self._read_chess_mode() != '战':
            logger.debug('Stop battle economy shop open: mode is no longer 战')
            return False
        if getattr(self, '_shop_assumed_open', False):
            logger.debug('Chess battle economy shop is assumed open')
            return True
        if self._shop_open_confirm_marker_visible():
            self._shop_assumed_open = True
            logger.debug('Chess battle economy shop is visibly open')
            return True

        logger.debug(
            'Open Chess shop once for battle economy; subsequent state is '
            'tracked internally'
        )
        self.device.click_record_remove_rule(self.I_MARKET)
        self.click(self.I_MARKET)
        time.sleep(2 * self.SLOW_POLL_INTERVAL)
        self.screenshot()
        if self._read_chess_mode() != '战':
            logger.debug('Battle mode ended while opening economy shop')
            return False
        self._shop_assumed_open = True
        return True

    def _ensure_shop_closed(
        self,
        allowed_modes: tuple[str, ...] = ('备',),
    ) -> bool:
        """在指定模式内关闭商店；上卡默认仅允许“备”。"""
        if self._read_chess_mode() not in allowed_modes:
            logger.debug(
                'Stop closing Chess shop: mode is outside '
                f'{allowed_modes}'
            )
            return False
        shop_visible = self._shop_refresh_marker_visible()
        shop_assumed_open = getattr(self, '_shop_assumed_open', False)
        if not shop_visible and not shop_assumed_open:
            logger.debug('Chess shop is already closed')
            return True
        if self._read_chess_mode() != '战' and not shop_visible:
            # 非战斗画面刷新按钮不会被技能遮挡；看不到即以实际画面为准，
            # 清除跨阶段遗留的内部状态，禁止误点后把商店反而打开。
            self._shop_assumed_open = False
            logger.debug('Chess shop is visibly closed; clear stale state')
            return True
        if (
            self._read_chess_mode() in ('鬼', '待')
            and not shop_visible
        ):
            # 进入鬼/待后游戏会自行收起商店；内部状态可能仍停留在上一帧。
            # 此时不能点击不可用的商店位置，只清除脚本侧状态。
            self._shop_assumed_open = False
            logger.debug('Clear stale Chess shop state in passive mode')
            return True

        logger.debug('Chess shop is open, click market to close it')
        self.device.click_record_remove_rule(self.I_MARKET)
        # 战斗中刷新按钮可能完全被技能遮挡。若商店仅由内部状态确认，
        # 固定点击一次即可关闭，禁止进入“看不见 -> 再点一次”的抖动。
        if (
            self._read_chess_mode() == '战'
            and shop_assumed_open
            and not shop_visible
        ):
            self.click(self.I_MARKET)
            time.sleep(2 * self.SLOW_POLL_INTERVAL)
            self.screenshot()
            self._shop_assumed_open = False
            logger.debug('Chess battle economy shop closed by one-shot toggle')
            return True

        deadline = time.monotonic() + self.SHOP_CLOSE_TIMEOUT
        while time.monotonic() < deadline:
            if self._read_chess_mode() not in allowed_modes:
                logger.debug(
                    'Stop closing Chess shop: mode changed outside '
                    f'{allowed_modes}'
                )
                return False
            self.click(self.I_MARKET)
            attempt_deadline = min(
                deadline,
                time.monotonic() + 2 * self.SLOW_POLL_INTERVAL,
            )
            while time.monotonic() < attempt_deadline:
                time.sleep(self.SCREENSHOT_INTERVAL)
                self.screenshot()
                if self._read_chess_mode() not in allowed_modes:
                    logger.debug(
                        'Stop closing Chess shop: mode changed outside '
                        f'{allowed_modes}'
                    )
                    return False
                if not self._is_shop_open():
                    logger.debug('Chess shop closed successfully')
                    self._shop_assumed_open = False
                    return True

        logger.warning('Chess shop failed to close before timeout')
        return False

    def _clear_economy_click_history(self) -> None:
        """豁免合法的经验/刷新循环，保留其他按钮的全局防重复点击保护。"""
        removed_experience = self.device.click_record_remove_rule(self.I_EXPERIENCE)
        removed_refresh = self.device.click_record_remove_rule(self.I_REFRESH)
        if removed_experience or removed_refresh:
            logger.debug(
                'Clear Chess economy click history before legal loop: '
                f'experience={removed_experience}, refresh={removed_refresh}'
            )


    def _recognize_shop_slot(
        self,
        slot_index: int,
        click_rule: RuleClick,
        expected_name: str | None = None,
    ) -> dict | None:
        """商店统一识别入口：金币定位，Frame 读取名字、特质及头像。"""
        return self._frame_shop_in_slot(click_rule, expected_name=expected_name)

    @staticmethod
    def _template_gray(image: np.ndarray) -> np.ndarray:
        if image.ndim == 2:
            return image
        if image.shape[2] == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


    @staticmethod
    def _slot_click_record_key(click_rule) -> str:
        """设备层点击记录里的键。

        `Control.click()` 写记录用的是 `control_name`（即 `RuleClick.name`
        字符串），而不是规则对象；历史代码用 `click_record_remove(click_rule)`
        删除对象，实测什么也删不掉（no-op），于是同一格多次购买会累计到
        10 次触发全局 GameTooManyClickError。这里统一按名字取键。
        """
        name = getattr(click_rule, 'name', None)
        return str(name) if name else str(click_rule)

    def _clear_slot_click_record(self, click_rule) -> int:
        """清掉该商店格在设备点击记录里的历史，返回删除条数。"""
        key = self._slot_click_record_key(click_rule)
        removed = int(self.device.click_record_remove(key) or 0)
        if removed:
            logger.debug(
                f'Chess cleared {removed} click record(s) for shop slot: {key}'
            )
        return removed

    def _buy_shop_slot(
        self,
        slot_index: int,
        click_rule: RuleClick,
        matched_name: str,
    ) -> bool:
        """持续点击目标商店格，直到原头像不再出现在该格。"""
        known_price = SHIKIGAMI_BY_ROMAJI[matched_name].cost
        deadline = time.monotonic() + self.SHOP_BUY_TIMEOUT
        current_match = self._recognize_shop_slot(
            slot_index,
            click_rule,
            expected_name=matched_name,
        )
        # A target missing before any click is not a confirmed purchase.
        if current_match is None:
            return False
        attempts = 0
        emergency_cleanup_done = False
        lineup_sale_done = False

        while current_match is not None and time.monotonic() < deadline:
            if not self._is_purchase_allowed():
                logger.debug(
                    f'Stop buying {matched_name}: Hyakki mode detected'
                )
                return False
            if not self._can_afford_shop_shikigami(
                slot_index,
                known_price=known_price,
            ):
                logger.debug(
                    f'Skip buying {matched_name}: insufficient gold for '
                    f'shop slot {slot_index}'
                )
                return False
            if attempts >= self.SHOP_BUY_MAX_ATTEMPTS:
                logger.warning(
                    f'Chess buy {matched_name} cancelled after '
                    f'{attempts} attempts: slot {slot_index} still shows the '
                    'same card (hand full or purchase rejected)'
                )
                return False
            attempts += 1
            # 同一格本来就是重复点击的合法目标。设备层的「同一按钮 10 次」
            # 保护会把它判成卡死并抛 GameTooManyClickError 整局失败；
            # 每次尝试前清掉该格的点击记录，让保护只在单次尝试内生效。
            self._clear_slot_click_record(click_rule)
            logger.info(
                f'Buy Chess card: '
                f'{self._shikigami_display_name(matched_name)} '
                f'(slot={slot_index}, '
                f'attempt={attempts}, source={current_match["source"]}, '
                f'identity_score={current_match["score"]:.3f})'
            )
            clicked_and_gone = self.ui_click_until_disappear(
                click_rule,
                click_target=click_rule,
                visible=lambda: self._recognize_shop_slot(
                    slot_index, click_rule, expected_name=matched_name,
                ) is not None,
                guard=lambda: self._is_purchase_allowed() and not self.appear(self.I_SELECT_GRIGRI),
                interval=0.3,
                timeout=min(2.0, max(0.0, deadline-time.monotonic())),
                confirm_frames=2,
                max_clicks=3,
            )
            if clicked_and_gone:
                # 已确认购买完成，旧卡的点击不应计入此格下一张卡。
                self._clear_slot_click_record(click_rule)
                return True
            if not self._is_purchase_allowed() or self.appear(self.I_SELECT_GRIGRI):
                return False
            current_match = self._recognize_shop_slot(
                slot_index,
                click_rule,
                expected_name=matched_name,
            )
            if current_match is None:
                # Timeout or pre-click disappearance did not pass two-frame confirmation.
                return False
            if current_match is not None:
                if not self._is_purchase_allowed():
                    logger.debug(
                        f'Stop buying {matched_name}: Hyakki mode detected'
                    )
                    return False
                if not self._can_afford_shop_shikigami(
                    slot_index,
                    known_price=known_price,
                ):
                    logger.debug(
                        f'Stop retrying {matched_name}: insufficient gold '
                        f'for shop slot {slot_index}'
                    )
                    return False
                logger.debug(
                    f'Chess shop slot {slot_index} still matches '
                    f'{matched_name} after click, free hand space and retry'
                )
                if emergency_cleanup_done and lineup_sale_done:
                    logger.warning(
                        f'Chess buy {matched_name} remains blocked after '
                        'emergency cleanup and one lineup-card sale; cancel current purchase'
                    )
                    return False
                sell_lineup = emergency_cleanup_done
                recovery = self._free_one_hand_slot_for_purchase(
                    sell_lineup=sell_lineup,
                )
                if recovery is None:
                    logger.warning(
                        f'Chess buy {matched_name} is still unconfirmed and '
                        'no safe hand card can be cleared; block shop refresh'
                    )
                    return False
                if sell_lineup or recovery.get('type') == 'lineup':
                    emergency_cleanup_done = True
                    lineup_sale_done = True
                    logger.debug(
                        f'Retry Chess buy {matched_name} after selling one '
                        'lineup card'
                    )
                else:
                    emergency_cleanup_done = True
                    logger.debug(
                        f'Retry Chess buy {matched_name} after emergency '
                        'hand cleanup'
                    )

        if current_match is None:
            logger.debug(
                'Chess shop purchase succeeded by identity disappearance: '
                f'slot={slot_index}, name={matched_name}, attempts={attempts}'
            )
            return True

        logger.warning(
            f'Chess shop purchase timed out: slot={slot_index}, '
            f'name={matched_name}, identity remains in slot'
        )
        return False

    def _shop_slot_has_card(self, click_rule) -> bool:
        """该格是否**有卡片**（不要求识别出名字）。

        `_frame_card_scan('store')` 会返回所有检测到的卡框（未识别出名字的
        name 为 None），所以能区分「空格子」和「有卡但认不出」。
        实测：低阶商店只有 3~4 张卡，最左边的格子是空的；旧逻辑把它记成
        「未识别」，导致「识别失败率」被高估到 7%。
        """
        try:
            x, y, w, h = click_rule.roi_back
            for card in self._frame_card_scan('store'):
                cx, cy = card['position']
                if x <= cx < x + w and y <= cy < y + h:
                    return True
        except Exception as error:  # noqa: BLE001 - 判定失败按「有卡」保守处理
            logger.debug(f'Chess shop slot card check failed: {error!r}')
            return True
        return False

    # 过渡位不追升星：临时占场的低费卡一旦阵容换血就会被当杂卡卖掉
    # （实测 validate4 一局里 凤凰火 买 3 张、卖 3 张）。所以过渡位只买一张。
    TRANSITION_COPY_LIMIT = 1

    def _skip_transition_duplicate(
        self,
        name: str,
        copies_in_hand: int,
    ) -> bool:
        """过渡位已有 1 张在手时不再购买；正式阵容卡不受影响。"""
        if not name:
            return False
        is_transition = getattr(self, 'is_transition_unit', None)
        if not callable(is_transition):
            return False
        try:
            if not is_transition(name):
                return False
        except Exception:  # noqa: BLE001 - 读不到阵容信息时不拦购买
            return False
        return int(copies_in_hand or 0) >= self.TRANSITION_COPY_LIMIT

    def buy_lineup_shikigami_from_shop(self) -> list[str] | None:
        """整页识别一次记录目标，再逐格复核头像并购买。"""
        if not self._is_purchase_allowed():
            logger.debug('Stop Chess shop purchase: Hyakki mode detected')
            return None
        if not self._ensure_shop_open():
            return None

        logger.debug('Scan all Chess shop slots before purchasing')
        targets = []
        recognized_slots = {}
        shop_offers: list[str] = []

        # 手牌里各阵容卡已有几张：只算一次，避免每格都重扫手牌。
        hand_lineup_counts: Counter = Counter()
        try:
            for card in self._hand_card_detections():
                info = self.classify_hand_card(card['roi'])
                card_name = info.get('name') if isinstance(info, dict) else None
                if card_name:
                    hand_lineup_counts[card_name] += 1
        except Exception as error:  # noqa: BLE001 - 统计失败不应拦购买
            logger.debug(f'Chess shop hand count scan failed: {error!r}')

        # 本次商店循环里已买了几张：防止同一刷新中同名卡占两格时都买，
        # 也覆盖「商店面板遮住手牌 → hand_lineup_counts 恒为 0」的退化情况。
        bought_in_pass: Counter = Counter()

        for slot_index, click_rule in self._shop_slots():
            if not self._is_purchase_allowed():
                logger.debug('Stop Chess shop purchase: Hyakki mode detected')
                return None
            matched = self._recognize_shop_slot(
                slot_index,
                click_rule,
            )
            if matched is None:
                # 低阶商店只有 3~4 张卡，其余格子是空的。以前把空格也记成
                # 「未识别」，把 7% 的「识别失败率」几乎全填成了空格。
                if not self._shop_slot_has_card(click_rule):
                    recognized_slots[slot_index] = '空'
                    logger.debug(
                        f'Chess shop slot {slot_index}: empty (no card)'
                    )
                    continue
                recognized_slots[slot_index] = '未识别'
                logger.debug(
                    f'Chess shop slot {slot_index}: identity not recognized'
                )
                continue

            shop_offers.append(matched['name'])
            recognized_slots[slot_index] = self._shikigami_display_name(
                matched['name']
            )

            if matched['name'] not in self.shikigami_deploy_positions:
                logger.debug(
                    f'Chess shop slot {slot_index}: recognized non-lineup '
                    f'card {matched["name"]}'
                )
                continue

            copies_known = (
                hand_lineup_counts.get(matched['name'], 0)
                + bought_in_pass.get(matched['name'], 0)
            )
            if self._skip_transition_duplicate(matched['name'], copies_known):
                logger.info(
                    'Chess shop skip transition duplicate: '
                    f'{self._shikigami_display_name(matched["name"])}, '
                    f'copies={copies_known} '
                    '(过渡位只买一张，换血后会变成杂卡)'
                )
                continue

            logger.debug(
                f'Chess shop slot {slot_index}: {matched["source"]} -> '
                f'{matched["name"]}, score={matched["score"]:.3f}'
            )
            targets.append({
                'slot_index': slot_index,
                'click_rule': click_rule,
                'matched_name': matched['name'],
                'known_price': SHIKIGAMI_BY_ROMAJI[matched['name']].cost,
            })

        # 随机应变：记录本回合商店里出现过的卡，供下一次选羁绊参考。
        note_offers = getattr(self, 'note_adaptive_shop_offers', None)
        if callable(note_offers):
            note_offers(shop_offers)
        # 资源编号从右向左为 1→5；日志按玩家看到的左→右输出 5→1。
        logger.info(
            'Chess shop recognized (left->right): '
            + ' | '.join(
                recognized_slots.get(slot_index, '未识别')
                for slot_index in range(5, 0, -1)
            )
        )
        unknown_slots = [
            slot_index for slot_index, name in recognized_slots.items()
            if name == '未识别'
        ]
        if unknown_slots:
            # 存下整页样本，供离线分类（图标缺失/阈值/动画遮挡/新式神…）
            # 与回归测试使用；只存有未识别的帧，并有总量上限。
            self._dump_unknown_shop_frame(recognized_slots, unknown_slots)
        logger.debug(
            'Chess shop target scan complete: '
            f'{[(item["slot_index"], item["matched_name"]) for item in targets]}'
        )
        purchased = []
        for target in targets:
            if not self._is_purchase_allowed():
                logger.debug('Stop Chess shop purchase: Hyakki mode detected')
                return None
            if not self._can_afford_shop_shikigami(
                target['slot_index'],
                known_price=target['known_price'],
            ):
                logger.debug(
                    'Skip unaffordable Chess shop target: '
                    f'slot={target["slot_index"]}, '
                    f'name={target["matched_name"]}'
                )
                continue
            if self._buy_shop_slot(
                slot_index=target['slot_index'],
                click_rule=target['click_rule'],
                matched_name=target['matched_name'],
            ):
                purchased.append(target['matched_name'])
                bought_in_pass[target['matched_name']] += 1
                # Reuse the two-frame disappearance confirmation screenshot;
                # the next target rechecks only its own portrait before clicking.
            elif not self._is_purchase_allowed():
                return None
            elif not self._can_afford_shop_shikigami(
                target['slot_index'],
                known_price=target['known_price'],
            ):
                logger.debug(
                    'Chess target became unaffordable; skip it and continue: '
                    f'slot={target["slot_index"]}, '
                    f'name={target["matched_name"]}'
                )
                continue
            else:
                logger.warning(
                    'Chess target purchase was not confirmed by avatar '
                    'disappearance; '
                    f'slot={target["slot_index"]}, '
                    f'name={target["matched_name"]}; '
                    'stop this shop cycle before any refresh'
                )
                return None

        logger.debug(f'Chess shop check complete, purchased={purchased}')
        return purchased

    def purchase_lineup_cards_once(self) -> list[str] | None:
        """确保商店打开后扫描并购买，不负责关闭商店。"""
        return self.buy_lineup_shikigami_from_shop()

    # 未识别样本上限：整页 PNG 约 1.5MB，20 张 ≈ 30MB，够做分类与回归。
    SHOP_UNKNOWN_DUMP_LIMIT = 20

    def _dump_unknown_shop_frame(self, recognized_slots, unknown_slots) -> None:
        """保存含「未识别」格的商店整页截图 + 每格识别结果（JSON）。

        目的：把识图失败变成**可复现样本**。离线用
        `dev_tools/shop_frame_scan.py <png>` 重跑识别链路，即可判断是
        图标缺失、阈值偏低、动画遮挡还是新式神，而不是靠猜。
        """
        count = int(getattr(self, '_shop_unknown_dumps', 0))
        if count >= self.SHOP_UNKNOWN_DUMP_LIMIT:
            return
        try:
            image = self.device.image
            folder = Path('./log/chess_ref/shop_unknown')
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%H%M%S_%f')[:-3]
            name = f'{stamp}_{len(unknown_slots)}unknown'
            cv2.imwrite(
                str(folder / f'{name}.png'),
                cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
            )
            meta = {
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'round': getattr(self, '_current_round_no', None),
                'mode': self._read_chess_mode(),
                'recognized_slots': {
                    str(key): value for key, value in recognized_slots.items()
                },
                'unknown_slots': list(unknown_slots),
                'gold': self._read_shop_gold(),
            }
            (folder / f'{name}.json').write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            self._shop_unknown_dumps = count + 1
            logger.info(
                'Chess shop unknown-frame saved: '
                f'{name}.png slots={sorted(unknown_slots)}'
            )
        except Exception as error:  # noqa: BLE001 - 存样本失败不能中断经济
            logger.debug(f'Chess shop unknown-frame dump failed: {error!r}')

    def _reset_economy_state(self) -> None:
        """重置单局可暂停的回目结束任务和商店状态。"""
        self._economy_pending = False
        self._economy_step_state = 'idle'
        self._economy_sequence_level = None
        self._economy_sequence_index = 0
        self._economy_battle_mode = False
        self._shop_assumed_open = False

    def _schedule_economy_cycle(self) -> None:
        """登记一次经济任务；已有未完成原子动作时保持其精确进度。"""
        if getattr(self, '_economy_pending', False):
            logger.debug(
                'Chess economy is already pending: '
                f'state={self._economy_step_state}'
            )
            return
        self._economy_pending = True
        self._economy_step_state = 'ready'
        logger.debug('Schedule Chess economy upgrade/refresh cycle')


    def _finish_economy_cycle(self, reason: str) -> None:
        self._economy_pending = False
        self._economy_step_state = 'idle'
        logger.debug(f'Chess economy cycle complete: {reason}')

    def _click_economy_button_and_confirm_gold(
        self,
        button: RuleImage,
        expected_cost: int,
        label: str,
        allow_hidden: bool,
    ) -> str:
        """点击经济按钮，以金币下降确认；返回 success/no_progress/unknown。"""
        gold_before = self._read_shop_gold()
        if gold_before is None:
            logger.warning(f'Cannot confirm Chess {label}: gold OCR unavailable')
            return 'no_progress'
        if not allow_hidden and not self.appear(button):
            logger.warning(f'Cannot execute Chess {label}: button is missing')
            return 'no_progress'

        for attempt in range(1, self.ECONOMY_CONFIRM_RETRIES + 1):
            logger.debug(
                f'Chess {label}: fixed click attempt={attempt}, '
                f'gold_before={gold_before}'
            )
            self._clear_economy_click_history()
            self.click(button)
            time.sleep(self.ACTION_SETTLE_INTERVAL)
            self.screenshot()
            gold_after = self._read_shop_gold()
            if gold_after is None:
                logger.warning(
                    f'Chess {label} was clicked but confirmation OCR is '
                    'unavailable; preserve forward progress'
                )
                return 'unknown'
            if gold_after <= gold_before - expected_cost:
                logger.debug(
                    f'Chess {label} confirmed by gold: '
                    f'{gold_before} -> {gold_after}'
                )
                return 'success'
            logger.warning(
                f'Chess {label} click made no confirmed progress: '
                f'{gold_before} -> {gold_after}'
            )

        return 'no_progress'

    def _shop_slot_refresh_snapshot(self) -> dict[int, dict]:
        """保存五个商店槽位的身份和低分辨率图像，用于确认刷新。"""
        snapshot = {}
        image_height, image_width = self.device.image.shape[:2]
        for slot_index, click_rule in self._shop_slots():
            matched = self._recognize_shop_slot(
                slot_index,
                click_rule,
            )
            x, y, width, height = click_rule.roi_back
            x = max(0, int(x))
            y = max(0, int(y))
            width = min(int(width), image_width - x)
            height = min(int(height), image_height - y)
            crop = self.device.image[y:y + height, x:x + width]
            if crop.size:
                gray = self._template_gray(crop)
                signature = cv2.resize(
                    gray,
                    (32, 32),
                    interpolation=cv2.INTER_AREA,
                )
            else:
                signature = None
            snapshot[slot_index] = {
                'name': None if matched is None else matched['name'],
                'image': signature,
            }
        return snapshot

    def _shop_refresh_changed_slots(
        self,
        before: dict[int, dict],
        after: dict[int, dict],
    ) -> tuple[list[int], dict[int, float]]:
        """比较对应槽位；身份可用时比较身份，否则以卡面图像差异兜底。"""
        changed = []
        image_differences = {}
        for slot_index in range(1, 6):
            previous = before[slot_index]
            current = after[slot_index]
            previous_name = previous['name']
            current_name = current['name']
            previous_image = previous['image']
            current_image = current['image']
            difference = 0.0
            if previous_image is not None and current_image is not None:
                difference = float(np.mean(cv2.absdiff(
                    previous_image,
                    current_image,
                )))
            image_differences[slot_index] = difference
            if previous_name is not None and current_name is not None:
                slot_changed = previous_name != current_name
            else:
                slot_changed = (
                    difference
                    >= self.SHOP_REFRESH_SLOT_IMAGE_DIFF_THRESHOLD
                )
            if slot_changed:
                changed.append(slot_index)
        return changed, image_differences

    def _click_shop_refresh_and_confirm_slots(
        self,
        allow_hidden: bool,
    ) -> str:
        """点击刷新，以至少三个对应商店槽位变化确认成功。"""
        if not allow_hidden and not self.appear(self.I_REFRESH):
            logger.warning('Cannot refresh Chess shop: button is missing')
            return 'no_progress'

        before = self._shop_slot_refresh_snapshot()
        for attempt in range(1, self.ECONOMY_CONFIRM_RETRIES + 1):
            self._clear_economy_click_history()
            self.click(self.I_REFRESH)
            time.sleep(self.ACTION_SETTLE_INTERVAL)
            self.screenshot()
            after = self._shop_slot_refresh_snapshot()
            changed, differences = self._shop_refresh_changed_slots(
                before,
                after,
            )
            logger.info(
                'Chess shop refresh slot comparison: '
                f'attempt={attempt}, changed={changed}, '
                f'changed_count={len(changed)}/5, '
                f'before_names='
                f'{[before[index]["name"] for index in range(5, 0, -1)]}, '
                f'after_names='
                f'{[after[index]["name"] for index in range(5, 0, -1)]}, '
                f'image_diff='
                f'{[round(differences[index], 2) for index in range(5, 0, -1)]}'
            )
            if len(changed) >= self.SHOP_REFRESH_CHANGED_SLOT_MINIMUM:
                for slot_index, click_rule in self._shop_slots():
                    if slot_index in changed:
                        self.device.click_record_remove_rule(click_rule)
                return 'success'
            before = after
        logger.warning(
            'Chess shop refresh was not confirmed: fewer than '
            f'{self.SHOP_REFRESH_CHANGED_SLOT_MINIMUM} slots changed'
        )
        return 'no_progress'

    def _economy_sequence_for_level(self, level: int) -> tuple[str, ...]:
        """返回当前阶数的原子操作序列。"""
        if level >= self._lineup_final_level():
            return ('refresh',)
        if level <= 2:
            return ('experience',)
        if level <= 7:
            return ('experience', 'refresh')
        return ('experience', 'refresh', 'refresh')

    # 经济停止阈值：为「上阵补人」预留固定金币。中后期原先只留 10~23 金，
    # 刷新/买经验常把金币花到上阵不够，回目里就少上人；统一抬到 25 金，
    # 让经济操作提前收手，把补上阵的窗口留出来。
    ECONOMY_DEPLOY_RESERVE = 25

    def _economy_reserve_for_level(self, level: int) -> int:
        if level >= self._lineup_final_level():
            return 0
        if level <= 2:
            return 0
        if level <= 5:
            return 35
        return self.ECONOMY_DEPLOY_RESERVE

    def _reset_economy_sequence_if_level_changed(self, level: int) -> None:
        if self._economy_sequence_level == level:
            return
        logger.debug(
            'Reset Chess economy operation counter: '
            f'{self._economy_sequence_level} -> {level}'
        )
        self._economy_sequence_level = level
        self._economy_sequence_index = 0

    def _next_economy_operation(self, level: int) -> str:
        self._reset_economy_sequence_if_level_changed(level)
        sequence = self._economy_sequence_for_level(level)
        index = self._economy_sequence_index % len(sequence)
        operation = sequence[index]
        logger.debug(
            'Chess economy next operation: '
            f'level={level}, sequence={sequence}, index={index}, '
            f'operation={operation}'
        )
        return operation

    def _advance_economy_operation_counter(self, level: int) -> None:
        self._reset_economy_sequence_if_level_changed(level)
        sequence = self._economy_sequence_for_level(level)
        self._economy_sequence_index = (
            self._economy_sequence_index + 1
        ) % len(sequence)
        logger.debug(
            'Advance Chess economy operation counter: '
            f'level={level}, next_index={self._economy_sequence_index}'
        )

    def _can_execute_economy_operation(
        self,
        level: int,
        gold: int,
        operation: str,
    ) -> bool:
        reserve = self._economy_reserve_for_level(level)
        cost = (
            self.EXPERIENCE_COST
            if operation == 'experience'
            else self.SHOP_REFRESH_COST
        )
        if level < self._lineup_final_level() and gold <= reserve:
            return False
        return gold >= reserve + cost

    def _run_economy_atomic_batch(self, battle_mode: bool = False) -> str:
        """执行一个由计数器决定的升级/刷新原子动作。"""
        if not getattr(self, '_economy_pending', False):
            return 'complete'
        if not self._is_purchase_allowed():
            logger.debug('Pause Chess economy: Hyakki mode detected')
            return 'blocked'

        previous_battle_mode = getattr(self, '_economy_battle_mode', False)
        self._economy_battle_mode = battle_mode
        try:
            level = self._read_level()
            gold = self._read_shop_gold()
            if level is None or gold is None:
                logger.warning(
                    'Pause Chess economy: level or gold OCR unavailable'
                )
                return 'blocked'

            operation = self._next_economy_operation(level)
            if not self._can_execute_economy_operation(level, gold, operation):
                self._finish_economy_cycle(
                    f'budget limit reached, level={level}, gold={gold}, '
                    f'operation={operation}, '
                    f'reserve={self._economy_reserve_for_level(level)}, '
                    f'final_level={self._lineup_final_level()}'
                )
                return 'complete'

            if operation == 'experience':
                result = self._click_economy_button_and_confirm_gold(
                    self.I_EXPERIENCE,
                    self.EXPERIENCE_COST,
                    'buy experience',
                    allow_hidden=battle_mode,
                )
                if result == 'no_progress':
                    return 'blocked'
                if result == 'success':
                    # 购买经验成功（金币下降已确认）后，等稳定帧用新鲜
                    # OCR 读数更新等级缓存：升级动画期间等级常读空，只靠
                    # 被动读帧会让兜底停在旧值（少上阵）。绝不猜测 +1，
                    # 读不到就保持缓存并打一行 INFO。
                    for _attempt in range(2):
                        time.sleep(self.ACTION_SETTLE_INTERVAL)
                        self.screenshot()
                        if self._level_label_visible():
                            re_read = self._read_level()
                            if re_read is not None:
                                logger.info(
                                    'Chess level re-read after experience '
                                    f'purchase: {re_read}'
                                )
                            else:
                                logger.info(
                                    'Chess level re-read after experience '
                                    'purchase: label visible but OCR '
                                    'failed; cache unchanged'
                                )
                            break
                    else:
                        logger.info(
                            'Chess level re-read after experience purchase: '
                            'label not visible; cache unchanged'
                        )
                self._advance_economy_operation_counter(level)
            else:
                # 只有刷新动作要求商店打开；购买经验不改变商店状态。
                if not self._ensure_shop_open():
                    return 'blocked'
                result = self._click_shop_refresh_and_confirm_slots(
                    allow_hidden=battle_mode,
                )
                if result == 'no_progress':
                    return 'blocked'
                logger.info('Refresh Chess shop completed')
                self._advance_economy_operation_counter(level)
                # 槽位变化确认时商店已完成换牌；仍额外等待稳定帧，
                # 再读取费用和羁绊，避免残余动画影响购买识别。
                time.sleep(self.ACTION_SETTLE_INTERVAL)
                self.screenshot()
                purchased = self.purchase_lineup_cards_once()
                if purchased is None:
                    logger.warning(
                        'Pause Chess economy after refresh: purchase not '
                        'confirmed'
                    )
                    return 'blocked'
            self._economy_step_state = 'ready'

            logger.debug(
                'Chess economy atomic batch finished: '
                f'operation={operation}, battle_mode={battle_mode}'
            )

            # 只判断是否还有下一批；真正执行留到外层重新截图、检查回目
            # 后，确保新回目的备阶段能够抢占长时间经济循环。
            level = self._read_level()
            gold = self._read_shop_gold()
            if level is None or gold is None:
                return 'pending'
            next_operation = self._next_economy_operation(level)
            if self._can_execute_economy_operation(
                level,
                gold,
                next_operation,
            ):
                return 'pending'
            self._finish_economy_cycle(
                f'budget limit reached after batch, level={level}, '
                f'gold={gold}, next_operation={next_operation}, '
                f'reserve={self._economy_reserve_for_level(level)}, '
                f'final_level={self._lineup_final_level()}'
            )
            return 'complete'
        finally:
            self._economy_battle_mode = previous_battle_mode
