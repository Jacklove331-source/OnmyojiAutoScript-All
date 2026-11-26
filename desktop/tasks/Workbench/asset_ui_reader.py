"""账号资产：OAS 自己从游戏界面读（不依赖平安志，不碰登录/选区/买/确认）。

只做「点开顶部资源图标 / 打开商店」这类只读导航，绝不点登录、选区、选角、购买、确认。

已标定的精确来源（2026-11-21 实测，1280x720 横屏，override rotation=1）：
  * 庭院顶栏：金币(万)/勾玉(精确或万)/体力(万) —— 三个数值按 x 从小到大固定为 金币→勾玉→体力
    oas1 实测 [534,22]2667万  [749,20]132+  [906,20]2.0万
    oas3 实测 [533,20]3828万  [736,22]11万  [908,22]3.5万
  * 点顶部「体力」图标 → 弹层「体力 已拥有 20475」= 精确
  * 商店页面（热门推荐/皮肤屋/杂货铺…）顶栏：皮肤券 / 符咒 / 魂玉 / 勾玉 = 精确
    oas1 实测 x 顺序 820(674) 6(863) 0(1037) 132(1180)
  * 返回：左上返回箭头 (35,35)，**每点一次退一层**（热门推荐→商店→庭院），实测有效；
    `input keyevent 4` 在该页面无效（保留为第 3 次兜底）。

用法：
  toolkit\\python.exe tasks\\Workbench\\asset_ui_reader.py --serial 127.0.0.1:16385 --dry
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _find_adb() -> str:
    cands = [
        os.environ.get("OAS_ADB") or "",
        r"E:\MuMuPlayer\nx_device\12.0\shell\adb.exe",
        str(Path(sys.prefix) / "Lib" / "site-packages" / "adbutils" / "binaries" / "adb.exe"),
        "adb",
    ]
    for c in cands:
        if c and (Path(c).exists() or c == "adb"):
            return c
    return "adb"


ADB = _find_adb()
OUT = REPO / "config" / "workbench" / "asset_live.json"
FRAME = Path(r"D:\3D\YYS2\_tools\_asset_frame.png")

TAP_STAMINA = (950, 30)
TAP_SHOP = (715, 655)
TAP_BLANK = (1200, 620)
BACK_XY = (35, 35)              # 左上返回箭头（实测每点一次退一层）

COURT_MARKERS = ("探索", "町中")
SHOP_MARKERS = ("周边屋", "寄售屋", "插画屋", "秘卷屋", "秘魂屋", "皮肤屋", "杂货铺", "魂玉屋", "礼包屋")
SHOP_BAR_BOX = (450, 0, 830, 60)     # 商店顶栏资源区（x,y,w,h）


def sh(*cmd: str) -> bool:
    """adb 调用不因瞬时失败抛异常（掉线/过载时返回 False）。"""
    try:
        r = subprocess.run(list(cmd), check=False, capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def ensure_device(serial: str) -> bool:
    """设备不在线时尝试 adb connect 一次。"""
    if sh(ADB, "-s", serial, "shell", "echo", "ok"):
        return True
    sh(ADB, "connect", serial)
    return sh(ADB, "-s", serial, "shell", "echo", "ok")


def grab(serial: str) -> Path:
    sh(ADB, "-s", serial, "shell", "screencap", "-p", "/sdcard/_a.png")
    sh(ADB, "-s", serial, "pull", "/sdcard/_a.png", str(FRAME))
    return FRAME


def tap(serial: str, xy: Tuple[int, int]) -> None:
    sh(ADB, "-s", serial, "shell", f"input tap {xy[0]} {xy[1]}")


def ocr(serial: str) -> List[Dict[str, Any]]:
    from asset_collector import ocr_boxes

    return ocr_boxes(grab(serial))


def roi_rows(serial: str, box: List[int], scale: int = 3) -> List[Dict[str, Any]]:
    """抓一帧后对 ROI 放大 OCR（单位数在原尺寸上常漏读）。"""
    from asset_collector import ocr_region

    return ocr_region(grab(serial), box, scale)


def rows_in(rows, x0, y0, x1, y1):
    out = []
    for r in rows:
        x, y, w, h = r["box"]
        cx, cy = x + w / 2, y + h / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            out.append(r)
    return out


def top_values(rows) -> List[Dict[str, Any]]:
    """庭院顶栏资源数值：只取 y<60 且 x>=450 的，按 x 升序 = [金币, 勾玉, 体力]。"""
    vals: List[Dict[str, Any]] = []
    for r in rows:
        x, y, w, h = r["box"]
        if y >= 60 or x < 450:
            continue
        t = r["text"].replace(",", "").strip()
        m = re.fullmatch(r"(\d+(?:\.\d+)?)([万亿])?\s*[+＋]?", t)
        if not m:
            continue
        unit = m.group(2) or ""
        mult = {"万": 10000, "亿": 100000000}.get(unit, 1)
        vals.append({
            "x": x, "text": r["text"], "unit": unit,
            "num": int(round(float(m.group(1)) * mult)),
            "exact": unit == "",
        })
    vals.sort(key=lambda v: v["x"])
    return vals


def looks_courtyard(rows) -> bool:
    """庭院判定：命中「探索/町中」，或顶栏呈 金币(万)+勾玉+体力(万) 三段固定排布。"""
    texts = "|".join(r["text"] for r in rows)
    if any(m in texts for m in COURT_MARKERS):
        return True
    vals = top_values(rows)
    if len(vals) >= 3 and vals[0]["unit"] == "万" and vals[2]["unit"] == "万":
        return True
    return False


def ensure_courtyard(serial: str, tries: int = 8) -> bool:
    """把界面拉回庭院：点左上返回箭头（每点一次退一层），最多 tries 次。

    商店/热门推荐这类子页面 `keyevent 4` 无效，所以每 3 次才补一次兜底。
    连续两次读到非庭院才动手，避免在庭院里误点左上角头像。
    """
    for attempt in range(tries):
        rows = ocr(serial)
        if looks_courtyard(rows):
            return True
        rows2 = ocr(serial)
        if looks_courtyard(rows2):
            return True
        rows = rows2
        texts = "|".join(r["text"] for r in rows)
        if ("设置" in texts) and (("音量" in texts) or ("画质" in texts)):
            tap(serial, (1110, 100))             # 设置弹窗：点右上关闭
            time.sleep(2)
            continue
        if any(m in texts for m in SHOP_MARKERS):
            tap(serial, TAP_BLANK)               # 关掉可能的购买/预览弹层
            time.sleep(1)
        tap(serial, BACK_XY)
        if attempt % 3 == 2:
            sh(ADB, "-s", serial, "shell", "input keyevent 4")
        time.sleep(2.5 if attempt < 4 else 4)
    return False


CLOCK_RE = re.compile(r"^\d{1,2}:\d{2}$")


def _pick_profile(cands: List[Dict[str, Any]]) -> Optional[str]:
    """从昵称候选里挑一个：优先含中文、分数≥0.85、排除时钟/纯数字。"""
    good = []
    for r in cands:
        t = (r["text"] or "").strip()
        if not t or CLOCK_RE.match(t):
            continue
        if r.get("score", 0) < 0.85:
            continue
        if re.fullmatch(r"\d+", t):
            continue
        good.append(r)
    if not good:
        return None
    good.sort(key=lambda r: (0 if re.search(r"[\u4e00-\u9fff]", r["text"]) else 1, -r["score"]))
    return good[0]["text"].strip()


def read_courtyard(serial: str) -> Dict[str, Any]:
    rows = ocr(serial)
    res: Dict[str, Any] = {"raw_top": [r["text"] for r in rows_in(rows, 450, 0, 1280, 60)]}
    res["page"] = "courtyard" if looks_courtyard(rows) else "unknown"
    vals = top_values(rows)
    res["digits_top"] = [v["num"] for v in vals]
    if len(vals) >= 3:
        res["coin_text"] = vals[0]["text"]
        res["ap_text"] = vals[2]["text"]
        jade = vals[1]
        if jade["exact"]:
            res["jade"] = jade["num"]
        else:
            res["jade_approx"] = jade["num"]
            res["jade_text"] = jade["text"]
        res["third_text"] = vals[-1]["text"]
    elif len(vals) == 2:
        res["coin_text"] = vals[0]["text"]
        res["ap_text"] = vals[1]["text"]
    # 昵称：先按原尺寸读，读不到再放大 4x（拉丁短名/e· 这类小字必须放大）
    nick = rows_in(rows, 40, 25, 300, 75)
    profile = _pick_profile(nick)
    if profile is None:
        profile = _pick_profile(roi_rows(serial, [60, 15, 240, 55], 4))
    if profile:
        res["profile_text"] = profile
    return res


def read_stamina_tooltip(serial: str) -> Optional[int]:
    tap(serial, TAP_STAMINA)
    time.sleep(2.5)
    rows = ocr(serial)
    for r in rows:
        m = re.search(r"已拥有[:：]\s*(\d[\d,]*)", r["text"])
        if m:
            return int(m.group(1).replace(",", ""))
    for r in rows:
        if "已拥有" in r["text"]:
            nums = re.findall(r"\d[\d,]*", r["text"])
            if nums:
                return int(nums[-1].replace(",", ""))
    return None


def tap_text(serial: str, text: str, nth: int = 0) -> bool:
    """OCR 找到文字再点它的中心；找不到返回 False。比盲点坐标稳。"""
    rows = ocr(serial)
    hits = []
    for r in rows:
        if text in r["text"]:
            x, y, w, h = r["box"]
            hits.append((y, x + w // 2, y + h // 2))
    if not hits:
        return False
    hits.sort()
    _, cx, cy = hits[min(nth, len(hits) - 1)]
    tap(serial, (cx, cy))
    return True


def ordered_bar_numbers(rows, box: List[int], min_gap: int = 120) -> List[Dict[str, Any]]:
    """顶栏资源数字：过滤纯数字行 → 按 x 排序 → 贪心去重（间隔 < min_gap 视为同一列的杂读）。

    「+」图标常被误读成单位数（实测商店顶栏 [1123,18]'6' 其实是勾玉的加号），
    真实四列间隔 140~190，因此用 min_gap=120 能稳定去掉这类杂读。
    """
    x0, y0, w, h = box
    cands: List[Dict[str, Any]] = []
    for r in rows:
        bx, by, bw, bh = r["box"]
        cx, cy = bx + bw / 2, by + bh / 2
        if not (x0 <= cx <= x0 + w and y0 <= cy <= y0 + h):
            continue
        t = (r["text"] or "").replace(",", "").strip()
        if not re.fullmatch(r"\d{1,9}", t):
            continue
        cands.append({"x": bx, "num": int(t), "text": t, "score": r.get("score", 0.0)})
    cands.sort(key=lambda c: (c["x"], -c["score"]))
    out: List[Dict[str, Any]] = []
    for c in cands:
        if out and c["x"] - out[-1]["x"] < min_gap:
            if c["score"] > out[-1]["score"]:
                out[-1] = c
            continue
        out.append(c)
    return out


def map_shop_bar(nums: List[Dict[str, Any]], court_jade: Optional[int] = None):
    """把商店顶栏数字映射成 皮肤券/符咒/魂玉/勾玉：**以最右一列（勾玉）为锚**。

    规则（实测 oas1 [534]/oas3 [533] 两个账号）：四列 x≈674/863/1037/1180，最右是勾玉。
    实测 23:39 那轮 OCR 多读了一个 "49"（页面其它数字），若按"取前四列"会把
    皮肤券读成 49、符咒读成 820 —— 所以只认「以最右列结尾的最后四列」，
    并且最右列的数值必须与庭院勾玉一致，否则整组不写（保留上次成功值）。

    返回 (mapping, None) 或 (None, 失败原因)。
    """
    if len(nums) < 4:
        return None, f"顶栏只认出 {len(nums)} 列"
    last = nums[-1]
    if last["x"] < 1050:
        return None, f"最右列 x={last['x']} 不像勾玉位"
    if court_jade is not None and last["num"] != court_jade:
        return None, f"勾玉列 {last['num']} != 庭院 {court_jade}"
    window = nums[-4:]
    if any(window[i + 1]["x"] - window[i]["x"] < 110 for i in range(3)):
        return None, "列间距异常"
    return ({"skin_token": window[0]["num"], "ofuda": window[1]["num"],
             "s_jade": window[2]["num"], "jade": last["num"]}, None)


def read_shop_bar(serial: str, court_jade: Optional[int] = None) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    nums: List[Dict[str, Any]] = []
    mapped = None
    why = ""
    for attempt in range(3):
        if not tap_text(serial, "商店"):
            tap(serial, TAP_SHOP)
        time.sleep(6)
        rows = ocr(serial)
        nums = ordered_bar_numbers(rows, SHOP_BAR_BOX)
        mapped, why = map_shop_bar(nums, court_jade)
        if mapped is None and len(nums) < 4:
            # 单位数（符咒 6 / 魂玉 0）在原尺寸上常漏读：放大 3x 再读一次后合并
            nums = ordered_bar_numbers(rows + roi_rows(serial, SHOP_BAR_BOX, 3), SHOP_BAR_BOX)
            mapped, why = map_shop_bar(nums, court_jade)
        if mapped is not None:
            break
        tap(serial, TAP_BLANK)       # 关掉可能挡住的弹层再试
        time.sleep(2)
    out: Dict[str, Any] = {"raw": [n["text"] for n in nums],
                           "raw_x": [n["x"] for n in nums],
                           "raw_first_pass": [r["text"] for r in rows_in(rows, 450, 0, 1280, 60)]}
    if mapped is not None:
        out.update(mapped)
    else:
        out["error"] = f"商店顶栏校验失败：{why}"
    tap(serial, TAP_BLANK)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default="127.0.0.1:16385")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    _ = ensure_device

    payload: Dict[str, Any] = {
        "ok": False,
        "source": "oas-ui",
        "serial": args.serial,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "assets": {},
    }
    try:
        if not ensure_courtyard(args.serial):
            payload["status"] = "wrong_page"
            payload["assets"].update({"jade": None})
            payload["error"] = "cannot reach courtyard"
            print(json.dumps(payload, ensure_ascii=False, indent=1))
            return 3
        court = read_courtyard(args.serial)
        payload["profile"] = court.get("profile_text")
        payload["raw"] = {"court": court}
        if court.get("page") != "courtyard":
            payload["status"] = "wrong_page"
            payload["assets"].update({"jade": None})
        else:
            payload["assets"].update({
                "jade": court.get("jade"),
                "coin_text": court.get("coin_text"),
                "ap_text": court.get("ap_text"),
            })
            payload["assets"]["ap"] = read_stamina_tooltip(args.serial)
            tap(args.serial, TAP_BLANK)
            shop = read_shop_bar(args.serial)
            for k in ("skin_token", "ofuda", "s_jade"):
                if shop.get(k) is not None:
                    payload["assets"][k] = shop[k]
            payload["raw"] = {"court": court, "shop": shop}
            payload["ok"] = (payload["assets"].get("jade") is not None
                             and payload["assets"].get("ap") is not None)
            payload["status"] = "ok" if payload["ok"] else "partial"
            payload["back_to_courtyard"] = ensure_courtyard(args.serial)
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(payload, ensure_ascii=False, indent=1))
    if not args.dry and payload["ok"]:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"written -> {OUT}")
    return 0 if payload["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
