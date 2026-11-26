# -*- coding: utf-8 -*-
"""给 GameUi 导航补「退出游戏」确认框处理 + 返回键兜底守卫。

根因：close_unknown_pages 的 Android 返回键兜底会在庭院页误按 BACK，
游戏随即弹出「退出游戏 / 确定要退出游戏? 取消/确认」。该弹窗不在任何关闭模板里，
后续所有点击都被它吞掉，最终 GameTooManyClickError，任务连续 3 次失败后脚本退出。

用法：python dev_tools/patch_navigator_exit_dialog.py
"""
import pathlib
import py_compile
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TARGET = REPO / "tasks" / "GameUi" / "navigator.py"

CONSTANTS = '''    # 「退出游戏」确认框：庭院或任务页误触返回键时弹出；必须点「取消」，
    # 点「确认」会直接退出游戏，所以放在未知页关闭动作里优先处理。
    O_EXIT_GAME_CONFIRM = RuleOcr(
        roi=(330, 300, 620, 90),
        area=(0, 0, 1280, 720),
        mode="Full",
        method="Default",
        keyword="退出游戏",
        name="exit_game_confirm",
    )
    C_EXIT_GAME_CANCEL = RuleClick(
        roi_front=(760, 425, 100, 40),
        roi_back=(760, 425, 100, 40),
        name="exit_game_cancel",
    )

'''

DIALOG_HANDLER = '''        # 「退出游戏」确认框优先处理：其它关闭模板可能误点「确认」把游戏退掉
        if self.appear(self.O_EXIT_GAME_CONFIRM):
            logger.info('Detected exit-game confirm dialog, click cancel')
            x, y = self.C_EXIT_GAME_CANCEL.coord()
            self.device.click(x, y, control_check=False, control_name='EXIT_GAME_CANCEL')
            self._record_unknown_close_event('exit_game_cancel')
            time.sleep(0.8)
            return True

        logger.warning("Try switch to a supported page")'''

BACK_GUARD = '''        # 兜底：大部分弹窗只有 Android 返回键能关；但庭院页按返回键会弹「退出游戏」，
        # 所以先确认当前不是庭院页，再按返回键。
        if not self.appear(self.I_CHECK_MAIN):
            try:
                self.device.adb_shell(['input', 'keyevent', '4'])
                self._record_unknown_close_event('back_key')
                time.sleep(1.0)
                return True
            except Exception as error:  # noqa: BLE001
                logger.debug(f'Back key fallback failed: {error}')
        else:
            logger.warning('Back key fallback skipped on page_main (would open exit dialog)')'''

OLD_BACK = '''        # 兜底：庭院功能面板等弹窗所有模板关闭动作都匹配不到，只有 Android 返回键能关
        try:
            self.device.adb_shell(['input', 'keyevent', '4'])
            self._record_unknown_close_event('back_key')
            time.sleep(1.0)
            return True
        except Exception as error:  # noqa: BLE001
            logger.debug(f'Back key fallback failed: {error}')'''


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    changed = False

    if "O_EXIT_GAME_CONFIRM" not in text:
        anchor = "    def __init__(self, config, device):"
        if anchor not in text:
            print("anchor for constants not found")
            return 1
        text = text.replace(anchor, CONSTANTS + anchor, 1)
        changed = True

    if "Detected exit-game confirm dialog" not in text:
        anchor = '        logger.warning("Try switch to a supported page")'
        if anchor not in text:
            print("anchor for dialog handler not found")
            return 1
        text = text.replace(anchor, DIALOG_HANDLER, 1)
        changed = True

    if OLD_BACK in text:
        text = text.replace(OLD_BACK, BACK_GUARD, 1)
        changed = True
    elif "Back key fallback skipped on page_main" not in text:
        print("anchor for back-key guard not found")
        return 1

    if changed:
        TARGET.write_text(text, encoding="utf-8")
        print("patched", TARGET)
    else:
        print("already patched", TARGET)

    py_compile.compile(str(TARGET), doraise=True)
    print("py_compile ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
