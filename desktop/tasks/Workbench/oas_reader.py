# -*- coding: utf-8 -*-
"""OAS 自主读取游戏资产（不再依赖平安志 App 运行）。

原理：把已解密的读取脚本重新打包进一个 .so（长度补齐/同种子重加密，见
`references/paz-payload-reversing.md` §7），用平安志那个通用注入器
（`yys-mumu-injector --pid <pid> --library <so>`，纯 ptrace+dlopen）注入游戏进程，
脚本在游戏自带的 CPython 里执行，把 16 段 JSON 写到游戏 cache。本模块负责：
push .so → 注入 → pull 结果 → 解析成可用的字段。

用法：
    toolkit\\python.exe tasks\\Workbench\\oas_reader.py --serial 127.0.0.1:16384 --once
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
SO_NAME = "liboas-reader.so"
SO_REMOTE = "/data/local/tmp/%s" % SO_NAME
INJ_LOCAL = REPO / "dev_tools" / "bin" / "yys-mumu-injector"   # 仓库自带（vendor），不依赖平安志安装目录
INJ_REMOTE = "/data/local/tmp/oas-injector"
RESULT_NAME = "oas_assets.json"
PKG = "com.netease.onmyoji.wyzymnqsd_cps"

# 载荷产出的 16 段顺序（与解出的 script_task 尾部 snapshot 列表一一对应）
SECTION_NAMES = [
    "player", "currencies", "heroes", "souls", "item_presets", "hero_shards",
    "realm_cards", "story_tasks", "guild", "heroes_bag", "server_name_errors",
    "hero_runtime_probe", "task_records", "contract_spirits", "collections",
    "account_comparison",
]

ADB_CANDIDATES = [
    Path(r"E:\MuMuPlayer\nx_device\12.0\shell\adb.exe"),
    Path(r"E:\MuMuPlayer\nx_main\adb.exe"),
]


def adb_path() -> str:
    for cand in ADB_CANDIDATES:
        if cand.exists():
            return str(cand)
    found = shutil.which("adb")
    if found:
        return found
    raise RuntimeError("找不到 adb.exe")


def _run(args: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout)


def _adb(serial: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return _run([adb_path(), "-s", serial, *args], timeout=timeout)


def _su(serial: str, command: str, timeout: int = 90) -> str:
    """设备上执行 root 命令（su 0 形式，不要用 su -c "..."，会被二次解析）。"""
    return _adb(serial, "shell", "su 0 sh -c '%s'" % command, timeout=timeout).stdout


def game_pid(serial: str) -> Optional[int]:
    out = _adb(serial, "shell", "pidof", PKG).stdout.strip()
    return int(out.split()[0]) if out else None


def local_so() -> Path:
    """优先用工作区里已生成的 .so；没有就用原包重建（需要同目录脚本）。"""
    for cand in (REPO.parent / "supervise" / "paz_src" / SO_NAME,
                 REPO / "dev_tools" / SO_NAME):
        if cand.exists():
            return cand
    raise RuntimeError(f"找不到 {SO_NAME}：请先跑 supervise/repack_payload.py 生成")


def read_assets(serial: str, *, max_age: float = 120.0) -> Dict[str, Any]:
    """注入一次并把结果解析出来。"""
    if not game_pid(serial):
        raise RuntimeError(f"游戏没在跑（{PKG}）")
    so = local_so()
    if not INJ_LOCAL.exists():
        raise RuntimeError(f"仓库里没有注入器：{INJ_LOCAL}")
    for local, remote in ((so, SO_REMOTE), (INJ_LOCAL, INJ_REMOTE)):
        res_push = _adb(serial, "push", str(local), remote)
        if res_push.returncode != 0:
            raise RuntimeError(f"push {local.name} 失败: " + res_push.stderr[-200:])
    _su(serial, "chmod 755 %s" % INJ_REMOTE)

    gpid = game_pid(serial)
    # 注入器报 dlclose/bridge 失败时载荷其实已经跑完，只看结果文件
    _su(serial, "%s --pid %d --library %s" % (INJ_REMOTE, gpid, SO_REMOTE))
    result_remote = "/data/user/0/%s/cache/%s" % (PKG, RESULT_NAME)
    deadline = time.time() + max_age
    local_tmp = Path(os.environ.get("TEMP", ".")) / RESULT_NAME
    while time.time() < deadline:
        time.sleep(2)
        stat = _su(serial, "ls -la %s" % result_remote)
        if "No such file" in stat or not stat.strip():
            continue
        _su(serial, "cp %s /data/local/tmp/%s && chmod 644 /data/local/tmp/%s"
            % (result_remote, RESULT_NAME, RESULT_NAME))
        pulled = _adb(serial, "pull", "/data/local/tmp/" + RESULT_NAME, str(local_tmp))
        if pulled.returncode == 0 and local_tmp.exists():
            data = json.loads(local_tmp.read_text(encoding="utf-8"))
            return _shape(data)
    raise RuntimeError("注入后没有拿到 %s（%ds 超时）" % (RESULT_NAME, int(max_age)))


def _shape(data: Any) -> Dict[str, Any]:
    """16 段数组 → 命名字典。"""
    if isinstance(data, dict):
        # 脚本出错时写 {"error": ..., "tb": ...}，不能当成成功
        return {"ok": False, "error": data.get("error"),
                "tb_tail": (data.get("tb") or "")[-600:],
                "account": {}, "currencies_raw": [], "counts": {},
                "sections": {}}
    sections = {name: (data[i] if i < len(data) else None)
                for i, name in enumerate(SECTION_NAMES)}
    player = sections.get("player") or []
    meta = player[5] if len(player) > 5 and isinstance(player[5], dict) else {}
    currencies = sections.get("currencies") or []
    return {
        "ok": True,
        "account": {"short_id": player[0] if player else None,
                    "server_id": player[1] if len(player) > 1 else None,
                    "name": player[2] if len(player) > 2 else None,
                    "level": player[3] if len(player) > 3 else None,
                    "server_name": player[4] if len(player) > 4 else None},
        "currencies_raw": currencies,
        "counts": {k: (len(v) if isinstance(v, list) else None) for k, v in sections.items()},
        "sections": sections,
        "meta": {"app_version": (meta.get("globals_probe") or {}).get("app_version")},
        "items": items_from({"currencies_raw": currencies}),
    }


# 载荷里 currency_ids 的 21 个 id 按固定顺序输出；下表是把它们对齐到 PAZ 的字段名
# （2026-11-22 用同一时刻的 PAZ normalizedItems 逐值匹配：coin/jade/action_point/auto_point/
#  honor/medal/contrib/totem_pass/s_jade/skin_token/realm_raid_pass/broken_amulet/
#  mystery_amulet/ar_amulet/ofuda/gold_ofuda/scale/reverse_scale/demon_soul/foolery_pass/
#  sp_skin_token —— 21/21 唯一命中）
CURRENCY_FIELDS = ['coin', 'jade', 'action_point', 'auto_point', 'honor', 'medal', 'contrib', 'totem_pass', 's_jade', 'skin_token', 'realm_raid_pass', 'broken_amulet', 'mystery_amulet', 'ar_amulet', 'ofuda', 'gold_ofuda', 'scale', 'reverse_scale', 'demon_soul', 'foolery_pass', 'sp_skin_token']


def items_from(res):
    """把 currencies_raw 变成 {字段名: 值}；长度对不上就返回空 dict（宁缺勿错）。"""
    raw = res.get("currencies_raw") or []
    if len(raw) != len(CURRENCY_FIELDS):
        return {}
    return {name: raw[i] for i, name in enumerate(CURRENCY_FIELDS)}


def write_store(config: str, res: Dict[str, Any], serial: str = "") -> Dict[str, Any]:
    """把读取结果并进 config/workbench/assets/<config>.json（面板读的文件）。"""
    sys.path.insert(0, str(REPO))
    from tasks.Workbench import asset_store as S  # noqa: PLC0415

    items = res.get("items") or {}
    if not items:
        raise RuntimeError("没有可写的资产项（items 为空）")
    readings = {k: {"value": v, "precision": "exact", "source": "oas.reader", "error": None}
                for k, v in items.items()}
    required = [k for k in ("jade", "coin", "ap", "s_jade", "skin_token", "mystery_amulet",
                            "gold_ofuda", "honor", "medal", "contrib", "scale", "demon_soul")
                if k in readings or True]  # 键名沿用面板口径（ap ← action_point）
    # 面板字段名与载荷字段名有两处差异：ap ← action_point
    if "action_point" in readings:
        readings["ap"] = dict(readings["action_point"])
    return S.merge(REPO / "config" / "workbench", config,
                   serial=serial, profile=(res.get("account") or {}).get("name") or None,
                   readings=readings, required=required,
                   meta={"realm_cards": (res.get("counts") or {}).get("realm_cards"),
                         "heroes": (res.get("counts") or {}).get("heroes"),
                         "souls": (res.get("counts") or {}).get("souls"),
                         "source": "oas.reader"},
                   source="oas.reader")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dump", default="", help="把解析结果写到这个 json")
    ap.add_argument("--write", default="", help="写入这个 config 的资产面板（如 oas1）")
    args = ap.parse_args(argv)
    res = read_assets(args.serial)
    print(json.dumps({"ok": res["ok"], "account": res["account"],
                      "currencies_raw": res["currencies_raw"][:6],
                      "counts": res["counts"]}, ensure_ascii=False, indent=1))
    if args.dump:
        Path(args.dump).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    if args.write:
        state = write_store(args.write, res, serial=args.serial)
        print("store:", json.dumps({"config": args.write, "status": state.get("status"),
                                    "complete": state.get("complete"),
                                    "captured_at": state.get("captured_at")},
                                   ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
