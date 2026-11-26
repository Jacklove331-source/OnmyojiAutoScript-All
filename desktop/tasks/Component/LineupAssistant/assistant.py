# This Python file uses the following encoding: utf-8

"""游戏内「阵容助手」自动化。

官方功能说明（2025-06 上线）：式神录顶部 → 阵容助手，可按副本筛选官方/社区/我的
阵容，点「计算」按自己的式神与御魂算出实际配置，再「一键应用」套用。

OAS 原来的「自动阵容」只是往任务配置里写预设组/队号，需要提前自己配好预设，
和这个功能不是一回事。本模块负责真正调用游戏内的阵容助手。
"""

from module.atom.ocr import RuleOcr
from module.logger import logger
from tasks.GameUi.page import page_main, page_shikigami_records

import time


def _ocr(roi, keyword='', name='lineup_assistant') -> RuleOcr:
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


class LineupAssistantMixin:
    """驱动游戏内「阵容助手」：筛选副本 → 计算 → 一键应用。"""

    # OAS 任务 -> (分类, 大副本, 小副本关键字)
    DUNGEON_MAP = {
        'Orochi': ('御魂', '八岐大蛇', None),
        'OrochiAdventure': ('御魂', '八岐大蛇', None),
        'SoulZones': ('御魂', '八岐大蛇', None),
        'Sougenbi': ('御魂', '业原火', None),
        'FallenSun': ('御魂', '日轮之陨', None),
        'EternitySea': ('御魂', '永生之海', None),
        'TrueOrochi': ('御魂', '真·八岐大蛇', None),
        'EvoZone': ('觉醒材料', None, None),
        'BondlingFairyland': ('探查', '契灵', None),
        'DemonEncounter': ('逢魔之时', None, None),
        'DemonicEncounter': ('逢魔之时', None, None),
        'AreaBoss': ('地域鬼王', None, None),
        'Duel': ('斗技', None, None),
    }

    # 界面区域
    ROI_TITLE = (60, 5, 260, 60)          # 「阵容助手」标题（避开式神录右上角的入口按钮）
    ROI_TAB = (20, 100, 80, 420)          # 官方 / 社区 / 我的
    ROI_FILTER = (100, 70, 500, 130)      # 副本筛选框
    ROI_FILTER_LEFT = (105, 135, 275, 700)   # 筛选弹窗左列（分类 / 大副本）
    ROI_FILTER_RIGHT = (285, 150, 500, 640)  # 筛选弹窗右侧（小副本）
    ROI_EMPTY = (330, 200, 950, 620)      # 「暂无阵容」提示
    ROI_CONTENT = (80, 140, 1270, 700)    # 阵容列表内容区
    ROI_FIRST_CARD = (100, 140, 1270, 310)   # 第一条阵容卡
    ROI_COMPUTE = (1100, 180, 1270, 300)  # 第一张卡的「计算」按钮
    ROI_APPLY = (500, 540, 820, 650)      # 阵容确认里的「一键应用」
    ROI_FILTER_CONFIRM = (380, 620, 520, 700)  # 副本筛选弹窗底部的「确定」按钮
    ROI_CONFIRM = (630, 395, 870, 495)    # 更换御魂二次确认「确定」按钮

    def apply_from_assistant(self, task_name: str, dungeon: str | None = None) -> bool:
        """打开阵容助手，挑选该任务的阵容并一键应用。"""
        category, group, sub = self.DUNGEON_MAP.get(task_name, (None, None, None))
        if dungeon:
            # 允许配置覆盖，例如 "御魂/八岐大蛇/神罚"
            parts = [p for p in str(dungeon).replace('·', '/').split('/') if p]
            if len(parts) == 1:
                sub = parts[0]
            elif len(parts) == 2:
                category, sub = parts
            elif len(parts) >= 3:
                category, group, sub = parts[:3]

        logger.hr('Lineup assistant', level=1)
        if not self._open_assistant():
            return False
        try:
            self._dismiss_dialogs()
            if category or group or sub:
                self._select_dungeon(category, group, sub)
            if not self._pick_first_available_card():
                logger.warning('[LineupAssistant] no lineup available for this dungeon')
                return False
            if not self._compute_and_apply():
                return False
            logger.info('[LineupAssistant] lineup applied from game assistant')
            return True
        finally:
            self._close_assistant()

    # ---------------- 界面操作 ----------------

    def _dismiss_dialogs(self) -> None:
        """清掉上一次残留的「计算中」「阵容确认」或「保存新预设」弹窗，否则点击会被吃掉。"""
        self.screenshot()
        if self.ocr_appear(_ocr((280, 610, 560, 705), '保存新预设', 'assistant_preset_probe')):
            logger.info('[LineupAssistant] close leftover preset save dialog')
            self.device.click(1220, 200, control_check=False)
            time.sleep(0.8)
            self.screenshot()
        if self.ocr_appear(_ocr((460, 520, 860, 670), '一键应用', 'assistant_apply_probe')):
            logger.info('[LineupAssistant] close leftover confirm dialog')
            self.device.click(1210, 110, control_name='assistant_close_result')
            time.sleep(1.0)
            self.screenshot()
            return
        if self.ocr_appear(_ocr((450, 240, 900, 600), '点击屏幕隐藏', 'assistant_computing_probe')):
            logger.info('[LineupAssistant] cancel leftover computing dialog')
            self.ocr_appear_click(_ocr((680, 250, 860, 360), '取消', 'assistant_cancel'), interval=1)
            time.sleep(1.0)
            self.screenshot()

    def _open_assistant(self) -> bool:
        self.screenshot()
        if self.ocr_appear(_ocr(self.ROI_TITLE, '阵容助手', 'assistant_title'), exact=True):
            logger.info('[LineupAssistant] already on assistant page')
            return True
        # 若上次异常停留在「阵容编辑」，先安全退回阵容助手列表
        if self.ocr_appear(_ocr(self.ROI_TITLE, '阵容编辑', 'assistant_editor_probe'), exact=True):
            logger.info('[LineupAssistant] back out of leftover editor page')
            self.device.click(42, 42, control_check=False)
            time.sleep(0.9)
            self.screenshot()
            self.ocr_appear_click(_ocr((430, 395, 620, 495), '退出', 'assistant_editor_exit'), interval=0.8)
            time.sleep(0.9)
            self.screenshot()
            if self.ocr_appear(_ocr(self.ROI_TITLE, '阵容助手', 'assistant_title'), exact=True):
                return True
        self.goto_page(page_shikigami_records)
        # 「阵容助手」按钮在式神录标题右边 (302, 30)，旧坐标 355 容易点到「近期调整」
        for idx in range(5):
            entry_x = 302 if idx < 3 else 355
            self.device.click(entry_x, 30, control_name='lineup_assistant_entry')
            time.sleep(1.0)
            self.screenshot()
            if self.ocr_appear(_ocr(self.ROI_TITLE, '阵容助手', 'assistant_title'), exact=True):
                logger.info('[LineupAssistant] opened')
                return True
        logger.warning('[LineupAssistant] cannot open game lineup assistant')
        return False

    def _filter_popup_open(self) -> bool:
        self.screenshot()
        if self.ocr_appear(_ocr(self.ROI_FILTER_CONFIRM, '确定', 'assistant_filter_confirm_probe'), exact=True):
            return True
        return self.ocr_appear(_ocr(self.ROI_FILTER_LEFT, '御魂', 'assistant_filter_probe'), exact=True)

    def _select_dungeon(self, category, group, sub) -> bool:
        """按分类/大副本/小副本设置筛选；若筛选框已是目标副本则直接跳过。"""
        self.screenshot()
        target_kw = sub or group or category
        if target_kw and self.ocr_appear(_ocr(self.ROI_FILTER, target_kw, 'assistant_filter_cur')):
            logger.info(f'[LineupAssistant] filter already matches: {target_kw}')
            return True

        opened = self._filter_popup_open()
        for attempt in range(4):
            if opened:
                break
            # 点筛选框的箭头位置更容易命中
            x = 365 if attempt % 2 == 0 else 455
            self.device.click(x, 99, control_name='lineup_assistant_filter')
            time.sleep(1.2)
            opened = self._filter_popup_open()
        if not opened:
            logger.warning('[LineupAssistant] filter popup not open')
            return False

        for keyword in (category, group, sub):
            if not keyword:
                continue
            clicked = False
            for roi in (self.ROI_FILTER_LEFT, self.ROI_FILTER_RIGHT):
                rule = _ocr(roi, keyword, f'assistant_{keyword}')
                if self.ocr_appear_click(rule, interval=1.2):
                    clicked = True
                    time.sleep(0.8)
                    self.screenshot()
                    break
            if not clicked:
                logger.warning(f'[LineupAssistant] filter option not found: {keyword}')

        if not self.ocr_appear_click(_ocr(self.ROI_FILTER_CONFIRM, '确定', 'assistant_confirm'), interval=1.5):
            logger.warning('[LineupAssistant] filter confirm not found')
        time.sleep(0.8)
        self.screenshot()
        return True

    def _pick_first_available_card(self) -> bool:
        """我的 → 官方 → 社区，找到第一个有阵容的页签。"""
        for tab in ('我的', '官方', '社区'):
            if not self.ocr_appear_click(_ocr(self.ROI_TAB, tab, f'assistant_tab_{tab}'), interval=1.2):
                continue
            time.sleep(0.8)
            self.screenshot()
            if self.ocr_appear(_ocr(self.ROI_EMPTY, '暂无阵容', 'assistant_empty'), exact=True):
                logger.info(f'[LineupAssistant] tab {tab} has no lineup, try next')
                continue
            if not self.ocr_appear(_ocr(self.ROI_CONTENT, '计算', 'assistant_has_compute')):
                logger.info(f'[LineupAssistant] tab {tab} has no compute button, try next')
                continue
            logger.info(f'[LineupAssistant] use tab {tab}')
            return True
        return False

    def apply_official_best(self, max_rows: int = 3, task_name: str = '',
                            dungeon: str | None = None,
                            save_to_preset: bool = True,
                            team_group: str = '适配',
                            preset_group: int = 10,
                            preset_team: int = 1) -> bool:
        """官方模式：逐套官方阵容试算，跳过缺式神/御魂的方案，存入第10组预设并一键应用+二次确认。"""
        from tasks.Component.LineupAssistant.builder import FastLineupBuilder

        logger.hr('Official lineup (best of rows)', level=1)
        if not self._open_assistant():
            return False
        builder = FastLineupBuilder(self)
        try:
            self._dismiss_dialogs()
            category, group, sub = self.DUNGEON_MAP.get(task_name, (None, None, None))
            if dungeon:
                parts = [p for p in str(dungeon).replace('·', '/').split('/') if p]
                if len(parts) == 1:
                    sub = parts[0]
                elif len(parts) == 2:
                    category, sub = parts
                elif len(parts) >= 3:
                    category, group, sub = parts[:3]
            if category or group or sub:
                self._select_dungeon(category, group, sub)
            self.device.click(45, 130, control_check=False)  # 官方页签
            time.sleep(0.8)
            self.screenshot()
            for index in range(max_rows):
                logger.info(f'[LineupAssistant] try official row {index}')
                if builder.compute_save_and_apply(
                    row_index=index,
                    save_to_preset=save_to_preset,
                    team_group=team_group,
                    preset_group=preset_group,
                    preset_team=preset_team,
                    check_missing=True,
                ):
                    logger.info(f'[LineupAssistant] applied official row {index}')
                    return True
            logger.warning('[LineupAssistant] no feasible official lineup found')
            return False
        finally:
            builder.restore_ime()
            self._close_assistant()

    def apply_or_build_spec(self, spec, dungeon_tuple: tuple | None = None,
                            reuse_existing: bool = True,
                            save_to_preset: bool = True,
                            team_group: str = '适配',
                            preset_group: int = 10,
                            preset_team: int = 1) -> dict:
        """按 LineupSpec 极速落地：若「我的」已有同名方案则直接复用计算，否则自动建阵并写入第10组预设。"""
        from tasks.Component.LineupAssistant.builder import FastLineupBuilder

        t0 = time.monotonic()
        if dungeon_tuple is None:
            parts = [p for p in str(getattr(spec, 'dungeon', '') or '').replace('·', '/').split('/') if p]
            if len(parts) >= 3:
                dungeon_tuple = (parts[0], parts[1], parts[2])
            elif len(parts) == 2:
                dungeon_tuple = (parts[0], parts[1], None)
            elif len(parts) == 1:
                dungeon_tuple = (None, None, parts[0])
            else:
                dungeon_tuple = (None, None, None)

        logger.hr(f'Auto lineup spec: {spec.name}', level=1)
        if not self._open_assistant():
            return {'ok': False, 'reason': 'cannot open lineup assistant', 'reused': False}

        builder = FastLineupBuilder(self)
        reused = False
        try:
            self._dismiss_dialogs()
            # 切「我的」页签并按副本筛选
            self.device.click(45, 370, control_check=False)
            time.sleep(0.8)
            cat, grp, sub = dungeon_tuple
            if cat or grp or sub:
                self._select_dungeon(cat, grp, sub)
                self.device.click(45, 370, control_check=False)
                time.sleep(0.6)
            self.screenshot()

            # 快路径：若「我的」首条已存在同名阵容（按钮为「计算」或已算好的「应用」），直接复用
            has_action = (
                self.ocr_appear(_ocr(self.ROI_COMPUTE, '计算', 'assistant_reuse_compute'))
                or self.ocr_appear(_ocr(self.ROI_COMPUTE, '应用', 'assistant_reuse_apply'))
            )
            key_name = str(spec.name or '').replace('[OAS]', '').strip()[:6]
            matched_existing = (
                reuse_existing
                and has_action
                and bool(key_name)
                and (
                    self.ocr_appear(_ocr(self.ROI_FIRST_CARD, key_name, 'assistant_reuse_name'))
                    or self.ocr_appear(_ocr(self.ROI_FIRST_CARD, '[OAS]', 'assistant_reuse_tag'))
                )
            )
            if matched_existing:
                logger.info(f'[LineupAssistant] 命中已有方案，走快路径复用: {spec.name}')
                reused = True
            else:
                if not builder.build_spec_in_editor(spec, dungeon_tuple):
                    return {'ok': False, 'reason': 'build_spec_in_editor failed', 'reused': False}
                # 回到列表后确保仍在「我的」页签
                self.device.click(45, 370, control_check=False)
                time.sleep(0.7)

            ok = builder.compute_save_and_apply(
                row_index=0,
                save_to_preset=save_to_preset,
                team_group=team_group,
                preset_group=preset_group,
                preset_team=preset_team,
                check_missing=False,
            )
            elapsed = round(time.monotonic() - t0, 1)
            return {
                'ok': bool(ok),
                'reused': reused,
                'elapsed_s': elapsed,
                'preset_group': preset_group,
                'preset_team': preset_team,
                'name': spec.name,
            }
        finally:
            builder.restore_ime()
            self._close_assistant()

    def _compute_and_apply(self) -> bool:
        if not self.ocr_appear_click(_ocr(self.ROI_COMPUTE, '计算', 'assistant_compute'), interval=2):
            logger.warning('[LineupAssistant] compute button not found')
            return False
        logger.info('[LineupAssistant] computing lineup...')
        apply_rule = _ocr(self.ROI_APPLY, '一键应用', 'assistant_apply')
        for index in range(180):
            time.sleep(0.8)
            self.screenshot()
            if self.ocr_appear_click(apply_rule, interval=2):
                logger.info('[LineupAssistant] click apply')
                time.sleep(1.0)
                self.screenshot()
                self.ocr_appear_click(_ocr(self.ROI_CONFIRM, '确定', 'assistant_apply_confirm'), interval=1.5)
                return True
            if index % 15 == 0:
                logger.debug(f'[LineupAssistant] waiting compute... ({index})')
        logger.warning('[LineupAssistant] compute result timeout')
        return False

    # ---------------- 中文输入（阵容名称 / 搜索框） ----------------

    def _set_clipboard(self, text: str) -> bool:
        """把文本写入 Windows 剪贴板；MuMu 与宿主机共享剪贴板。"""
        import json as _json
        import subprocess

        for _ in range(3):
            try:
                subprocess.run(
                    ['powershell', '-STA', '-Command',
                     f'Set-Clipboard -Value {_json.dumps(text)}'],
                    timeout=10, check=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return True
            except Exception:  # noqa: BLE001 - 剪贴板偶发被占用，重试即可
                time.sleep(0.3)
        return False

    def _paste_text(self, text: str) -> bool:
        """中文输入：先写剪贴板，再给输入框发 ADB 粘贴键(279)。

        游戏自带的输入弹窗支持粘贴，这是目前唯一稳定的中文输入路径
        （`input text` 只支持 ASCII）。
        """
        if not self._set_clipboard(text):
            logger.warning('[LineupAssistant] set clipboard failed')
            return False
        try:
            self.device.adb_shell(['input', 'keyevent', '279'])
        except Exception as error:  # noqa: BLE001
            logger.warning(f'[LineupAssistant] paste key failed: {error}')
            return False
        time.sleep(1.0)
        self.screenshot()
        return True

    def _open_new_lineup_editor(self) -> bool:
        """我的 → 新建阵容：打开「阵容编辑」页。"""
        self.device.click(1145, 645, control_check=False)
        time.sleep(1.5)
        self.screenshot()
        return self.ocr_appear(_ocr((40, 10, 260, 60), '阵容编辑', 'lineup_editor_title'))

    def _set_lineup_name(self, name: str) -> bool:
        """填写阵容名称：点名称框 → 粘贴 → 弹窗确定。"""
        self.device.click(260, 97, control_check=False)
        time.sleep(1.2)
        self.screenshot()
        if not self._paste_text(name):
            return False
        # 输入弹窗右上角「确定」
        self.device.click(1075, 33, control_check=False)
        time.sleep(1.0)
        self.screenshot()
        return True

    def _add_shikigami(self, name: str, slot_x: int) -> bool:
        """点第 N 个式神位的 +，搜索名字并选中第一张卡。"""
        self.device.click(slot_x, 350, control_check=False)
        time.sleep(1.2)
        self.screenshot()
        if not self.ocr_appear(_ocr((280, 20, 700, 70), '选择加入阵容的式神', 'lineup_add_title')):
            logger.warning('[LineupAssistant] shikigami picker not opened')
            return False
        self.device.click(640, 114, control_check=False)
        time.sleep(1.0)
        self.screenshot()
        if not self._paste_text(name):
            return False
        self.device.click(1075, 33, control_check=False)
        time.sleep(1.2)
        self.screenshot()
        # 选中第一张卡（左上角卡片）
        self.device.click(455, 250, control_check=False)
        time.sleep(0.8)
        self.screenshot()
        return True

    def create_lineup_in_my(self, name: str, shikigami, dungeon: str | None = None) -> bool:
        """AI 模式：在「我的」里新建阵容（名称 + 式神 + 保存）。

        流程已按实机界面写好（阵容编辑页 → 名称 → 5 个位 → 保存），
        但尚未跑通完整验证，调用方必须检查返回值并复核保存结果。
        """
        logger.hr('Create lineup in My', level=1)
        if not self._open_assistant():
            return False
        try:
            self._dismiss_dialogs()
            if not self.ocr_appear_click(_ocr(self.ROI_TAB, '我的', 'assistant_tab_mine'), interval=1.2):
                logger.warning('[LineupAssistant] cannot switch to My tab')
                return False
            time.sleep(0.8)
            self.screenshot()
            if not self._open_new_lineup_editor():
                logger.warning('[LineupAssistant] cannot open lineup editor')
                return False
            self._set_lineup_name(name)
            slots = (350, 520, 700, 880, 1060)
            for slot_x, unit in zip(slots, list(shikigami)[:5]):
                if not self._add_shikigami(unit, slot_x):
                    logger.warning(f'[LineupAssistant] add shikigami failed: {unit}')
            self.device.click(1130, 98, control_check=False)  # 保存阵容
            time.sleep(2.0)
            self.screenshot()
            return True
        finally:
            self._close_assistant()

    def _close_assistant(self) -> None:
        for _ in range(3):
            self.device.click(30, 30)
            self.screenshot()
            if not _ocr(self.ROI_TITLE, '阵容助手', 'assistant_title').ocr(self.device.image):
                break
        try:
            self.goto_page(page_main)
        except Exception as error:  # noqa: BLE001 - 返回庭院失败不影响已应用的阵容
            logger.warning(f'[LineupAssistant] back to main failed: {error}')
        logger.info('[LineupAssistant] closed')
