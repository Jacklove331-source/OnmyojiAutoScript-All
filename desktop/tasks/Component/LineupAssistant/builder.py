# This file uses the following encoding: utf-8
"""游戏内「阵容助手」极速建阵与第十组预设写入执行器。

基于 1280x720 实机验证坐标，将一次完整的：
  新建阵容 -> 选目标副本 -> 选阴阳师 -> 批量选5式神（按稀有度防同名冲突）
  -> 配置四/两件套御魂、二号位主属性、速度/满暴限制、自动施放锁技能
  -> 保存阵容 -> 一键计算 -> 存入指定预设分组（默认第10组「适配」）
  -> 一键应用 -> 点击二次确认弹窗「确定」
压缩到单次本地批处理执行（约 25-35 秒），并在「我的」已有同名方案时走快路径（约 6-10 秒）。
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Callable, Optional

from module.atom.ocr import RuleOcr
from module.base.timer import Timer
from module.logger import logger


def _ocr(roi, keyword: str = '', name: str = 'lineup_builder') -> RuleOcr:
    """将 (x1, y1, x2, y2) 屏幕坐标转换为 OAS RuleOcr 所需的 (x, y, w, h)。"""
    x1, y1, x2, y2 = roi
    xywh = (int(x1), int(y1), max(1, int(x2) - int(x1)), max(1, int(y2) - int(y1)))
    return RuleOcr(
        roi=xywh,
        area=(0, 0, 100, 100),
        mode='Full',
        method='Default',
        keyword=keyword,
        name=name,
    )


# 阴阳师 / 英杰选择弹窗坐标
ONMYOJI_POS = {
    '晴明': (265, 395),
    '神乐': (435, 395),
    '源博雅': (605, 395),
    '八百比丘尼': (775, 395),
    '源赖光': (945, 395),
}

# 选式神弹窗左侧稀有度页签坐标（用于精确区分 SP/SSR 同名式神如 不见岳 vs 云间不见岳）
RARITY_TAB_POS = {
    '全部': (325, 185),
    'UR': (325, 235),
    'SP': (325, 285),
    'SSR': (325, 340),
    'SR': (325, 395),
    'R': (325, 445),
    'N': (325, 495),
    '素材': (325, 545),
}

# 成员详情页底部 5 个式神位坐标
MEMBER_SLOT_POS = (
    (497, 628),
    (595, 628),
    (693, 628),
    (791, 628),
    (889, 628),
)

# 自动施放锁技能弹窗内按钮坐标
LOCK_SKILL_POS = {
    1: (342, 360),
    2: (421, 360),
    3: (500, 360),
    '1': (342, 360),
    '2': (421, 360),
    '3': (500, 360),
    'auto': (579, 360),
    '智能': (579, 360),
}

# 二号位主属性下拉复选坐标（初始默认勾选「攻击加成」）
SLOT2_MAIN_POS = {
    '攻击加成': (1050, 297),
    '防御加成': (1215, 297),
    '生命加成': (1050, 353),
    '速度': (1215, 353),
}

# 御魂弹窗左侧分类页签坐标
SOUL_CAT_POS = {
    '全部': (382, 170),
    '攻击加成': (382, 222),
    '暴击': (382, 275),
    '暴击伤害': (382, 328),
    '防御加成': (382, 380),
    '生命加成': (382, 432),
    '效果命中': (382, 515),
    '效果抵抗': (382, 575),
}

# 四件套高频御魂直选表：(分类名, x, y)，无需打字搜索
SOUL4_DIRECT = {
    '隐念': ('全部', 625, 195),
    '贝吹坊': ('全部', 870, 195),
    '兵主部': ('全部', 625, 322),
    '尘冢': ('全部', 870, 322),
    '狂骨': ('全部', 625, 448),
    '阴摩罗': ('全部', 870, 448),
    '心眼': ('全部', 625, 575),
    '鸣屋': ('全部', 870, 575),
    '应声虫': ('暴击', 625, 195),
    '海月火玉': ('暴击', 870, 195),
    '青女房': ('暴击', 625, 322),
    '片叶之苇': ('暴击', 870, 322),
    '针女': ('暴击', 625, 448),
    '镇墓兽': ('暴击', 870, 448),
    '破势': ('暴击', 625, 575),
    '伤魂鸟': ('暴击', 870, 575),
    '元兴寺': ('效果命中', 625, 195),
    '遗念火': ('效果命中', 870, 195),
    '飞缘魔': ('效果命中', 625, 322),
    '油赤子': ('效果命中', 870, 322),
    '蚌精': ('效果命中', 625, 448),
    '火灵': ('效果命中', 870, 448),
}

# 两件套属性直选坐标（位于「两件套 -> 全部」首屏）
SOUL2_DIRECT = {
    '暴击': (625, 182),
    '攻击加成': (870, 182),
    '攻击': (870, 182),
    '生命加成': (625, 252),
    '生命': (625, 252),
    '防御加成': (870, 252),
    '防御': (870, 252),
    '效果命中': (625, 322),
    '命中': (625, 322),
    '效果抵抗': (870, 322),
    '抵抗': (870, 322),
    '暴击伤害': (625, 392),
    '暴伤': (625, 392),
}

# 阵容编辑页「请选择目标副本」中八岐大蛇各层直选坐标
OROCHI_SUB_DIRECT = {
    '虚无': (865, 210),
    '神罚': (1085, 210),
    '悲鸣': (865, 263),
    '拾层': (1085, 263),
    '玖层': (865, 316),
    '捌层': (1085, 316),
    '柒层': (865, 369),
    '陆层': (1085, 369),
    '伍层': (865, 422),
    '肆层': (1085, 422),
    '叁层': (865, 475),
    '贰层': (1085, 475),
    '壹层': (865, 528),
}


class FastLineupBuilder:
    """面向游戏「阵容助手」的极速建阵与预设写入执行器。"""

    def __init__(self, runner):
        """
        :param runner: 混入了 LineupAssistantMixin + GameUi 的运行器实例
        """
        self.runner = runner
        self.device = runner.device
        self._u2 = None
        self._ime_switched = False
        self._prev_ime = ''

    # ---------------- 基础交互原语 ----------------

    def _tap(self, x: int, y: int, wait: float = 0.55) -> None:
        self.device.click(int(x), int(y), control_check=False)
        if wait > 0:
            time.sleep(wait)

    def _swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350, wait: float = 0.45) -> None:
        try:
            self.device.adb_shell([
                'input', 'swipe', str(int(x1)), str(int(y1)),
                str(int(x2)), str(int(y2)), str(int(duration_ms)),
            ])
        except Exception:  # noqa: BLE001
            self.device.swipe((int(x1), int(y1)), (int(x2), int(y2)))
        if wait > 0:
            time.sleep(wait)

    def _ensure_fast_ime(self):
        if self._u2 is not None:
            return self._u2
        try:
            import uiautomator2 as u2
            serial = getattr(self.device, 'serial', None)
            if not serial:
                return None
            # 记录当前输入法以便结束时恢复
            try:
                out = self.device.adb_shell(['settings', 'get', 'secure', 'default_input_method'])
                self._prev_ime = str(out or '').strip()
            except Exception:  # noqa: BLE001
                self._prev_ime = ''
            d = u2.connect(serial)
            d.set_fastinput_ime(True)
            self._u2 = d
            self._ime_switched = True
            return d
        except Exception as exc:  # noqa: BLE001
            logger.debug(f'[LineupBuilder] FastInputIME init skipped: {exc}')
            return None

    def restore_ime(self) -> None:
        if not self._ime_switched:
            return
        try:
            if self._prev_ime and 'fastinput' not in self._prev_ime.lower():
                self.device.adb_shell(['ime', 'set', self._prev_ime])
            elif self._u2 is not None:
                self._u2.set_fastinput_ime(False)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._ime_switched = False

    def _set_win_clipboard(self, text: str) -> bool:
        """毫秒级写入 Windows 剪贴板（ctypes Win32 API，无需启动 PowerShell 或切换输入法）。"""
        try:
            import ctypes
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
            kernel32.GlobalAlloc.restype = ctypes.c_void_p
            kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            user32.SetClipboardData.restype = ctypes.c_void_p

            data = (str(text) + '\x00').encode('utf-16le')
            for _ in range(5):
                if user32.OpenClipboard(None):
                    try:
                        user32.EmptyClipboard()
                        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                        ptr = kernel32.GlobalLock(h_mem)
                        ctypes.memmove(ptr, data, len(data))
                        kernel32.GlobalUnlock(h_mem)
                        user32.SetClipboardData(CF_UNICODETEXT, h_mem)
                        return True
                    finally:
                        user32.CloseClipboard()
                time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f'[LineupBuilder] win32 clipboard fallback: {exc}')
        return self.runner._set_clipboard(str(text))

    def _input_text(self, x: int, y: int, text: str, press_enter: bool = True, clear_first: bool = True) -> bool:
        """向输入框极速写入中文：Win32 剪贴板(<5ms) + ADB 粘贴(279)，不干扰 minitouch/uiautomator。"""
        self._tap(x, y, 0.5)
        if clear_first:
            try:
                self.device.adb_shell(['input', 'keyevent', '123'] + ['67'] * 12)
                time.sleep(0.15)
            except Exception:  # noqa: BLE001
                pass
        ok = self._set_win_clipboard(str(text))
        if ok:
            time.sleep(0.12)
            try:
                self.device.adb_shell(['input', 'keyevent', '279'])  # KEYCODE_PASTE
                time.sleep(0.3)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f'[LineupBuilder] adb paste failed: {exc}')
                ok = False
        if ok and press_enter:
            try:
                self.device.adb_shell(['input', 'keyevent', '66'])  # KEYCODE_ENTER
                time.sleep(0.35)
            except Exception:  # noqa: BLE001
                pass
        return ok

    # ---------------- 核心建阵流程 ----------------

    def _select_editor_dungeon(self, category: str, group: str, sub: str) -> None:
        """在「阵容编辑」页选择目标副本。"""
        if not (category or group or sub):
            return
        confirm_rule = _ocr((1030, 610, 1200, 690), '确定', 'builder_dg_confirm')
        opened = False
        for px, py in ((900, 98), (1005, 98)):
            self._tap(px, py, 0.9)  # 请选择目标副本 / 切换图标
            self.runner.screenshot()
            if self.runner.ocr_appear(confirm_rule, exact=True):
                opened = True
                break
        if not opened:
            logger.warning('[LineupBuilder] 未能打开目标副本选择弹窗，跳过副本选择')
            return

        # 左侧分类 / 大副本
        for kw in (category, group):
            if not kw:
                continue
            rule = _ocr((595, 145, 745, 675), kw, f'builder_dg_left_{kw}')
            if self.runner.ocr_appear_click(rule, interval=0.8):
                time.sleep(0.45)
                self.runner.screenshot()

        # 右侧小副本
        if sub:
            if group == '八岐大蛇' and sub in OROCHI_SUB_DIRECT:
                px, py = OROCHI_SUB_DIRECT[sub]
                self._tap(px, py, 0.45)
            else:
                rule = _ocr((755, 165, 1195, 585), sub, f'builder_dg_right_{sub}')
                if self.runner.ocr_appear_click(rule, interval=0.8):
                    time.sleep(0.45)

        # 确定
        self._tap(1115, 650, 0.65)

    def _select_onmyoji(self, onmyoji_name: str) -> None:
        """选择 0 号位阴阳师/英杰（自动进入成员详情配置页）。"""
        self._tap(160, 390, 0.85)  # 槽 0 + 中心
        px, py = ONMYOJI_POS.get(onmyoji_name, ONMYOJI_POS['晴明'])
        self._tap(px, py, 0.85)

    def _batch_pick_members(self, members: list) -> None:
        """打开第 1 个式神槽选人弹窗，在一个弹窗内批量勾选全部式神（按稀有度防同名冲突）。"""
        if not members:
            return
        self._tap(MEMBER_SLOT_POS[0][0], MEMBER_SLOT_POS[0][1], 0.8)  # 打开选人弹窗
        for m in members[:5]:
            rarity = str(getattr(m, 'rarity', '') or '').upper()
            if rarity in RARITY_TAB_POS:
                rx, ry = RARITY_TAB_POS[rarity]
                self._tap(rx, ry, 0.35)
            else:
                self._tap(*RARITY_TAB_POS['全部'], 0.35)
            self._input_text(450, 118, m.name, press_enter=True)
            self._tap(975, 118, 0.55)  # 放大镜确认搜索
            self._tap(455, 240, 0.55)  # 左上角满级 6★ 卡片
            logger.info(f'[LineupBuilder] 已勾选式神: {rarity or "*"} {m.name}')
        # 关闭选人弹窗
        self._tap(1041, 67, 0.7)

    def _pick_soul_in_popup(self, soul_name: str, two_piece: bool = False) -> bool:
        """在已打开的御魂弹窗内选择四件套或两件套。"""
        if not soul_name:
            return False
        if two_piece:
            self._tap(495, 96, 0.4)  # 两件套页签
            if soul_name in SOUL2_DIRECT:
                px, py = SOUL2_DIRECT[soul_name]
                self._tap(px, py, 0.55)
                return True
        else:
            self._tap(352, 96, 0.4)  # 四件套页签
            if soul_name in SOUL4_DIRECT:
                cat, px, py = SOUL4_DIRECT[soul_name]
                cx, cy = SOUL_CAT_POS[cat]
                self._tap(cx, cy, 0.45)
                self._tap(px, py, 0.55)
                return True
        # 未在直选表中的御魂走搜索框兜底
        self._input_text(860, 96, soul_name, press_enter=True)
        time.sleep(0.45)
        self._tap(625, 195, 0.55)
        return True

    def _configure_member(self, index: int, member) -> None:
        """配置单只式神：御魂套装、二号位主属性、属性限制（速度/满暴/超星）、自动施放锁技能。"""
        sx, sy = MEMBER_SLOT_POS[index]
        self._tap(sx, sy, 0.65)

        soul_count = 0
        # 1. 四件套
        if getattr(member, 'soul4', ''):
            self._tap(1106, 107, 0.65)
            if self._pick_soul_in_popup(member.soul4, two_piece=False):
                soul_count += 1
        # 2. 两件套
        if getattr(member, 'soul2', ''):
            plus_x = 1055 if soul_count >= 1 else 1106
            self._tap(plus_x, 107, 0.65)
            if self._pick_soul_in_popup(member.soul2, two_piece=True):
                soul_count += 1

        # 3. 二号位主属性（默认只有「攻击加成」被勾选）
        wanted_s2 = list(getattr(member, 'slot2', None) or ['攻击加成'])
        default_s2 = {'攻击加成'}
        diff = (set(wanted_s2) ^ default_s2) & set(SLOT2_MAIN_POS)
        if diff:
            self._tap(1020, 235, 0.45)
            for stat in diff:
                px, py = SLOT2_MAIN_POS[stat]
                self._tap(px, py, 0.35)
            self._tap(200, 150, 0.4)

        # 4. 属性限制（速度 / 超星 / 满暴）
        min_spd = getattr(member, 'min_speed', None)
        super_star = bool(getattr(member, 'super_star', False))
        full_crit = bool(getattr(member, 'full_crit', False))
        if min_spd or super_star or full_crit:
            self._tap(1104, 489, 0.6)
            if min_spd or super_star:
                self._tap(525, 392, 0.4)  # 速度
                if super_star and not min_spd:
                    self._tap(978, 540, 0.4)  # 一键超星
                elif min_spd:
                    # 实测速度滑块左端 760 约对应 115-119 速，右端 1030 约对应 284 速（跨度 ~165 速 / 270px）
                    ratio = max(0.0, min(0.92, (float(min_spd) - 115.0) / 165.0))
                    target_x = int(760 + ratio * 270)
                    if target_x > 764:
                        self._swipe(760, 476, target_x, 476, duration_ms=350, wait=0.4)
            if full_crit:
                self._tap(525, 338, 0.4)  # 暴击
                self._tap(978, 540, 0.4)  # 一键满暴
            self._tap(200, 150, 0.5)

        # 5. 自动施放锁技能
        lock_skill = getattr(member, 'lock_skill', 'auto')
        if lock_skill in LOCK_SKILL_POS and lock_skill not in ('auto', '智能', None, ''):
            self._tap(603, 495, 0.45)
            px, py = LOCK_SKILL_POS[lock_skill]
            self._tap(px, py, 0.45)

    def build_spec_in_editor(self, spec, dungeon_tuple: tuple[str, str, str]) -> bool:
        """在已打开「我的」页签的前提下，极速新建一套完整阵容并保存。"""
        t0 = time.monotonic()
        # 1. 点击「新建阵容」
        self._tap(1160, 668, 1.2)
        self.runner.screenshot()
        if not self.runner.ocr_appear(_ocr((40, 10, 260, 60), '阵容编辑', 'builder_editor_title')):
            logger.warning('[LineupBuilder] 未能打开阵容编辑页')
            return False

        try:
            # 2. 选择目标副本
            cat, grp, sub = dungeon_tuple
            self._select_editor_dungeon(cat, grp, sub)

            # 3. 选择阴阳师/英杰（自动切入成员详情页）
            self._select_onmyoji(spec.onmyoji or '晴明')

            # 4. 批量勾选 5 只式神
            self._batch_pick_members(spec.members)

            # 5. 逐只配置御魂 / 主属性 / 速度与满暴 / 锁技能
            for idx, member in enumerate(spec.members[:5]):
                self._configure_member(idx, member)

            # 6. 保存成员设置并退回「阵容编辑」总览页
            self._tap(1102, 628, 0.75)  # 保存设置
            self._tap(1210, 62, 0.8)    # X 返回阵容编辑

            # 7. 填阵容名称与简介，点击保存阵容
            self._input_text(210, 96, spec.name[:20], press_enter=True)
            if spec.description:
                self._input_text(240, 665, spec.description[:95], press_enter=True)
            self._tap(1135, 98, 2.2)    # 保存阵容

            # 等待保存完成回到阵容助手列表（exact=True 防止误匹配「阵容编辑」）
            wait_timer = Timer(12).start()
            while not wait_timer.reached():
                self.runner.screenshot()
                if self.runner.ocr_appear(_ocr((60, 5, 260, 60), '阵容助手', 'builder_back_list'), exact=True):
                    elapsed = time.monotonic() - t0
                    logger.info(f'[LineupBuilder] 建阵完成: {spec.name} ({elapsed:.1f}s)')
                    return True
                time.sleep(0.6)
            logger.warning('[LineupBuilder] 保存阵容后未检测到返回列表')
            return False
        finally:
            self.restore_ime()

    # ---------------- 计算 + 存第 10 组预设 + 一键应用 ----------------

    def save_current_to_preset_group(self, team_group: str = '适配', preset_group: int = 10,
                                     preset_team: int = 1, from_confirm_dialog: bool = True) -> bool:
        """把当前计算好的阵容写入指定预设分组（默认第 10 组「适配」）。"""
        if from_confirm_dialog:
            preset_btn = _ocr((150, 540, 450, 660), '预设', 'builder_open_preset')
            if not self.runner.ocr_appear_click(preset_btn, interval=0.8):
                self._tap(275, 595, 0.2)
            time.sleep(1.2)
        else:
            self._tap(1125, 170, 1.2)  # 列表第一行的 ⬇️ 按钮

        # 滚动左侧分组列到底部以定位「适配」（第 10 组）
        if preset_group >= 7:
            self._swipe(98, 560, 98, 220, duration_ms=350, wait=0.45)
            self._swipe(98, 560, 98, 220, duration_ms=350, wait=0.45)
        self.runner.screenshot()

        picked_group = False
        if team_group:
            rule = _ocr((20, 210, 175, 650), team_group, f'builder_preset_group_{team_group}')
            if self.runner.ocr_appear_click(rule, interval=0.8):
                picked_group = True
                time.sleep(0.55)
        if not picked_group:
            # 第 10 组滚动到底后固定位于最底部 (98, 608)
            self._tap(98, 608, 0.55)

        self.runner.screenshot()
        # 判断目标队号是否已存在：检测中间列表里是否存在「替换」目标；若为空组则直接点「保存新预设」
        has_existing = self.runner.ocr_appear(
            _ocr((215, 240, 560, 310), str(preset_team), f'builder_team_slot_{preset_team}')
        )
        if has_existing and preset_team >= 1:
            # 拖右侧「队伍1」卡片到左侧对应槽位实现原地替换，避免无限新增堆满预设
            target_y = min(580, 295 + (preset_team - 1) * 125)
            self._swipe(960, 355, 380, target_y, duration_ms=500, wait=0.8)
            self.runner.screenshot()
            # 替换可能弹出二次确认
            self.confirm_modal_if_present(timeout=1.8)
        else:
            save_new = _ocr((320, 620, 540, 705), '保存新预设', 'builder_save_new_preset')
            if not self.runner.ocr_appear_click(save_new, interval=0.8):
                self._tap(425, 665, 0.2)
            time.sleep(0.9)
            self.confirm_modal_if_present(timeout=1.5)

        # 关闭预设保存弹窗；若随后弹出「御魂方案」保存页则顺手确认/退出，避免阻塞后续界面
        self._tap(1220, 200, 0.7)
        self.runner.screenshot()
        if self.runner.ocr_appear(_ocr((40, 5, 280, 70), '御魂方案', 'builder_soul_scheme_title')):
            # 优先点底部「保存方案/确定」，否则点左上角返回并确认退出
            save_scheme = _ocr((450, 580, 900, 700), '保存', 'builder_soul_scheme_save')
            if self.runner.ocr_appear_click(save_scheme, interval=0.8):
                time.sleep(0.8)
                self.confirm_modal_if_present(timeout=1.5)
            self.runner.screenshot()
            if self.runner.ocr_appear(_ocr((40, 5, 280, 70), '御魂方案', 'builder_soul_scheme_title2')):
                self._tap(35, 35, 0.8)
                self.confirm_modal_if_present(timeout=2.0)
        logger.info(f'[LineupBuilder] 已保存阵容到预设第 {preset_group} 组({team_group}) 第 {preset_team} 队')
        return True

    def confirm_modal_if_present(self, timeout: float = 2.8) -> bool:
        """等待并点击「即将为全队式神更换预设御魂 / 确定」二次确认弹窗。"""
        wait = Timer(timeout).start()
        confirm_rule = _ocr((630, 395, 870, 495), '确定', 'builder_modal_confirm')
        while not wait.reached():
            self.runner.screenshot()
            if self.runner.ocr_appear_click(confirm_rule, interval=0.8):
                time.sleep(0.8)
                return True
            time.sleep(0.25)
        return False

    def compute_save_and_apply(self, row_index: int = 0, save_to_preset: bool = True,
                               team_group: str = '适配', preset_group: int = 10,
                               preset_team: int = 1, check_missing: bool = False,
                               compute_timeout: float = 95.0) -> bool:
        """对列表第 row_index 行执行：计算/打开确认 -> (可选检查缺人) -> 一键应用+二次确认 -> 存第10组预设。"""
        y = 240 + row_index * 155
        self._tap(1200, y, 2.0)  # 点击该行的「计算」或已算好的「应用」

        # 等待「阵容确认」+「一键应用」出现（3500+ 御魂账号实测计算可能需 20~60s）
        apply_rule = _ocr((460, 520, 860, 670), '一键应用', 'builder_apply_btn')
        wait = Timer(compute_timeout).start()
        found_confirm = False
        while not wait.reached():
            self.runner.screenshot()
            if self.runner.ocr_appear(apply_rule):
                found_confirm = True
                break
            time.sleep(1.0)
        if not found_confirm:
            logger.warning(f'[LineupBuilder] 第 {row_index} 行计算超时({compute_timeout:.0f}s)或未弹出阵容确认')
            return False

        if check_missing:
            missing = (
                self.runner.ocr_appear(_ocr((60, 220, 900, 620), '未拥有', 'builder_missing'))
                or self.runner.ocr_appear(_ocr((60, 220, 900, 620), '缺少', 'builder_lack'))
            )
            if missing:
                logger.info(f'[LineupBuilder] 第 {row_index} 行缺式神/御魂，关闭确认页')
                self._tap(1210, 110, 0.8)
                return False

        # 1. 先点「一键应用」+ 点击「即将为全队式神更换预设御魂 -> 确定」，确保 5 式神实时穿好新御魂
        if not self.runner.ocr_appear_click(apply_rule, interval=0.8):
            self._tap(640, 595, 0.2)
        time.sleep(0.8)
        confirmed = self.confirm_modal_if_present(timeout=3.0)
        logger.info(f'[LineupBuilder] 一键应用完成 (二次确认={confirmed})')

        # 2. 再把这套阵容存入第 10 组「适配」预设（若「阵容确认」仍在则点「阵容预设」，否则从列表首行 ⬇️ 进入）
        if save_to_preset:
            try:
                self.runner.screenshot()
                still_confirm = self.runner.ocr_appear(
                    _ocr((150, 540, 450, 660), '预设', 'builder_preset_check')
                )
                self.save_current_to_preset_group(
                    team_group=team_group,
                    preset_group=preset_group,
                    preset_team=preset_team,
                    from_confirm_dialog=still_confirm,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f'[LineupBuilder] 写入第 {preset_group} 组预设失败: {exc}')

        # 关闭残留的阵容确认弹窗
        self._tap(1210, 110, 0.6)
        return True
