# 资产自读通道 · 交付与验收（2026-11-22）

目标：资产面板的 12 项精确值由 OAS 自己读取，**不再依赖平安志运行**。
本文是交付清单 + 验收证据，操作手册见 `docs/OAS_资产自读通道.md`。

## 一、交付清单

| # | 交付项 | 文件 | 测试 | 证据 |
|---|---|---|---|---|
| 1 | 读取载荷（内含 Python，由游戏自带 CPython 执行） | `dev_tools/liboas-reader.so`（210,096 B） | `selftest_selfread.py` 第 13–17 项：可解出 `def monitor_snapshot`、内容含 `oas_assets.json`、已不含平安志结果路径 | 17/17 PASS |
| 2 | 通用注入器（ptrace + dlopen，仓库自带） | `dev_tools/bin/yys-mumu-injector`（11,504 B） | `selftest_selfread.py` 第 11–12 项：在位且大小正确 | 17/17 PASS |
| 3 | 编排模块：push → 注入 → 拉取 → 解析 16 段 → 写面板 | `tasks/Workbench/oas_reader.py` | `selftest_selfread.py` 第 1–10 项（导入/字段表/16 段解析/错误 JSON 判失败） | 17/17 PASS |
| 4 | 21 个货币 → 面板字段的映射 | `oas_reader.CURRENCY_FIELDS` | 同上第 2–4 项（长度 21、唯一、前三位 coin/jade/action_point） | 17/17 PASS |
| 5 | 守护进程自读优先通道 | `dev_tools/asset_collect_daemon.py`（`SELF_READ_ENABLED` + `collect_self()`） | 实机触发一次手动请求 | `log/asset_collect.log`：`[oas1] self-read ok status=ok complete=True jade=278 coin=26991897` |
| 6 | 操作手册 | `docs/OAS_资产自读通道.md` | — | — |

## 二、实机验收（双号）

| 账号 | 配置 / 设备 | 结果 |
|---|---|---|
| DemoRole（2441356，全平台正式服110，Lv60） | oas1 / `127.0.0.1:16384` | `complete=True`；coin 26,991,897 · jade 278 · ap 20,760 · honor 1,270 · medal 274 · skin_token 827 · gold_ofuda 30 · scale 2,715 · demon_soul 212；式神 1441 / 御魂 3226 / 结界卡 95 |
| 半生緣（105585081，全平台正式服31，Lv60） | oas3 / `127.0.0.1:16416` | `complete=True`；coin 39,242,099 · jade 114,976 · ap 35,176 · honor 3,420 · medal 36,533 · skin_token 671 · gold_ofuda 126 · scale 2,827 · demon_soul 1,569；式神 3211 / 御魂 3505 / 结界卡 737 |

面板文件 `config/workbench/assets/<config>.json` 中每项 `precision=exact`、`source=oas.reader`，
`meta` 带 `heroes / souls / realm_cards / source`。

## 三、复现命令

```powershell
cd D:\3D\YYS2\OnmyojiAutoScript-easy-install-1
$env:PYTHONPATH='D:\3D\YYS2\OnmyojiAutoScript-easy-install-1'

# 离线自检（不碰设备）
.\toolkit\python.exe tasks\Workbench\selftest_selfread.py

# 实机读取（游戏需已进入（过了标题页）、adb 在线、su 可用）
.\toolkit\python.exe tasks\Workbench\oas_reader.py --serial 127.0.0.1:16384 --once --write oas1
.\toolkit\python.exe tasks\Workbench\oas_reader.py --serial 127.0.0.1:16416 --once --write oas3
```

## 四、验收判据

1. `selftest_selfread.py` 退出码 0（17 项全 PASS）；
2. 实机两次读取都返回 `ok: true` 且面板 `complete=True`、`source=oas.reader`；
3. 守护日志出现 `self-read ok`，且**不再**出现 `paz refresh` 作为首选路径；
4. 关掉平安志进程后重跑步骤 2 仍成功（注入器与载荷都已 vendor 进仓库）。

## 五、已知边界

- 必须在**游戏内**（过了标题页/加载中即可；战斗中、战斗结算页、町中、庭院都能读——2026-11-22 在「战斗失败」结算页实测 jade=115059）；停在标题页时返回
  `{"error": "Player data not available."}`，属正常拒绝。
- 需要设备 root（`su 0`）与 adb 在线。
- 面板字段只有 12 个（`asset_store.ASSET_KEYS`），载荷多读的 `ofuda / sp_skin_token / reverse_scale …`
  属于快照里存在但面板未展示的项，不算缺失。
- 自研注入器（替换那个 11.5 KB 二进制）尚未做：本机 NDK 的 `clang++.exe` 缺 DLL（退出码 `0xC0000135`），
  机器上也没有 go/rustc/cl/gcc；vendor 之后已不构成阻塞。