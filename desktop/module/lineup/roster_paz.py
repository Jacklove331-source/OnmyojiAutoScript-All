# This file uses the following encoding: utf-8
"""从平安志 sqlite 生成式神池（roster）——截图 OCR 之外的可选数据源。

对应 OAS 原有功能「式神识别」：`module/lineup/scan.py` 每周截图 OCR 写
`config/roster.json`。本模块直接读平安志的权威库：

    owned_shikigami  3201 条实例：shikigami_id / star / level / awakened / skills_json
    shikigami        281 条目录：name / rarity

生成结构与 scan.py 完全一致（name/rarity/level/skills/speed/souls），
所以 `config/auto_lineup.py` 不用改。

限制：
  * 数据新鲜度 = 上次「平安志数据读取」的快照，不是实时；
  * 库里 2558/3201 是素材，默认按 SP,SSR,SR 过滤，别整库灌进 roster。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_DB = os.path.join(os.environ.get("APPDATA", ""), "com.yys.analysis",
                          "current-analysis.sqlite3")
DEFAULT_RARITIES = "SP,SSR,SR"
MATERIAL = "素材"


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)


def _skills(raw: Any) -> List[int]:
    """skills_json → 按 skillId 升序的技能等级列表（与扫描器一致，最多 3 个）。"""
    try:
        rows = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (TypeError, ValueError):
        return []
    pairs = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pairs.append((int(row.get("skillId") or 0), int(row.get("level") or 0)))
        except (TypeError, ValueError):
            continue
    pairs.sort()
    return [lv for _sid, lv in pairs if 1 <= lv <= 5][:3]


def _parse_rarities(text: Any) -> List[str]:
    if text is None:
        return [r for r in DEFAULT_RARITIES.split(",")]
    raw = str(text).strip()
    if not raw:
        return []      # 空字符串 = 不按稀有度过滤
    return [r.strip() for r in raw.split(",") if r.strip()]


def load_owned(db: Optional[str] = None, rarities: Any = DEFAULT_RARITIES,
               include_skills: bool = True) -> Dict[str, Any]:
    """读平安志库存并聚合到「每个式神名一条」。

    同名多实例（素材、复制人）取 level/star/awakened 最高的一条。
    """
    path = db or DEFAULT_DB
    if not os.path.isfile(path):
        return {"ok": False, "reason": "找不到平安志数据库: %s" % path,
                "source": "paz-db", "shikigami": []}
    wanted = _parse_rarities(rarities)
    con = _ro(path)
    try:
        rows = con.execute(
            "select s.name, s.rarity, o.star, o.level, o.awakened, o.skills_json "
            "from owned_shikigami o "
            "left join shikigami s on s.shikigami_id = o.shikigami_id").fetchall()
    finally:
        con.close()

    best: Dict[str, Dict[str, Any]] = {}
    unknown = 0
    skipped_rarity = 0
    for name, rarity, star, level, awakened, skills_json in rows:
        if not name:
            unknown += 1
            continue
        rarity = str(rarity or "")
        if wanted and rarity not in wanted:
            skipped_rarity += 1
            continue
        item = {
            "name": str(name),
            "rarity": rarity,
            "level": int(level or 0),
            "star": int(star or 0),
            "awakened": bool(awakened),
            "skills": _skills(skills_json) if include_skills else [],
            "speed": None,
            "souls": [],
            "source": "paz-db",
        }
        cur = best.get(item["name"])
        key = (item["level"], item["star"], item["awakened"])
        if cur is None or key > (cur["level"], cur["star"], cur["awakened"]):
            best[item["name"]] = item

    items = sorted(best.values(), key=lambda x: (-x["level"], -x["star"], x["name"]))
    return {"ok": True, "source": "paz-db", "db": path,
            "rarities": wanted, "shikigami": items, "count": len(items),
            "unknown_catalog": unknown, "skipped_by_rarity": skipped_rarity}


def _load_roster(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"shikigami": []}
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"shikigami": []}


def _save_roster(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(path))


def save(data: Dict[str, Any], output: Any = "config/roster.json") -> Dict[str, Any]:
    """把 load_owned() 的结果合并进 roster 文件。

    手填/其他来源的条目保留；同名的既有条目保留 speed/souls（扫描器/历史算出来的）。
    """
    out_path = Path(str(output))
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    existing = _load_roster(out_path)
    old = {str(x.get("name")): x for x in existing.get("shikigami", []) if x.get("name")}
    merged = []
    for item in data.get("shikigami", []):
        prev = old.get(item["name"]) or {}
        for key in ("speed", "souls"):
            if item.get(key) in (None, []) and prev.get(key) not in (None, []):
                item[key] = prev[key]
        merged.append(item)
    new_names = {x["name"] for x in merged}
    kept = 0
    for name, item in old.items():
        if name in new_names:
            continue
        if str(item.get("source") or "") in ("scan", "paz-db"):
            continue          # 旧自动条目以本次库为准；手填条目保留
        merged.append(item)
        kept += 1
    out = dict(existing)
    out["shikigami"] = merged
    out["updated_at"] = time.strftime("%Y-%m-%d")
    out["source"] = data.get("source", "paz-db")
    _save_roster(out_path, out)
    return {"ok": True, "count": len(merged), "manual_kept": kept,
            "path": str(out_path), "source": out["source"]}


def build_and_save(cfg) -> Dict[str, Any]:
    """按 RosterScan 配置对象生成并写入 roster 文件（供 script.py 调用）。"""
    rarity_text = str(getattr(cfg, "rarities", DEFAULT_RARITIES) or "")
    data = load_owned(rarities=rarity_text,
                      include_skills=bool(getattr(cfg, "include_skills", True)))
    if not data.get("ok"):
        return data
    result = save(data, getattr(cfg, "output_file", "config/roster.json"))
    result.update({"unknown_catalog": data["unknown_catalog"],
                   "skipped_by_rarity": data["skipped_by_rarity"],
                   "rarities": data["rarities"]})
    return result
