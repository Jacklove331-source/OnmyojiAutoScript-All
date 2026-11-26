# This Python file uses the following encoding: utf-8

"""悬赏封印新 UI（2026-10）适配层：全部用 OCR 文本 + 文本块坐标驱动。

旧 UI 是「左侧一列任务 + 前往按钮模板」，新 UI 是：
- 面板：标题「悬赏封印」+ 3 张任务卡（任务文本 / 进度 a/b / 碎片奖励）+ 右下「追踪」+「下次刷新」；
- 点开某张卡 → 详情弹窗：「悬赏目标：击败N个X」+ 进度 + 推荐地点行（类型 / 地点(数量:N) / 前往）。

本模块只负责「看得懂 + 点得准」，具体打哪个本仍复用旧逻辑（secret/explore/challenge）。
坐标全部相对 1280x720；不同分辨率由 OAS 的 RuleOcr/RuleImage 自动缩放。
"""

import re
from time import sleep

from module.atom.ocr import RuleOcr
from module.logger import logger
from tasks.GameUi.page import page_main

# ---------------------------------------------------------------- 面板
O_WQ_PANEL_TITLE = RuleOcr(
    roi=(500, 14, 300, 52), area=(500, 14, 300, 52),
    mode='Single', method='Default', keyword='', name='wq2_panel_title')
O_WQ_REFRESH = RuleOcr(
    roi=(470, 632, 320, 48), area=(470, 632, 320, 48),
    mode='Single', method='Default', keyword='', name='wq2_refresh')
# 任务卡：**新版是四张**（2026-11-01 实机 OCR）：
#   任务文本 y≈456-459，x 起 155 / 429 / 699 / 988
#   进度「a/b」  y≈481-483，x 起 199 / 482 / 763 / 1040
# （2026-10-28 当时是三张、x 起 440/717/992，说明官方在陆续调整，识别要按四张写）
O_WQ_CARD_TEXT = (
    RuleOcr(roi=(140, 448, 260, 32), area=(140, 448, 260, 32),
            mode='Single', method='Default', keyword='', name='wq2_card1_text'),
    RuleOcr(roi=(415, 448, 250, 32), area=(415, 448, 250, 32),
            mode='Single', method='Default', keyword='', name='wq2_card2_text'),
    RuleOcr(roi=(685, 448, 250, 32), area=(685, 448, 250, 32),
            mode='Single', method='Default', keyword='', name='wq2_card3_text'),
    RuleOcr(roi=(975, 448, 260, 32), area=(975, 448, 260, 32),
            mode='Single', method='Default', keyword='', name='wq2_card4_text'),
)
# 进度行整条横带一次 Full OCR，再按 x 就近分配给四张卡（单卡微 ROI 误差大）
# 面板卡片整块扫描（覆盖四张卡 + 协作卡的两行任务）
O_WQ_PANEL_SCAN = RuleOcr(
    roi=(100, 420, 1110, 130), area=(100, 420, 1110, 130),
    mode='Full', method='Default', keyword='', name='wq2_panel_scan')

O_WQ_PROGRESS_SCAN = RuleOcr(
    roi=(140, 472, 1080, 32), area=(140, 472, 1080, 32),
    mode='Full', method='Default', keyword='', name='wq2_progress_scan')
O_WQ_CARD_CENTER = ((272, 470), (548, 470), (818, 470), (1100, 470))

# ---------------------------------------------------------------- 详情弹窗
# 详情页兜底判定：全屏扫「前往 + a/b 进度」
O_WQ_DETAIL_SCAN = RuleOcr(
    roi=(0, 120, 1280, 520), area=(0, 120, 1280, 520),
    mode='Full', method='Default', keyword='', name='wq2_detail_scan')

O_WQ_DETAIL_TITLE = RuleOcr(
    roi=(600, 148, 380, 44), area=(600, 148, 380, 44),
    mode='Single', method='Default', keyword='', name='wq2_detail_title')
O_WQ_DETAIL_PROGRESS = RuleOcr(
    roi=(820, 190, 160, 40), area=(820, 190, 160, 40),
    mode='Single', method='Default', keyword='', name='wq2_detail_prog')
_ROW_Y = (270, 346, 422, 498)   # 四行的左上 y（行高约 76）
O_WQ_ROW_TYPE = tuple(
    RuleOcr(roi=(636, y, 90, 40), area=(636, y, 90, 40),
            mode='Single', method='Default', keyword='',
            name=f'wq2_type_{index + 1}')
    for index, y in enumerate(_ROW_Y)
)
O_WQ_ROW_INFO = tuple(
    RuleOcr(roi=(708, y, 330, 40), area=(708, y, 330, 40),
            mode='Single', method='Default', keyword='',
            name=f'wq2_info_{index + 1}')
    for index, y in enumerate(_ROW_Y)
)
# 「前往」按钮扫描区（三行都在这一列，用文本块定位，避免模板失效）
O_WQ_GOTO_SCAN = RuleOcr(
    roi=(1050, 250, 200, 320), area=(1050, 250, 200, 320),
    mode='Full', method='Default', keyword='', name='wq2_goto_scan')
C_WQ_CANCEL = (1189, 583)      # 详情弹窗「取消」
C_WQ_TRACK = (1194, 631)       # 详情弹窗「追踪」
C_WQ_PANEL_ENTRY = (85, 340)   # 町中左侧「悬赏封印」入口

# 面板右下角的「前往探索」按钮（实测 1021,679 一带），用 OCR 定位以免坐标漂移
O_WQ_GOTO_EXPLORATION_SCAN = RuleOcr(
    roi=(940, 640, 340, 80), area=(940, 640, 340, 80),
    mode='Full', method='Default', keyword='', name='wq2_goto_exploration')

TYPE_ALIASES = {
    '妖气': '探索',
    '妖气封印': '探索',
    '探索': '探索',
    '秘闻': '秘闻',
    '式神': '式神',
    '挑战': '式神',
}


def wq2_map_type(row_type: str) -> str:
    """把新 UI 的地点类型映射成 OAS 内部类型（探索/秘闻/式神）。"""
    text = str(row_type or '').strip()
    if text in TYPE_ALIASES:
        return TYPE_ALIASES[text]
    for key, value in TYPE_ALIASES.items():
        if key and key in text:
            return value
    return text


# ---------------------------------------------------------------- 协作邀请弹窗
# 面板左侧协作卡上的「邀请好友」(246,416) → 弹窗：好友/跨区 两个页签 +
# 「邀请7级以上好友」+ 左列好友名单 + 右列「推荐阴阳师」+ 底部 取消(265,559)/邀请(536,557)
_WQ2_OCR_CONFUSIONS = (
    ('邀清', '邀请'), ('邀情', '邀请'), ('邀請', '邀请'),
    ('級', '级'), ('級', '级'),
    ('陰陽師', '阴阳师'), ('阴踢师', '阴阳师'),
    ('推荐隆阳师', '推荐阴阳师'),
)


def wq2_normalize(text: str) -> str:
    """把 OCR 常见误读归一到预期写法（比较关键字前先过一遍）。"""
    value = str(text or '')
    for wrong, right in _WQ2_OCR_CONFUSIONS:
        value = value.replace(wrong, right)
    return value


# 面板左侧的「邀请好友」入口：手机上坐标会漂，用 OCR 找文字更稳
O_WQ_INVITE_ENTRY_SCAN = RuleOcr(
    roi=(0, 150, 470, 470), area=(0, 150, 470, 470),
    mode='Full', method='Default', keyword='邀请', name='wq2_invite_entry_scan')

# 兜底判定用：全屏扫一遍（弹窗特征词）
O_WQ_INVITE_SCAN = RuleOcr(
    roi=(0, 60, 1280, 660), area=(0, 60, 1280, 660),
    mode='Full', method='Default', keyword='', name='wq2_invite_scan')

C_WQ_INVITE_ENTRY = (246, 416)
C_WQ_INVITE_ENSURE = (536, 557)
C_WQ_INVITE_CANCEL = (265, 559)
O_WQ_INVITE_TITLE = RuleOcr(
    roi=(440, 88, 400, 44), area=(440, 88, 400, 44),
    mode='Single', method='Default', keyword='', name='wq2_invite_title')
O_WQ_INVITE_FRIENDS = RuleOcr(
    roi=(206, 168, 420, 360), area=(206, 168, 420, 360),
    mode='Full', method='Default', keyword='', name='wq2_invite_friends')
# 名单里会混进来的非好友文本
_INVITE_NOISE = ('好友', '跨区', '邀请', '取消', '推荐阴阳师', '对方同意后自动发送协作邀请',
                 '不期而遇', '在线', '离线', '7级')


def wq2_is_friend_name(text: str) -> bool:
    """左列文本块是不是好友名：排除纯数字（等级如「55」）、纯符号和噪声词。"""
    value = str(text or '').strip()
    if not value:
        return False
    if any(noise in value for noise in _INVITE_NOISE):
        return False
    if value.isdigit():
        return False
    # 至少含一个中文或字母，纯符号/纯标点一律不要
    return any('\u4e00' <= ch <= '\u9fff' or ch.isalpha() for ch in value)


PROGRESS_RE = re.compile(r'(\d+)\s*[/7]\s*(\d+)')
NUMBER_RE = re.compile(r'^(.*?)[（(]?数量[：:]\s*(\d+)[)）]')


class WantedQuestsUiV2:
    """新 UI 的识别与点击原语；由 ScriptTask 混入使用。"""

    def wq2_map_type(self, row_type: str) -> str:
        """实例方法版类型映射（run_v2 通过 self 调用）。"""
        return wq2_map_type(row_type)

    # ------------------------------------------------------------ 面板
    def wq2_panel_visible(self) -> bool:
        """是否停在悬赏封印**面板**（标题或「下次刷新」任一命中，且不是详情弹窗）。"""
        if self.wq2_detail_visible():
            return False
        title = str(O_WQ_PANEL_TITLE.ocr(self.device.image) or '')
        refresh = str(O_WQ_REFRESH.ocr(self.device.image) or '')
        if '悬赏封印' in title or '下次刷新' in refresh:
            return True
        # 单帧 OCR 偶发读出乱码（实测 [43498]）：重拍一次再判
        self.screenshot()
        title = str(O_WQ_PANEL_TITLE.ocr(self.device.image) or '')
        refresh = str(O_WQ_REFRESH.ocr(self.device.image) or '')
        if '悬赏封印' in title or '下次刷新' in refresh:
            return True
        # 兜底（借鉴上游 PR #1987 的 wanted_board_open）：模板匹配对亮度不敏感，
        # 用「追踪按钮存在 且 没有详情/邀请弹窗」判定面板。
        return self._wq2_panel_by_templates()

    def _wq2_panel_by_templates(self) -> bool:
        try:
            if self.appear(self.I_WQ_TRACE_ONE_CHECK_OPENED) or self.appear(self.I_WQ_INVITE_ENSURE):
                return False
            return self.appear(self.I_TRACE_ENABLE) or self.appear(self.I_TRACE_DISABLE)
        except Exception:  # noqa: BLE001
            return False

    def wq2_ensure_panel(self, tries: int = 8) -> bool:
        """从任意界面（含战后探索页）稳定回到悬赏封印面板。"""
        for _ in range(tries):
            self.screenshot()
            if self.wq2_panel_visible():
                return True
            # 详情/弹窗先关
            if self.wq2_detail_visible():
                self.wq2_close_detail()
                sleep(0.8)
                continue
            # 探索页：先回主界面（町中入口只在庭院/町中上）
            try:
                if self.appear(self.I_CHECK_EXPLORATION):
                    logger.info('[WQ] on exploration page, back to main')
                    self.goto_page(page_main)
                    sleep(1.0)
                    continue
            except Exception as error:  # noqa: BLE001
                logger.debug(f'[WQ] exploration check failed: {error!r}')
            # 常规：点町中入口
            self.device.click(*C_WQ_PANEL_ENTRY, control_check=False)
            sleep(1.2)
        self.screenshot()
        return self.wq2_panel_visible()

    def wq2_open_panel(self, tries: int = 6) -> bool:
        """点开悬赏封印面板；已开详情弹窗时先关掉。"""
        self.screenshot()
        if self.wq2_detail_visible():
            self.wq2_close_detail()
        for _ in range(tries):
            self.screenshot()
            if self.wq2_panel_visible():
                return True
            self.device.click(*C_WQ_PANEL_ENTRY, control_check=False)
            sleep(1.2)
        self.screenshot()
        return self.wq2_panel_visible()

    def wq2_progress_tokens(self) -> list[tuple]:
        """读进度横带：返回 [(x_center_abs, current, total, raw), ...]（x 可能为 None）。"""
        try:
            items = O_WQ_PROGRESS_SCAN.detect_and_ocr(self.device.image) or []
        except Exception as error:  # noqa: BLE001
            logger.debug(f'wq2 progress scan failed: {error!r}')
            return []
        roi_x = O_WQ_PROGRESS_SCAN.roi[0]
        tokens = []
        for item in items:
            raw = str(getattr(item, 'ocr_text', '') or '').strip()
            cleaned = (raw.replace('V', '0').replace('O', '0').replace('l', '1')
                       .replace('I', '1').replace('／', '/').replace('：', '/')
                       .replace(' ', ''))
            match = PROGRESS_RE.search(cleaned)
            box = getattr(item, 'box', None)
            x_center = (roi_x + (int(box[0][0]) + int(box[2][0])) // 2) if box is not None else None
            if match:
                tokens.append((x_center, int(match.group(1)), int(match.group(2)), raw))
                continue
            digits = re.sub(r'\D', '', cleaned)
            if len(digits) == 2:
                tokens.append((x_center, int(digits[0]), int(digits[1]), raw))
        return tokens

    # 四张卡的 x 中心（实测 1280x720）
    _CARD_COLUMNS = (240, 510, 780, 1050)

    @staticmethod
    def _norm_progress(text: str):
        """把 OCR 出来的进度归一化成 (当前, 总数)；失败返回 (None, None)。"""
        import re as _re
        value = wq2_normalize(text).replace(' ', '')
        value = (value.replace('Oy', '0').replace('O/', '0/').replace('o/', '0/')
                      .replace('O', '0').replace('l', '1').replace('I', '1'))
        match = _re.search(r'(\d+)/(\d+)', value)
        if match:
            return int(match.group(1)), int(match.group(2))
        digits = _re.sub(r'\D', '', value)
        if len(digits) == 2:
            return int(digits[0]), int(digits[1])
        return None, None

    def wq2_cards(self) -> list[dict]:
        """读取任务卡：整块 OCR → 按列聚类 → 文本与进度按 y 就近配对。"""
        try:
            items = O_WQ_PANEL_SCAN.detect_and_ocr(self.device.image) or []
        except Exception as error:  # noqa: BLE001
            logger.debug(f'wq2 panel scan failed: {error!r}')
            return []
        roi_x, roi_y = O_WQ_PANEL_SCAN.roi[0], O_WQ_PANEL_SCAN.roi[1]

        columns = {index: [] for index in range(len(self._CARD_COLUMNS))}
        for item in items:
            text = str(getattr(item, 'ocr_text', '') or '').strip()
            box = getattr(item, 'box', None)
            if not text or box is None:
                continue
            try:
                x1, y1 = roi_x + int(box[0][0]), roi_y + int(box[0][1])
                x2, y2 = roi_x + int(box[2][0]), roi_y + int(box[2][1])
            except (IndexError, TypeError):
                continue
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            index = min(range(len(self._CARD_COLUMNS)),
                        key=lambda i: abs(self._CARD_COLUMNS[i] - cx))
            columns[index].append({'text': text, 'center': (cx, cy), 'y': cy})

        cards = []
        for index in range(len(self._CARD_COLUMNS)):
            rows = sorted(columns[index], key=lambda r: r['y'])
            # 进度候选
            progresses = []
            for row in rows:
                current, total = self._norm_progress(row['text'])
                if current is not None:
                    progresses.append((row, current, total))
            if not progresses:
                continue
            # 每个进度配它上方最近的一行非进度文本
            pairs = []
            for row, current, total in progresses:
                text_row = None
                for candidate in rows:
                    if candidate['y'] >= row['y']:
                        continue
                    if self._norm_progress(candidate['text'])[0] is not None:
                        continue
                    if text_row is None or candidate['y'] > text_row['y']:
                        text_row = candidate
                text = text_row['text'] if text_row else ''
                center = text_row['center'] if text_row else row['center']
                pairs.append({'index': index + 1, 'text': text,
                              'progress_text': row['text'],
                              'current': current, 'total': total,
                              'center': center})
            cards.extend(pairs)
        return cards

    def wq2_unfinished_cards(self) -> list[dict]:
        """返回未完成任务卡；空结果重扫一次（面板刚打开可能没渲染完）。"""
        cards = self.wq2_cards()
        logger.info('[WQ] card scan: ' + ' | '.join(
            f"#{c['index']} {c['text'][:12]!r} {c['progress_text']}" for c in cards))
        unfinished = [card for card in cards
                      if card['current'] is not None and card['total']
                      and card['current'] < card['total']]
        if unfinished:
            return unfinished
        sleep(1.0)
        self.screenshot()
        cards = self.wq2_cards()
        logger.info('[WQ] card scan(2nd): ' + ' | '.join(
            f"#{c['index']} {c['text'][:12]!r} {c['progress_text']}" for c in cards))
        unfinished = [card for card in cards
                      if card['current'] is not None and card['total']
                      and card['current'] < card['total']]
        # 「自己击败」优先，协作/好友卡（需要好友参与）排最后
        def rank(card):
            text = wq2_normalize(card.get('text', ''))
            if '自己' in text:
                return 0
            if '好友' in text or '协作' in text:
                return 2
            return 1
        return sorted(unfinished, key=rank)

    # ------------------------------------------------------------ 详情
    def wq2_detail_visible(self) -> bool:
        """详情弹窗判定。

        桌面版标题是「悬赏目标」，手机当前版本是「自己击败XX」，所以两种都认；
        再加一个结构化兜底：详情页一定同时有「前往」按钮和 a/b 进度。
        """
        text = wq2_normalize(O_WQ_DETAIL_TITLE.ocr(self.device.image))
        if '悬赏目标' in text or '击败' in text:
            return True
        try:
            items = O_WQ_DETAIL_SCAN.detect_and_ocr(self.device.image) or []
        except Exception:  # noqa: BLE001
            return False
        joined = wq2_normalize(' '.join(str(getattr(item, 'ocr_text', '') or '')
                                        for item in items))
        has_forward = '前往' in joined
        has_progress = bool(re.search(r'\d+\s*/\s*\d+', joined))
        return has_forward and has_progress

    def wq2_goto_exploration(self, tries: int = 3) -> bool:
        """点新 UI 的「前往探索」直达探索页（不绕庭院），并校验探索页标志。"""
        for _ in range(tries):
            self.screenshot()
            if self.appear(self.I_CHECK_EXPLORATION):
                return True
            if not self.wq2_panel_visible():
                self.wq2_open_panel()
                continue
            point = self._find_exploration_button()
            if point is None:
                logger.warning('[WQ] 面板上没找到「前往探索」按钮')
                return False
            logger.info(f'[WQ] 点击前往探索 ({point[0]},{point[1]})')
            self.device.click(point[0], point[1], control_check=False)
            sleep(2.0)
            self.screenshot()
            if self.appear(self.I_CHECK_EXPLORATION):
                return True
        self.screenshot()
        return self.appear(self.I_CHECK_EXPLORATION)

    def _find_exploration_button(self):
        """在面板右下角找「前往探索」文本块，返回其中心（已加回 ROI 原点）。"""
        try:
            items = O_WQ_GOTO_EXPLORATION_SCAN.detect_and_ocr(self.device.image) or []
        except Exception:  # noqa: BLE001
            return None
        roi_x, roi_y = O_WQ_GOTO_EXPLORATION_SCAN.roi[0], O_WQ_GOTO_EXPLORATION_SCAN.roi[1]
        for item in items:
            if '前往探索' not in str(getattr(item, 'ocr_text', '') or ''):
                continue
            box = getattr(item, 'box', None)
            if box is None:
                continue
            return (roi_x + (int(box[0][0]) + int(box[2][0])) // 2,
                    roi_y + (int(box[0][1]) + int(box[2][1])) // 2)
        return None

    def wq2_open_card(self, card: dict, tries: int = 4) -> bool:
        for _ in range(tries):
            self.screenshot()
            if self.wq2_detail_visible():
                return True
            self.device.click(*card['center'], control_check=False)
            sleep(1.2)
        self.screenshot()
        return self.wq2_detail_visible()

    def wq2_detail_progress(self) -> tuple:
        text = str(O_WQ_DETAIL_PROGRESS.ocr(self.device.image) or '')
        match = PROGRESS_RE.search(text)
        if not match:
            return None, None
        return int(match.group(1)), int(match.group(2))

    def wq2_rows(self) -> list[dict]:
        """读取详情弹窗的推荐地点行：类型 / 地点(数量:N) / 前往按钮中心。"""
        gotos = self.wq2_goto_boxes()
        rows = []
        for index in range(4):
            row_type = str(O_WQ_ROW_TYPE[index].ocr(self.device.image) or '').strip()
            info = str(O_WQ_ROW_INFO[index].ocr(self.device.image) or '').strip()
            info_norm = (info.replace('：', ':').replace('（', '(')
                         .replace('）', ')'))
            match = NUMBER_RE.match(info_norm)
            if not row_type and not match:
                continue
            destination = match.group(1).strip() if match else ''
            number = int(match.group(2)) if match else 0
            y_center = _ROW_Y[index] + 20
            goto = None
            if gotos:
                # 选纵向最接近该行的「前往」
                goto = min(gotos, key=lambda box: abs(box[1] - y_center))
                if abs(goto[1] - y_center) > 60:
                    goto = None
            rows.append({
                'row': index + 1,
                'type': row_type,
                'info': info,
                'destination': destination,
                'number': number,
                'goto': goto,
            })
        self._wq2_fill_row_counts(rows)
        return rows

    def _wq2_fill_row_counts(self, rows: list[dict]) -> None:
        """窄区 OCR 漏读数量时，用整页识别补同一行的地点和数量。"""
        missing = [row for row in rows if row['goto'] and not row['number']]
        if not missing:
            return
        try:
            items = O_WQ_DETAIL_SCAN.detect_and_ocr(self.device.image) or []
        except Exception as error:  # noqa: BLE001
            logger.debug(f'wq2 detail scan failed: {error!r}')
            return
        roi_x, roi_y = O_WQ_DETAIL_SCAN.roi[:2]
        for item in items:
            text = str(item.ocr_text or '').strip()
            match = NUMBER_RE.match(text)
            if not match or item.box is None:
                continue
            x = roi_x + int(item.box[0][0])
            y = roi_y + (int(item.box[0][1]) + int(item.box[2][1])) // 2
            if not 690 <= x < O_WQ_GOTO_SCAN.roi[0]:
                continue
            row = min(missing, key=lambda candidate: abs(candidate['goto'][1] - y))
            if abs(row['goto'][1] - y) <= 30 and not row['number']:
                row.update(info=text, destination=match[1].strip(), number=int(match[2]))

    def wq2_goto_boxes(self) -> list[tuple]:
        """返回所有「前往」文本块的中心点列表。"""
        try:
            items = O_WQ_GOTO_SCAN.detect_and_ocr(self.device.image)
        except Exception as error:  # noqa: BLE001
            logger.debug(f'wq2 goto scan failed: {error!r}')
            return []
        boxes = []
        for item in items or []:
            text = str(getattr(item, 'ocr_text', '') or '')
            if '前往' not in text:
                continue
            box = getattr(item, 'box', None)
            if box is None:
                continue
            try:
                x1, y1 = int(box[0][0]), int(box[0][1])
                x2, y2 = int(box[2][0]), int(box[2][1])
            except (IndexError, TypeError):
                continue
            roi_x, roi_y = O_WQ_GOTO_SCAN.roi[0], O_WQ_GOTO_SCAN.roi[1]
            boxes.append(((roi_x + x1 + roi_x + x2) // 2,
                          (roi_y + y1 + roi_y + y2) // 2))
        return boxes

    # ------------------------------------------------------------ 协作邀请
    def wq2_invite_dialog_visible(self) -> bool:
        """邀请弹窗是否已打开：只认弹窗特有的「邀请7级以上好友 / 推荐阴阳师」。

        实测：面板和详情页都没有这两处文本，用宽松条件（好友+取消）会误判。
        """
        # OCR 会把「邀请」读成「邀清/邀情」、「级」读成「級」→ 先归一化再比关键字
        title = wq2_normalize(O_WQ_INVITE_TITLE.ocr(self.device.image))
        if '邀请7级' in title or '邀请20级' in title or '推荐' in title:
            logger.debug(f'[WQ] invite dialog by title: {title!r}')
            return True
        try:
            items = O_WQ_INVITE_FRIENDS.detect_and_ocr(self.device.image)
        except Exception:  # noqa: BLE001
            return False
        joined = wq2_normalize(' '.join(str(getattr(item, 'ocr_text', '') or '')
                                        for item in items or []))
        if '邀请7级以上好友' in joined or '邀请20级以上好友' in joined or '推荐' in joined:
            return True
        # 兜底：弹窗特有组合（取消 + 邀请 + 推荐），面板本身没有这组文案
        try:
            full = wq2_normalize(' '.join(
                str(getattr(item, 'ocr_text', '') or '')
                for item in (O_WQ_INVITE_SCAN.detect_and_ocr(self.device.image) or [])))
        except Exception:  # noqa: BLE001
            return False
        return '取消' in full and '邀请' in full and '推荐' in full

    def wq2_click_invite_entry(self) -> bool:
        """用 OCR 在面板左侧找「邀请」入口并点击（坐标会随 UI/分辨率漂移）。

        返回是否用 OCR 命中；未命中时退回固定坐标，保证老环境仍可跑。
        """
        try:
            items = O_WQ_INVITE_ENTRY_SCAN.detect_and_ocr(self.device.image) or []
        except Exception as error:  # noqa: BLE001
            logger.debug(f'wq2 invite entry scan failed: {error!r}')
            items = []
        roi_x, roi_y = O_WQ_INVITE_ENTRY_SCAN.roi[0], O_WQ_INVITE_ENTRY_SCAN.roi[1]
        for item in items:
            text = str(getattr(item, 'ocr_text', '') or '')
            if '邀请' not in text:
                continue
            box = getattr(item, 'box', None)
            if box is None:
                continue
            try:
                x = roi_x + (int(box[0][0]) + int(box[2][0])) // 2
                y = roi_y + (int(box[0][1]) + int(box[2][1])) // 2
            except (IndexError, TypeError):
                continue
            logger.info(f"[WQ] invite entry by OCR: '{text}' at ({x},{y})")
            self.device.click(x, y, control_check=False)
            return True
        logger.warning('[WQ] invite entry not found by OCR, fallback to fixed coordinate')
        self.device.click(*C_WQ_INVITE_ENTRY, control_check=False)
        return False

    def wq2_open_invite_dialog(self, tries: int = 2) -> bool:
        """打开协作邀请弹窗：先 OCR 定位入口，失败退回固定坐标。"""
        for _ in range(tries):
            self.screenshot()
            if self.wq2_invite_dialog_visible():
                return True
            self.wq2_click_invite_entry()
            sleep(1.5)
        self.screenshot()
        return self.wq2_invite_dialog_visible()

    def wq2_friend_names(self) -> list[tuple]:
        """左列好友名单：[(名字, (x, y)), ...]（过滤页签/按钮等噪声）。"""
        try:
            items = O_WQ_INVITE_FRIENDS.detect_and_ocr(self.device.image)
        except Exception as error:  # noqa: BLE001
            logger.debug(f'wq2 friend scan failed: {error!r}')
            return []
        roi_x, roi_y = O_WQ_INVITE_FRIENDS.roi[0], O_WQ_INVITE_FRIENDS.roi[1]
        friends = []
        for item in items or []:
            text_value = str(getattr(item, 'ocr_text', '') or '').strip()
            if not wq2_is_friend_name(text_value):
                continue
            box = getattr(item, 'box', None)
            if box is None:
                continue
            try:
                x1, y1 = int(box[0][0]), int(box[0][1])
                x2, y2 = int(box[2][0]), int(box[2][1])
            except (IndexError, TypeError):
                continue
            # detect_and_ocr 返回 ROI 相对坐标，加回原点
            friends.append((text_value, (roi_x + (x1 + x2) // 2,
                                         roi_y + (y1 + y2) // 2)))
        return friends

    def wq2_invite_one(self, preferred: str = '', exclude=()) -> str:
        """邀请一个好友：开弹窗 → 选中 → 点「邀请」→ 弹窗关闭才算发送成功。"""
        if not self.wq2_open_invite_dialog():
            logger.warning('[WQ] invite: dialog did not open')
            return ''
        friends = [item for item in self.wq2_friend_names()
                   if item[0] not in exclude]
        if not friends:
            logger.warning('[WQ] invite: no friend found in list')
            self.device.click(*C_WQ_INVITE_CANCEL, control_check=False)
            sleep(0.6)
            return ''
        target = next((item for item in friends
                       if preferred and preferred in item[0]), None)
        target = target or friends[0]
        name, (x, y) = target
        logger.info(f'[WQ] invite friend: {name} at ({x},{y})')
        self.device.click(x, y, control_check=False)
        sleep(0.8)
        self.screenshot()
        self.device.click(*C_WQ_INVITE_ENSURE, control_check=False)
        sleep(2.0)
        self.screenshot()
        if self.wq2_invite_dialog_visible():
            # 弹窗没关 = 这次没发出去，取消掉避免卡住
            logger.warning(f'[WQ] invite {name} failed (dialog still open)')
            self.device.click(*C_WQ_INVITE_CANCEL, control_check=False)
            sleep(0.8)
            return ''
        logger.info(f'[WQ] invite sent -> {name}')
        return name

    def wq2_invite_friends(self, count: int = 1, preferred: str = '') -> int:
        """邀请 count 个好友：每位单独开一次弹窗（实测点「邀请」后弹窗会关闭）。"""
        sent = []
        for index in range(count):
            name = self.wq2_invite_one(
                preferred if preferred and preferred not in sent else '',
                exclude=tuple(sent))
            if not name:
                break
            sent.append(name)
            logger.info(f'[WQ] invite {len(sent)}/{count} -> {name}')
            self.wq2_ensure_panel()
        return len(sent)

    def wq2_click_goto(self, goto) -> bool:
        if not goto:
            return False
        self.device.click(goto[0], goto[1], control_check=False)
        sleep(1.5)
        return True

    def wq2_close_detail(self) -> None:
        self.screenshot()
        if self.appear(self.I_UI_BACK_RED):
            self.ui_click_until_disappear(self.I_UI_BACK_RED)
            return
        self.device.click(*C_WQ_CANCEL, control_check=False)
        sleep(0.8)
        if self.wq2_detail_visible():
            self.device.adb_shell(['input', 'keyevent', '4'])
            sleep(0.8)
