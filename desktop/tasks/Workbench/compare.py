"""角色分析：两个历史快照的差异对比（复刻平安志 get_character_archive_comparison 的行为）。

数据来源：平安志的 `current-analysis.sqlite3`（只读），表 `snapshot` / `snapshot_soul` /
`snapshot_inventory_item`。平安志那条命令要求「两个不同的历史版本」，本模块同样如此：
id 相同、id 不存在、快照无御魂行时都返回明确错误而不是空结果。

返回结构（与仓库其它 features 返回体风格一致）：
    {"ok": True, "older": {...}, "newer": {...}, "souls": {...}, "items": {...}, "summary": {...}}
其中 souls/items 各有 added(新增)/removed(消失)/changed(同键但字段变了) 与样例列表。
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_DB = os.path.join(
    os.environ.get("APPDATA", ""), "com.yys.analysis", "current-analysis.sqlite3"
)

# 参与「变更」判定的字段（同键不同值即算 changed）
_SOUL_FIELDS = ("set_id", "slot", "quality", "level", "main_attr_type", "initial_substat_count")
_SAMPLE_LIMIT = 20


def _ro(db: Optional[str]) -> sqlite3.Connection:
    path = db or DEFAULT_DB
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _snapshot_meta(con: sqlite3.Connection, snapshot_id: str) -> Optional[Dict[str, Any]]:
    row = con.execute(
        "select id, captured_at, source_kind, completeness from snapshot where id = ?",
        (snapshot_id,),
    ).fetchone()
    return dict(row) if row else None


def _souls(con: sqlite3.Connection, snapshot_id: str) -> Dict[str, Dict[str, Any]]:
    """把快照的御魂读成 {key: fields}；key 用 internal_id（平安志的面板/评分也用它）。"""
    out: Dict[str, Dict[str, Any]] = {}
    cur = con.execute(
        "select internal_id, set_id, slot, quality, level, main_attr_type, initial_substat_count"
        " from snapshot_soul where snapshot_id = ?", (snapshot_id,))
    for row in cur:
        key = str(row["internal_id"])
        out[key] = {f: row[f] for f in _SOUL_FIELDS}
    return out


def _items(con: sqlite3.Connection, snapshot_id: str) -> Dict[str, str]:
    """库存道具：key=soul_key，值=presence_state（在/不在）。"""
    out: Dict[str, str] = {}
    try:
        cur = con.execute(
            "select soul_key, presence_state from snapshot_inventory_item where snapshot_id = ?",
            (snapshot_id,))
    except sqlite3.OperationalError:
        return out
    for row in cur:
        out[str(row["soul_key"])] = str(row["presence_state"] or "")
    return out


def _diff(older: Dict[str, Any], newer: Dict[str, Any]) -> Dict[str, Any]:
    okeys, nkeys = set(older), set(newer)
    added = sorted(nkeys - okeys)
    removed = sorted(okeys - nkeys)
    changed: List[Tuple[str, Dict[str, Any]]] = []
    for key in sorted(okeys & nkeys):
        before, after = older[key], newer[key]
        delta = {f: (before.get(f), after.get(f)) for f in before
                 if str(before.get(f)) != str(after.get(f))}
        if delta:
            changed.append((key, delta))
    return {
        "count": {"older": len(older), "newer": len(newer)},
        "added": added[:_SAMPLE_LIMIT],
        "removed": removed[:_SAMPLE_LIMIT],
        "changed": [{"key": k, "delta": d} for k, d in changed[:_SAMPLE_LIMIT]],
        "totals": {"added": len(added), "removed": len(removed), "changed": len(changed)},
    }


def compare_snapshots(older_snapshot_id: str, newer_snapshot_id: str,
                      db: Optional[str] = None) -> Dict[str, Any]:
    """对比两个历史快照。id 相同/缺失/无数据都返回明确错误。"""
    older_id = str(older_snapshot_id or "").strip()
    newer_id = str(newer_snapshot_id or "").strip()
    if not older_id or not newer_id:
        return {"ok": False, "error": "olderSnapshotId / newerSnapshotId 不能为空"}
    if older_id == newer_id:
        return {"ok": False, "error": "对比必须选择两条不同的历史版本"}

    path = db or DEFAULT_DB
    if not os.path.isfile(path):
        return {"ok": False, "error": f"找不到平安志数据库: {path}"}

    with _ro(path) as con:
        metas = {}
        for label, sid in (("older", older_id), ("newer", newer_id)):
            meta = _snapshot_meta(con, sid)
            if meta is None:
                return {"ok": False, "error": f"snapshotId 不存在：{sid}"}
            metas[label] = meta
        souls_old, souls_new = _souls(con, older_id), _souls(con, newer_id)
        items_old, items_new = _items(con, older_id), _items(con, newer_id)

    if not souls_old and not souls_new:
        return {"ok": False, "error": "两个快照都没有御魂行，无法对比"}

    soul_diff = _diff(souls_old, souls_new)
    item_diff = _diff({k: {"presence": v} for k, v in items_old.items()},
                      {k: {"presence": v} for k, v in items_new.items()})
    return {
        "ok": True,
        "older": metas["older"],
        "newer": metas["newer"],
        "souls": soul_diff,
        "items": item_diff,
        "summary": {
            "captured_delta_seconds": None,
            "souls": soul_diff["totals"],
            "items": item_diff["totals"],
        },
    }
