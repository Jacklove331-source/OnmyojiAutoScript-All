# -*- coding: utf-8 -*-
"""资产自读通道的离线回归（不碰设备、不碰游戏）。

跑法（仓库根目录）：
    .\\toolkit\\python.exe tasks\\Workbench\\selftest_selfread.py
退出码 0 = 全绿。

覆盖 6 项：
 1. oas_reader 可导入；
 2. 21 个货币字段名唯一且前三项是 coin/jade/action_point；
 3. 16 段数组 → 解析出 account + 21 项 items；
 4. 脚本返回错误 dict 时不会被当成成功；
 5. 仓库自带的两件二进制在位且大小对（注入器 11,504 / 载荷 .so 210,096）；
 6. 仓库里那个 .so 的载荷能解出来，并且内容是我们的（含 monitor_snapshot + oas_assets.json）。
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SO = os.path.join(REPO, "dev_tools", "liboas-reader.so")
INJ = os.path.join(REPO, "dev_tools", "bin", "yys-mumu-injector")

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, detail))


def decrypt_payload(path):
    """xorshift64(13,7,17) 解 .rodata+0x690 的载荷（种子在 .data 0x32c58）。"""
    data = open(path, "rb").read()
    seed = int.from_bytes(data[0x32C58:0x32C60], "little")
    length = 0x2F7DD
    cipher = data[0x1250:0x1250 + length]
    m64 = (1 << 64) - 1
    out = bytearray(length)
    x = seed
    for i in range(length):
        x ^= (x << 13) & m64
        x ^= x >> 7
        x ^= (x << 17) & m64
        out[i] = cipher[i] ^ (x & 0xFF)
    return bytes(out)


def main():
    from tasks.Workbench import oas_reader as R

    check("oas_reader import", True)

    fields = list(getattr(R, "CURRENCY_FIELDS", []))
    check("CURRENCY_FIELDS 长度=21", len(fields) == 21, str(len(fields)))
    check("CURRENCY_FIELDS 唯一", len(set(fields)) == len(fields), str(fields[:3]))
    check("前三位 = coin/jade/action_point",
          fields[:3] == ["coin", "jade", "action_point"], str(fields[:3]))

    section0 = [2441356, 10123, "DemoRole", 60, "全平台正式服110", {"globals_probe": {"app_version": "2.8.78"}}]
    currencies = list(range(21))
    synthetic = [section0, currencies] + [None] * 14
    shaped = R._shape(synthetic)
    check("16 段解析 ok", shaped.get("ok") is True)
    check("账号名解析", (shaped.get("account") or {}).get("name") == "DemoRole",
          str(shaped.get("account")))
    check("items 21 项", len(shaped.get("items") or {}) == 21,
          str(len(shaped.get("items") or {})))
    check("items.coin 取到", (shaped.get("items") or {}).get("coin") == 0)

    err = R._shape({"error": "Player data not available.", "tb": "Traceback ..."})
    check("错误 JSON 判失败", err.get("ok") is False)
    check("错误原因透传", err.get("error") == "Player data not available.",
          str(err.get("error")))

    check("注入器在位", os.path.exists(INJ), INJ)
    if os.path.exists(INJ):
        check("注入器大小 11504", os.path.getsize(INJ) == 11504, str(os.path.getsize(INJ)))
    check("载荷 .so 在位", os.path.exists(SO), SO)
    if os.path.exists(SO):
        check("载荷 .so 大小 210096", os.path.getsize(SO) == 210096, str(os.path.getsize(SO)))
        plain = decrypt_payload(SO)
        text = plain.decode("utf-8", "ignore")
        check("载荷可解出 monitor_snapshot", "def monitor_snapshot" in text)
        check("载荷是我们的（写 oas_assets.json）", "oas_assets.json" in text,
              hashlib.sha256(plain[:-1]).hexdigest()[:16])
        check("载荷已不含平安志结果路径", "yys-mumu-snapshot-result.json" not in text.split("get_result_path")[1][:400])

    print("\nTOTAL %d PASS %d FAIL %d" % (PASS + FAIL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
