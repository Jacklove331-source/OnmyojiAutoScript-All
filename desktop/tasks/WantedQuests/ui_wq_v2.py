# This Python file uses the following encoding: utf-8

"""悬赏封印新 UI（2026-10）适配层：全部用 OCR 文本 + 文本块坐标驱动。

旧 UI 是「左侧一列任务 + 前往按钮模板」，新 UI 是：
- 面板：标题「悬赏封印」+ 3 张任务卡（任务文本 / 进度 a/b / 碎片奖励）+ 右下「追踪」+「下次刷新」；
- 点开某张卡 → 详情弹窗：「悬赏目标：击败N个X」+ 进度 + 推荐地点行（类型 / 地点(数量:N) / 前往）。

本模块只负责「看得懂 + 点得准」，具体打哪个本仍复用旧逻辑（secret/explore/challenge）。
坐标全部相对 1280x720；不同分辨率由 OAS 的 RuleOcr/RuleImage 自动缩放。
"""

from copy import copy
import re
import time
from time import sleep

from module.atom.ocr import RuleOcr
from module.logger import logger
from tasks.GameUi.page import page_main

# ---------------------------------------------------------------- 面板
# 2026-11-22 对照 nearaura/OnmyojiAutoScript@dev 后重写判据：
# 原来这两条是**窄 ROI 全等**（标题 (500,14,300,52) / 刷新 (470,632,320,48)），
# 游戏改版后 ROI 全过期 → 读空 → 面板判不到 → run_v2 直接 break，
# 用户看到「悬赏没做完就结束了」。而上游根本不用这两条 OCR 判面板。
# 现在统一改成**全屏包含匹配**（与已验证可用的 O_WQ_INVITE_SCAN 同 ROI），
# 关键字仍在调用处判断（'悬赏封印' in title / '下次刷新' in refresh），所以不需要动调用逻辑。
O_WQ_PANEL_TITLE = RuleOcr(
    roi=(0, 60, 1280, 660), area=(0, 60, 1280, 660),
    mode='Full', method='Default', keyword='', name='wq2_panel_title')
O_WQ_REFRESH = RuleOcr(
    roi=(0, 60, 1280, 660), area=(0, 60, 1280, 660),
    mode='Full', method='Default', keyword='', name='wq2_refresh')
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
    ('級', '级'), ('悬賞', '悬赏'), ('懸賞', '悬赏'), ('賞', '赏'),
    ('陰陽師', '阴阳师'), ('阴踢师', '阴阳师'),
    ('推荐隆阳师', '推荐阴阳师'),
)


def wq2_normalize(text: str) -> str:
    """把 OCR 常见误读归一到预期写法（比较关键字前先过一遍）。"""
    value = str(text or '')
    for wrong, right in _WQ2_OCR_CONFUSIONS:
        value = value.replace(wrong, right)
    return value


def wq2_text_is_invite_dialog(text: str) -> bool:
    """归一化后的整页文本是否表明当前覆盖着协作邀请弹窗。"""
    norm = wq2_normalize(text).replace(' ', '')
    if not norm:
        return False
    if any(key in norm for key in (
        '推荐阴阳师', '对方同意后自动发送协作', '邀请7级', '邀请15级', '邀请20级',
    )):
        return True
    return '取消' in norm and '邀请' in norm and '推荐' in norm


def wq2_text_is_detail_popup(text: str) -> bool:
    """归一化后的整页文本是否表明当前覆盖着悬赏详情/现世弹窗。"""
    norm = wq2_normalize(text).replace(' ', '')
    if not norm:
        return False
    if any(key in norm for key in (
        '悬赏目标', '前往现世', '请大人在移动端上使用该功能', '需前往现世消灭',
    )):
        return True
    no_explore = norm.replace('前往探索', '')
    return '前往' in no_explore and bool(PROGRESS_RE.search(no_explore))


# 庭院左侧「封」入口 OCR 兜底区域（避开顶部横幅与右侧活动栏）
O_WQ_SEAL_ENTRY_SCAN = RuleOcr(
    roi=(0, 120, 420, 420), area=(0, 120, 420, 420),
    mode='Full', method='Default', keyword='', name='wq2_seal_entry_scan')

# 面板四张卡上半区扫描：用于识别每张卡顶部的「现世妖怪」标记，避免点进现世任务或触发其协作邀请
O_WQ_CARD_HEADER_SCAN = RuleOcr(
    roi=(100, 130, 1110, 310), area=(100, 130, 1110, 310),
    mode='Full', method='Default', keyword='', name='wq2_card_header_scan')


def find_realworld_columns(items, card_columns=None, roi_x: int = 0, roi_y: int = 0) -> set[int]:
    """根据面板上半区 OCR 结果找出标注「现世妖怪 / 现世」的卡片列号（1-based: 1..4）。"""
    columns = tuple(card_columns or _CARD_COLUMN_X)
    cols: set[int] = set()
    for item in items or []:
        text = wq2_normalize(str(getattr(item, 'ocr_text', '') or '').strip()).replace(' ', '')
        if not text or '现世' not in text:
            continue
        box = getattr(item, 'box', None)
        if box is None:
            continue
        try:
            cx = roi_x + (int(box[0][0]) + int(box[2][0])) // 2
        except (IndexError, TypeError, ValueError):
            continue
        nearest = min(range(len(columns)), key=lambda i: abs(columns[i] - cx))
        if abs(columns[nearest] - cx) <= _CARD_COLUMN_TOLERANCE:
            cols.add(nearest + 1)
    return cols


# 面板上的「邀请好友」入口：覆盖四张卡区域以便跳过现世妖怪卡、只点普通协作卡
O_WQ_INVITE_ENTRY_SCAN = RuleOcr(
    roi=(100, 150, 1110, 470), area=(100, 150, 1110, 470),
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

# 标题「悬赏封印」自身含「封」「印」，和逐卡竖排「已封印」的标记同字，必须靠文本+坐标排除
_TITLE_TEXT_HINTS = ('悬赏', '懸賞', '悬賞')
_TITLE_ZONE_Y = 170          # 标题区下沿（1280x720 实测标题 y≈60-125）
_TITLE_ZONE_X = (280, 1000)  # 标题区左右边界
_CARD_COLUMN_X = (240, 510, 780, 1050)   # 与 WantedQuestsUiV2._CARD_COLUMNS 一致
_CARD_COLUMN_TOLERANCE = 120             # 进度/封印判定归列的容差（px）
_SEALED_MIN = 2                          # 至少两处封印标记（四卡面板，OCR 会漏字）


def compute_sealed_verdict(items, card_columns=None, roi_x=0, roi_y=0) -> dict:
    """把悬赏面板的全屏 OCR items 判成「全部已封印」的计数与结论（纯函数，可离线复核）。

    计数规则：
    - title：任一文本含「悬赏/懸賞/悬賞」→ 面板标题在，用来排除探索页/聊天文本误命中；
    - sealed：文本同时含「封」「印」且不是标题文本、坐标不在标题区 → 记一处封印标记；
    - claimable：文本含「领取/領取」→ 有可领奖的卡；
    - progress：卡片列 x 容差内、且形如 `a/b` 的文本 → 还有没打完的卡；
    - completed = title and sealed >= 2 and claimable == 0 and progress == 0。

    :param items: RuleOcr.detect_and_ocr() 的结果（含 ocr_text / box）
    :param card_columns: 四张卡的 x 中心，默认与生产判据一致
    :param roi_x: OCR ROI 的左上角 x（box 是 ROI 局部坐标）
    :param roi_y: OCR ROI 的左上角 y
    """
    columns = tuple(card_columns or _CARD_COLUMN_X)
    title = False
    sealed = 0
    claimable = 0
    progress = 0
    sealed_hits = []
    progress_hits = []
    claim_hits = []
    for item in items or []:
        text = str(getattr(item, 'ocr_text', '') or '').strip()
        if not text:
            continue
        box = getattr(item, 'box', None)
        cx = cy = None
        try:
            cx = roi_x + (int(box[0][0]) + int(box[2][0])) // 2
            cy = roi_y + (int(box[0][1]) + int(box[2][1])) // 2
        except (IndexError, TypeError, ValueError, AttributeError):
            cx = cy = None
        in_title_zone = (cy is not None and cy < _TITLE_ZONE_Y
                         and cx is not None and _TITLE_ZONE_X[0] <= cx <= _TITLE_ZONE_X[1])
        if any(hint in text for hint in _TITLE_TEXT_HINTS):
            title = True
            continue
        if '领取' in text or '領取' in text:
            claimable += 1
            claim_hits.append((text, cx, cy))
            continue
        if ('封' in text and '印' in text) and not in_title_zone:
            sealed += 1
            sealed_hits.append((text, cx, cy))
            continue
        normalized = wq2_normalize(text).replace(' ', '')
        # 与 PROGRESS_RE 一致：OCR 常把 '/' 读成 '7'，所以两条都算进度形状。
        # 这条判据宁严勿松：多认出一次进度只是多跑一轮，漏认就会把没打完的卡当已完成。
        if not re.search(r'\d+\s*[/7]\s*\d+', normalized):
            continue    # 没有 `a/b` 形状就不算任务进度（'30'/'30000' 这类奖励数字要排除）
        if cx is not None and min(abs(cx - column) for column in columns) > _CARD_COLUMN_TOLERANCE:
            continue    # 没落在卡片列上，不是任务卡进度
        progress += 1
        progress_hits.append((text, cx, cy))
    completed = bool(title) and sealed >= _SEALED_MIN and claimable == 0 and progress == 0
    return {
        'title': title,
        'sealed': sealed,
        'claimable': claimable,
        'progress': progress,
        'sealed_hits': sealed_hits,
        'claim_hits': claim_hits,
        'progress_hits': progress_hits,
        'completed': completed,
    }


def format_sealed_verdict(verdict: dict) -> str:
    """把一个 compute_sealed_verdict() 结果压成一行日志/报告文本。"""
    verdict = verdict or {}
    return (f"title={verdict.get('title')} sealed={verdict.get('sealed')} "
            f"claimable={verdict.get('claimable')} progress={verdict.get('progress')} "
            f"hits={verdict.get('sealed_hits')} "
            f"claim={verdict.get('claim_hits')} prog={verdict.get('progress_hits')}")


class WantedQuestsUiV2:
    """新 UI 的识别与点击原语；由 ScriptTask 混入使用。"""

    # 面板探针降级参数。实测证据（2026-11-19 快照 1795089574020 / 1795089795748）：
    # [WQ2_PANEL_TITLE] / [WQ2_REFRESH] 两条 ROI OCR 反复 `No text detected in ROI`，
    # 面板判定实际只剩模板兜底，兜底不中就在「开面板→判不见→再开」之间空转到看门狗
    # 300s 报 GameStuckError。而 log 里 WQ2_PANEL_TITLE 历史读数从来没有「悬赏封印」
    # 关键字（读到的是顶栏数字，如 [12069...556] / [8213..21383]）→ 该 ROI 已过期。
    # 所以：连续读空 → 停用 OCR 探针一段时间，改用进度横带/模板探针，不反复空转。
    _WQ2_OCR_EMPTY_LIMIT = 3      # 连续读空次数上限
    _WQ2_OCR_BACKOFF = 60.0       # 停用 OCR 探针的秒数

    def wq2_map_type(self, row_type: str) -> str:
        """实例方法版类型映射（run_v2 通过 self 调用）。"""
        return wq2_map_type(row_type)

    # ------------------------------------------------------------ 面板
    def _wq2_ocr_probe_state(self) -> dict:
        state = getattr(self, '_wq2_ocr_state', None)
        if state is None:
            state = {'empty': 0, 'resume_at': 0.0}
            self._wq2_ocr_state = state
        return state

    def _wq2_ocr_probe_active(self) -> bool:
        """标题/刷新两条 OCR 探针当前是否还值得跑（读空太多次就暂时停用）。"""
        return time.monotonic() >= self._wq2_ocr_probe_state()['resume_at']

    def _wq2_ocr_probe_record(self, hit: bool) -> None:
        state = self._wq2_ocr_probe_state()
        if hit:
            state['empty'] = 0
            state['resume_at'] = 0.0
            return
        state['empty'] += 1
        if state['empty'] >= self._WQ2_OCR_EMPTY_LIMIT and state['resume_at'] <= 0:
            state['resume_at'] = time.monotonic() + self._WQ2_OCR_BACKOFF
            logger.warning(
                f'[WQ] 面板标题/刷新 OCR 连续 {state["empty"]} 次读空'
                f'（ROI 可能已过期），{self._WQ2_OCR_BACKOFF:.0f}s 内换用进度横带/模板探针')

    def _wq2_scan_text(self, rule: RuleOcr) -> str:
        """对 mode='Full' 的 RuleOcr 执行 detect_and_ocr 并拼接归一化文本。

        注意：mode='Full' 时调用 rule.ocr(image) 返回的是坐标元组 (x,y,w,h) 而非字符串，
        直接拿 `'悬赏封印' in str(rule.ocr(...))` 永远为 False。
        """
        try:
            items = rule.detect_and_ocr(self.device.image) or []
        except Exception as error:  # noqa: BLE001
            logger.debug(f'[WQ] full OCR scan failed ({rule.name}): {error!r}')
            return ''
        joined = ''.join(
            ''.join(str(getattr(item, 'ocr_text', '') or '').split())
            for item in items)
        return wq2_normalize(joined)

    def wq2_panel_visible(self) -> bool:
        """是否停在悬赏封印**主面板**（排除邀请弹窗、详情弹窗与现世弹窗）。

        2026-11-24 修复：
        1. O_WQ_PANEL_TITLE/O_WQ_REFRESH 为 mode='Full'，旧代码调 .ocr() 得到坐标元组导致
           标题/刷新永远判 False，并连扫 4~6 次全屏 OCR 耗尽 60s 预算；
        2. 归一化繁体「悬賞封印」→「悬赏封印」；
        3. 邀请弹窗和「现世妖怪」详情页背后仍可见「悬赏封印/下次刷新」，必须先排除覆盖弹窗。
        """
        # 探针 0（最便宜 ~50ms）：右下「追踪 / 取消追踪」模板 + 两个窄 ROI 排除覆盖弹窗
        if self._wq2_panel_by_templates():
            try:
                detail_title = wq2_normalize(O_WQ_DETAIL_TITLE.ocr(self.device.image))
            except Exception:  # noqa: BLE001
                detail_title = ''
            try:
                invite_title = wq2_normalize(O_WQ_INVITE_TITLE.ocr(self.device.image))
            except Exception:  # noqa: BLE001
                invite_title = ''
            is_detail = '悬赏目标' in detail_title or '击败' in detail_title
            is_invite = (wq2_text_is_invite_dialog(invite_title)
                         or '对方同意后自动发送协作' in detail_title)
            if not is_detail and not is_invite:
                self._wq2_ocr_probe_record(True)
                return True

        # 探针 1：单次全屏 detect_and_ocr 同时完成「排除弹窗 + 确认面板标题/刷新」
        full_text = self._wq2_scan_text(O_WQ_PANEL_TITLE)
        if wq2_text_is_invite_dialog(full_text) or wq2_text_is_detail_popup(full_text):
            return False
        if '悬赏封印' in full_text or '下次刷新' in full_text:
            self._wq2_ocr_probe_record(True)
            return True

        # 探针 2：进度横带读得到 ≥2 个 a/b —— 面板四张卡独有（已排除弹窗）
        try:
            if len(self.wq2_progress_tokens()) >= 2:
                self._wq2_ocr_probe_record(True)
                return True
        except Exception as error:  # noqa: BLE001 - 探针失败继续往下试
            logger.debug(f'[WQ] progress probe failed: {error!r}')

        self._wq2_ocr_probe_record(False)
        return False

    def _wq2_panel_by_templates(self) -> bool:
        """与上游 `pre_work()` 对齐：能点到「一键追踪 / 取消追踪」就说明面板开着。

        2026-11-22 对照 nearaura/OnmyojiAutoScript@dev 的 `tasks/WantedQuests/script_task.py`：
        上游只用 `self.appear(self.I_TRACE_ENABLE)` / `self.appear(self.I_TRACE_DISABLE)` 判面板，
        **没有**任何否定条件；而我们这里额外要求「单个任务展开标志」和「邀请确定按钮」都不出现，
        条件过严 —— 面板明明开着也被否掉，`run_v2` 于是 break，用户看到「悬赏没做完就结束了」。
        这两个模板的坐标与上游完全一致（1159,606,52,58 / 1154,609,68,58），所以直接按上游收回条件即可。
        """
        try:
            return self.appear(self.I_TRACE_ENABLE) or self.appear(self.I_TRACE_DISABLE)
        except Exception:  # noqa: BLE001
            return False

    def _wq2_ensure_main_for_seal(self) -> None:
        """「封」/町中入口只存在于庭院主界面；探索页、战斗页上点它等于空点。

        2026-11-22 复测：战后停在探索页时 `I_CHECK_EXPLORATION` 模板没命中（日志里
        20:09:14 → 20:09:36 之间没有任何「回主界面」动作），于是 8 次尝试都在探索页上
        空点「封」，最后靠探针 4 误命中收场。这里改用 page_main 直判（今天刚用皮肤模板
        修过，实测 1.000），不在主界面就先 `goto_page(page_main)` 再点。
        """
        try:
            if not self.match_page_once(page_main):
                logger.info('[WQ] not on page_main, back to main before opening panel')
                self.goto_page(page_main)
                sleep(0.6)
        except Exception as error:  # noqa: BLE001
            logger.debug(f'[WQ] back-to-main before seal failed: {error!r}')

    def _wq2_click_panel_entry(self) -> bool:
        # 有些庭院将「封」图标放在屏幕左缘，旁边是独立的「新手」入口。
        left_seal = copy(self.I_WQ_SEAL)
        left_seal.roi_front = list(left_seal.roi_front)
        left_seal.roi_back = (0, 230, 67, 220)
        # 2026-11-24：部分庭院皮肤（如云景阆苑）下「封」灯笼缩放约 1.1x，单尺度模板分值掉到 0.72 (<0.8)，
        # 导致战后回庭院完全点不到入口；改为多尺度匹配（0.8~1.25）+ 左侧「封」字 OCR 兜底。
        # 同时有卡可领/已完成时入口会变成勾号 I_WQ_DONE，同样走多尺度匹配。
        if (self.appear_then_click(left_seal, interval=1)
                or self.appear_then_click(self.I_WQ_SEAL, interval=1)
                or self.appear_then_click(self.I_WQ_DONE, interval=1)):
            return True
        if (self.appear_then_click_multi_scale(
                self.I_WQ_SEAL, interval=1, scale_range=(0.8, 1.25))
                or self.appear_then_click_multi_scale(
                    self.I_WQ_DONE, interval=1, scale_range=(0.8, 1.25))
                or self.appear_then_click_multi_scale(
                    left_seal, interval=1, scale_range=(0.8, 1.25))):
            return True
        # 最后兜底：左侧庭院区 OCR 定位独立的「封」字灯笼
        try:
            items = O_WQ_SEAL_ENTRY_SCAN.detect_and_ocr(self.device.image) or []
        except Exception as error:  # noqa: BLE001
            logger.debug(f'[WQ] seal OCR fallback failed: {error!r}')
            return False
        roi_x, roi_y = O_WQ_SEAL_ENTRY_SCAN.roi[:2]
        for item in items:
            text = str(getattr(item, 'ocr_text', '') or '').strip()
            box = getattr(item, 'box', None)
            if text != '封' or box is None:
                continue
            try:
                x = roi_x + (int(box[0][0]) + int(box[2][0])) // 2
                y = roi_y + (int(box[0][1]) + int(box[2][1])) // 2
            except (IndexError, TypeError):
                continue
            logger.info(f"[WQ] click seal entry by OCR fallback at ({x},{y})")
            self.device.click(x, y, control_name='WQ2_SEAL_OCR')
            return True
        return False

    def _wq2_dismiss_reward_popup(self) -> None:
        """关掉「获得奖励」展示框。实测：点屏幕任何位置都无效，input keyevent 4(BACK) 才关。"""
        try:
            self.device.adb_shell(['input', 'keyevent', '4'])
        except Exception as error:  # noqa: BLE001
            logger.debug(f'[WQ] back key failed: {error!r}')
        sleep(0.8)

    def wq2_claim_rewards(self, max_click: int = 4) -> int:
        """把进度已满（a/a）的卡的「领取」点掉。返回点击次数。

        2026-11-23：改用**进度横带**定位——进度 `a/a` 的卡，红色圆形「领取」就在进度行正上方
        (x, 405)。之前按 OCR 文本「领取」的框去点，因 ROI 局部坐标/框抖动会点到卡面弹详情。
        """
        clicked = 0
        for _ in range(max_click):
            self.screenshot()
            targets = [x for (x, cur, total, _raw) in self.wq2_progress_tokens()
                       if x and cur and total and cur >= total]
            if not targets:
                break
            x = int(targets[0])
            logger.info(f'[WQ] claim reward at ({x}, 405)')
            self.device.click(x, 405, control_name='WQ2_CLAIM')
            clicked += 1
            sleep(1.5)
            # 领取成功会弹「获得奖励」展示框：**点屏幕关不掉，只有 BACK 键有效**（2026-11-23 实测）
            self._wq2_dismiss_reward_popup()
            self.screenshot()   # 必须重拍：不拍会读到点击前旧帧，判不出领取后弹出的详情页
            try:
                if self.wq2_detail_visible():
                    self.wq2_close_detail()
                    sleep(0.8)
            except Exception:  # noqa: BLE001
                pass
        if clicked:
            logger.info(f'[WQ] claimed rewards x{clicked}')
        return clicked

    def wq2_ensure_panel(self, tries: int = 8, budget: float = 60.0) -> bool:
        """从任意界面（含战后探索页）稳定回到悬赏封印面板。

        有界：tries 次或 budget 秒先到先停；期间登记 `LONG_WAIT` 占位符，
        避免看门狗 60s 短定时器把「切页 → 开面板」这段正常等待判成卡死
        （快照 1795089574020：面板 OCR 全空 → 循环空转 85s → GameStuckError）。
        """
        deadline = time.monotonic() + max(5.0, float(budget))
        self.device.stuck_record_add('LONG_WAIT')
        try:
            for attempt in range(tries):
                self.screenshot()
                if self.wq2_panel_visible():
                    return True
                # 邀请弹窗/详情弹窗先关，避免把它们当成不在面板而去点庭院
                if self.wq2_invite_dialog_visible():
                    self.wq2_close_invite_dialog()
                    sleep(0.6)
                elif self.wq2_detail_visible():
                    self.wq2_close_detail()
                    sleep(0.8)
                else:
                    # 探索页：先回主界面（町中入口只在庭院/町中上）
                    try:
                        if self.appear(self.I_CHECK_EXPLORATION):
                            logger.info('[WQ] on exploration page, back to main')
                            self.goto_page(page_main)
                            sleep(1.0)
                        else:
                            # 常规：先确保在庭院主界面（模板漏判时也兜得住），再点町中入口
                            self._wq2_ensure_main_for_seal()
                            self._wq2_click_panel_entry()
                            sleep(1.2)
                    except Exception as error:  # noqa: BLE001
                        logger.debug(f'[WQ] panel nav step failed: {error!r}')
                if time.monotonic() >= deadline:
                    logger.warning(
                        f'[WQ] 开面板超过 {budget:.0f}s（第 {attempt + 1} 次尝试仍判不到面板），'
                        f'放弃本面板，不再空转')
                    break
            self.screenshot()
            visible = self.wq2_panel_visible()
            if not visible:
                logger.warning(f'[WQ] 开面板失败（{tries} 次尝试未判到面板），放弃本面板')
            return visible
        finally:
            self.device.stuck_record_remove('LONG_WAIT')

    def wq2_open_panel(self, tries: int = 6, budget: float = 45.0) -> bool:
        """点开悬赏封印面板；已开邀请/详情弹窗时先关掉。有界 + LONG_WAIT。"""
        self.screenshot()
        if self.wq2_invite_dialog_visible():
            self.wq2_close_invite_dialog()
        elif self.wq2_detail_visible():
            self.wq2_close_detail()
        deadline = time.monotonic() + max(5.0, float(budget))
        self.device.stuck_record_add('LONG_WAIT')
        try:
            for _ in range(tries):
                self.screenshot()
                if self.wq2_panel_visible():
                    return True
                if self.wq2_invite_dialog_visible():
                    self.wq2_close_invite_dialog()
                    sleep(0.6)
                    continue
                if self.wq2_detail_visible():
                    self.wq2_close_detail()
                    sleep(0.8)
                    continue
                self._wq2_ensure_main_for_seal()
                self._wq2_click_panel_entry()
                sleep(1.2)
                if time.monotonic() >= deadline:
                    logger.warning(f'[WQ] 开面板超过 {budget:.0f}s，放弃本面板')
                    break
            self.screenshot()
            visible = self.wq2_panel_visible()
            if not visible:
                logger.warning(f'[WQ] 开面板失败（{tries} 次尝试未判到面板），放弃本面板')
            return visible
        finally:
            self.device.stuck_record_remove('LONG_WAIT')

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

    def wq2_panel_all_sealed(self) -> bool:
        """面板上每张卡都只剩竖排「已封印」且没有「领取」→ 今日悬赏已做完。

        判据（全部满足才算完成，任一含糊就返回 False 交回旧的「判据可疑」路径）：
        1. 全屏 OCR 里出现面板标题「悬赏封印」（排除探索页/聊天文本误命中）；
        2. 没有任何「领取」（有可领奖的卡一定带红色「领取」；实测 OCR 稳定读到）；
        3. 卡片列范围内没有任何 `a/b` 进度（有条目就会被 wq2_cards 读到，走不到这里）；
        4. 至少读到 2 处封印标记（`封`+`印` 都出现即算，标题文本先排除——实测 OCR 会把
           「已封印」丢成「封印」，严格匹配「已封印」在这张参考帧上只有 1 个）。
        具体计数逻辑在模块级 compute_sealed_verdict()，dev_tools/wq_sealed_check.py 直接复用。
        注意这里用**全屏** O_WQ_PANEL_TITLE（(0,60,1280,660)）而不是卡片列 ROI
        O_WQ_PANEL_SCAN：实测卡片列 ROI 只读到「碎片」，「已封印」只出现在全屏 OCR 里。
        """
        try:
            # 只有 OCR 调用放在 try 里：OCR 失败只影响本判据，不该中断任务；
            # compute_sealed_verdict() 是纯函数，它出错（NameError/KeyError 级别）必须暴露出来，
            # 不能再被宽泛 except 静默降级成「判据可疑」。
            items = O_WQ_PANEL_TITLE.detect_and_ocr(self.device.image) or []
        except Exception as error:  # noqa: BLE001
            logger.warning(f'wq2 sealed probe OCR failed: {error!r}')
            return False
        verdict = compute_sealed_verdict(items, self._CARD_COLUMNS,
                                         O_WQ_PANEL_TITLE.roi[0],
                                         O_WQ_PANEL_TITLE.roi[1])
        if verdict.get('completed'):
            logger.info('[WQ] sealed probe: ' + format_sealed_verdict(verdict)
                        + ' -> all sealed')
        else:
            logger.debug('[WQ] sealed probe: ' + format_sealed_verdict(verdict)
                         + ' -> not sealed')
        return bool(verdict.get('completed'))

    def wq2_realworld_columns(self) -> set[int]:
        """扫描面板四张卡上半区，返回标注了「现世妖怪」的列号集合（1..4）。"""
        try:
            items = O_WQ_CARD_HEADER_SCAN.detect_and_ocr(self.device.image) or []
        except Exception as error:  # noqa: BLE001
            logger.debug(f'wq2 card header scan failed: {error!r}')
            return set()
        cols = find_realworld_columns(
            items, self._CARD_COLUMNS,
            O_WQ_CARD_HEADER_SCAN.roi[0], O_WQ_CARD_HEADER_SCAN.roi[1])
        if cols:
            logger.info(f'[WQ] 检测到现世妖怪卡片列: {sorted(cols)}（将跳过不做）')
        return cols

    def wq2_cards(self) -> list[dict]:
        """读取任务卡：整块 OCR → 按列聚类 → 文本与进度按 y 就近配对，并标记现世妖怪列。"""
        realworld_cols = self.wq2_realworld_columns()
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
                is_realworld = (index + 1) in realworld_cols or ('现世' in wq2_normalize(text))
                pairs.append({'index': index + 1, 'text': text,
                              'progress_text': row['text'],
                              'current': current, 'total': total,
                              'center': center,
                              'is_realworld': is_realworld})
            cards.extend(pairs)
        return cards

    def wq2_unfinished_cards(self) -> list[dict]:
        """返回未完成任务卡；空结果重扫一次（面板刚打开可能没渲染完）。"""
        def _fmt(card):
            tag = '[现世]' if card.get('is_realworld') else ''
            return f"#{card['index']}{tag} {card['text'][:12]!r} {card['progress_text']}"

        cards = self.wq2_cards()
        logger.info('[WQ] card scan: ' + ' | '.join(_fmt(c) for c in cards))
        unfinished = [card for card in cards
                      if card['current'] is not None and card['total']
                      and card['current'] < card['total']]
        if unfinished:
            return unfinished
        sleep(1.0)
        self.screenshot()
        cards = self.wq2_cards()
        logger.info('[WQ] card scan(2nd): ' + ' | '.join(_fmt(c) for c in cards))
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
        """详情弹窗判定（含现世妖怪详情页）。

        桌面版标题是「悬赏目标」，手机当前版本是「自己击败XX」，所以两种都认；
        再加一个结构化兜底：详情页一定有「悬赏目标 / 前往现世」或同时有「前往（非前往探索）」+ a/b 进度。
        """
        text = wq2_normalize(O_WQ_DETAIL_TITLE.ocr(self.device.image))
        if '悬赏目标' in text or '击败' in text:
            return True
        joined = self._wq2_scan_text(O_WQ_DETAIL_SCAN)
        if wq2_text_is_detail_popup(joined):
            return True
        # 现世妖怪详情页里「悬赏目标」可能落在 y<120 或「前往现世」落在 y>640，用全屏补判一次
        if '现世妖怪' in joined and ('裂缝' in joined or '收服' in joined or '移动端' in joined):
            return True
        return False

    def wq2_detail_is_realworld(self) -> bool:
        """当前打开的详情页是否为「现世妖怪」（模拟器无法完成，一律不做）。"""
        joined = self._wq2_scan_text(O_WQ_PANEL_TITLE)
        return any(key in joined for key in (
            '现世妖怪', '前往现世', '请大人在移动端上使用该功能', '需前往现世消灭',
        ))

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
        """邀请弹窗是否已打开：只认弹窗特有的「邀请7/15/20级以上好友 / 推荐阴阳师 / 对方同意后自动发送协作」。

        实测：面板和详情页都没有这些文本，用宽松条件（好友+取消）会误判。
        """
        # OCR 会把「邀请」读成「邀清/邀情」、「级」读成「級」→ 先归一化再比关键字
        title = wq2_normalize(O_WQ_INVITE_TITLE.ocr(self.device.image))
        if any(key in title for key in ('邀请7级', '邀请15级', '邀请20级', '推荐')):
            logger.debug(f'[WQ] invite dialog by title: {title!r}')
            return True
        joined = self._wq2_scan_text(O_WQ_INVITE_FRIENDS)
        if wq2_text_is_invite_dialog(joined):
            return True
        full = self._wq2_scan_text(O_WQ_INVITE_SCAN)
        return wq2_text_is_invite_dialog(full)

    def wq2_close_invite_dialog(self, tries: int = 2) -> bool:
        """稳妥关闭协作邀请弹窗：优先模板/OCR「取消」，再退固定坐标，最后 BACK 兜底。"""
        for attempt in range(tries):
            self.screenshot()
            if not self.wq2_invite_dialog_visible():
                return True
            clicked = False
            try:
                if hasattr(self, 'I_WQ_INVITE_CANCEL') and self.appear_then_click(self.I_WQ_INVITE_CANCEL, interval=0.8):
                    clicked = True
            except Exception:  # noqa: BLE001
                pass
            if not clicked:
                # OCR 定位弹窗底部的「取消」按钮
                try:
                    items = O_WQ_INVITE_SCAN.detect_and_ocr(self.device.image) or []
                    roi_x, roi_y = O_WQ_INVITE_SCAN.roi[:2]
                    for item in items:
                        if str(getattr(item, 'ocr_text', '') or '').strip() == '取消' and getattr(item, 'box', None) is not None:
                            b = item.box
                            cx = roi_x + (int(b[0][0]) + int(b[2][0])) // 2
                            cy = roi_y + (int(b[0][1]) + int(b[2][1])) // 2
                            self.device.click(cx, cy, control_check=False)
                            clicked = True
                            break
                except Exception:  # noqa: BLE001
                    pass
            if not clicked:
                self.device.click(*C_WQ_INVITE_CANCEL, control_check=False)
            sleep(1.0)
            self.screenshot()
            if not self.wq2_invite_dialog_visible():
                return True
            if attempt == tries - 1:
                try:
                    self.device.adb_shell(['input', 'keyevent', '4'])
                    sleep(0.8)
                except Exception:  # noqa: BLE001
                    pass
        self.screenshot()
        return not self.wq2_invite_dialog_visible()

    def wq2_click_invite_entry(self) -> bool:
        """用 OCR 在面板上找非「现世妖怪」卡的「邀请」入口并点击。

        如果面板上只有现世妖怪卡带「邀请好友」（或无普通协作入口），直接跳过不点，
        防止盲点固定坐标落到第 1 张现世妖怪卡上。
        """
        realworld_cols = self.wq2_realworld_columns()
        try:
            items = O_WQ_INVITE_ENTRY_SCAN.detect_and_ocr(self.device.image) or []
        except Exception as error:  # noqa: BLE001
            logger.debug(f'wq2 invite entry scan failed: {error!r}')
            items = []
        roi_x, roi_y = O_WQ_INVITE_ENTRY_SCAN.roi[0], O_WQ_INVITE_ENTRY_SCAN.roi[1]
        skipped_realworld = False
        for item in items:
            text = wq2_normalize(str(getattr(item, 'ocr_text', '') or ''))
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
            col = min(range(len(self._CARD_COLUMNS)),
                      key=lambda i: abs(self._CARD_COLUMNS[i] - x)) + 1
            if col in realworld_cols:
                logger.info(f"[WQ] 第 {col} 张卡为「现世妖怪」，跳过其邀请入口 ({x},{y})")
                skipped_realworld = True
                continue
            logger.info(f"[WQ] invite entry by OCR: '{text}' at ({x},{y}) (col={col})")
            self.device.click(x, y, control_check=False)
            return True
        if skipped_realworld or 1 in realworld_cols:
            logger.info('[WQ] 未找到非现世妖怪的协作邀请入口，跳过邀请')
            return False
        logger.warning('[WQ] invite entry not found by OCR, fallback to fixed coordinate')
        self.device.click(*C_WQ_INVITE_ENTRY, control_check=False)
        return False

    def wq2_open_invite_dialog(self, tries: int = 2) -> bool:
        """打开协作邀请弹窗：先 OCR 定位非现世卡入口；若无可用入口则立刻返回 False。"""
        for _ in range(tries):
            self.screenshot()
            if self.wq2_invite_dialog_visible():
                return True
            if not self.wq2_click_invite_entry():
                return False
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
            self.wq2_close_invite_dialog()
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
            # 弹窗没关 = 这次没发出去，稳妥取消掉避免残留在面板上
            logger.warning(f'[WQ] invite {name} failed (dialog still open)')
            self.wq2_close_invite_dialog()
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
