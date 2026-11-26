"""平安志功能面接入 OAS：直接读本机平安志留下的数据库与资料库（不依赖平安志程序）。

数据来源（只读，不写对方任何文件）：
  %APPDATA%\\com.yys.analysis\\current-analysis.sqlite3      —— 42 张表（快照/御魂/式神/规则/评分）
  %APPDATA%\\com.yys.analysis\\reference-data-v1\\stable\\current.json —— 12 个资料文档（传记/悬赏/分类/联动/契灵/时装/伤害模拟…）

对外统一返回 {ok: bool, ...}，失败时带 reason，方便前端降级显示。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

APPDATA = Path(os.environ.get("APPDATA", ""))
PAZ_ROOT = Path(os.environ.get("WORKBENCH_PAZ_ROOT", APPDATA / "com.yys.analysis"))
DB_PATH = PAZ_ROOT / "current-analysis.sqlite3"
REF_PATH = PAZ_ROOT / "reference-data-v1" / "stable" / "current.json"

_lock = threading.Lock()
_db_cache: Dict[str, Any] = {"key": None, "con": None}
_doc_cache: Dict[str, Any] = {"key": None, "data": None}


# ── 基础读取 ────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    """只读连接；按 (path, mtime) 复用，避免每请求重开。"""
    key = None
    if DB_PATH.exists():
        st = DB_PATH.stat()
        key = (str(DB_PATH), st.st_mtime_ns, st.st_size)
    if _db_cache["key"] != key or _db_cache["con"] is None:
        if not DB_PATH.exists():
            raise FileNotFoundError(f"未找到分析库：{DB_PATH}")
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5.0,
                              check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        _db_cache["con"] = con
        _db_cache["key"] = key
    return _db_cache["con"]


def _docs() -> Dict[str, Any]:
    key = None
    if REF_PATH.exists():
        st = REF_PATH.stat()
        key = (str(REF_PATH), st.st_mtime_ns, st.st_size)
    if _doc_cache["key"] != key or _doc_cache["data"] is None:
        if not REF_PATH.exists():
            return {}
        with open(REF_PATH, "r", encoding="utf-8") as f:
            _doc_cache["data"] = json.load(f)
        _doc_cache["key"] = key
    return _doc_cache["data"]


def _rows(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    with _lock:
        con = _db()
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def _count(sql: str, params: tuple = ()) -> int:
    with _lock:
        con = _db()
        row = con.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _doc(name: str) -> Any:
    return (_docs().get("documents") or {}).get(name)


# ── 概览 ────────────────────────────────────────────────────

def summary() -> Dict[str, Any]:
    """功能面总览：表/文档/关键数量。用于工作台首页与自检。"""
    try:
        tables = [r["name"] for r in _rows(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts: Dict[str, int] = {}
        for t in tables:
            try:
                counts[t] = _count(f"SELECT COUNT(*) FROM '{t}'")
            except sqlite3.Error:
                counts[t] = -1
        docs = (_docs().get("documents") or {})
        doc_stat: Dict[str, Any] = {}
        for k, v in docs.items():
            if isinstance(v, dict):
                doc_stat[k] = {ik: (len(iv) if isinstance(iv, (list, dict)) else iv)
                               for ik, iv in list(v.items())[:4]}
            elif isinstance(v, list):
                doc_stat[k] = {"count": len(v)}
        return {
            "ok": True,
            "source": "paz-local",
            "db": str(DB_PATH),
            "ref": str(REF_PATH),
            "tables": len(tables),
            "table_counts": counts,
            "documents": doc_stat,
        }
    except FileNotFoundError as e:
        return {"ok": False, "reason": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


# ── 式神 ────────────────────────────────────────────────────

def shikigami(query: str = "", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """式神名录 + 已拥有数量。"""
    try:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        where, params = "", ()
        if query:
            where = "WHERE s.name LIKE ?"
            params = (f"%{query}%",)
        total = _count(f"SELECT COUNT(*) FROM shikigami s {where}", params)
        rows = _rows(
            f"""SELECT s.shikigami_id, s.name, s.rarity, s.panel_level, s.awakened,
                       (SELECT COUNT(*) FROM owned_shikigami o
                        WHERE o.shikigami_id = s.shikigami_id) AS owned
                FROM shikigami s {where}
                ORDER BY s.sort_order, s.shikigami_id LIMIT ? OFFSET ?""",
            params + (limit, offset))
        return {"ok": True, "total": total, "returned": len(rows),
                "offset": offset, "limit": limit, "rows": rows}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


# ── 御魂分组/评分规则 ───────────────────────────────────────

def yuhun_groups() -> Dict[str, Any]:
    try:
        groups = _rows("SELECT * FROM yuhun_group ORDER BY id")
        members = _rows("SELECT * FROM yuhun_group_member ORDER BY group_id")
        by_group: Dict[int, List[Dict[str, Any]]] = {}
        for m in members:
            by_group.setdefault(m.get("group_id"), []).append(m)
        for g in groups:
            g["members"] = by_group.get(g.get("id"), [])
        return {"ok": True, "count": len(groups), "groups": groups}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


def rule_presets() -> Dict[str, Any]:
    try:
        presets = _rows("SELECT * FROM rule_preset ORDER BY id")
        versions = _rows("SELECT * FROM rule_version ORDER BY id")
        return {"ok": True, "presets": presets, "versions": versions}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


# ── 资料库文档 ─────────────────────────────────────────────

DOC_NAMES = (
    "biographies", "biography-rules", "bounty-seal", "classification",
    "collaborations", "contract-spirits", "cosmetics", "damage-simulation",
    "realm-cards", "shikigami", "skills", "yao-effects",
)


def doc(name: str, key: str = "", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """按名取资料文档；key 用于取子字典里的某个键；列表型分页返回。"""
    try:
        if name not in DOC_NAMES:
            return {"ok": False, "reason": f"未知文档：{name}",
                    "available": list(DOC_NAMES)}
        data = _doc(name)
        if data is None:
            return {"ok": False, "reason": f"资料库缺少 {name}"}
        if key:
            if isinstance(data, dict) and key in data:
                data = data[key]
            else:
                return {"ok": False, "reason": f"{name} 没有键 {key}",
                        "keys": list(data)[:20] if isinstance(data, dict) else []}
        if isinstance(data, list):
            limit = max(1, min(int(limit), 500))
            offset = max(0, int(offset))
            return {"ok": True, "total": len(data), "returned": len(data[offset:offset + limit]),
                    "offset": offset, "limit": limit, "rows": data[offset:offset + limit]}
        if isinstance(data, dict):
            return {"ok": True, "keys": list(data.keys()), "data": data}
        return {"ok": True, "data": data}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


def bounty_seal() -> Dict[str, Any]:
    """悬赏封印：线索 + 关卡。"""
    d = _doc("bounty-seal") or {}
    clues = d.get("clues") or {}
    stages = d.get("stages") or []
    targets = d.get("targetNames") or []
    sample_clues = list(clues.items())[:5] if isinstance(clues, dict) else clues[:5]
    return {"ok": bool(d), "clues": len(clues), "stages": len(stages),
            "targets": len(targets), "updated_at": d.get("updatedAt"),
            "sample_clues": sample_clues, "sample_stages": stages[:3]}


def cosmetics_page(query: str = "", limit: int = 60, offset: int = 0) -> Dict[str, Any]:
    """时装/外观目录（2367 条）。"""
    d = _doc("cosmetics") or {}
    entries = d.get("entries") or []
    if query:
        q = query.lower()
        entries = [e for e in entries
                   if q in json.dumps(e, ensure_ascii=False).lower()]
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    return {"ok": True, "total": len(entries), "returned": len(entries[offset:offset + limit]),
            "offset": offset, "limit": limit, "rows": entries[offset:offset + limit]}


def damage_presets() -> Dict[str, Any]:
    d = _doc("damage-simulation") or {}
    presets = d.get("presets") or []
    onmyoji = d.get("onmyoji") or []
    return {"ok": bool(d), "presets": presets, "onmyoji": onmyoji}


def realm_cards_catalog() -> Dict[str, Any]:
    cards = _doc("realm-cards") or []
    return {"ok": bool(cards), "count": len(cards), "rows": cards}


def collect() -> Dict[str, Any]:
    """刷新动作：扫描本机快照文件，返回最新时点；可选带上 MuMu 快照目录里的最新文件。

    不启动任何外部程序（平安志已不在本机），只做“重新读取 + 报告新鲜度”。
    """
    try:
        files = [f for f in PAZ_ROOT.glob("current-data-*.json")
                 if "-snapshot-" not in f.name and "projection" not in f.name]
        if not files:
            return {"ok": False, "reason": "没有快照文件（需要先采一次）"}
        newest = max(files, key=lambda p: p.stat().st_mtime)
        with open(newest, "r", encoding="utf-8") as f:
            data = json.load(f)
        payload = data.get("sourcePayload") or {}
        captured = (payload.get("taskRecords") or {}).get("capturedAt") \
            or data.get("capturedAt")
        mm = (os.environ.get("WORKBENCH_MUMU_SNAPSHOT_DIR") or "").strip()
        mumu_latest = None
        if mm and Path(mm).exists():
            cand = sorted(Path(mm).glob("mumu-snapshot-*.json"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if cand:
                mumu_latest = {"file": str(cand[0]),
                               "mtime": cand[0].stat().st_mtime}
        return {
            "ok": True,
            "file": newest.name,
            "file_mtime": newest.stat().st_mtime,
            "captured_at": captured,
            "snapshot_count": len(files),
            "mumu_latest": mumu_latest,
            "note": "已重新读取本机快照；要更新到当前游戏状态仍需新的采集",
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


# ── 快照任务状态（任务库）──────────────────────────────────

def task_status(limit: int = 200) -> Dict[str, Any]:
    """从快照 JSON 的任务记录里抽「任务库」用的条目。"""
    try:
        snap_env = (os.environ.get("WORKBENCH_PAZ_SNAPSHOT") or "").strip()
        snap = Path(snap_env).expanduser() if snap_env else None
        if snap is None or not snap.exists():
            files = sorted(PAZ_ROOT.glob("current-data-*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            files = [f for f in files if "-snapshot-" not in f.name and "projection" not in f.name]
            if not files:
                return {"ok": False, "reason": "没有快照文件"}
            snap = files[0]
        with open(snap, "r", encoding="utf-8") as f:
            data = json.load(f)
        payload = data.get("sourcePayload") or {}
        records = (payload.get("taskRecords")
                   or data.get("taskRecords") or {})
        captured = records.get("capturedAt") or data.get("capturedAt")
        daily = records.get("daily") or {}
        weekly = records.get("weekly") or {}
        items: List[Dict[str, Any]] = []
        for scope, block in (("daily", daily), ("weekly", weekly)):
            for name, val in block.items():
                if not isinstance(val, dict):
                    continue
                items.append({
                    "scope": scope,
                    "task": name,
                    "available": val.get("available"),
                    "completed": val.get("completed", val.get("challengeCompleted")),
                    "current": val.get("current"),
                    "limit": val.get("limit"),
                    "remaining": val.get("remaining"),
                    "source": val.get("source"),
                })
        items.sort(key=lambda x: (x["scope"], str(x["task"])))
        return {"ok": True, "captured_at": captured,
                "count": len(items), "rows": items[:max(1, min(int(limit), 500))]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
