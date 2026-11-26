"""资产页采集守护 v3：按配置采集、与托管互斥、写入 assets/<config>.json。

与 live 监控的分工：
  * dev_tools/asset_live_watch.py —— 读实时帧，10s 级更新勾玉（精确）/金币、体力（简写）；
  * 本守护 —— 需要切页面才能拿到的项（体力精确值、皮肤券、符咒、魂玉），只在实例空闲时跑，
    默认 30 分钟一轮，也可由客户端刷新请求立刻唤醒；持文件锁，不与 live 监控抢设备。

启动：
  toolkit\\python.exe dev_tools\\asset_collect_daemon.py --interval 1800
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tasks" / "Workbench"))

import asset_store as S  # noqa: E402

WB = REPO / "config" / "workbench"
REQ = WB / "asset_request.json"
QUEUE = WB / "asset_queue.json"
LOCK = WB / "asset.lock"
LOG = REPO / "log" / "asset_collect.log"

BUSY_WINDOW_SEC = 120


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_queue(**kw) -> None:
    data = {}
    if QUEUE.exists():
        try:
            data = json.loads(QUEUE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(kw)
    data["updated_at"] = S.now()
    WB.mkdir(parents=True, exist_ok=True)
    QUEUE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def configs() -> list[str]:
    return sorted(p.stem for p in (REPO / "config").glob("oas*.json"))


def config_serial(config: str) -> str:
    try:
        cfg = json.loads((REPO / "config" / f"{config}.json").read_text(encoding="utf-8"))
        ser = cfg["script"]["device"]["serial"]
        return ser if isinstance(ser, str) else (ser[0] if ser else "")
    except Exception:
        return ""


def instance_busy(config: str) -> bool:
    lf = REPO / "log" / f"{time.strftime('%Y-%m-%d')}_{config}.txt"
    try:
        return lf.exists() and (time.time() - lf.stat().st_mtime) < BUSY_WINDOW_SEC
    except Exception:
        return False


ROLES_FILE = WB / "asset_accounts.json"


def expected_role(config: str):
    """配置 → 期望角色名（跨账号保护：PAZ 快照属于别人时拒绝写入）。"""
    try:
        return (json.loads(ROLES_FILE.read_text(encoding="utf-8")) or {}).get(config)
    except Exception:
        return None


_PAZ_CACHE: dict = {"key": None, "res": None}


def _paz_read_cached(config: str):
    """读 PAZ 快照（按快照 mtime + 配置缓存，避免每 15s 轮询都解析 10 MB JSON）。"""
    import paz_asset_reader as PZ

    snap = PZ._latest("current-data-*-snapshot-*.json")
    key = (config, str(snap), snap.stat().st_mtime if snap else None)
    if _PAZ_CACHE["res"] is not None and _PAZ_CACHE["key"] == key:
        return _PAZ_CACHE["res"]
    res = PZ.read(expect_role=expected_role(config))
    _PAZ_CACHE.update({"key": key, "res": res})
    return res


def paz_ready(config: str) -> bool:
    """该配置是否有可用的 PAZ 快照（有就可以不等托管空闲、立刻读）。"""
    try:
        return bool(_paz_read_cached(config).get("ok"))
    except Exception:
        return False


SELF_READ_ENABLED = True   # 自读通道开关（失败自动退回平安志）
UI_FALLBACK_ENABLED = os.environ.get("OAS_ASSET_UI_FALLBACK", "0") == "1"   # UI 兜底（会点屏幕），默认关，避免每 5 分钟干扰正在跑的任务


def paz_available() -> bool:
    """平安志程序是否还在（卸载后自读通道照常，PAZ 通道直接跳过）。"""
    try:
        import paz_refresh
        return bool(getattr(paz_refresh, "PAZ_EXE", None)) and paz_refresh.PAZ_EXE.exists()
    except Exception:  # noqa: BLE001
        return False


def collect_self(config: str, serial: str) -> bool:
    """自读通道：注入我们自己的读取载荷，直接拿 21 项资产（不依赖平安志 App）。

    失败（游戏不在庭院 / 注入失败 / 设备离线）返回 False，由调用方退回平安志路径。
    """
    try:
        import oas_reader as R  # tasks/Workbench 已在 sys.path
        res = R.read_assets(serial, max_age=45)
        if not res.get("ok") or not res.get("items"):
            log(f"[{config}] self-read failed: {res.get('error') or 'no items'}")
            return False
        state = R.write_store(config, res, serial=serial)
        log(f"[{config}] self-read ok status={state.get('status')} complete={state.get('complete')} "
            f"jade={(res.get('items') or {}).get('jade')} coin={(res.get('items') or {}).get('coin')}")
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"[{config}] self-read exception: {type(exc).__name__}: {exc}")
        return False


def collect_paz(config: str, serial: str, max_age: int = 0, allow_refresh: bool = True) -> bool:
    """首选：读 PAZ 落地快照（12 项全精确，含金币与结界卡），不碰设备。

    快照过期（> max_age 秒）且 allow_refresh 时，先用平安志自己的读取任务刷新一次
    （= 它界面上的「数据读取→模拟器→开始读取」，走 CDP 调 `start_mumu_read_task`）。
    返回 True = 本轮已写快照；False = 无可用/不属于该角色，交给页采集兜底。
    """
    import paz_asset_reader as PZ

    if max_age and allow_refresh:
        stale = True
        try:
            cur = PZ.read(expect_role=expected_role(config))
            stale = bool(cur.get("age_sec") is None or cur.get("age_sec") > max_age or not cur.get("ok"))
        except Exception:
            stale = True
        if stale:
            try:
                import paz_refresh

                r = paz_refresh.refresh(timeout=60, endpoint=serial or config_serial(config),
                                        expect_role=expected_role(config))
                log(f"[{config}] paz refresh ok={r.get('ok')} before={r.get('before_captured')} "
                    f"after={r.get('after_captured')} waited={r.get('waited_sec')}s err={r.get('error')}")
                _PAZ_CACHE["res"] = None      # 新快照落地 → 丢掉缓存
                if not r.get("ok"):
                    # 刷新失败：只记错误，绝不用旧快照冒充本轮成功（last_ok_at 不能前进）
                    msg = f"平安志刷新失败：{r.get('error')}"
                    S.merge(WB, config, serial=serial, error=msg, source="paz.snapshot")
                    write_queue(config=config, status="failed", pending=False, note="", error=msg)
                    log(f"[{config}] refresh failed, keep previous values: {msg}")
                    return True
            except Exception as exc:  # noqa: BLE001
                log(f"[{config}] paz refresh error: {type(exc).__name__}: {exc}")

    try:
        paz = _paz_read_cached(config)
    except Exception as exc:  # noqa: BLE001
        log(f"[{config}] paz error: {type(exc).__name__}: {exc}")
        return False
    if not paz.get("ok"):
        log(f"[{config}] paz skip: {paz.get('error')}")
        return False

    readings = PZ.readings_from(paz)
    meta = {
        "realm_cards": paz.get("realm_cards"),
        "souls": paz.get("souls"),
        "shikigami": paz.get("shikigami"),
        "paz_captured_at": paz.get("captured_at"),
        "paz_completeness": paz.get("completeness"),
        "paz_snapshot": paz.get("snapshot_file"),
    }
    state = S.merge(WB, config, serial=serial, profile=paz.get("account_name"),
                    readings=readings,
                    raw={"paz": {k: paz.get(k) for k in
                                 ("snapshot_file", "captured_at", "completeness", "account_name",
                                  "items_raw_count", "realm_cards", "souls", "shikigami", "extras")},
                         "paz_extras": paz.get("extras")},
                    meta=meta, required=S.PAZ_REQUIRED, source="paz.snapshot")
    write_queue(config=config, status=state["status"], pending=False, note="",
                error=state.get("last_error"))
    log(f"[{config}] paz.snapshot status={state['status']} complete={state['complete']} "
        f"jade={readings['jade']['value']} coin={readings['coin']['value']} ap={readings['ap']['value']} "
        f"skin={readings['skin_token']['value']} ofuda={readings['mystery_amulet']['value']} "
        f"s_jade={readings['s_jade']['value']} gold_ofuda={readings['gold_ofuda']['value']} "
        f"honor={readings['honor']['value']} medal={readings['medal']['value']} "
        f"contrib={readings['contrib']['value']} scale={readings['scale']['value']} "
        f"demon_soul={readings['demon_soul']['value']} realm_cards={paz.get('realm_cards')} "
        f"paz_at={paz.get('captured_at')}")
    return True


def collect(config: str, serial: str, paz_max_age: int = 0, allow_refresh: bool = True) -> bool:
    import asset_ui_reader as A

    # 0) 自读优先：注入我们自己的载荷直接读（不依赖平安志 App）
    if SELF_READ_ENABLED and collect_self(config, serial):
        return True

    # 1) 退回平安志快照（未安装则跳过，不产生噪声日志）
    if paz_available() and collect_paz(config, serial, max_age=paz_max_age,
                                       allow_refresh=allow_refresh):
        return True

    if not A.ensure_device(serial):
        log(f"[{config}] device {serial} offline")
        S.merge(WB, config, serial=serial, error=f"设备不在线: {serial}", source="page-collect")
        write_queue(config=config, status="failed", pending=False, error="device offline")
        return False

    if not UI_FALLBACK_ENABLED:
        # 自读失败时默认不做 UI 兜底：UI 采集会点屏幕，5 分钟一轮会干扰正在运行的任务
        log(f"[{config}] ui fallback disabled (set OAS_ASSET_UI_FALLBACK=1 to enable)")
        S.merge(WB, config, serial=serial, error="自读失败且 UI 兜底已关闭", source="oas.reader")
        write_queue(config=config, status="failed", pending=False, error="self-read failed, ui fallback off")
        return False

    try:
        return _collect_ui(config, serial, A)
    except Exception as exc:  # noqa: BLE001
        # UI 兜底链很脆（截图文件缺失/截图超时等），不能让它把整轮/守护打挂
        log(f"[{config}] ui-collect exception: {type(exc).__name__}: {exc}")
        S.merge(WB, config, serial=serial, error=f"UI 采集异常: {type(exc).__name__}", source="page-collect")
        write_queue(config=config, status="failed", pending=False, error=f"ui exception: {type(exc).__name__}")
        return False


def _collect_ui(config: str, serial: str, A) -> bool:
    in_court = A.ensure_courtyard(serial)
    if not in_court:
        log(f"[{config}] not in courtyard, skip")
        S.merge(WB, config, serial=serial, error="不在庭院", source="page-collect")
        write_queue(config=config, status="failed", pending=False, error="not in courtyard")
        return False

    readings, raw = {}, {}
    court = A.read_courtyard(serial)
    for _ in range(3):
        if court.get("jade") is not None or court.get("jade_approx") is not None:
            break
        time.sleep(2)
        court = A.read_courtyard(serial)
    raw["court"] = court
    if court.get("jade") is not None:
        readings["jade"] = {"value": court["jade"], "precision": "exact",
                            "source": "page.courtyard.topbar", "error": None}
    elif court.get("jade_approx") is not None:
        # 勾玉上万后只显示「11万」这种简写：只能记 approx，不能算完整成功
        readings["jade"] = {"value": court["jade_approx"], "precision": "approx",
                            "source": "page.courtyard.topbar", "error": None,
                            "text": court.get("jade_text")}
    else:
        # 失败项不带 precision：避免把上次的 approx 值升格成 exact
        readings["jade"] = {"value": None, "source": "page.courtyard.topbar",
                            "error": "顶栏未读到勾玉"}
    if isinstance(court.get("coin_text"), str):
        readings["coin"] = {"value": None, "precision": "approx",
                            "source": "page.courtyard.topbar",
                            "error": "只有简写 " + court["coin_text"]}

    ap_val = A.read_stamina_tooltip(serial)
    if ap_val is None:
        A.tap(serial, A.TAP_BLANK)
        time.sleep(1)
        ap_val = A.read_stamina_tooltip(serial)
    raw["ap_tooltip"] = ap_val
    readings["ap"] = {"value": ap_val, "precision": "exact", "source": "page.tooltip.stamina",
                      "error": None if ap_val is not None else "弹层未读到「已拥有」"}
    A.tap(serial, A.TAP_BLANK)

    shop = A.read_shop_bar(serial, court_jade=court.get("jade"))
    raw["shop"] = shop
    shop_err = shop.get("error")
    for key, skey in (("skin_token", "skin_token"), ("mystery_amulet", "ofuda"), ("s_jade", "s_jade")):
        val = shop.get(skey)
        rec = {"value": val, "source": "page.shop.topbar",
               "error": None if val is not None else (shop_err or "商店顶栏未读到")}
        if val is not None:
            rec["precision"] = "exact"
        readings[key] = rec

    # 读完商店必须回庭院：否则下一轮从商店页起，会被判"不在庭院"（本轮实测踩过）
    back = A.ensure_courtyard(serial)
    raw["back_to_courtyard"] = back
    if not back:
        log(f"[{config}] WARN 读完商店没能回到庭院")

    state = S.merge(WB, config, serial=serial, profile=court.get("profile_text"),
                    readings=readings, raw=raw, source="page-collect")
    write_queue(config=config, status=state["status"], pending=False, note="",
                error=state.get("last_error"))
    log(f"[{config}] page-collect status={state['status']} complete={state['complete']} "
        f"jade={readings['jade']['value']}({readings['jade']['precision']}) ap={ap_val} "
        f"skin={shop.get('skin_token')} ofuda={shop.get('ofuda')} s_jade={shop.get('s_jade')} "
        f"profile={court.get('profile_text')} back={back}")
    return state["complete"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=1800)
    ap.add_argument("--tick", type=int, default=15)
    ap.add_argument("--config", default="", help="只采集指定配置（默认所有已知配置）")
    ap.add_argument("--paz-max-age", type=int, default=1500,
                    help="平安志快照超过该秒数就先触发它重新采集（默认 1500；0=不刷新）")
    ap.add_argument("--no-paz-refresh", action="store_true", help="只读快照，不触发平安志重新采集")
    args = ap.parse_args()

    from filelock import FileLock

    log(f"daemon v3 start interval={args.interval}s configs={configs()} "
        f"paz_max_age={args.paz_max_age}s refresh={not args.no_paz_refresh}")
    if paz_available():
        try:
            import paz_refresh
            ens = paz_refresh.ensure_paz()
            log(f"paz ensure ok={ens.get('ok')} started={ens.get('started')} err={ens.get('error')}")
        except Exception as exc:  # noqa: BLE001
            log(f"paz ensure error: {type(exc).__name__}: {exc}")
    else:
        log("paz not installed -> skip paz channel (self-read + UI only)")
    last = {cfg: time.time() for cfg in configs()}

    while True:
        try:
            if REQ.exists():
                try:
                    req = json.loads(REQ.read_text(encoding="utf-8-sig"))  # 兼容 BOM：PowerShell Set-Content -Encoding UTF8 会写 BOM
                except Exception:
                    req = {}
                config = (req.get("config") or "").strip()
                if not config:
                    REQ.unlink(missing_ok=True)
                    time.sleep(args.tick)
                    continue
                serial = (req.get("serial") or "").strip() or config_serial(config)
                # 自读通道不需要空闲画面（注入+读约 35 s，不改游戏状态）；
                # 只有要退回 UI 采集（要独占画面点按钮）才排队等空闲。
                if (not SELF_READ_ENABLED) and (not paz_ready(config)) and instance_busy(config):
                    write_queue(config=config, pending=True, status="queued", serial=serial,
                                requested_at=req.get("requested_at"),
                                note="托管运行中，等空闲（UI 采集需要画面）")
                    time.sleep(args.tick)
                    continue
                write_queue(config=config, pending=True, status="collecting", serial=serial,
                            requested_at=req.get("requested_at"), started_at=S.now())
                with FileLock(str(LOCK), timeout=5):
                    manual = str(req.get("mode") or "manual") == "manual"
                eff_age = 1 if manual else args.paz_max_age
                ok = collect(config, serial, paz_max_age=eff_age, allow_refresh=not args.no_paz_refresh)
                REQ.unlink(missing_ok=True)
                last[config] = time.time()
                log(f"[{config}] served request ok={ok}")
                continue

            targets = [args.config] if args.config else configs()
            for cfg in targets:
                if time.time() - last.get(cfg, 0) < args.interval:
                    continue
                if (not paz_ready(cfg)) and instance_busy(cfg):
                    write_queue(config=cfg, pending=False, status="queued",
                                note="托管运行中，自动轮次推迟（无平安志快照可读）")
                    last[cfg] = time.time() - args.interval + 60
                    continue
                serial = config_serial(cfg)
                write_queue(config=cfg, pending=True, status="collecting", serial=serial,
                            started_at=S.now())
                with FileLock(str(LOCK), timeout=5):
                    collect(cfg, serial, paz_max_age=args.paz_max_age, allow_refresh=not args.no_paz_refresh)
                last[cfg] = time.time()
                log(f"[{cfg}] auto collected")
        except KeyboardInterrupt:
            log("daemon stop")
            return 0
        except Exception:
            log("loop error:\n" + traceback.format_exc())
        time.sleep(args.tick)


if __name__ == "__main__":
    raise SystemExit(main())
