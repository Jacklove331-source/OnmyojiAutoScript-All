# -*- coding: utf-8 -*-
"""host 侧自检：oas_mobile/ai_recovery_history.build_payload

不依赖 Android/Chaquopy，也不需要 OAS 仓库：造三个 incidents 目录，覆盖
正常条目、类型脏数据、缺 anomaly.json（id 回退）三种情况，断言：
- 脏数据不抛异常，按默认值兜底
- entries 按 limit 截断，summary 统计**全量**（scope=all, scanned=3）
- total 是全量目录数

跑法：python test/host/test_ai_recovery_history.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "android", "app", "src", "main", "python"))

from oas_mobile.ai_recovery_history import build_payload  # noqa: E402


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def main():
    root = tempfile.mkdtemp(prefix="oas_ai_hist_")
    try:
        incidents = os.path.join(root, "log", "ai_recovery", "incidents")
        # 1) 正常：AI 介入并恢复
        a = os.path.join(incidents, "20261126_101010_aaaa1111")
        write(os.path.join(a, "anomaly.json"), {
            "type": "device_stuck", "task": "WeeklyPurchase", "page": "page_mall",
            "severity": "warning", "detail": "卡死", "incident_id": "aaaa1111",
            "created_at_iso": "2026-11-26 10:10:10"})
        write(os.path.join(a, "result.json"), {
            "outcome": "recovered", "message": "ok", "ai_called": True, "verified": True,
            "tokens": 1200, "cost_cny": 0.0024,
            "diagnosis": {"page": "商城", "cause": "点到导航栏边缘"},
            "actions_done": [{"action": "click", "ok": True}, {"action": "wait", "ok": False}]})
        # 2) 脏数据：tokens 带千分位、cost 非数字、diagnosis 是字符串、actions 是数字
        b = os.path.join(incidents, "20261126_111111_bbbb2222")
        write(os.path.join(b, "anomaly.json"), {"incident_id": "bbbb2222", "task": "Orochi"})
        write(os.path.join(b, "result.json"), {
            "outcome": "needs_human", "ai_called": True,
            "tokens": "1,234", "cost_cny": "abc",
            "diagnosis": "oops", "actions_done": 5})
        # 3) 缺 anomaly.json：id 从目录名回退（目录名里含下划线）
        c = os.path.join(incidents, "20261126_121212_inc_a1b2")
        write(os.path.join(c, "result.json"), {"outcome": "stopped", "ai_called": False})

        out = build_payload(root, limit=1, config="oas1")
        assert out["ok"] is True, out
        assert out["total"] == 3, out["total"]
        assert out["count"] == 1, out["count"]
        assert len(out["entries"]) == 1
        assert out["summary"]["scope"] == "all", out["summary"]
        assert out["summary"]["scanned"] == 3, out["summary"]
        assert out["summary"]["recovered"] == 1
        assert out["summary"]["needs_human"] == 1
        assert out["summary"]["stopped"] == 1
        assert out["summary"]["ai_calls"] == 2
        # 1200 + 0（"1,234" 解析失败兜底 0）+ 0 = 1200
        assert out["summary"]["tokens"] == 1200, out["summary"]["tokens"]
        assert out["summary"]["cost_cny"] == 0.0024, out["summary"]["cost_cny"]

        full = build_payload(root, limit=50)
        by_id = {e["id"]: e for e in full["entries"]}
        assert set(by_id) == {"aaaa1111", "bbbb2222", "inc_a1b2"}, by_id.keys()
        assert by_id["aaaa1111"]["actions"] == 2 and by_id["aaaa1111"]["actions_ok"] == 1
        assert by_id["aaaa1111"]["cause"] == "点到导航栏边缘"
        # 脏数据兜底
        assert by_id["bbbb2222"]["tokens"] == 0
        assert by_id["bbbb2222"]["cost_cny"] == 0.0
        assert by_id["bbbb2222"]["cause"] == ""
        assert by_id["bbbb2222"]["actions"] == 0
        # 缺 anomaly.json 时 id 取目录名时间戳之后的部分
        assert by_id["inc_a1b2"]["outcome"] == "stopped"

        # 4) 空目录 / 不存在的根目录都不炸
        for root2 in (os.path.join(root, "empty"), os.path.join(root, "nope")):
            os.makedirs(os.path.join(root2, "log", "ai_recovery", "incidents"), exist_ok=True)
            empty = build_payload(root2, limit=10)
            assert empty["total"] == 0 and empty["entries"] == [], empty

        print("test_ai_recovery_history: OK")
        print("  summary ->", json.dumps(out["summary"], ensure_ascii=False))
        print("  ids     ->", sorted(by_id))
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
