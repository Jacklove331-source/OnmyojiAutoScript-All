"""OAS 核心导入自检：在 APK 里逐个 import 关键模块，报告到底缺什么。"""

from __future__ import annotations

import json
import traceback

TARGETS = [
    "module.logger",
    "module.exception",
    "module.base.decorator",
    "module.base.timer",
    "module.base.utils",
    "module.atom.image",
    "module.atom.click",
    "module.config.utils",
    "module.config.config",
    "tasks.base_task",
    "tasks.Restart.script_task",
    "tasks.WantedQuests.script_task",
]

def run() -> str:
    report = {"ok": True, "python": None, "results": []}
    try:
        import sys
        report["python"] = sys.version.split()[0]
    except Exception:  # noqa: BLE001
        pass
    failed = 0
    for name in TARGETS:
        try:
            __import__(name)
            report["results"].append({"module": name, "ok": True})
        except Exception as error:  # noqa: BLE001
            failed += 1
            tb = traceback.format_exc().strip().splitlines()
            report["results"].append({
                "module": name,
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
                "where": tb[-4:],
            })
    report["ok"] = failed == 0
    report["failed"] = failed
    report["total"] = len(TARGETS)
    return json.dumps(report, ensure_ascii=False, indent=1)
