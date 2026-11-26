"""Apply one team's saved souls and confirm that same battle preset."""
from dataclasses import dataclass
import time
import cv2
import numpy as np

from module.atom.image import RuleImage
from module.atom.ocr import RuleOcr
from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.preset_list import (
    IndexedPresetList, PresetListLayout, PresetUnavailable, BATTLE_GROUPS, BATTLE_TEAMS,
    SOUL_GROUPS, SOUL_TEAMS, foreground_rule, preset_index,
)
from tasks.Component.SwitchSoul.assets import SwitchSoulAssets

POPUP_CONFIRM = RuleImage(
    (669, 485, 118, 45), (659, 475, 138, 65), "Template matching", .88,
    "./tasks/Component/GeneralBattle/gb/preset_popup_confirm.png")
POPUP_GROUPS = PresetListLayout("副本预设分组", (410, 158, 117, 291), 186, 50, 465)
POPUP_TEAMS = PresetListLayout("副本预设队伍", (538, 158, 380, 291), 207, 96, 700)
RECORD_PRESET_TITLE = RuleOcr(roi=(723, 75, 122, 38), area=(723, 75, 122, 38),
                              mode="Single", method="Default", keyword="",
                              name="record_preset_title")

# 打开战斗预设面板的预算：点击次数与每次点击后的验证时间都设上限，
# 避免旧逻辑那样“识别到预设就一直点”，把面板开开关关拖到超时。
PANEL_OPEN_TIMEOUT = 12.
PANEL_VERIFY_TIMEOUT = 2.5
PANEL_OPEN_CLICKS = 4


@dataclass(frozen=True)
class Panel:
    groups: PresetListLayout
    teams: PresetListLayout
    confirm: object
    popup: bool = False


def linked_preset_config(task):
    """Resolve only the current task and active Hunt branch."""
    from module.config.utils import convert_to_underscore
    try:
        name = task.get_task_name()
        conf = getattr(task.config, convert_to_underscore(name), None)
    except (AttributeError, TypeError):
        return None
    if conf is None:
        return None
    if name == "Hunt":
        key = "kirin_battle_config" if getattr(task, "kirin_day", True) else "netherworld_battle_config"
        battle = getattr(conf, key, None)
    elif name == "Dokan" and getattr(getattr(conf, "dokan_config", None), "switch_preset_enable", False):
        return conf.dokan_config
    else:
        battle = next((getattr(conf, key) for key in ("general_battle_config", "general_battle")
                       if getattr(conf, key, None) is not None), None)
    return battle if battle is not None and getattr(battle, "preset_enable", False) else None


def uses_linked_preset(task):
    return linked_preset_config(task) is not None


def defer_next_run(task):
    """预设降级时推迟下一次运行；每次任务只记一次，失败也不抛错。"""
    if getattr(task, '_preset_defer_done', False):
        return False
    setter = getattr(task, 'set_next_run', None)
    getter = getattr(task, 'get_task_name', None)
    if not callable(setter) or not callable(getter):
        return False
    try:
        name = getter()
    except (AttributeError, TypeError):
        return False
    if not name:
        return False
    try:
        # 等价于 set_next_run(success=False)：按失败间隔推迟，不中断当前玩法
        setter(name, success=False, finish=True)
    except Exception as error:
        logger.warning(f'预设降级时设置下次运行时间失败：{type(error).__name__}')
        return False
    task._preset_defer_done = True
    return True


def degrade(task, message, end_task=False):
    """预设无法伸达时的统一降级：WARNING + 推迟下次运行，可继续战斗。

    end_task=True 时用 TaskEnd 结束本次任务（而不是抛 ScriptError 让进程 exit）。
    """
    logger.warning(message)
    defer_next_run(task)
    if end_task:
        raise TaskEnd
    return False


def _panel(task):
    if (foreground_rule(task, task.I_PRESET_ENSURE)
            or foreground_rule(task, task.I_PRESENT_LESS_THAN_5)):
        return Panel(BATTLE_GROUPS, BATTLE_TEAMS, task.I_PRESET_ENSURE)
    if foreground_rule(task, POPUP_CONFIRM):
        return Panel(POPUP_GROUPS, POPUP_TEAMS, POPUP_CONFIRM, True)
    return None


def _find_soul_button(image, layout, center_y):
    """Never match or click the equip button belonging to another row.

    找不到（例如该队伍没保存过御魂预设）或位置不唯一时返回 None，
    由调用方记录 WARNING 并跳过御魂装配，绝不误点别的行。
    """
    x, top, width, height = map(int, layout.roi)
    in_records = layout == SOUL_TEAMS
    # 式神录的编辑按钮紧挨御魂按钮。只比较最右侧按钮的形状，兼容幕间换色。
    left = max(x, x + width - (40 if in_records else 120))
    y = max(top, int(center_y - layout.stride / 2))
    end = min(top + height, int(center_y + layout.stride / 2))
    region = image[y:end, left:x + width]
    if not region.size:
        logger.warning("选中队伍的御魂按钮区域不可见，跳过御魂装配")
        return None
    source = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY) if in_records else region
    threshold = .68 if in_records else .84
    candidates = []
    rules = (SwitchSoulAssets.I_SOU_SWITCH_1, SwitchSoulAssets.I_SOU_SWITCH_2,
             SwitchSoulAssets.I_SOU_SWITCH_3, SwitchSoulAssets.I_SOU_SWITCH_4,
             SwitchSoulAssets.I_SOU_CLICK_PRESENT)
    for rule in rules:
        for scale in (.8, .9, 1., 1.1, 1.2):
            template = cv2.resize(rule.image, None, fx=scale, fy=scale)
            h, w = template.shape[:2]
            if h > region.shape[0] or w > region.shape[1]:
                continue
            signal = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY) if in_records else template
            _, score, _, (dx, dy) = cv2.minMaxLoc(
                cv2.matchTemplate(source, signal, cv2.TM_CCOEFF_NORMED))
            matched = region[dy:dy+h, dx:dx+w]
            if (np.isfinite(score) and score >= threshold
                    and float(matched.mean()) >= float(template.mean()) * .78):
                candidates.append((float(score), left + dx + w // 2, y + dy + h // 2))
    if not candidates:
        logger.warning("未找到选中队伍的一键预设御魂按钮（该队伍可能没保存御魂预设）")
        return None
    candidates.sort(reverse=True)
    best = candidates[0]
    if any(abs(other[1]-best[1]) + abs(other[2]-best[2]) > 15
           and best[0] - other[0] < .03 for other in candidates[1:]):
        logger.warning("选中队伍的御魂按钮位置不唯一，跳过御魂装配以免点错行")
        return None
    return best[1], best[2]


def _soul_messages(task):
    from module.ocr.models import get_ocr_model
    results = get_ocr_model().detect_and_ocr(task.device.image[100:560, 230:1080])
    return "".join(str(result.ocr_text) for result in results)


def apply_team_souls(task, panel, center_y):
    return _apply_saved_souls(task, panel.teams, center_y, lambda: _panel(task) == panel)


def prepare_record_team_souls(task, group, team):
    """Equip the selected team's saved souls from the Shikigami Records preset list."""
    task._record_team_souls = None
    group, team = preset_index(group), preset_index(team)
    visible = lambda: record_preset_visible(task)
    try:
        task.click_preset()
        navigator = IndexedPresetList(task, visible)
        navigator.select(group, SOUL_GROUPS)
        _, center_y = navigator.locate(team, SOUL_TEAMS)
    except PresetUnavailable as error:
        degrade(task, f'式神录预设无法定位（{error}），跳过御魂装配并继续战斗')
        return False
    if _apply_saved_souls(task, SOUL_TEAMS, center_y, visible):
        task._record_team_souls = (group, team)
        return True
    return False


def record_preset_visible(task):
    if foreground_rule(task, task.I_SOU_TEAM_PRESENT):
        return True
    image = task.device.image
    x, y, width, height = RECORD_PRESET_TITLE.roi
    title = image[y:y + height, x:x + width]
    bright_text = np.count_nonzero(np.min(title, axis=2) > 180)
    return (bright_text >= width * height * .08
            and RECORD_PRESET_TITLE.ocr(image).strip() == "队伍预设")


def _apply_saved_souls(task, layout, center_y, panel_visible):
    """点一次一键预设御魂并确认；任何异常态都降级返回 False，不中断玩法。"""
    task.screenshot()
    if not panel_visible():
        return degrade(task, "队伍预设面板已改变，跳过御魂装配")
    button = _find_soul_button(task.device.image, layout, center_y)
    if button is None:
        return degrade(task, "选中的队伍未找到一键预设御魂按钮，跳过御魂装配并用当前御魂出战")
    task.device.click(button[0], button[1], control_name="PRESET_TEAM_APPLY_SAVED_SOULS")
    deadline = time.monotonic() + 8
    stable = 0
    while time.monotonic() < deadline:
        time.sleep(.35)
        task.screenshot()
        message = _soul_messages(task)
        failed = any(word in message for word in
                     ("失败", "无法", "不能", "未保存", "未设置", "未配置", "未预设", "未装备", "没有预设",
                      "御魂不足", "御魂缺失", "御魂冲突", "不完整", "不齐全", "缺少"))
        if "御魂" in message and failed:
            return degrade(task, "队伍的一键预设御魂未成功：" + message[:180])
        if foreground_rule(task, task.I_UI_CONFIRM):
            if ("御魂" not in message
                    or not any(word in message for word in ("装备", "装配", "穿戴"))
                    or not any(word in message for word in ("预设", "一键"))
                    or any(word in message for word in ("覆盖", "同步到", "修改预设", "重置", "删除"))):
                return degrade(task, "装配御魂时出现未知确认框，跳过确认以免改错御魂")
            task.appear_then_click(task.I_UI_CONFIRM, interval=.8)
            stable = 0
            continue
        if not panel_visible():
            return degrade(task, "装配御魂后未返回同一预设面板，跳过御魂装配")
        stable += 1
        if stable >= 3:
            logger.info("已优先点击选中队伍的一键预设御魂，确认后继续应用队伍")
            return True
    return degrade(task, "队伍预设御魂确认超时，跳过御魂装配")


def _wait_panel(task, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task.screenshot()
        panel = _panel(task)
        if panel is not None:
            return panel
        time.sleep(.3)
    return None


def _open_panel(task, deadline):
    """点一次预设入口就必须验证面板是否出现，最多 PANEL_OPEN_CLICKS 次。"""
    clicks = 0
    while time.monotonic() < deadline and clicks < PANEL_OPEN_CLICKS:
        task.screenshot()
        panel = _panel(task)
        if panel is not None:
            return panel
        clicked = bool(task.appear_then_click(task.I_PRESET, threshold=.8, interval=1)
                       or task.appear_then_click(task.I_PRESET_WIT_NUMBER, threshold=.8, interval=1))
        if not clicked:
            if task.ocr_appear(task.O_PRESET_FULL):
                clicked = bool(task.click(task.O_PRESET_FULL, interval=1))
            elif task.ocr_appear(task.O_PRESET):
                clicked = bool(task.click(task.O_PRESET, interval=1))
        if not clicked:
            time.sleep(.3)
            continue
        clicks += 1
        panel = _wait_panel(task, PANEL_VERIFY_TIMEOUT)
        if panel is not None:
            return panel
        logger.warning(f'点击战斗预设入口后面板仍未出现（第{clicks}次）')
    return _panel(task)


def select_battle_team(task, enable=False, preset_group=1, preset_team=1):
    """选定战斗预设并确认。返回 True 表示预设已生效。

    面板打不开 / 目标项不存在 / 一键预设御魂按钮缺失 一律降级：
    记 WARNING、推迟下次运行、跳过预设继续战斗，绝不抛出让进程 exit 的异常。
    """
    if not enable:
        logger.info("Preset is disabled")
        return True
    group, team = preset_index(preset_group), preset_index(preset_team)
    panel = _open_panel(task, time.monotonic() + PANEL_OPEN_TIMEOUT)
    if panel is None:
        return degrade(task, f'战斗预设面板未打开，跳过第{group}组第{team}队预设继续战斗')
    visible = lambda: _panel(task) == panel
    try:
        navigator = IndexedPresetList(task, visible)
        navigator.select(group, panel.groups)
        _, center_y = navigator.select(team, panel.teams)
    except PresetUnavailable as error:
        return degrade(task, f'战斗预设无法定位（{error}），跳过第{group}组第{team}队预设继续战斗')
    task.screenshot()
    if task.appear(task.I_PRESENT_LESS_THAN_5):
        return degrade(task, f'选定预设队伍不足5位式神，结束本次出战（第{group}组第{team}队）',
                       end_task=True)
    if not panel.popup and getattr(task, '_record_team_souls', None) == (group, team):
        logger.info(f"同一队伍已在式神录应用御魂，确认第{group}组第{team}队出战")
    elif not apply_team_souls(task, panel, center_y):
        logger.warning(f'第{group}组第{team}队的御魂预设未应用，继续用该队伍出战')
    deadline = time.monotonic() + 10
    confirmed = False
    while time.monotonic() < deadline:
        task.screenshot()
        if task.appear(task.I_PRESENT_LESS_THAN_5):
            return degrade(task, '选定预设队伍不足5位式神，结束本次出战', end_task=True)
        if visible():
            if task.appear_then_click(panel.confirm, threshold=.8, interval=1):
                confirmed = True
            continue
        if confirmed:
            logger.info(f"Preset and saved souls selected: group {group}, team {team}")
            return True
        return degrade(task, '预设御魂装配后队伍面板异常关闭，跳过预设继续战斗')
    return degrade(task, f'第{group}组第{team}队预设确认超时，跳过预设继续战斗')
