"""平安志（PAZ）落地快照读取器：一处读全 12 项资产（含金币精确值与结界卡）。

数据来自 PAZ 自己采集并落地的明文（不点设备、不注入、零风险）：
  %APPDATA%\\com.yys.analysis\\current-data-<profile>-snapshot-<id>.json   → normalizedItems（21 项精确整数）
  %APPDATA%\\com.yys.analysis\\current-data-<profile>.projection.json       → summary（角色名/完整度/数量）

字段口径（2026-11-21 实测，见 references/paz-snapshot-fields.md）：
  jade←jade  coin←coin  ap←action_point  s_jade←s_jade  skin_token←skin_token
  mystery_amulet←mystery_amulet  gold_ofuda←gold_ofuda  honor←honor  medal←medal
  contrib←contrib  scale←scale  demon_soul←demon_soul

用法：
  toolkit\\python.exe tasks\\Workbench\\paz_asset_reader.py            # 打印摘要 + 映射结果
  toolkit\\python.exe tasks\\Workbench\\paz_asset_reader.py --json out.json
"""
from __future__ import annotations

import argparse
import calendar
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

PAZ_DIR = Path(os.environ.get("PAZ_DATA_DIR") or (Path(os.environ.get("APPDATA", "")) / "com.yys.analysis"))

# 卡片键 ← PAZ normalizedItems 键
FIELD_MAP = {
    "jade": "jade",
    "coin": "coin",
    "ap": "action_point",
    "s_jade": "s_jade",
    "skin_token": "skin_token",
    "mystery_amulet": "mystery_amulet",
    "gold_ofuda": "gold_ofuda",
    "honor": "honor",
    "medal": "medal",
    "contrib": "contrib",
    "scale": "scale",
    "demon_soul": "demon_soul",
}
EXTRA_FIELDS = ("ofuda", "sp_skin_token", "reverse_scale", "ar_amulet", "broken_amulet",
                "auto_point", "foolery_pass", "realm_raid_pass", "totem_pass")


def _norm_name(s: Optional[str]) -> str:
    """角色名归一：去空白与装饰符号，只留字母数字与汉字（'e·' vs 'eؼ' 这类编码噪声也能对上）。"""
    if not s:
        return ""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", s).lower()


def role_matches(paz_name: Optional[str], expect: Optional[str]) -> bool:
    a, b = _norm_name(paz_name), _norm_name(expect)
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def _latest(pattern: str) -> Optional[Path]:
    files = sorted(PAZ_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def profiles() -> list[dict]:
    """列出 PAZ 每个 profile（= 每个角色）：{profile_id, account_name, imported_at, projection, snapshots}。"""
    out: list[dict] = []
    for pf in sorted(PAZ_DIR.glob("current-data-*.projection.json"),
                     key=lambda p: p.stat().st_mtime, reverse=True):
        pid = pf.name.replace("current-data-", "").replace(".projection.json", "")
        summary: Dict[str, Any] = {}
        try:
            summary = (json.loads(io.open(pf, "r", encoding="utf-8").read()).get("summary") or {})
        except Exception:
            summary = {}
        snaps = sorted(PAZ_DIR.glob(f"current-data-{pid}-snapshot-*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        out.append({"profile_id": pid, "account_name": summary.get("account_name"),
                    "imported_at": summary.get("imported_at"), "projection": pf,
                    "snapshots": snaps})
    return out


def snapshot_for_role(expect_role: Optional[str] = None):
    """挑出该角色对应的最新快照文件；不指定角色时退回全局最新。

    平安志按 profile（角色）分文件存：oas1=DemoRole、oas3=半生緣 各自一份，
    所以绝不能"谁最新用谁"（两个号会互相顶掉）。
    """
    groups = profiles()
    if expect_role:
        for g in groups:
            if role_matches(g.get("account_name"), expect_role) and g["snapshots"]:
                return g["snapshots"][0], g
        return None, None
    if groups and groups[0]["snapshots"]:
        return groups[0]["snapshots"][0], groups[0]
    snap = _latest("current-data-*-snapshot-*.json")
    return snap, None


def read(max_age_sec: int = 0, expect_role: Optional[str] = None) -> Dict[str, Any]:
    """读 PAZ 快照（按角色挑 profile）。max_age_sec>0 时超过该秒数判过期（只记 stale，不丢数据）。"""
    out: Dict[str, Any] = {"ok": False, "source": "paz.snapshot", "paz_dir": str(PAZ_DIR)}
    snap, group = snapshot_for_role(expect_role)
    if snap is None:
        if expect_role and profiles():
            names = [g.get("account_name") for g in profiles()]
            out["error"] = f"PAZ 里没有「{expect_role}」的快照（现有：{names}）"
        else:
            out["error"] = f"没有 PAZ 快照文件（{PAZ_DIR}）"
        return out
    proj = group.get("projection") if group else _latest("current-data-*.projection.json")
    try:
        data = json.loads(io.open(snap, "r", encoding="utf-8").read())
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"读快照失败: {type(exc).__name__}: {exc}"
        return out

    summary: Dict[str, Any] = {}
    if proj is not None:
        try:
            summary = (json.loads(io.open(proj, "r", encoding="utf-8").read()).get("summary") or {})
        except Exception:
            summary = {}

    items_raw = data.get("normalizedItems") or {}
    items: Dict[str, int] = {}
    for card_key, paz_key in FIELD_MAP.items():
        val = items_raw.get(paz_key)
        if isinstance(val, (int, float)):
            items[card_key] = int(val)
    extras = {k: int(items_raw[k]) for k in EXTRA_FIELDS if isinstance(items_raw.get(k), (int, float))}

    captured = data.get("capturedAt") or summary.get("imported_at")
    age = None
    if captured:
        try:
            # capturedAt 是 UTC（形如 2026-11-21T19:21:29.523Z）：必须按 UTC 换算，否则 age 差 8 小时
            iso = captured.replace("Z", "").replace("+00:00", "")
            st = time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
            age = int(time.time() - calendar.timegm(st))
        except Exception:
            age = None

    out.update({
        "ok": bool(items) and len(items) >= 10,
        "captured_at": captured,
        "age_sec": age,
        "stale": bool(max_age_sec and age is not None and age > max_age_sec),
        "completeness": data.get("completeness") or summary.get("completeness"),
        "account_name": summary.get("account_name"),
        "guild_name": summary.get("guild_name"),
        "items": items,
        "extras": extras,
        "realm_cards": len(data.get("normalizedRealmCards") or []),
        "souls": len(data.get("normalizedSouls") or []),
        "shikigami": len(data.get("normalizedShikigami") or []),
        "snapshot_file": snap.name,
        "items_raw_count": len(items_raw),
    })
    if expect_role is not None and not role_matches(out.get("account_name"), expect_role):
        out["ok"] = False
        out["error"] = f"PAZ 当前快照属于「{out.get('account_name')}」，与配置角色「{expect_role}」不一致（拒绝跨账号写入）"
    return out


def readings_from(paz: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """把 PAZ 结果转成 asset_store.merge() 要的 readings（12 项全部 exact）。"""
    readings: Dict[str, Dict[str, Any]] = {}
    for card_key in FIELD_MAP:
        val = (paz.get("items") or {}).get(card_key)
        readings[card_key] = {"value": val, "precision": "exact",
                              "source": "paz.snapshot",
                              "error": None if val is not None else "PAZ 快照里没有该字段"}
    return readings


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="", help="把结果写到该文件")
    ap.add_argument("--expect-role", default="", help="校验角色名（跨账号保护）")
    args = ap.parse_args(argv)
    res = read(expect_role=args.expect_role or None)
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
        print(f"written -> {args.json}")
    else:
        print(text)
    return 0 if res.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
