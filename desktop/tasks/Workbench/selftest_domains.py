# This file uses the following encoding: utf-8
"""六个数据域读取回归（本机有平安志库时做真实校验，否则跳过）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tasks.Workbench.ingest import paz_extra as X

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def main() -> int:
    s = X.summary()
    db_ok = s["inventory"].get("ok") or s["commonness"].get("ok")
    if not db_ok:
        print("INFO  平安志库不可用，跳过真实校验：%s" % s["inventory"].get("reason"))
    else:
        inv = s["inventory"]
        check("domain.inventory", inv.get("ok") and inv.get("total", 0) > 0,
              str(inv)[:160])
        check("domain.inventory_by_slot", bool(inv.get("by_slot")), str(inv.get("by_slot")))

        com = s["commonness"]
        check("domain.commonness", com.get("ok") and com.get("defaults", 0) > 0,
              str(com)[:120])

        rel = s["relevance"]
        check("domain.relevance", rel.get("ok") and rel.get("count", 0) > 0,
              str(rel)[:120])
        check("domain.relevance_active", isinstance(rel.get("active"), dict), str(rel.get("active")))

        grp = s["yuhun_groups"]
        check("domain.groups", grp.get("ok") and grp.get("groups", 0) > 0,
              str(grp)[:120])

        todo = s["todos"]
        check("domain.todos", todo.get("ok") and todo.get("total", 0) > 0,
              str(todo)[:120])

    lin = s["lineup_catalog"]
    check("domain.lineup_catalog", "ok" in lin, str(lin)[:120])
    if lin.get("ok"):
        check("domain.lineup_samples", isinstance(lin.get("samples"), list), str(lin)[:120])

    print("\nWORKBENCH DOMAINS SELFTEST: %s   TOTAL %d PASS %d FAIL %d"
          % ("PASSED" if FAIL == 0 else "FAILED", PASS + FAIL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
