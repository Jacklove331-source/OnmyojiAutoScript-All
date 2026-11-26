# This file uses the following encoding: utf-8
"""平安志 mumu-snapshot 原始结果 → 本模块的画像/御魂/式神。

原始格式（实测，`/data/user/<uid>/<pkg>/cache/yys-mumu-snapshot-result.json`，2.6MB）：
    顶层是长度 16 的**位置数组**，无字段名：
      [0]  角色信息  [short_id, server_id, name, level, exp, {…}]
      [2]  式神      [uid, id, ..., level, ..., awakened, ..., star, [[技能id, lv]…], ...]
      [3]  御魂      [uid, set_id, 星级, 号位(0基), 等级, 时间, locked, ?, 主属性?, [主attr_id, 主值], 副属性[[attr_id, 值, 强化次],…], [], []]
      [12] 日常/周常 {daily, weekly, realmCardStatus, pvpProfile}
      [13] 契灵      {hunlings:[…]}
      [13..15] 图鉴/外观/资源
本模块只消费 [0]/[2]/[3]；其余原样透传以备后用。

属性 id 映射见 data/paz_attr_map.json（certain 实测确定，provisional 待对表）。
"""
from __future__ import annotations

import io
import json
import os
from typing import Any, Dict, List, Optional, Tuple

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def load_attr_map(path: Optional[str] = None) -> Tuple[Dict[str, str], Dict[str, str]]:
    p = path or os.path.join(DATA_DIR, "paz_attr_map.json")
    d = json.load(io.open(p, encoding="utf-8"))
    return d.get("certain", {}), d.get("provisional", {})


def _set_map() -> Dict[str, str]:
    try:
        return json.load(io.open(os.path.join(DATA_DIR, "set_icon_map.json"), encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _attr_name(attr_id: Any, certain: Dict[str, str], prov: Dict[str, str]) -> str:
    k = str(attr_id)
    return certain.get(k) or prov.get(k) or ("attr_%s" % k)


def parse_player(raw: List[Any]) -> Dict[str, Any]:
    head = raw[0] if raw and isinstance(raw[0], list) else []
    info = raw[0][5] if len(head) > 5 and isinstance(head[5], dict) else {}
    return {
        "short_id": head[0] if head else None,
        "server_id": head[1] if len(head) > 1 else None,
        "name": info.get("name") or (head[2] if len(head) > 2 else ""),
        "level": info.get("level") or (head[3] if len(head) > 3 else None),
        "headIcon": info.get("headIcon"),
    }


def parse_souls(raw: List[Any], attr_map: Optional[str] = None) -> List[Dict[str, Any]]:
    """[3] → 本模块 soul 结构（set/slot/main/substats/level/quality/initial_substat_count）。"""
    certain, prov = load_attr_map(attr_map)
    set_names = _set_map()
    out = []
    for item in (raw[3] if len(raw) > 3 else []) or []:
        if not isinstance(item, list) or len(item) < 11:
            continue
        inner_id = item[0]
        set_id = item[1]
        quality = item[2]
        slot = int(item[3]) + 1              # 0 基 → 1..6
        level = item[5] if len(item) > 5 else 0      # item[4] 是主属性合成 id，不是等级
        main_pair = item[10] if isinstance(item[10], list) and len(item[10]) >= 2 else [None, 0]
        subs_raw = item[9] if isinstance(item[9], list) else []
        substats: Dict[str, float] = {}
        rolls: Dict[str, int] = {}
        for s in subs_raw:
            if not isinstance(s, list) or len(s) < 2:
                continue
            name = _attr_name(s[0], certain, prov)
            val = float(s[1])
            substats[name] = round(val * 100, 3) if name.endswith("_rate") or name in (
                "crit_rate", "crit_damage", "effect_hit", "effect_resist") else round(val, 3)
            rolls[name] = int(s[2]) if len(s) > 2 else 0
        main_name = _attr_name(main_pair[0], certain, prov)
        main_val = float(main_pair[1])
        out.append({
            "internal_id": inner_id,
            "set": set_names.get(str(set_id), str(set_id)),
            "slot": slot,
            "quality": int(quality) if quality else 6,
            "level": int(level or 0),
            "main": main_name,
            "main_value": round(main_val * 100, 3) if main_name.endswith("_rate") or main_name in (
                "crit_rate", "crit_damage", "effect_hit", "effect_resist") else round(main_val, 3),
            "substats": substats,
            "rolls": rolls,
            "initial_substat_count": len(substats),
            "locked": bool(item[6]) if len(item) > 6 else False,
        })
    return out


def parse_shikigami(raw: List[Any]) -> List[Dict[str, Any]]:
    from tasks.Workbench.ingest import norm_skills

    out = []
    for item in (raw[2] if len(raw) > 2 else []) or []:
        if not isinstance(item, list) or len(item) < 8:
            continue
        star = None
        if len(item) > 11 and isinstance(item[11], (int, float)) and not isinstance(item[11], bool):
            star = int(item[11])
        elif len(item) > 12 and isinstance(item[12], (int, float)) and not isinstance(item[12], bool):
            star = int(item[12])
        raw_skills = item[9] if len(item) > 9 and isinstance(item[9], list) else []
        out.append({
            "internal_id": item[0],
            "instance_id": item[0],
            "id": item[1],
            "level": item[3] if len(item) > 3 else None,
            "awakened": bool(item[7]) if len(item) > 7 else False,
            "star": star,
            "skills": norm_skills(raw_skills),
        })
    return out


def load_file(path: str) -> Dict[str, Any]:
    raw = json.load(io.open(path, encoding="utf-8"))
    if not isinstance(raw, list) or len(raw) < 4:
        raise ValueError("不是平安志 mumu-snapshot 结果（期望长度≥4 的位置数组）")
    souls = parse_souls(raw)
    shikis = parse_shikigami(raw)
    return {
        "source": "paz-snapshot",
        "file": path,
        "player": parse_player(raw),
        "souls": souls,
        "owned": shikis,
        "weekly": raw[12] if len(raw) > 12 and isinstance(raw[12], dict) else None,
        "raw_sections": len(raw),
    }


def validate(souls: List[Dict[str, Any]]) -> Dict[str, Any]:
    """用游戏规则做自洽校验：一号位主属性必须是攻击、三号位防御、五号位生命。"""
    expect = {1: "attack_flat", 3: "defense_flat", 5: "hp_flat"}
    bad = []
    for s in souls:
        want = expect.get(s["slot"])
        if want and s["main"] != want:
            bad.append({"slot": s["slot"], "main": s["main"], "expect": want})
    return {"total": len(souls), "violations": len(bad), "samples": bad[:5],
            "ok": not bad}
