# This file uses the following encoding: utf-8
"""roster_paz 自检：从平安志 sqlite 生成式神池（缺库时 SKIP，不算失败）。

用法（仓库根目录）：
    .\\toolkit\\python.exe module\\lineup\\selftest_roster_paz.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from module.lineup import roster_paz

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def main() -> int:
    db = roster_paz.DEFAULT_DB
    if not os.path.isfile(db):
        print("SKIP: 找不到平安志数据库 %s" % db)
        return 0

    data = roster_paz.load_owned()
    check("paz.ok", data.get("ok") is True, str(data.get("reason")))
    items = data.get("shikigami") or []
    check("paz.count>100", len(items) > 100, str(len(items)))
    check("paz.no_material", all(x["rarity"] != roster_paz.MATERIAL for x in items),
          str(sorted({x["rarity"] for x in items})))
    check("paz.rarities_filtered", set(x["rarity"] for x in items) <= {"SP", "SSR", "SR"},
          str(sorted({x["rarity"] for x in items})))
    check("paz.unique_names", len({x["name"] for x in items}) == len(items))
    check("paz.fields", all(
        {"name", "rarity", "level", "star", "awakened", "skills", "speed", "souls", "source"} <= set(x)
        for x in items))
    check("paz.skills_shape", all(
        len(x["skills"]) <= 3 and all(1 <= lv <= 5 for lv in x["skills"]) for x in items))
    check("paz.source", all(x["source"] == "paz-db" for x in items))

    all_data = roster_paz.load_owned(rarities="")
    check("paz.all_count>=paz.filtered", all_data["count"] >= data["count"],
          "%s vs %s" % (all_data["count"], data["count"]))
    check("paz.unknown_reported", isinstance(all_data["unknown_catalog"], int),
          str(all_data["unknown_catalog"]))

    tmp = tempfile.mkdtemp(prefix="roster_paz_")
    try:
        out = os.path.join(tmp, "roster.json")
        paz_item = dict(items[0])
        paz_item["speed"] = None
        old_name = paz_item["name"]
        io.open(out, "w", encoding="utf-8").write(json.dumps({
            "shikigami": [
                {"name": "测试手填条目", "level": 40, "skills": [5, 5, 5],
                 "speed": 200, "souls": [], "source": "manual"},
                {"name": old_name, "level": 1, "skills": [], "speed": 188,
                 "souls": [], "source": "scan"},
            ]
        }, ensure_ascii=False))

        # 手填条目保留；同名旧自动条目的 speed/souls 被新条目继承
        res = roster_paz.save({"source": "paz-db", "shikigami": [paz_item]}, out)
        check("save.count", res["count"] == 2, str(res))
        check("save.manual_kept", res["manual_kept"] == 1, str(res))
        saved = json.load(io.open(out, encoding="utf-8"))
        names = {x["name"] for x in saved["shikigami"]}
        check("save.manual_name", "测试手填条目" in names, str(names))
        check("save.updated_at", bool(saved.get("updated_at")), str(saved.get("updated_at")))
        kept = next(x for x in saved["shikigami"] if x["name"] == old_name)
        check("save.inherit_speed", kept.get("speed") == 188, str(kept.get("speed")))
        check("save.paz_source", kept.get("source") == "paz-db", str(kept.get("source")))

        # build_and_save：配置对象驱动
        out2 = os.path.join(tmp, "roster2.json")
        cfg = SimpleNamespace(rarities="SP", include_skills=False, output_file=out2)
        res2 = roster_paz.build_and_save(cfg)
        check("cfg.ok", res2.get("ok") is True and res2["count"] > 0, str(res2))
        saved2 = json.load(io.open(out2, encoding="utf-8"))
        check("cfg.rarity", all(x["rarity"] == "SP" for x in saved2["shikigami"]))
        check("cfg.no_skills", all(x["skills"] == [] for x in saved2["shikigami"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nROSTER PAZ SELFTEST: %s   TOTAL %d PASS %d FAIL %d" % (
        "PASSED" if FAIL == 0 else "FAILED", PASS + FAIL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
