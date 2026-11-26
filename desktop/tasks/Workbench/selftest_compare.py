"""角色分析双版本对比自检。

跑法（仓库根目录，PYTHONPATH 必须含仓库根）：
    $env:PYTHONPATH='D:\3D\YYS2\OnmyojiAutoScript-easy-install-1'
    .\toolkit\python.exe tasks\Workbench\selftest_compare.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

from tasks.Workbench import compare as C

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


def _latest_snapshot() -> str:
    if not os.path.isfile(C.DEFAULT_DB):
        return ""
    con = sqlite3.connect(f"file:{C.DEFAULT_DB}?mode=ro", uri=True)
    row = con.execute("select id from snapshot order by captured_at desc limit 1").fetchone()
    return row[0] if row else ""


def main() -> int:
    print("[1] 参数与错误分支")
    check("空 id 拒绝", C.compare_snapshots("", "x").get("ok") is False)
    same = C.compare_snapshots("a", "a")
    check("两个相同 id 拒绝", same.get("ok") is False, str(same.get("error")))
    check("相同 id 报错文案含『不同』", "不同" in str(same.get("error")), str(same.get("error")))
    missing = C.compare_snapshots("不存在的快照", "也不存在")
    check("不存在的 id 拒绝", missing.get("ok") is False, str(missing.get("error")))

    print("[2] 真实快照：自身对比应无差异（用数据库副本造第二个 id 不可行，改为断言结构）")
    sid = _latest_snapshot()
    if not sid:
        print("  INFO  没有平安志数据库，跳过真实数据段")
    else:
        # 同一个快照复制两份（临时库）让两条 id 不同但内容一致，验证 diff 逻辑本身
        tmp = os.path.join(os.environ.get("TEMP", "."), "compare_probe.sqlite3")
        con = sqlite3.connect(C.DEFAULT_DB)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
            dst = sqlite3.connect(tmp)
            con.backup(dst)
            dst.close()
        finally:
            con.close()
        con = sqlite3.connect(tmp)
        # 整行复制（raw_sha256 / content_fingerprint 是 NOT NULL 且有唯一约束，加后缀区分）
        con.execute(
            "insert into snapshot (id, profile_id, raw_sha256, content_fingerprint, source_kind,"
            " completeness, scope_json, captured_at, game_version, adapter_version, parser_version,"
            " catalog_version, parent_snapshot_id, forced, created_at, scope_fingerprint)"
            " select ?, profile_id, raw_sha256 || '-copy', content_fingerprint || '-copy',"
            " source_kind, completeness, scope_json, captured_at, game_version, adapter_version,"
            " parser_version, catalog_version, parent_snapshot_id, forced, created_at,"
            " scope_fingerprint from snapshot where id = ?", (sid + "-copy", sid))
        con.execute("update snapshot_soul set snapshot_id = snapshot_id where 0")  # no-op keep schema warm
        # 复制御魂行（不是搬走）：让两条 id 内容一致，用来验证 diff 逻辑本身
        cols = [r[1] for r in con.execute("pragma table_info(snapshot_soul)")]
        sel = ", ".join("?" if c == "snapshot_id" else c for c in cols)
        con.execute(f"insert into snapshot_soul ({', '.join(cols)}) select {sel}"
                    " from snapshot_soul where snapshot_id = ?", (sid + "-copy", sid))
        try:
            icols = [r[1] for r in con.execute("pragma table_info(snapshot_inventory_item)")]
            isel = ", ".join("?" if c == "snapshot_id" else c for c in icols)
            con.execute(f"insert into snapshot_inventory_item ({', '.join(icols)}) select {isel}"
                        " from snapshot_inventory_item where snapshot_id = ?", (sid + "-copy", sid))
        except sqlite3.OperationalError:
            pass
        con.commit()
        con.close()

        res = C.compare_snapshots(sid, sid + "-copy", db=tmp)
        check("内容相同的两版本 ok", res.get("ok") is True, str(res.get("error")))
        check("added=0", (res.get("souls") or {}).get("totals", {}).get("added") == 0,
              str((res.get("souls") or {}).get("totals")))
        check("removed=0", (res.get("souls") or {}).get("totals", {}).get("removed") == 0)
        check("changed=0", (res.get("souls") or {}).get("totals", {}).get("changed") == 0)
        check("souls.count 两边相等",
              (res.get("souls") or {}).get("count", {}).get("older")
              == (res.get("souls") or {}).get("count", {}).get("newer"),
              str((res.get("souls") or {}).get("count")))
        try:
            os.remove(tmp)
        except OSError:
            pass

    print()
    print(f"WORKBENCH COMPARE SELFTEST: {'PASSED' if FAIL == 0 else 'FAILED'}   "
          f"TOTAL {PASS + FAIL} PASS {PASS} FAIL {FAIL}")
    if FAILED:
        print("failed:", ", ".join(FAILED))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.path.insert(0, r"D:\3D\YYS2\OnmyojiAutoScript-easy-install-1")
    raise SystemExit(main())
