# 资产自读通道（不依赖平安志）

> 2026-11-22 落地并双号验证。资产面板（勾玉/金币/体力/魂玉/符咒/荣誉/勋章/贡献/皮肤券/千纸鹤·白/妖怪之魂/神秘符咒）
> 现在由 OAS 自己注入读取载荷取得，**平安志主程序、界面、CDP、落盘快照全部不参与**。

## 组成（都在本仓库内，卸载平安志不影响）

| 文件 | 说明 |
|---|---|
| `dev_tools/liboas-reader.so` | 读取载荷（210,096 B）。内含一段 Python，由游戏自带的 CPython 执行；输出写到游戏 cache 的 `oas_assets.json` |
| `dev_tools/bin/yys-mumu-injector` | 11,504 B 的通用注入器（纯 ptrace + `dlopen`），把上面那个 `.so` 加载进游戏进程 |
| `tasks/Workbench/oas_reader.py` | 编排：push → 注入 → 拉取 → 解析 16 段 → 写面板 store |

## 用法

```powershell
cd D:\3D\YYS2\OnmyojiAutoScript-easy-install-1
$env:PYTHONPATH='D:\3D\YYS2\OnmyojiAutoScript-easy-install-1'

# 读一台设备并写进对应配置的面板
.\toolkit\python.exe tasks\Workbench\oas_reader.py --serial 127.0.0.1:16384 --once --write oas1
.\toolkit\python.exe tasks\Workbench\oas_reader.py --serial 127.0.0.1:16416 --once --write oas3

# 只看结果不写面板
.\toolkit\python.exe tasks\Workbench\oas_reader.py --serial 127.0.0.1:16384 --once --dump out.json
```

输出 JSON 是 16 段数组（货币/式神/御魂/御魂明细/预设/碎片/结界卡/剧情/寮/式神袋/诊断/传记索引/任务/契灵/收集/账号对比），
`oas_reader` 已把 21 个货币按字段名展开，并写进 `config/workbench/assets/<config>.json`。

## 前置条件

1. 模拟器 adb 在线（`adb connect 127.0.0.1:<port>`；端口以 `MuMuManager info -v N` 为准）；
2. 游戏进程在跑**且已经进入游戏**（过了标题页/加载页，Globals.player1 就存在——
   **战斗中、战斗结算页、町中、庭院都能读**：2026-11-22 实测在「战斗失败」结算页读取成功（jade=115059）；
   只有标题页/加载中/被顶号时会返回 {"error": "Player data not available."}，属正常拒绝而非注入失败）；
3. 设备 root（`su 0` 可用）——注入器需要 ptrace 权限。

## 守护进程（自动刷新）

`dev_tools/asset_collect_daemon.py` 已加**自读优先**通道：

- 顺序：`collect_self()`（本通道）→ 失败才退回 `collect_paz()`（平安志）→ 再退回 UI 采集；
- 开关：`SELF_READ_ENABLED = True`；日志在 `log/asset_collect.log`，成功行：
  `[oas1] self-read ok status=ok complete=True jade=278 coin=26991897`；
- 触发一轮：写 `config/workbench/asset_request.json`
  （`{"config":"oas1","serial":"127.0.0.1:16384","mode":"manual"}`，**必须无 BOM**），守护 15 s 内捡起；
- 改完守护代码要重启：杀 `asset_collect_daemon.py` 那个进程，再跑 `dev_tools\start_asset_watch.ps1`（幂等）。

## 已验证口径（2026-11-22）

| 账号 | 配置 | 结果 |
|---|---|---|
| DemoRole / 2441356 / 全平台正式服110 | oas1 | `complete=True`，coin 26,991,897 / jade 278 / ap 20,760，式神 1441 / 御魂 3226 / 结界卡 95 |
| 半生緣 / 105585081 / 全平台正式服31 | oas3 | `complete=True`，coin 39,242,099 / jade 114,976 / ap 35,176，式神 3211 / 御魂 3505 / 结界卡 737 |

面板文件里每项都是 `precision=exact`、`source=oas.reader`。

## 故障对照

| 现象 | 原因 / 处置 |
|---|---|
| `Player data not available.` | 游戏停在标题页/加载中/被顶号（不是“必须回庭院”：战斗中/结算页也能读）。进游戏后重试 |
| `找不到 liboas-reader.so` | 仓库缺 `dev_tools/liboas-reader.so` |
| `游戏没在跑（com.netease.onmyoji.wyzymnqsd_cps）` | 包名手打错或实例没起；用 `pm list packages \| grep -i onmyoji` 现查 |
| 注入器报 `dlclose 清理测试库失败` / `stage:bridge` | **载荷其实已经跑完**，只看结果文件时间戳与大小 |
| 点刷新毫无反应 | `asset_request.json` 被写成带 BOM → 守护解析失败并静默删请求；用无 BOM 重写 |

## 自检与验收

```powershell
# 离线自检（17 项，不碰设备）
.\toolkit\python.exe tasks\Workbench\selftest_selfread.py
```

交付清单与双号验收证据见 `docs/workbench_asset_selfread_verification.md`。