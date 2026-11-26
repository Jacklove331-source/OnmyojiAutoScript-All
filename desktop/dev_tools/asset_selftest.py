"""资产链路回归自测（不碰设备，纯函数级）：把 2026-11-21 踩到的坑钉死。

覆盖：
  1. 商店顶栏映射：多读一个前缀数字（实测 '49'）时仍要映射正确；
     勾玉列与庭院不一致时**必须拒绝写值**（不能把 49 当皮肤券）。
  2. 庭院顶栏解析：金币/勾玉/体力按 x 排序；勾玉上万只记 approx；非庭院帧不写值。
  3. 快照合并语义：失败只写 last_attempt_at/error 且保留旧值；complete 只认本轮五项；
     precision=exact 时实时帧简写不覆盖（touch）。
  4. 配置隔离：oas1 / oas3 各写各的快照文件，不互相兜底。

用法：
  toolkit\\python.exe dev_tools\\asset_selftest.py
退出码 0 = 全绿。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tasks" / "Workbench"))

import asset_store as S  # noqa: E402
import asset_ui_reader as A  # noqa: E402
import asset_live_watch as L  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def row(text: str, x: int, y: int = 20, w: int = 47, h: int = 24) -> dict:
    return {"text": text, "box": [x, y, w, h], "score": 1.0}


# ---------------------------------------------------------------- 1. 商店顶栏
def test_shop_bar() -> None:
    print("[1] 商店顶栏映射（x 顺序 674/863/1037/1180，勾玉在最右）")
    nums = A.ordered_bar_numbers(
        [row("49", 470), row("820", 674), row("6", 863), row("0", 1037), row("132", 1179)],
        A.SHOP_BAR_BOX)
    check("候选按 x 排序且都保留", [n["num"] for n in nums] == [49, 820, 6, 0, 132],
          str([n["num"] for n in nums]))
    mapped, why = A.map_shop_bar(nums, court_jade=132)
    check("多读前缀数字仍映射正确",
          mapped == {"skin_token": 820, "ofuda": 6, "s_jade": 0, "jade": 132}, f"{mapped} {why}")

    nums2 = A.ordered_bar_numbers(
        [row("49", 470), row("820", 674), row("6", 863), row("0", 1037), row("999", 1179)],
        A.SHOP_BAR_BOX)
    mapped2, why2 = A.map_shop_bar(nums2, court_jade=132)
    check("勾玉列与庭院不一致 → 拒绝写值", mapped2 is None and why2 is not None, str(why2))

    check("列数不足 → 拒绝", A.map_shop_bar([row("820", 674), row("132", 1179)], 132)[0] is None)


# ---------------------------------------------------------------- 2. 庭院顶栏
def test_courtyard() -> None:
    print("[2] 庭院顶栏解析")
    court = [row("2667万", 534, 22), row("132+", 749, 20), row("2.0万", 906, 20),
             row("60", 10, 22), row("22:53", 136, 8), row("探索", 475, 101)]
    info = L.parse_top(court)
    check("庭院帧识别", info["in_court"] is True)
    check("金币=简写 2667万", info["coin"] == 26670000 and info["coin_text"] == "2667万", str(info))
    check("勾玉=精确 132", info["jade"] == 132 and info["jade_exact"] is True, str(info))
    check("体力=简写 2.0万", info["ap"] == 20000 and info["ap_text"] == "2.0万", str(info))

    oas3 = [row("3828万", 533, 20), row("11万", 736, 22), row("3.5万", 908, 22)]
    i3 = L.parse_top(oas3)
    check("上万勾玉只记 approx", i3["in_court"] and i3["jade"] == 110000 and i3["jade_exact"] is False,
          str(i3))
    battle = [row("5/30+", 879, 29), row("3.5万", 1083, 30)]
    ib = L.parse_top(battle)
    check("非庭院帧不写值", ib["in_court"] is False and ib["jade"] is None, str(ib))


# ---------------------------------------------------------------- 3. 合并语义
def test_merge() -> None:
    print("[3] 快照合并语义（失败保留旧值 / complete 只认本轮 / 不降级）")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        ok = S.merge(base, "oasX", serial="S",
                     readings={k: {"value": v, "precision": "exact"}
                               for k, v in (("jade", 132), ("ap", 20475), ("skin_token", 820),
                                            ("mystery_amulet", 6), ("s_jade", 0))},
                     source="page-collect")
        check("五项齐 → complete=True", ok["complete"] is True and ok["status"] == "ok", ok["status"])

        bad = S.merge(base, "oasX", serial="S",
                      readings={"jade": {"value": None, "error": "不在庭院"},
                                "ap": {"value": None, "error": "不在庭院"}},
                      error="不在庭院", source="page-collect")
        items = bad["items"]
        check("失败保留旧值", items["jade"]["value"] == 132 and items["ap"]["value"] == 20475)
        check("失败不改 captured_at", bad["captured_at"] == ok["captured_at"])
        check("失败写 error/window", items["jade"]["error"] == "不在庭院")
        check("刚成功过不算过期", items["jade"]["stale"] is False)

        # 把 last_ok_at 改成 2 小时前，再过一轮失败 → 必须标过期但仍然保留旧值
        path = S.path_for(base, "oasX")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["items"]["jade"]["last_ok_at"] = "2026-01-01T00:00:00"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        old = S.merge(base, "oasX", serial="S",
                      readings={"jade": {"value": None, "error": "不在庭院"}}, source="page-collect")
        check("超 30 分钟的旧值标过期且保留",
              old["items"]["jade"]["stale"] is True and old["items"]["jade"]["value"] == 132,
              str(old["items"]["jade"]))

        # 已有精确 ap 时，实时帧的简写不能覆盖
        touched = S.merge(base, "oasX", serial="S",
                          readings={"ap": {"value": None, "touch": True},
                                    "jade": {"value": 999, "precision": "exact"}},
                          source="live-frame")
        check("touch 不降级 ap", touched["items"]["ap"]["value"] == 20475
              and touched["items"]["ap"]["precision"] == "exact", str(touched["items"]["ap"]))
        check("实时帧精确勾玉可写", touched["items"]["jade"]["value"] == 999)

        # 失败项不带 precision：approx 值不会被升格
        approx = S.merge(base, "oasY", serial="S",
                         readings={"jade": {"value": 110000, "precision": "approx",
                                            "text": "11万"}}, source="live-frame")
        after = S.merge(base, "oasY", serial="S",
                        readings={"jade": {"value": None, "error": "实时帧非庭院"}},
                        source="live-frame")
        check("approx 不因失败被升格成 exact",
              approx["items"]["jade"]["precision"] == "approx"
              and after["items"]["jade"]["precision"] == "approx", str(after["items"]["jade"]))

        # 配置隔离
        S.merge(base, "oasA", serial="A", readings={"jade": {"value": 1}}, source="t")
        S.merge(base, "oasB", serial="B", readings={"jade": {"value": 2}}, source="t")
        a = json.loads(S.path_for(base, "oasA").read_text(encoding="utf-8"))
        b = json.loads(S.path_for(base, "oasB").read_text(encoding="utf-8"))
        check("配置隔离：各写各的文件", a["items"]["jade"]["value"] == 1
              and b["items"]["jade"]["value"] == 2)


def main() -> int:
    test_shop_bar()
    test_courtyard()
    test_merge()
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
