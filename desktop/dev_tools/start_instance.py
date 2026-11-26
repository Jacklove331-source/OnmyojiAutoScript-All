# -*- coding: utf-8 -*-
"""直接启动某个 OAS 实例的脚本主循环（脱离 WebUI 使用）。

背景：WebUI 的 /{script_name}/start 在旧版代码里漏了 await，进程退出后无法从接口重新拉起；
在服务端重启生效前，用本脚本手动拉起实例（日志仍写 log/<date>_<config>.txt）。

注意：用 `cmd /c start /b` 拉起时进程没有有效控制台，colorama 写 stdout 会抛
OSError [Errno 22]，所以这里先把 stdout/stderr 重定向到 log/<config>_console.log。

用法：python dev_tools/start_instance.py oas3
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python dev_tools/start_instance.py <config_name>")
        return 2
    config = sys.argv[1]

    log_dir = REPO / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    console = open(log_dir / f"{config}_console.log", "a", encoding="utf-8", errors="replace")
    sys.stdout = console
    sys.stderr = console

    sys.path.insert(0, str(REPO))
    import os

    os.chdir(REPO)

    from module.logger import set_file_logger

    set_file_logger(name=config)

    from script import Script

    script = Script(config_name=config)
    script.loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
