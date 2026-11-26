# This file uses the following encoding: utf-8
"""一键从平安志 sqlite 生成 config/roster.json（式神识别 · 数据读取通道）。

用法（仓库根目录）：
    .\\toolkit\\python.exe dev_tools\\roster_paz.py                 # 默认 SP,SSR,SR → config/roster.json
    .\\toolkit\\python.exe dev_tools\\roster_paz.py --dry-run       # 只看统计，不写文件
    .\\toolkit\\python.exe dev_tools\\roster_paz.py --rarities ""   # 不按稀有度过滤（会含素材）
    .\\toolkit\\python.exe dev_tools\\roster_paz.py --out log/roster_dry.json
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from module.lineup import roster_paz


def main() -> int:
    ap = argparse.ArgumentParser(description="从平安志 sqlite 生成式神池 roster")
    ap.add_argument("--db", default=None, help="平安志 sqlite 路径（默认 %%APPDATA%%）")
    ap.add_argument("--rarities", default="SP,SSR,SR",
                    help='稀有度过滤，逗号分隔；传空字符串=不过滤（会含素材）')
    ap.add_argument("--out", default="config/roster.json", help="输出文件")
    ap.add_argument("--no-skills", action="store_true", help="不写技能等级")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = ap.parse_args()

    data = roster_paz.load_owned(db=args.db, rarities=args.rarities,
                                 include_skills=not args.no_skills)
    if not data.get("ok"):
        print("FAIL:", data.get("reason"))
        return 1
    items = data["shikigami"]
    by_rarity = {}
    for item in items:
        by_rarity[item["rarity"]] = by_rarity.get(item["rarity"], 0) + 1
    print("source=%s db=%s" % (data["source"], data["db"]))
    print("rarities=%s -> %d 个式神，目录缺失 %d 个实例，按稀有度跳过 %d 个实例" % (
        data["rarities"] or "(不过滤)", data["count"],
        data["unknown_catalog"], data["skipped_by_rarity"]))
    print("by_rarity=%s" % by_rarity)
    for item in items[:5]:
        print("  %s %s Lv%s %s 技能%s" % (item["name"], item["rarity"], item["level"],
                                         "觉醒" if item["awakened"] else "未觉醒",
                                         item["skills"]))
    if args.dry_run:
        print("dry-run：未写文件")
        return 0
    result = roster_paz.save(data, args.out)
    print("saved %d -> %s（保留手填 %d 条）" % (
        result["count"], result["path"], result["manual_kept"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
