# This Python file uses the following encoding: utf-8

"""修补登录流程：点「进入游戏」后等加载完成，而不是只等 5 秒就重试点击。

证据（2026-11-09，oas1 当日日志）：
  - `Click ... @ LOGIN_ENTER_GAME_ORIGIN` 46 次，`Wait until appear
    LOGIN_LOGIN_SPECIFIC_SERVE timeout` 45 次；
  - 最后一次点击（22:17:11）到真正登录成功（22:17:44）相隔 33 秒；
  - `I_LOGIN_SPECIFIC_SERVE` 模板在 191 张历史截图里 0 命中（最高 0.382），
    说明它的等待目标本身也已经过时 —— 等它只是空等 5 秒。
  - `O_LOGIN_SPECIFIC_SERVE` 是 `OcrMode.FULL` + 空 keyword，在任意游戏画面上
    都会返回非零 ROI（实测在町中返回 (793,316,21,19)），不能当「角色页存在」判据。

改法：新增 `_wait_login_loaded()`，同时接受「角色选择页」与「已进庭院」，
最长等 45 秒；任一条命中立即返回，都不命中才让调用方重试点击。

用法：``.\\toolkit\\python.exe dev_tools\\patch_login_loaded_wait.py``
幂等：已经打过补丁会直接跳过。
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / 'tasks' / 'Restart' / 'login.py'

ANCHOR_DEF = '    def _app_handle_login(self) -> bool:\n'
METHOD = '''    def _wait_login_loaded(self, max_wait: float = 45) -> bool:
        """点「进入游戏」之后等加载完成，而不是死等角色页模板。

        2026-11-09 实测：点击到庭院/角色页约需 33s，旧逻辑只等 5s 就回到循环重新
        点击，把一次加载打断成几十次点击（oas1 当日 46 次点击仅 1 次成功，并触发
        TooManyClickError / Wait too long 重启）。这里同时接受「角色选择页」和
        「已进庭院」，任一出现立即返回；都不出现才等满 max_wait 让调用方重试。
        """
        timer = Timer(max_wait).start()
        while 1:
            self.screenshot()
            if self.appear(self.I_LOGIN_SPECIFIC_SERVE):
                return True
            if self.appear(self.I_BUFF_1) and \\
                    self.appear(self.I_MAIN_GOTO_SHIKIGAMI_RECORDS):
                return True
            if timer.reached():
                return False
            time.sleep(0.2)

'''

OLD_WAIT = '                self.wait_until_appear(self.I_LOGIN_SPECIFIC_SERVE, True, wait_time=5)\n'
NEW_WAIT = '                self._wait_login_loaded()\n'


def main() -> int:
    if not TARGET.exists():
        print(f'[FAIL] 找不到 {TARGET}')
        return 1
    text = TARGET.read_text(encoding='utf-8')
    if '_wait_login_loaded' in text:
        print('[SKIP] 已经打过补丁')
        return 0
    if OLD_WAIT not in text:
        print('[FAIL] 锚点未命中，login.py 结构已变化，请人工核对')
        return 1
    if ANCHOR_DEF not in text:
        print('[FAIL] 找不到 _app_handle_login 定义位置')
        return 1

    text = text.replace(ANCHOR_DEF, METHOD + ANCHOR_DEF, 1)
    text = text.replace(OLD_WAIT, NEW_WAIT, 1)
    TARGET.write_text(text, encoding='utf-8')

    import py_compile
    try:
        py_compile.compile(str(TARGET), doraise=True)
    except py_compile.PyCompileError as error:
        print(f'[FAIL] 编译失败，已回滚: {error}')
        return 1
    print('[OK] 已插入 _wait_login_loaded 并替换 5 秒空等')
    return 0


if __name__ == '__main__':
    sys.exit(main())
