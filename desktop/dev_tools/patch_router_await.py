# -*- coding: utf-8 -*-
"""修复 WebUI 启停脚本接口漏 await 的问题。

现象：GET /{script_name}/start、/stop 返回 null，但脚本进程既不启动也不停止。
根因：script_router.py 里调用的是 async 的 ScriptProcess.start()/stop()，却没有 await，
协程对象被直接丢弃，服务端只留下 RuntimeWarning: coroutine was never awaited。

用法：python dev_tools/patch_router_await.py
生效：需要重启 OAS WebUI 服务进程（server.py）。
"""
import pathlib
import py_compile
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TARGET = REPO / "module" / "server" / "script_router.py"

PAIRS = [
    (
        "    mm.script_process[script_name].start()\n",
        "    await mm.script_process[script_name].start()\n",
    ),
    (
        "    mm.script_process[script_name].stop()\n",
        "    await mm.script_process[script_name].stop()\n",
    ),
]


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    changed = False
    for old, new in PAIRS:
        if old in text:
            text = text.replace(old, new, 1)
            changed = True
        elif new in text:
            continue
        else:
            print("anchor not found:", old.strip())
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
