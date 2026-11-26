# This file uses the following encoding: utf-8
"""Workbench 数据存储：纯 JSON，落在 config/workbench/ 下。

目录结构（全部可手工编辑、可用 git 管）：
  profile.json          {"current": "<id>", "profiles": [{id,name,server,created,note}]}
  souls.json            [{set,slot,main,substats,level,quality,initial_substat_count,note}]
  owned.json            [{id,star,level,awakened,skin,note}]         # id = 式神 id 或名字
  decisions.json        {"<soul_key>": "keep|observe|plan_strengthen|plan_recycle|ignore"}
  tasklog.jsonl         每行 {ts, kind:"strengthen|cleanup", items:[...], status}
  monitor.jsonl         每行 {ts, kind, value, note}
  guild.json            {"name": "", "members": [{name, role, note}]}
  todo_decisions.json   {"<todo_id>": {todo_id,action,status,note,at}}   # 待办处置（OAS 自有）
  todo_decisions.log    每行一次处置动作（历史）
  standards.json        {"active": "<版本 id>", "updated": "..."}       # 只改 OAS 偏好
  log/workbench/backups/  备份产物 {index.json, wb-<时间>.json}（不复制平安志库）

所有写入都走原子替换（先写 .tmp 再 os.replace），避免半截文件。
任何写入都只落在 OAS 自己的目录，绝不回写平安志（%APPDATA%\com.yys.analysis）下的任何文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from filelock import FileLock

DEFAULT_DIR = os.path.join("config", "workbench")
# 备份默认落仓库的 log/workbench/backups/；路由层会传绝对路径（REPO_ROOT 下）
BACKUP_DIR = os.path.join("log", "workbench", "backups")


class Store:
    def __init__(self, base_dir: Optional[str] = None, *,
                 data_dir: Optional[str] = None, profile_id: Optional[str] = None) -> None:
        self.base = base_dir or DEFAULT_DIR
        self.data_dir = data_dir or self.base
        self.profile_id = profile_id
        os.makedirs(self.data_dir, exist_ok=True)

    # ---------- 基础 IO ----------
    def _path(self, name: str) -> str:
        return os.path.join(self.base if name == "profile.json" else self.data_dir, name)

    def lock(self, name: str) -> FileLock:
        return FileLock(self._path(name) + ".lock", timeout=10)

    def read_json(self, name: str, default: Any) -> Any:
        p = self._path(name)
        if not os.path.exists(p):
            return default
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (ValueError, OSError):
            return default

    def write_json(self, name: str, data: Any) -> str:
        p = self._path(name)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1, allow_nan=False)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return p

    def append_jsonl(self, name: str, row: Dict[str, Any]) -> str:
        p = self._path(name)
        row = dict(row)
        row.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        with self.lock(name), open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return p

    def read_jsonl(self, name: str, limit: int = 200) -> List[Dict[str, Any]]:
        p = self._path(name)
        if not os.path.exists(p):
            return []
        rows = []
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows[-limit:]

    # ---------- 档案 ----------
    def profiles(self) -> Dict[str, Any]:
        return self.read_json("profile.json", {"current": "", "profiles": []})

    def add_profile(self, pid: str, name: str, server: str = "", note: str = "") -> Dict[str, Any]:
        with self.lock("profile.json"):
            data = self.profiles()
            data.setdefault("legacy_profile", data.get("current") or pid)
            if not any(p["id"] == pid for p in data["profiles"]):
                data["profiles"].append({"id": pid, "name": name, "server": server,
                                         "note": note, "created": time.strftime("%Y-%m-%d")})
            data["current"] = pid
            self.write_json("profile.json", data)
        return data

    def current_profile(self) -> Optional[Dict[str, Any]]:
        data = self.profiles()
        cid = self.profile_id if self.profile_id is not None else data.get("current")
        for p in data.get("profiles", []):
            if p["id"] == cid:
                return p
        return data["profiles"][0] if data.get("profiles") else None

    def _scoped(self, data: Dict[str, Any], pid: str) -> Store:
        legacy = data.get("legacy_profile", data.get("current") or pid)
        path = self.base
        if pid and pid != legacy:
            path = os.path.join(path, "profiles", hashlib.sha256(pid.encode("utf-8")).hexdigest())
        return Store(self.base, data_dir=path, profile_id=pid)

    def for_profile(self, pid: Optional[str] = None) -> Store:
        data = self.profiles()
        ids = [p["id"] for p in data["profiles"]]
        if pid is None:
            current = data.get("current")
            pid = current if current in ids else next(iter(ids), "")
        if pid and pid not in ids:
            raise KeyError("找不到角色档案")
        return self._scoped(data, pid)

    def select_profile(self, pid: str) -> Dict[str, Any]:
        with self.lock("profile.json"):
            data = self.profiles()
            if not any(p["id"] == pid for p in data["profiles"]):
                raise KeyError("找不到角色档案")
            data.setdefault("legacy_profile", data.get("current") or pid)
            data["current"] = pid
            self.write_json("profile.json", data)
        return data

    def save_archive(self, profile: Dict[str, Any], souls: List[Dict[str, Any]],
                     owned: List[Dict[str, Any]], source: str, *,
                     extras: Optional[Dict[str, Any]] = None, activate: bool = True) -> Store:
        """提交完整快照，再发布档案索引。原有平铺数据仍归属原角色。"""
        pid = profile["id"]
        with self.lock("profile.json"):
            data = self.profiles()
            data.setdefault("legacy_profile", data.get("current") or "")
            scoped = self._scoped(data, pid)
            with scoped.lock("snapshot.json"):
                scoped.write_json("snapshot.json", {
                    "souls": souls, "owned": owned, "source": source,
                    "extras": extras or {},
                    "captured_at": profile.get("captured_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
            for row in data["profiles"]:
                if row["id"] == pid:
                    row.update(profile)
                    break
            else:
                data["profiles"].append(profile)
            if activate or not data.get("current"):
                data["current"] = pid
            self.write_json("profile.json", data)
        return scoped

    def inventory(self) -> Dict[str, Any]:
        snapshot = self.read_json("snapshot.json", None)
        if isinstance(snapshot, dict):
            return snapshot
        return {"souls": self.read_json("souls.json", []),
                "owned": self.read_json("owned.json", []), "source": "local"}

    # ---------- 御魂 / 式神 ----------
    def souls(self) -> List[Dict[str, Any]]:
        return self.inventory()["souls"]

    def save_souls(self, souls: List[Dict[str, Any]]) -> str:
        return self._save_inventory_field("souls", souls)

    def owned(self) -> List[Dict[str, Any]]:
        return self.inventory()["owned"]

    def save_owned(self, owned: List[Dict[str, Any]]) -> str:
        return self._save_inventory_field("owned", owned)

    def _save_inventory_field(self, field: str, rows: List[Dict[str, Any]]) -> str:
        with self.lock("snapshot.json"):
            snapshot = self.read_json("snapshot.json", None)
            if isinstance(snapshot, dict):
                snapshot[field] = rows
                return self.write_json("snapshot.json", snapshot)
            return self.write_json(field + ".json", rows)

    # ---------- 决策 / 记录 ----------
    def decisions(self) -> Dict[str, str]:
        return self.read_json("decisions.json", {})

    def set_decision(self, soul_key: str, decision: str, note: str = "") -> Dict[str, str]:
        with self.lock("decisions.json"):
            data = self.decisions()
            data[soul_key] = decision
            self.write_json("decisions.json", data)
            self.append_jsonl("decisions.log", {"soul_key": soul_key, "decision": decision, "note": note})
        return data

    def add_task(self, kind: str, items: List[Any], status: str = "draft") -> Dict[str, Any]:
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "kind": kind, "status": status, "items": items}
        self.append_jsonl("tasklog.jsonl", row)
        return row

    def tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.read_jsonl("tasklog.jsonl", limit)

    def add_monitor(self, kind: str, value: Any, note: str = "") -> Dict[str, Any]:
        row = {"kind": kind, "value": value, "note": note}
        self.append_jsonl("monitor.jsonl", row)
        return row

    def monitor(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self.read_jsonl("monitor.jsonl", limit)

    # ---------- 寮 ----------
    def guild(self) -> Dict[str, Any]:
        return self.read_json("guild.json", {"name": "", "members": []})

    def set_guild(self, data: Dict[str, Any]) -> str:
        return self.write_json("guild.json", data)

    # ---------- 待办处置（只写 OAS，不改平安志 analysis_todo） ----------
    def todo_decisions(self) -> Dict[str, Dict[str, Any]]:
        return self.read_json("todo_decisions.json", {})

    def set_todo_decision(self, todo_id: str, action: str, note: str = "") -> Dict[str, Any]:
        """action: done | ignore | restore（restore 收回处置，回到 open）。"""
        status = {"done": "done", "ignore": "ignored", "restore": "open"}[action]
        row = {"todo_id": str(todo_id), "action": action, "status": status,
               "note": str(note or "")[:200], "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with self.lock("todo_decisions.json"):
            data = self.todo_decisions()
            if action == "restore":
                data.pop(str(todo_id), None)
            else:
                data[str(todo_id)] = row
            self.write_json("todo_decisions.json", data)
            self.append_jsonl("todo_decisions.log", row)
        return row

    def todo_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self.read_jsonl("todo_decisions.log", limit)
        return list(reversed(rows))

    # ---------- 评分标准偏好（和平安志的 active_score_standard 无关） ----------
    def standards_pref(self) -> Dict[str, Any]:
        return self.read_json("standards.json", {"active": "", "updated": ""})

    def set_standard(self, version_id: str) -> Dict[str, Any]:
        row = {"active": str(version_id), "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
        with self.lock("standards.json"):
            self.write_json("standards.json", row)
        return row

    # ---------- 备份（OAS 自己的档案 + 决策，绝不复制平安志 471MB 库） ----------
    def create_backup(self, note: str = "", dest_dir: Optional[str] = None) -> Dict[str, Any]:
        directory = backup_dir(dest_dir)
        bid = "wb-" + time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
        path = os.path.join(directory, bid + ".json")
        inventory = self.inventory()
        payload = {
            "version": 1, "id": bid, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "note": str(note or ""), "scope": "oas-workbench",
            "profile": self.profiles(), "decisions": self.decisions(),
            "todo_decisions": self.todo_decisions(), "standards": self.standards_pref(),
            "inventory": {"source": inventory.get("source"),
                          "captured_at": inventory.get("captured_at"),
                          "souls": inventory.get("souls") or [],
                          "owned": inventory.get("owned") or [],
                          "extras": inventory.get("extras") or {}},
            "tasks": self.tasks(limit=0), "monitor": self.monitor(), "guild": self.guild(),
        }
        _write_json_atomic(path, payload)
        row = {"id": bid, "time": payload["created"], "size": os.path.getsize(path),
               "path": path, "note": payload["note"]}
        index_path = os.path.join(directory, "index.json")
        with FileLock(index_path + ".lock", timeout=10):
            rows = _read_json_file(index_path, [])
            rows = [r for r in rows if r.get("path") != path]
            rows.append(row)
            _write_json_atomic(index_path, rows[-50:])
        return row

    def backups(self, dest_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        directory = backup_dir(dest_dir)
        rows = _read_json_file(os.path.join(directory, "index.json"), [])
        alive = [r for r in rows if isinstance(r, dict) and os.path.isfile(str(r.get("path")))]
        return list(reversed(alive))


def backup_dir(dest_dir: Optional[str] = None) -> str:
    path = dest_dir or BACKUP_DIR
    os.makedirs(path, exist_ok=True)
    return path


def _read_json_file(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json_atomic(path: str, data: Any) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1, allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
