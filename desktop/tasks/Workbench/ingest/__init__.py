# This file uses the following encoding: utf-8
"""数据读取（in 平安志 = 数据读取）。

两条来源：
  1. from_json  —— 导入 JSON（平安志导出的档案 / 本模块自己的格式）
  2. from_screen—— OAS 设备截图 + OCR 文本解析

两者输出同一种结构：
    {"profile": {...}, "souls": [...], "owned": [...], "source": "json|screen"}

本文件不依赖 OAS 的 Device：截图路径由调用方传入，便于离线测试。
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Dict, List, Optional

SOUL_KEYS = ("souls", "yuhun", "soul", "御魂")
OWNED_KEYS = ("shikigami", "shikis", "owned", "roster", "式神")
PROFILE_KEYS = ("profile", "account", "character", "角色")

# OCR 行样例："破势 六号 暴击 15级"、"攻击加成 +55%"、"暴击伤害 +22%"
SET_LINE = re.compile(r"^(?P<set>[\u4e00-\u9fa5·]{2,8})\s*(?:[一二三四五六1-6]\s*号)?\s*(?P<main>[\u4e00-\u9fa5]{2,6})?\s*(?P<lv>\d{1,2})\s*级?$")
SUB_LINE = re.compile(r"^(?P<attr>[\u4e00-\u9fa5]{2,6})\s*[+＋]\s*(?P<val>\d+(?:\.\d+)?)\s*%?$")


def _pick(obj: Dict[str, Any], keys) -> Any:
    for k in keys:
        if k in obj:
            return obj[k]
    return None


def load_archive(path: str) -> Dict[str, Any]:
    """读取一个 JSON 档案文件并规范化为内部结构。"""
    with open(path, "r", encoding="utf-8-sig") as fh:
        return {**parse_archive(json.load(fh)), "file": path}


def parse_archive(raw: Any) -> Dict[str, Any]:
    """解析工作台档案、平安志 JSON 快照或旧版御魂列表。"""
    if isinstance(raw, list):
        return {"profile": None, "souls": _norm_souls(raw), "owned": [],
                "source": "json", "extras": {}}
    if not isinstance(raw, dict):
        raise ValueError("档案应为 JSON 对象或御魂数组")
    if "normalizedSouls" in raw or "normalizedShikigami" in raw:
        return _parse_paz_export(raw)
    souls = _pick(raw, SOUL_KEYS)
    owned = _pick(raw, OWNED_KEYS)
    profile = _pick(raw, PROFILE_KEYS)
    if profile is None and isinstance(raw.get("profiles"), list) and len(raw["profiles"]) == 1:
        profile = raw["profiles"][0]
    if isinstance(profile, dict) and souls is None:
        snap = profile.get("snapshot") or {}
        souls = snap.get("souls", snap.get("items"))
        owned = snap.get("owned", owned)
    if souls is None and owned is None:
        raise ValueError("档案没有御魂或式神字段")
    extras = raw.get("extras")
    return {"profile": profile, "souls": _norm_souls([] if souls is None else souls),
            "owned": _norm_owned([] if owned is None else owned), "source": "json",
            "extras": _archive_extras(extras) if isinstance(extras, dict) else {}}


def _norm_souls(rows: List[Any]) -> List[Dict[str, Any]]:
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("御魂字段应为对象数组")
    from tasks.Workbench.engine import normalize_soul

    out = []
    for r in rows:
        s = normalize_soul({**r, "slot": r.get("slot", r.get("position")),
                           "quality": r.get("quality") or r.get("rarity")})
        if not isinstance(s["set_id"], str) or not s["set_id"].strip() or s["slot"] not in range(1, 7):
            raise ValueError("御魂套装或号位无效")
        values = [*s["substats"].values(), *s["fixed_stats"].values()]
        if s["main_value"] is not None:
            values.append(s["main_value"])
        if not all(math.isfinite(v) for v in values):
            raise ValueError("御魂数值无效")
        extra = {k: v for k, v in r.items() if k != "set_id"}
        out.append({**extra, "set": s["set_id"], "slot": s["slot"],
                    "main": s["main_attribute"], "main_value": s["main_value"],
                    "substats": s["substats"], "rolls": s["rolls"],
                    "fixed_attrs": s["fixed_attrs"], "fixed_stats": s["fixed_stats"],
                    "level": s["level"], "quality": s["quality"],
                    "initial_substat_count": s["initial_substat_count"]})
    return out


def norm_skills(raw: Any) -> List[Dict[str, Any]]:
    """统一规整式神技能列表，兼容多种来源格式。

    支持：
      - JSON 字符串（平安志 sqlite owned_shikigami.skills_json）
      - 对象数组（{"skillId": 2001, "level": 5} / {"id": "2001", "name": ..., "level": 5}）
      - 二元数组（mumu-snapshot raw[2][9]: [[2241, 5], [2243, 4]]）
      - 纯等级数组（[1, 5, 5]）
    输出统一为 [{"id": str, "level": int, ...}]。
    """
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw, start=1):
        if isinstance(item, dict):
            sid = item.get("id")
            if sid is None:
                sid = item.get("skillId")
            if sid is None:
                sid = item.get("skill_id")
            if sid is None:
                sid = idx
            lv = item.get("level")
            if lv is None:
                lv = item.get("lv")
            try:
                level = int(lv or 0)
            except (TypeError, ValueError):
                level = 0
            entry: Dict[str, Any] = {"id": str(sid), "level": level}
            if item.get("name"):
                entry["name"] = str(item["name"])
            if item.get("type"):
                entry["type"] = str(item["type"])
            cost = item.get("energy_cost") if "energy_cost" in item else item.get("energyCost")
            if cost is not None:
                entry["energy_cost"] = cost
            out.append(entry)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                level = int(item[1] or 0)
            except (TypeError, ValueError):
                level = 0
            out.append({"id": str(item[0]), "level": level})
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            out.append({"id": str(idx), "level": int(item)})
    return out


def skill_levels_of(raw: Any) -> List[int]:
    """提取纯技能等级列表（如 [5, 5, 5]）。"""
    return [int(s["level"]) for s in norm_skills(raw)]


def _norm_owned(rows: List[Any]) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("式神字段应为数组")
    out = []
    for r in rows:
        if isinstance(r, dict):
            key = r.get("id") or r.get("shikigami_id") or r.get("shikigamiId") or r.get("name")
            if not isinstance(key, (str, int)) or not str(key).strip():
                raise ValueError("式神条目缺少编号或名称")
            raw_skills = None
            for sk in ("skills", "skills_json", "skillsJson", "skill_levels", "skillLevels"):
                if r.get(sk) is not None:
                    raw_skills = r.get(sk)
                    break
            out.append({"id": key, "name": r.get("name"), "star": r.get("star"),
                        "level": r.get("level"), "awakened": r.get("awakened", r.get("awake")),
                        "skin": r.get("skin") or r.get("skin_id") or r.get("skinId"),
                        "instance_id": r.get("instance_id") or r.get("instanceId"),
                        "skills": norm_skills(raw_skills)})
        elif isinstance(r, str):
            out.append({"id": r, "name": r, "skills": []})
        else:
            raise ValueError("式神条目无效")
    return out


def _parse_paz_export(raw: Dict[str, Any]) -> Dict[str, Any]:
    from tasks.Workbench.ingest.paz_db import attribute_value

    payload = raw.get("sourcePayload") or {}
    player = payload.get("player") or {}
    sid, uid = player.get("serverId"), player.get("shortId")
    profile = {"name": player.get("name"), "server": player.get("serverName"),
               "captured_at": raw.get("capturedAt")}
    if isinstance(sid, int) and isinstance(uid, int):
        profile["id"] = f"paz:{sid}:{uid}"
    souls = []
    for r in raw.get("normalizedSouls", []):
        main = r["main_attr_type"]
        subs, rolls, fixed = {}, {}, {}
        for a in r.get("attributes") or []:
            key = a["attribute_type"]
            value = attribute_value(key, a["value"])
            if a.get("fixed_attribute"):
                fixed[key] = value
            else:
                subs[key] = value
                rolls[key] = a.get("enhancement_count") or 0
        souls.append({"set": r["set_id"], "slot": r["slot"], "quality": r["quality"],
                      "level": r["level"], "main": main, "main_value": attribute_value(main, r["main_attr_value"]),
                      "initial_substat_count": r.get("initial_substat_count"),
                      "substats": subs, "rolls": rolls, "fixed_attrs": list(fixed), "fixed_stats": fixed,
                      "locked": r.get("locked_in_source"), "internal_id": r.get("source_stable_id")})
    return {"profile": profile, "souls": _norm_souls(souls),
            "owned": _norm_owned(raw.get("normalizedShikigami", [])),
            "extras": paz_export_extras(raw), "source": "paz-json"}


def _counts(value: Any) -> Dict[str, int]:
    return {str(k): v for k, v in value.items() if type(v) is int and v >= 0} if isinstance(value, dict) else {}


def _archive_extras(raw: Dict[str, Any]) -> Dict[str, Any]:
    extras = {}
    for key in ("shards", "currencies"):
        if isinstance(raw.get(key), dict):
            extras[key] = _counts(raw[key])
    guild = raw.get("guild")
    if isinstance(guild, dict):
        extras["guild"] = _counts(guild)
    return extras


def paz_export_extras(raw: Dict[str, Any]) -> Dict[str, Any]:
    payload = raw.get("sourcePayload")
    payload = payload if isinstance(payload, dict) else {}
    extras = {"currencies": raw.get("normalizedItems")}
    shards = payload.get("heroBookShards")
    if isinstance(shards, list):
        extras["shards"] = {str(row[0]): row[1] for row in shards
                            if isinstance(row, list) and len(row) in (2, 5) and type(row[0]) is int}
    guild = payload.get("guild")
    if isinstance(guild, dict) and guild.get("id"):
        extras["guild"] = {key: guild.get(key) for key in
                           ("level", "activeMemberCount", "activeRank", "funds", "pvpRank", "pvpScore")}
        if isinstance(guild.get("members"), list):
            extras["guild"]["members"] = len(guild["members"])
    return _archive_extras(extras)


def parse_ocr_lines(lines: List[str]) -> List[Dict[str, Any]]:
    """把御魂详情页的 OCR 文本行还原成御魂对象。

    约定（与游戏详情页顺序一致）：
        第一行：套装名 + 号位 + 主属性 + 等级
        其后：  副属性 +X% 若干行
    解析失败的行走 'unparsed' 返回，绝不静默丢弃。
    """
    souls: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    unparsed: List[str] = []
    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue
        m = SET_LINE.match(line)
        if m and not SUB_LINE.match(line):
            cur = {"set": m.group("set"), "slot": _slot_of(line),
                   "main": m.group("main") or "", "level": int(m.group("lv") or 0),
                   "substats": {}, "quality": 6, "initial_substat_count": 0}
            souls.append(cur)
            continue
        ms = SUB_LINE.match(line)
        if ms and cur is not None:
            cur["substats"][ms.group("attr")] = float(ms.group("val"))
            cur["initial_substat_count"] = len(cur["substats"])
            continue
        unparsed.append(line)
    return souls, unparsed


def _slot_of(line: str) -> int:
    for cn, num in (("一", 1), ("二", 2), ("三", 3), ("四", 4), ("五", 5), ("六", 6)):
        if cn + "号" in line:
            return num
    m = re.search(r"([1-6])\s*号", line)
    return int(m.group(1)) if m else 0


def save_frames_note(export_dir: str, source: str, files: List[str]) -> str:
    """留下摄取证据：来源 + 帧文件清单 + 时间。"""
    import time
    os.makedirs(export_dir, exist_ok=True)
    p = os.path.join(export_dir, "ingest-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"source": source, "frames": files,
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh, ensure_ascii=False, indent=1)
    return p
