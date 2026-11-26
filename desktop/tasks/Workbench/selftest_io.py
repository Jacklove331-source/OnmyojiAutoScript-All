# This file uses the following encoding: utf-8
"""数据读取 + 小工具的回归（离线为主，实机部分只做信息性输出）。

用法：
    .\\toolkit\\python.exe tasks\\Workbench\\selftest_io.py
    .\\toolkit\\python.exe tasks\\Workbench\\selftest_io.py --live 127.0.0.1:16448
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tasks.Workbench import ingest, tools  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def main() -> int:
    # ---------- OCR 行解析 ----------
    lines = ["破势 六号 暴击伤害 15级", "暴击 +22%", "攻击加成 +15%",
             "速度 +8", "生命加成 +6%", "针女 二号 速度 0级", "暴击 +9%"]
    souls, unparsed = ingest.parse_ocr_lines(lines)
    check("ocr.soul_count", len(souls) == 2, str(len(souls)))
    check("ocr.set_name", souls[0]["set"] == "破势", souls[0]["set"])
    check("ocr.slot", souls[0]["slot"] == 6, str(souls[0]["slot"]))
    check("ocr.level", souls[0]["level"] == 15, str(souls[0]["level"]))
    check("ocr.substats", len(souls[0]["substats"]) == 4, str(souls[0]["substats"]))
    check("ocr.main_attr", souls[0]["main"] == "暴击伤害", str(souls[0]["main"]))
    check("ocr.substat_value", souls[0]["substats"].get("暴击") == 22.0,
          str(souls[0]["substats"].get("暴击")))
    check("ocr.second_soul", souls[1]["set"] == "针女" and souls[1]["slot"] == 2,
          str(souls[1]))

    # ---------- 档案导入 ----------
    tmp = tempfile.mkdtemp(prefix="wbio_")
    try:
        a = os.path.join(tmp, "a.json")
        json.dump({"profile": {"name": "主号", "server": "官服"},
                   "souls": [{"set_name": "破势", "position": 6, "main_attribute": "crit_damage",
                              "level": 15, "sub_attributes": {"crit_rate": 9.0}}],
                   "shikigami": [{"shikigami_id": "609", "star": 6, "level": 40,
                                  "skills": [{"skillId": 6091, "level": 5}, {"skillId": 6092, "level": 4}, {"skillId": 6093, "level": 5}]}]},
                  open(a, "w", encoding="utf-8"), ensure_ascii=False)
        arch = ingest.load_archive(a)
        check("json.source", arch["source"] == "json")
        check("json.profile", arch["profile"]["name"] == "主号")
        check("json.souls", len(arch["souls"]) == 1, str(arch["souls"]))
        check("json.soul_slot", arch["souls"][0]["slot"] == 6, str(arch["souls"][0]))
        check("json.owned", len(arch["owned"]) == 1, str(arch["owned"]))
        check("json.owned_skills",
              arch["owned"][0].get("skills") == [{"id": "6091", "level": 5}, {"id": "6092", "level": 4}, {"id": "6093", "level": 5}],
              str(arch["owned"][0]))
        check("json.norm_skills_formats",
              ingest.skill_levels_of('[{"skillId":2001,"level":5},{"skillId":2002,"level":3}]') == [5, 3]
              and ingest.skill_levels_of([[2241, 5], [2242, 1]]) == [5, 1]
              and ingest.skill_levels_of([1, 5, 5]) == [1, 5, 5])

        b = os.path.join(tmp, "b.json")
        json.dump([{"set": "心眼", "slot": 3, "level": 0}], open(b, "w", encoding="utf-8"),
                  ensure_ascii=False)
        arch2 = ingest.load_archive(b)
        check("json.list_form", len(arch2["souls"]) == 1, str(arch2["souls"]))

        note = ingest.save_frames_note(os.path.join(tmp, "out"), "screen", ["f1.png"])
        check("ingest.evidence_file", os.path.exists(note))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------- 小工具：离线解析 ----------
    sample = json.dumps({
        "0": {"index": "0", "name": "MuMu安卓设备-1", "is_android_started": False,
              "is_process_started": False},
        "2": {"index": "2", "name": "OAS安卓独立测试", "is_android_started": True,
              "adb_port": 16448, "player_state": "start_finished"},
    })
    inst = tools.parse_instances(sample)
    check("tools.parse_count", len(inst) == 2, str(len(inst)))
    check("tools.parse_running", [i for i in inst if i["index"] == "2"][0]["running"] is True)
    check("tools.parse_port", [i for i in inst if i["index"] == "2"][0]["adb_port"] == 16448)

    # ---------- 虚拟定位：只验证"识别不到就如实返回" ----------
    det = tools.detect_mock_app("127.0.0.1:1")   # 连不上的 serial，不应抛异常
    check("tools.mock_detect_shape", isinstance(det, dict) and "ok" in det, str(det))
    loc = tools.set_location("127.0.0.1:1", 30.0, 120.0, action="")
    check("tools.location_no_action_fails",
          loc.get("ok") is False and "action" in (loc.get("reason") or ""), str(loc)[:120])

    # ---------- 实机（信息性，不计入失败） ----------
    serial = None
    if "--live" in sys.argv:
        serial = sys.argv[sys.argv.index("--live") + 1]
    # ---------- 实机 OCR（有 ppocronnx 且给了 --live 才跑） ----------
    try:
        from tasks.Workbench.ingest import screen as S
    except Exception:
        S = None
    if S is not None and S.available() and serial:
        frame = os.path.join(tempfile.gettempdir(), "wb_ocr_frame.png")
        cap = S.capture(serial, frame)
        if cap.get("ok"):
            rows = S.ocr_rows(frame)
            check("ocr.frame_rows", len(rows) >= 5, str(len(rows)))
            check("ocr.frame_texts", any(r["text"].strip() for r in rows))
            hits = S.find_text(rows, "式神")
            print("INFO  find_text('式神') -> %d hit(s)" % len(hits))
        else:
            print("INFO  screen capture failed: %s" % cap)

    live = tools.list_instances()
    print("INFO  muMu instances: %s" % json.dumps(
        live.get("instances", [])[:4], ensure_ascii=False))
    if serial:
        st = tools.game_state(serial)
        print("INFO  game_state(%s): running=%s focus=%s" % (serial, st["running"], st["focus"]))
        out = os.path.join(tempfile.gettempdir(), "wb_live_frame.png")
        cap = tools.capture(serial, out)
        print("INFO  capture: %s" % json.dumps(cap, ensure_ascii=False))
        # PNG 体积随画面内容变化（加载页大面积纯色时只有 ~70KB），只校验是有效图片而非固定大小
        check("live.capture_bytes", cap.get("bytes", 0) > 20000, str(cap.get("bytes")))

    print("\nWORKBENCH IO SELFTEST: %s   TOTAL %d PASS %d FAIL %d" % (
        "PASSED" if FAIL == 0 else "FAILED", PASS + FAIL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
