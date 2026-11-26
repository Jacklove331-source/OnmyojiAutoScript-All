"""平安志功能面接入自检：直接跑 ingest/paz_catalog，不依赖 HTTP 服务。

用法（仓库根目录）：.\toolkit\python.exe tasks\Workbench\selftest_paz_catalog.py
数据缺失（比如换了机器）时，相关用例会标记 SKIP 而不是 FAIL。
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tasks.Workbench.ingest import paz_catalog as PC  # noqa: E402

CASES = []
FAILED = 0
SKIPPED = 0


def check(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def expect(cond, msg=""):
    if not cond:
        raise AssertionError(msg or "期望不成立")


@check("数据源可用")
def _t_source():
    s = PC.summary()
    if not s.get("ok"):
        raise SkipTest(s.get("reason", "无数据"))
    expect(s["tables"] == 42, f"表数应为 42，实际 {s['tables']}")
    expect(len(s["documents"]) >= 12, f"文档数应 >=12，实际 {len(s['documents'])}")


@check("关键表有数据")
def _t_tables():
    s = PC.summary()
    if not s.get("ok"):
        raise SkipTest("无数据")
    c = s["table_counts"]
    expect(c.get("shikigami", 0) == 281, f"shikigami={c.get('shikigami')}")
    # 数据量与具体快照绑定，换一次读取就会变；断言用下界，避免把「换了快照」当成移植缺陷
    expect(c.get("owned_shikigami", 0) >= 1000, f"owned={c.get('owned_shikigami')}")
    expect(c.get("snapshot_soul", 0) >= 1000, f"soul={c.get('snapshot_soul')}")
    expect(c.get("soul_attribute", 0) >= c.get("snapshot_soul", 0), f"attr={c.get('soul_attribute')}")
    expect(c.get("soul_set", 0) == 78, f"set={c.get('soul_set')}")


@check("式神名录与拥有量")
def _t_shikigami():
    r = PC.shikigami("", limit=5)
    if not r.get("ok"):
        raise SkipTest(r.get("reason", ""))
    expect(r["total"] == 281, f"total={r['total']}")
    expect(r["returned"] == 5, f"returned={r['returned']}")
    expect(all("owned" in row for row in r["rows"]), "行里应含 owned")


@check("式神搜索")
def _t_search():
    r = PC.shikigami("神", limit=3)
    if not r.get("ok"):
        raise SkipTest(r.get("reason", ""))
    expect(r["total"] >= 1, "搜索“神”应至少 1 条")


@check("式神分页")
def _t_paging():
    page1 = PC.shikigami("", limit=24, offset=0)
    if not page1.get("ok"):
        raise SkipTest(page1.get("reason", ""))
    page2 = PC.shikigami("", limit=24, offset=12)
    expect(page2["offset"] == 12, f"offset={page2['offset']}")
    expect(len(page2["rows"]) == 24, f"returned={len(page2['rows'])}")
    expect(page1["rows"][0] != page2["rows"][0], "第二页应与第一页错开")
    expect(page2["total"] == page1["total"], "总数应一致")


@check("悬赏封印")
def _t_bounty():
    r = PC.bounty_seal()
    if not r.get("ok"):
        raise SkipTest("无悬赏数据")
    expect(r["clues"] == 36, f"clues={r['clues']}")
    expect(r["stages"] == 387, f"stages={r['stages']}")
    expect(r["targets"] == 152, f"targets={r['targets']}")


@check("御魂分组")
def _t_groups():
    r = PC.yuhun_groups()
    if not r.get("ok"):
        raise SkipTest(r.get("reason", ""))
    expect(r["count"] == 8, f"groups={r['count']}")


@check("伤害模拟预设")
def _t_damage():
    r = PC.damage_presets()
    if not r.get("ok"):
        raise SkipTest("无伤害模拟数据")
    expect(len(r["presets"]) == 71, f"presets={len(r['presets'])}")


@check("资料库文档读取")
def _t_docs():
    r = PC.doc("realm-cards", limit=5)
    if not r.get("ok"):
        raise SkipTest(r.get("reason", ""))
    expect(r["total"] == 119, f"realm-cards={r['total']}")
    c = PC.cosmetics_page("", limit=5)
    expect(c["total"] == 2367, f"cosmetics={c['total']}")
    bad = PC.doc("not-a-doc")
    expect(bad["ok"] is False and "available" in bad, "未知文档应返回可选列表")


@check("文档分页")
def _t_doc_paging():
    s = PC.doc("bounty-seal", key="stages", limit=5, offset=10)
    if not s.get("ok"):
        raise SkipTest(s.get("reason", ""))
    expect(s["total"] == 387, f"total={s['total']}")
    expect(s["returned"] == 5 and s["offset"] == 10, "分页参数应生效")
    d = PC.doc("damage-simulation", key="presets", limit=3)
    expect(len(d.get("rows") or []) == 3, "伤害预设应可取 3 条")


@check("图标路由")
def _t_icon_route():
    base = "http://127.0.0.1:22267/workbench/paz/icon/"
    oknames = ["jade", "coin", "action_point", "mystery_amulet", "skin_token"]
    for n in oknames:
        with urllib.request.urlopen(base + n, timeout=20) as r:
            expect(r.status == 200, f"{n} 应 200")
            expect((r.headers.get("content-type") or "").startswith("image/png"),
                   f"{n} content-type 应为 image/png")
    try:
        urllib.request.urlopen(base + "not_a_currency", timeout=20)
        raise AssertionError("未知图标应 404")
    except urllib.error.HTTPError as e:
        expect(e.code == 404, f"未知图标应 404，实际 {e.code}")


@check("刷新动作（collect）")
def _t_collect():
    r = PC.collect()
    if not r.get("ok"):
        raise SkipTest(r.get("reason", ""))
    expect(r["captured_at"], "应带快照时点")
    expect(r["snapshot_count"] >= 1, "至少 1 份快照")
    expect(r["file"].endswith(".json"), f"file={r['file']}")


@check("任务库")
def _t_tasks():
    r = PC.task_status(limit=10)
    if not r.get("ok"):
        raise SkipTest(r.get("reason", ""))
    expect(r["captured_at"], "应带快照时点")
    expect(r["rows"], "任务行不应为空")


class SkipTest(Exception):
    pass


def main() -> int:
    global FAILED, SKIPPED
    print(f"paz_catalog 自检 · db={PC.DB_PATH}")
    for name, fn in CASES:
        try:
            fn()
            print(f"  PASS  {name}")
        except SkipTest as e:
            SKIPPED += 1
            print(f"  SKIP  {name} ({e})")
        except Exception as e:  # noqa: BLE001
            FAILED += 1
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc(limit=2)
    total = len(CASES)
    print(f"\n{total - FAILED - SKIPPED}/{total} PASS, {SKIPPED} SKIP, {FAILED} FAIL")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
