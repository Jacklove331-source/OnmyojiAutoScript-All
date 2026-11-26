# OAS 整合版 — AI 会话交接文档

> 生成时间：2026-10-27 10:36（Asia/Shanghai）。本文件供新会话无缝接续，**不是**从零开始的开发说明。
> 原则：只写有证据的结论；"已实测"与"未实测"严格分开。

---

## 1. 项目位置与 Git 状态

| 项 | 值 |
| --- | --- |
| 项目根 | `D:\3D\YYS2\OnmyojiAutoScript-easy-install-1` |
| 上游 | runhey/OnmyojiAutoScript 的整合修改版（私有远端 `private` = `github.com:Jacklove331-source/OnmyojiAutoScript-easy-install-gai.git`） |
| 当前分支 | `port/xylolit-vendor` |
| 最新提交 | `84908287 docs(chess): 记录首局完整对局通过（第26回合/第3名/结算退出）与判定门槛` |
| 工作区 | 应保持干净（自动提交任务会每 20 分钟提交一次，提交信息形如 `[autosave] ...`） |
| Python | `.\toolkit\python.exe`（3.10，仓库自带，勿用系统 python） |
| WebUI 端口 | 22267（`config/deploy.yaml` 的 `WebuiPort`），默认无密码 |

检查命令：

```powershell
git rev-parse --abbrev-ref HEAD; git log --oneline -3; git status --porcelain
```

---

## 2. 项目架构与此前完成的自定义修改

- 调度：`script.py`（`Script(config).run(task)` / `.loop()`），任务跑在**独立子进程**（`module/server/script_process.py`）
- 任务：`tasks/<Task>/script_task.py`，页面图：`tasks/GameUi/navigator.py`（`GameUi(ChessBattleNavigationMixin, BaseTask, GameUiAssets)`）
- 配置：`module/config/config_model.py` 注册；文案：`module/config/i18n/zh-CN.json`（gitignore，需 `git add -f`）

已完成的整合改动（摘）：

- 实时画面：`module/device/screenshot.py` 写 `log/live/<config>.jpg`；`module/server/script_router.py` 提供 `GET /{script}/frame` 与 `/screen`(MJPEG)；RGB→BGR 修正
- 未知弹窗兜底：`tasks/GameUi/navigator.py` 的 `close_unknown_pages()` 增加 Android BACK 键
- 协同对弈 `tasks/DraftDuel/`（从 luchi5 fork 移植）
- 自动阵容：`module/lineup/*`、`tasks/Component/LineupAssistant/`、`tasks/Script/config_auto_lineup.py`
- 全中文界面：i18n 词条补齐

---

## 3. 百鬼棋局模块（27 文件 / 约 8200 行）

```
tasks/Chess/
├── script_task.py          # 入口：run() → _run_impl()，逐局循环、结算 OCR、退出
├── page.py                 # page_chess / page_chess_battle / page_chess_rank_result
├── assets.py               # 棋局模板
├── DEV_NOTES.md            # ★ 棋局专用开发记录（故障根因表 + 首局通过证据）
├── runtime/
│   ├── state_loop.py       # 状态机主循环
│   ├── round_state.py      # 阶段/回合状态与缓存（用 device.image_frame_id）
│   ├── economy.py          # 百鬼币、购买、出售、刷新、升级（调用 ui_click_until_disappear）
│   ├── hand_operations.py  # 手牌/商店操作（match_all_any(frame_id=...)）
│   ├── frame_cards.py      # 单帧卡片识别（依赖 BaseTask.runframe）
│   ├── recognition.py / press_and_drag.py / board_positions.py
│   ├── hyakki_draft.py / scout.py / settings.py / state.py / decision.py / events.py
└── strategy/
    ├── adaptive.py         # 随机应变（日志 "Chess adaptive plan: primary=... roster=..."）
    ├── lineup_strategy.py / lineup.py / shikigami_catalog.py / soul_catalog.py / grigri.py
```

继承链：`ScriptTask(ChessStateLoopMixin, ChessLegacyScriptTask)`，后者混入 6 个 runtime mixin + `GameUi` + `GeneralBattle` + `ChessAssets`。
**页面标志在 `PageCheckAssets`（`tasks/GameUi/page_check_assets.py`），不在 `GameUiAssets`**——页面模块写的是 `import PageCheckAssets as GameUiAssets` 别名。

---

## 4. 已确认的故障根因与修复（全部来自真实日志）

| 报错 | 根因 | 修复 |
| --- | --- | --- |
| `ScriptTask has no attribute 'chess_result_flow_visible'` | `GameUi` 丢失 `ChessBattleNavigationMixin` 基类 | 补回基类 |
| `has no attribute 'runframe'` | `BaseTask.runframe` 被 reset 冲掉 | 从快照 `0a6488cb` 恢复 |
| `Device has no attribute 'image_frame_id'` | 远程图像服务版接口被回退 | `Screenshot` 加 `_frame_id` 计数 + 属性 |
| `match_all_any() unexpected kw 'frame_id'` | `image.py` 回退为本地匹配器 | `match/match_all/match_all_any` 恢复 `frame_id` 形参 |
| `GeneralBattleAssets has no attribute 'I_EXIT_ENSURE'` | 资源定义丢失（png 还在） | 从快照拷回定义，`cv2.imread().shape` 校对 roi |
| `ScriptTask has no attribute 'I_CHESS_EXIT'` | 同上 | 定义加进 `GameUiAssets` |
| `has no attribute 'I_SM_CONFIRM_2' 文件缺失` | 模板 png 不存在 | 复用 `GlobalGameAssets.I_UI_CONFIRM` |
| `Device has no attribute '_invalidate_image_batch_cache'` | 拖拽清缓存接口缺失 | 加到 `Screenshot`（自增帧号） |
| `ui_click_until_disappear() unexpected kw 'click_target'` | 方法被回退成 `(click, interval)` | **整段恢复快照实现**，见 §8 |
| `'RuleClick' object has no attribute 'match'` | 用 `**kwargs` 吞参数，函数拿 RuleClick 去做 `appear()` | 同上，删掉 `**kwargs` |
| `FileNotFoundError page_chess_exit.png` | 模板缺失 | 已在仓库 |
| `MinitouchNotInstalledError` | 首次连接无 minitouch | 自动装 uiautomator2 后恢复（非 bug） |

> 共同根因：2026-10-26 OAS 自带更新器 `git reset --hard origin/master` 把公共组件回退，棋局代码按新接口调用。**这是整条故障链的源头**，见 §5。

---

## 5. 自动更新覆盖事故与防护（已落地）

**事故**：更新器执行 `git reset --hard origin/master` 两次，冲掉整合版分支、未提交改动、gitignore 的 `config/*.json`。

**六层防护**：

1. `deploy/git.py` → `GitManager._update_guard()`：四项检查（分支一致 / 无未推送提交 / 工作区干净 / 无未跟踪文件），任一不符**拒绝更新**；`git_repository_init()` 开头调用
2. `config/deploy.yaml`：`AutoUpdate: false`、`KeepLocalChanges: true`
3. `deploy/config.py`：`AutoUpdate` 默认值 `False`
4. `.git/hooks/reference-transaction`：ref 变更前把旧 sha 存进 `refs/autobackup/`
5. `dev_tools/repo_autosave.py` + 计划任务 `OAS-RepoAutosave`：定时提交；**提交前排除** `config/ log/ *.bundle *.key *.pem .credentials*`，只剩被排除内容则跳过
6. 回归测试 `dev_tools/updater_guard_check.py`（分支不一致 / 未跟踪文件 / keep_changes=True 仍拒绝 / 干净放行）

**bundle 还原演练（2026-10-27 已完成）**：

```powershell
git bundle verify D:\3D\YYS2\_backups\repo-20261027.bundle      # -> is okay (sha1)
git clone <bundle> D:\3D\YYS2\_restore_drill_<stamp>\repo        # -> exit 0
# 结果：branch=port/xylolit-vendor，tasks 69 个目录，tasks/Chess/*.py 27 个，script_task.py 在
#       克隆目录 51.6 MB，可留在原处作为证据
```

结论：**还原链路可用**。注意 bundle 每天只生成一次且已存在就跳过（`repo_autosave.bundle()`），
所以当天 00:35 之后提交的 38 个 commit 不在里面（主仓 2928 vs 克隆 2890）——
bundle 是「灾难兜底」，日常恢复请用 `.git` 里的 autosave 提交；若当天要快速回滚，
先在主仓 `git bundle create` 一份新的再演练。

---

## 6. ADB / MuMu / 游戏

- 模拟器：MuMu 5.30（`E:\MuMuPlayer`），实例 1 = **oas3**，adb 端口 **16416**（oas1 是 16384，账号缺式神，别用）
- MuMu 更新器已禁用：`E:\MuMuPlayer\nx_main\MuMuNxUpdater.exe.disabled`
- 查看实例：`E:\MuMuPlayer\nx_main\MuMuManager.exe info -v 1`（看 `adb_port` / `player_state` / `is_android_started`）
- 连接恢复（**当前必须手动**）：

```powershell
$adb = 'D:\3D\YYS2\OnmyojiAutoScript-easy-install-1\toolkit\Lib\site-packages\adbutils\binaries\adb.exe'
& $adb connect 127.0.0.1:16416
& $adb -s 127.0.0.1:16416 shell dumpsys window | Select-String mCurrentFocus   # 应看到 com.netease.onmyoji.wyzymnqsd_cps
```

- **`Game died` 的真相**：`module/device/device.py:182` 在 `app_is_running()` 为假时抛 `GameNotRunningError('Game died')`；实测多数是 **ADB 掉线**（`adb devices` 为空但 MuMu 实例仍 `start_finished`），不是游戏崩溃。排查顺序：MuMuManager → adb devices → adb connect → dumpsys window。

### 6.1 ADB 掉线自动重连（2026-10-27 已实现，实机验证见 §12）

| 文件 | 职责 |
| --- | --- |
| `module/device/mumu_state.py` | 查 `MuMuManager info -v all`（5s 缓存）→ 实例 `is_android_started` / `player_state` / `adb_host_ip:adb_port`；按 serial 端口匹配实例 |
| `module/device/reconnect.py` | `ConnectionRecovery` mixin：`classify_connection()`、`recover_connection(stage=)`（3 轮 × 配置 serial → MuMu 端口）、换绑 serial、`app_is_running_after_recovery()`、事件写 `log/device_connection.jsonl` |
| `module/exception.py` | `DeviceDisconnectedError(GameNotRunningError)`：区分「设备掉线」与「游戏真没跑」 |
| `module/device/device.py` | `Device` 混入该 mixin；`stuck_record_check()` 抛异常前先尝试恢复：adb 健康→`Game died`；掉线且恢复且游戏在前台→继续；恢复失败→`DeviceDisconnectedError`（调度器转 Restart，不再 `exit(1)`） |

- 状态分类：`ok / adb_server_down / adb_missing / adb_offline / adb_unauthorized / emulator_stopped / unknown`
- 实机验证工具：`dev_tools/adb_cycle_check.py status|cut|cut-server|port`
- 关键前提：截图走 nemu_ipc、点击走 minitouch，**ADB 掉线时画面与点击仍可用**，所以恢复后任务可以原地继续，不必重开一局

### 6.2 截图方式降级（2026-10-27 实测，已修）

MuMu 渲染 IPC 会偶发卡死：`adb exec-out screencap` 正常、前台是游戏，但
`nemu_capture_display` 每次 0.15s 超时 → 重试 3 次抛 `RequestHumanTakeover`
（= 需人工介入）→ 任务直接退出。现在 `Device.screenshot()` 捕获该异常后用 ADB
补一帧，成功则本会话固定走 ADB（`_screenshot_force_adb`）、**不改写用户配置**，
ADB 也失败才向上抛。验证：`dev_tools/screenshot_fallback_check.py`（8 项）；
12:48 第三轮连跑启动时真的触发了这条降级并继续跑。

---

## 7. Chess 任务结果状态系统

`tasks/Chess/script_task.py`：

```python
OUTCOME_SERIES_COMPLETED / OUTCOME_RECOVERED_CONTINUE / OUTCOME_RECOVERED_ABORTED
OUTCOME_FAILED / OUTCOME_STOPPED

def run(self):            # 包装器，TaskEnd 语义不变
    try: return self._run_impl()
    except TaskEnd: raise
    except DeviceDisconnectedError: _mark_outcome(RECOVERED_ABORTED, 'adb disconnected ...'); raise
    except GameNotRunningError:     _mark_outcome(RECOVERED_ABORTED, 'game died ...'); raise
    except Exception as e:          _mark_outcome(FAILED, repr(e)); raise
```

- 任务级记录：`log/chess_outcome.jsonl`（`time/config/status/reason/matches_started/matches_verified`）
- **逐局记录（2026-10-27 新增）**：`log/chess_matches.jsonl`，每局一行，字段
  `index/target/mode/round_reached/final_rank/game_over_detected/exit_confirmed/match_completed/error_kind/error_reason/recovered/duration_s`
- **计入完成的门槛**：结算页 OCR 得到 `final_rank`(1~8) 才 `completed += 1`；
  读不到名次记 `rank_ocr_unavailable`，退回大厅后重开一局；连续 2 局无证据则结束任务
- **任务级成功门槛**：`completed >= target_count` **且** 最近一局 `final_rank` 为 int
  **且** `exit_confirmed=True`（`return_to_chess_lobby()` 逐帧确认 `I_CHECK_CHESS`）
- 日志锚点（缺一不可）：`[ChessMatch] start/end` → `Chess game-over rank OCR` →
  `Chess game ended` → `Chess completed games` → `PAGE_CHESS_EXIT_TO_LOBBY(_2)` →
  `PAGE_CHESS_RANK_GOTO_LOBBY` → `[ChessOutcome]`
- `OUTCOME_RECOVERED_CONTINUE` 仍是常量，没有真实打点（恢复后继续的路径目前不写这个状态）

### 7.1 等级 OCR 兜底（2026-10-27）

`round_state._read_level()` 单帧 OCR 失败（日志里 `Chess level OCR invalid: []` 7 次）原本
直接导致 `lineup capacity unavailable` → **整轮停止上阵**。现在保留本局最后一次确认等级做兜底
（等级局内只增不减，旧值最多低估人口），新局在 `_start_chess_game()` 重置。

### 7.2 点击记录键必须是 control_name 字符串（2026-10-27 实测事故）

`GameTooManyClickError: Too many click for a button: shikigami_1` 曾把第 2 局跑挂：
`click_record_remove(click_rule)` 传对象等于 no-op（记录里存的是 `RuleClick.name` 字符串），
同一商店格反复购买累计到 10 次触发全局保护。

- 修复：`economy._clear_slot_click_record()` 按 `.name` 清理，**每次购买尝试前**都清；
  `SHOP_BUY_MAX_ATTEMPTS=6` 上限；`I_RESTART_AGAIN` / `I_BACK_RED` / 六界选择同步改用名字。
- 回归：`dev_tools/click_record_check.py`（12 项，含 no-op 反例与 10 次阈值验证）。
- **规则**：任何 `click_record_remove(...)` 都必须传 `name` 字符串，不要传规则对象。

---

## 8. `ui_click_until_disappear` 完整契约（关键）

`tasks/base_task.py`，**必须保持这个签名与语义**（从快照 `0a6488cb` 恢复，提交 `21bef1e4`）：

```python
def ui_click_until_disappear(self, click, interval: float = 1, *,
                             click_target=None, visible=None, guard=None,
                             timeout: float | None = None, confirm_frames: int = 1,
                             max_clicks: int | None = None):
```

- `click`：原接口的判定对象（可能是 `RuleImage`，也可能是 `RuleClick`——**RuleClick 没有 `match()`**，所以判定要靠 `visible`）
- `click_target`：实际点击区（可与 `click` 不同）
- `visible`：返回 bool 的回调，判断目标是否仍存在
- `guard`：保护条件，不满足立即返回 False
- `timeout`：整体超时，超时返回 False；`timeout<=0` 表示预算耗尽，**直接返回 False 不点击**
- `confirm_frames`：连续 N 帧不可见才算消失（`<1` 抛 `ValueError`）
- `max_clicks`：限制重复点击次数（`<1` 抛 `ValueError`）

**禁止再用 `**kwargs` 吞参数**——未知参数必须抛 `TypeError`。
`RuleClick`/`RuleLongClick` 在没有 `visible` 时**立即抛 TypeError**（不再静默走到 `match()`）。

真实调用点：`tasks/Chess/runtime/economy.py:429`（购买确认），参数：`click_rule, click_target=click_rule, visible=..., guard=..., interval=0.3, timeout=..., confirm_frames=2, max_clicks=3`。

**行为级回归测试已补**：`dev_tools/ui_click_regression.py`（31 项，见 §9）。

---

## 9. 测试工具（都在 `dev_tools/`）

| 工具 | 用途 | 用法 |
| --- | --- | --- |
| `missing_method_check.py` | MRO 级方法存在性扫描（62 任务） | `.\toolkit\python.exe dev_tools\missing_method_check.py` |
| `contract_scan.py` | 任务模块对 `BaseTask`/`GameUi` 的调用契约（方法/关键字/位置参数） | `... contract_scan.py Chess AreaBoss ...`（实测 8 模块 0 问题） |
| `asset_file_check.py` | 模板图片引用审计（2145 引用，仅报"真被引用"的缺失） | `... asset_file_check.py` |
| `oas_fix_smoke_test.py` | 真实截图接口冒烟（image_frame_id / match(frame_id) / 资源 / 导航） | `$env:OAS_DEV_CONFIG='oas3'; ... oas_fix_smoke_test.py` |
| `run_task_once.py` | 单任务实机跑，打印 `RESULT <task> -> <bool>` | `... run_task_once.py oas3 Chess` |
| `updater_guard_check.py` | 更新守卫回归（临时 git 仓库） | `... updater_guard_check.py` |
| `repo_autosave.py` | 自动提交 + 每日 bundle | 由计划任务调用 |
| **`ui_click_regression.py`** | `ui_click_until_disappear`/RuleClick 行为回归（31 项） | `... ui_click_regression.py` |
| **`adb_recovery_unit.py`** | ADB 分类/重连阶梯/异常类型（35 项，假对象） | `... adb_recovery_unit.py` |
| **`adb_cycle_check.py`** | ADB 真实断连恢复（**棋局空闲时**） | `... adb_cycle_check.py status|cut|cut-server|port oas3` |
| **`chess_level_cache_check.py`** | 等级 OCR 兜底（9 项） | `... chess_level_cache_check.py` |
| **`chess_series_test.py`** | 连续多局实机测试（内存覆盖 run_count/limit_time，结束恢复） | `cmd /c start "" /b "...\toolkit\python.exe" dev_tools\chess_series_test.py oas3 3 04:00:00 --log <utf8 日志>` |
| **`chess_log_report.py`** | 日志→逐局证据表（自动识别 UTF-16/UTF-8/gb18030） | `... chess_log_report.py <log>` |
| **`chess_econ_report.py`** | 逐局经济复盘：最高阶/金币水位/刷新·升级·买卡·卖卡·上阵次数 | `... chess_econ_report.py <log> [...]` |
| **`chess_roi_probe.py`** | 对实时帧/截图逐 OCR ROI 探针 + 导出裁剪图（只读，不碰模拟器） | `... chess_roi_probe.py --live oas3 --scan-top` |
| **`click_record_check.py`** | 点击记录键格式/清理回归（12 项，含「传对象是 no-op」反例） | `... click_record_check.py` |
| **`screenshot_fallback_check.py`** | 截图降级 nemu_ipc→ADB 实机验证（8 项） | `... screenshot_fallback_check.py oas3` |
| **`adb_cycle_check.py guard`** | `stuck_record_check()` 三分支接线验证 | `... adb_cycle_check.py guard oas3` |

---

## 10. 已实测通过的游戏流程（有日志证据）

### 10.1 连续三局连跑通过（2026-10-27 13:59，新代码，**稳定性基线**）

`dev_tools/chess_series_test.py oas3 3 04:00:00`（pid 36872，12:48 → 13:59，71.3 分钟，
6,834 行日志，0 ERROR / 0 Traceback），日志 `_recovery_070208/chess_series3_run3_1248.log`：

```
 #  start     end       round rank ended exit lobby count verified
 1  12:48:54  13:14:16    22    6     Y    Y    Y    1/3   YES
 2  13:14:17  13:35:41    20    6     Y    Y    Y    2/3   YES
 3  13:35:42  13:59:24    20    7     Y    Y    Y    3/3   YES
outcome: [ChessOutcome] status=series_completed reason=completed=3/3 settled=True
         exit_confirmed=True matches=3/3 verified limit_time=4:00:00
RESULT Chess -> True
```

- 三局各自有 `[ChessMatch] end ... exit_confirmed=True completed=True` 与 `log/chess_matches.jsonl` 落盘
- `round_reached` 22/20/20（修复后正确）；`level_ocr_invalid=0`、`capacity_blocked=0`
- 无 `GameTooManyClickError`；启动时 nemu_ipc 卡死已自动降级 ADB 截图并继续
- 一键复核：`.\toolkit\python.exe dev_tools\chess_log_report.py <log>`

> 名次观察：本轮 6/6/7（全程 ADB 截图），nemu 正常时的两组是 1/2 与 3。
> 样本小 + 对手随机，不能据此判定截图方式影响名次；如需验证要在 nemu 正常时再跑对照。

### 10.2 两局连跑（2026-10-27 11:26，修复前代码）

`run_task_once.py oas3 Chess`（pid 15112，10:24:40 → 11:26:23，61 分钟，0 ERROR）：

```
 #  start     end       round rank ended exit lobby count verified
 1  10:52:34  10:52:34    25    1     Y    Y    Y    1/3   YES
 2  11:25:56  11:25:56    31    2     Y    Y    Y    2/3   YES
Chess task time reached; stop before starting next game
[ChessOutcome] status=failed reason=completed=2/3 settled=True   ← 预算不足（30min），非对局失败
```

### 10.3 单局完整对局（2026-10-27 09:59，最早一次）

首局完整对局（2026-10-27，pid 31896，日志 `_recovery_070208\chess5.out.log`）：

```
Chess game-over rank OCR: [第三名] -> 第3名
Chess game ended: 第3名
Chess completed games: 1/1
Click (451,617) @ PAGE_CHESS_EXIT_TO_LOBBY      ← 退出结算页
Click (118,651) @ PAGE_CHESS_RANK_GOTO_LOBBY    ← 回到棋局大厅
Chess task loop finished: completed=1, target=1
[ChessOutcome] status=series_completed reason=completed=1/1
RESULT Chess -> True
```

- 最高回合 **26**，全程 0 ERROR；`log/chess_outcome.jsonl` 已落 `series_completed`
- 过程中确认可用的操作：商店购买、出售（`Chess card sale confirmed: ... count=1->0`）、拖拽布阵、符咒选择（`Chess grigri option: ... 勇气符咒(0.861)`）、自适应阵容（`Chess adaptive plan: primary=寒霜 ... roster=...`）、回合推进、结算识别、退出
- 修复前的最长记录是第 13 回合（`click_target` 崩溃）

---

## 11. 当前（2026-10-27 15:20 更新）运行状态

**验证轮次**（目标已从「能跑完」转为「决策更聪明」，见 §13）：逐个单局跑，
每轮改一处策略/识图并对比 `chess_decision_audit.py` 的指标。

| 轮次 | 日志 | 结果 | 关键指标 |
| --- | --- | --- | --- |
| run3（修前基线） | `chess_series3_run3_1248.log` | 6/6/7 名 | 上阵 4/1/7、购买确认率 15% |
| validate1（上阵优先等） | `chess_validate1_1435.log` | **第3名**，28 回合，30 分钟 | 上阵 8、购买 34 次确认 33 次、等级 8 |
| validate2（+金币两帧确认） | `chess_validate2_1508.log` | 进行中/见 DEV_NOTES §13 | - |

`log/chess_matches.jsonl` 最近记录：
```json
{"time":"2026-10-27 14:35:33","index":1,"round_reached":28,"final_rank":3,
 "game_over_detected":true,"exit_confirmed":true,"match_completed":true}
```

配置未被改动：`run_count=3` / `limit_time=00:30:00` / `screenshot_method=nemu_ipc`
（连测工具只在内存覆盖，结束 `finally` 恢复）。

`log/chess_outcome.jsonl` 当前内容（最近 3 条）：

```json
{"time":"2026-10-27 11:26:05","config":"oas3","status":"failed","reason":"completed=2/3 settled=True"}
{"time":"2026-10-27 12:43:40","config":"oas3","status":"failed","reason":"RequestHumanTakeover()"}
{"time":"2026-10-27 13:59:24","config":"oas3","status":"series_completed","reason":"completed=3/3 settled=True exit_confirmed=True matches=3/3 verified limit_time=4:00:00"}
```

---

## 12. 开发进度（严格区分）

**已实现并实测**
- 连续两局完整对局（第1名25回合 / 第2名31回合），逐局结算 OCR → 退出 → 回大厅 → 再开局，0 ERROR
- 单局完整对局（26 回合→第 3 名→`series_completed`）
- **连续三局连跑通过**（12:48→13:59，三局全部 `match_completed` + `series_completed`，见 §10.1）
- 逐局记录 `log/chess_matches.jsonl`（实测已落 4 条有效记录，含失败局分类）
- ADB 自动重连三场景实测：`cut` 0.17s / `port` 2.20s（换绑 serial 写回配置）/ `cut-server` 4.27s
- `stuck_record_check` 三分支实测：掉线恢复不误判 / 游戏真没跑仍 `Game died` / 恢复失败 `DeviceDisconnectedError`
- 等级 OCR 兜底（连续三局日志中 `level_ocr_invalid=0`）
- nemu_ipc 卡死 → ADB 截图降级（真实触发了 1 次，任务未中断）；`screenshot_fallback_check` 8 项
- 点击记录键修复（`GameTooManyClickError` 不再因商店格重复点击把整局跑挂）；`click_record_check` 12 项
- bundle 还原演练（`git bundle verify` + clone，分支/文件数核对通过，见 §5）
- 更新守卫（四条检查）+ 回归测试；公共接口恢复（`runframe`、`image_frame_id`、`match(frame_id=)`、`ui_click_until_disappear` 完整契约）
- 回归测试：`ui_click_regression` 31 项、`adb_recovery_unit` 35 项、`chess_level_cache_check` 9 项、
  `click_record_check` 12 项、`screenshot_fallback_check` 8 项、
  `contract_scan Chess` 0 问题、`missing_method_check` 62 任务 0 问题

**已实现未实测**
- 逐局 `match_completed` 门控在"读不到名次"场景（逻辑有单测；实机只遇到过一次 `GameTooManyClickError` 中断，被正确记 false）
- `RECOVERED_CONTINUE` 状态没有真实打点（恢复后继续的路径不写这个状态）

**尚未开发 / 已知缺口**
- ~~商店识图 `未识别` ≈7%~~ **已定性**：55 个「未识别」格子离线复现为
  EMPTY 25.5% / 识别成功 74.5% / **真实失败 0%**；空格子现记 `空`，不再计入失败率
  （`dev_tools/shop_frame_scan.py --summary`）
- **早期买不到东西**（最高优先级，有实测证据）：自适应阵容取「最强 8 张」（2~5 费），
  1~10 回合商店只出 1~3 费 → 前 10 回合 0~3 次购买、0 次上阵。
  方案：阵容加 1~2 个同羁绊低费「过渡位」+ 上阵动态空位（会动 `shikigami_deploy_positions`）
- **经济吃掉补上阵窗口**：经济停止阈值 `remaining < 15` 偏晚，补上阵常被阶段切走；
  方案：阈值提到 25s 或给上阵预留固定预算
- 符咒 OCR/图标冲突（朱印 vs 纹章）：样本已落 `log/chess_ref/grigri_conflict/`，待人工定规则
- 未知界面自学习机制（目前有候选分日志 + 失败样本落盘 + `log/error/` 截图）
- ~~新赛季主布局 ROI 错位~~ **已核实不成立**：`[CHESS_MODE]` 读到数字是「前三回合专用布局」判据
  （`_read_primary_round_layout`）在起作用，会自动切到 `O_ROUND_2`/`O_CHESS_MODE_2`；
  `dev_tools/chess_roi_probe.py --live oas3` 逐 ROI 实测正常

**已知风险**
- 长跑进程可能被外部终止（28212 案例），需要靠日志判断"被终止"还是"代码崩"
- `/oas3/start` 接口在本机返回 200 但不真正拉起脚本（`log/2026-10-27_oas3.txt` 不更新）→ 测试请用 `run_task_once.py` / `chess_series_test.py`
- `tasks/**/assets.py` 是 `dev_tools/assets_extract.py` 自动生成的，手改后若重跑生成脚本会被覆盖
- shell 重定向日志是 UTF-16LE：解析前必须识别编码（见 `chess_log_report.py`）
- 棋局模块大量坐标/模板仍是旧赛季资源，新赛季界面若有变化会识别失败

---

## 13. 最新赛季适配要求（未完成，高优先级）

- 2026-10-21 赛季更新：新增/调整羁绊、式神、符咒、幻境机制
- 数据落点：`tasks/Chess/strategy/shikigami_catalog.py`、`lineup*.py`、`soul_catalog.py`、`grigri.py`、`tasks/Chess/resource/badge/badge_hand_icons.py`
- 参考资料（仅学玩法，数据以官方/当前游戏为准）：`https://yys.huijiwiki.com/wiki/百鬼棋局`、`https://a.4399.cn/gg/54459041_99319.html`、B站 `BV1jRZ9BtEVn`（**本机无法播放视频/取字幕**，不能声称看过）、网易大神 `https://ds.163.com/feed/694a47181c7e777c11944e8e/`
- 注意区分：百鬼棋局（自走棋）/ 百鬼弈 / 对弈竞猜 / 百鬼夜行

### 13.1 2026-10-27 联网核查结论（只作参考，不作为数据源）

- 能抓到的中文攻略多为**旧赛季**内容（例：`https://www.qieyou.com/content/151471` 发布于 2026-01-13，
  讲中低分段「海国」阵容；`https://bbs.nga.cn/read.php?tid=44601580` 讨论下赛季金鱼姬调整）。
  这些**不足以**证明当前赛季的式神/羁绊/符咒清单，不能照搬。
- **以游戏内证据为准**：本轮实机日志的商店识别已经能读出当前赛季新增式神
  （荒骷髅 / 思金神 / 葛叶 / 封阳君 / 季 / 孔雀明王 / 禅心云外镜 / 寻香行 / 月读 / 言灵），
  说明 `shikigami_catalog.py` 的条目基本覆盖当前赛季。
- **赛季数据已独立成文件**：`tasks/Chess/strategy/season_data.py`
  （六道之门 / 六道·遗念·回响·常世 / 新式神 7 个 / 新符咒 3 个 / 幻境投票机制），
  带官方公告 URL 与核对日期；**六道「不随人数增强」已在 `adaptive.py` 里按 ×0.35 计入**。
- **真正的赛季影响暂未证实**：曾怀疑第一套布局 ROI 错位，逐 ROI 实测后确认那是
  **前三回合专用布局判据**在正常工作（见 §12 缺口条目），已撤回该判断。
  后续若要核对赛季差异，用 `dev_tools/chess_roi_probe.py --live oas3`（只读实时帧，
  不碰模拟器）+ 商店失败样本，比抓攻略更可靠。

---

## 14. 识图失败时的处理要求

1. 失败样本要落盘：商店未识别帧 → `log/chess_ref/shop_unknown/`（整页 PNG + JSON）；
   金币误读 → `log/chess_ref/gold_misread/`；符咒冲突 → `log/chess_ref/grigri_conflict/`
2. 离线复现：`dev_tools/shop_frame_scan.py <png|目录> --summary`
   —— 区分 EMPTY（空格子，不算失败）/ `card_but_unknown`（真失败）/ 识别成功，
   并给出真实失败率
3. 商店头像未达阈值时会打印最像的 3 个候选与分数 + 卡面 OCR 名字
   （`Chess store portrait below threshold (0.80); ocr_name=[..]; top candidates: name:score, ...`）
4. 联网查官方图鉴/公告核实（**不得凭猜测购买/出售**）；数据进 `season_data.py` 并标出处
5. 仍无法确定 → 走保守策略，不执行高风险操作；**禁止用降低全局阈值的方式掩盖问题**

---

## 15. 验收标准

**单局完成**必须同时满足：
- `Chess game-over rank OCR: [...] -> 第N名`
- `Chess game ended: 第N名`
- `Chess completed games: X/Y` 递增
- `Click @ PAGE_CHESS_EXIT_TO_LOBBY` → `Click @ PAGE_CHESS_RANK_GOTO_LOBBY`
- 新代码另加 `[ChessMatch] end ... exit_confirmed=True completed=True` 与 `log/chess_matches.jsonl` 一行
- `[ChessOutcome] status=series_completed`

一键判定：`.\toolkit\python.exe dev_tools\chess_log_report.py <logfile>`（自动识别编码，输出逐局证据表）。

**不算完成**：回合数增加、`errors=0`、`RESULT -> True`（Restart 恢复也会 True）、仅点击返回按钮。

**连续三局**：每局独立记录上述证据；期间异常（ADB 掉线/识图失败/卡死）要能分类记录并恢复，恢复成功不得计入完成局数。
注意 `limit_time` 只在新局开始前检查 —— `run_count=3` 必须配 ≥ 4 小时的预算，否则永远只跑到 2 局。

---

## 16. 用户已明确的开发要求与权限边界

- 汇报必须区分 **已完成并实测 / 已写未验证 / 阻塞**，禁止把未实现说成已完成
- 不要每修一个小问题就停下等确认；自主执行"分析→改→测→修→再测"
- **没做完不许停**，除非真被外部条件阻塞（此时明确说明阻塞点）
- 不得删除/覆盖用户已有配置、队伍预设、御魂预设；改动前记录可恢复
- Git 红线：禁止 `reset --hard` / `clean` / 丢弃未提交改动（本项目已有更新守卫）
- 界面全中文（阴阳师术语）
- 不得把 API Key、账号、Token 写入任何文件或聊天

---

## 17. 新会话开工指引

**先读**：本文件 → `tasks/Chess/DEV_NOTES.md` → `tasks/Chess/script_task.py`（`run` / `_run_impl` / `run_one_game`）

**检查环境**
```powershell
cd D:\3D\YYS2\OnmyojiAutoScript-easy-install-1
git status --porcelain; git log --oneline -1
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select ProcessId,CreationDate,CommandLine
Get-Content D:\3D\YYS2\_recovery_070208\chess_series3_20261027.log -Tail 5   # 有输出=仍在跑（UTF-8）
Get-Content log\chess_outcome.jsonl -Tail 3
Get-Content log\chess_matches.jsonl -Tail 3      # 逐局记录（新代码）
```

**判断连跑是否还活着**：进程表里有对应 `python.exe` 且日志 `LastWriteTime` 在最近 1-2 分钟内；
否则用 `dev_tools/chess_log_report.py <log>` 读结论（是"跑完/崩/被杀"一目了然）。

**不要**重复启动新的 Chess 进程；新测试用新的日志名与 `chess_series_test.py ... --log <新路径>`。

**下一阶段直接改（按优先级）**
1. **商店识图补录（≈7% 未识别）**：用 `Chess store portrait below threshold ... top candidates`
   日志判断是图鉴缺条目（补 `tasks/Chess/resource/.../card_index.json`）还是动画/遮挡（加重试帧）
2. **符咒冲突核查**：`Chess grigri OCR/icon conflict` 时 dump 一次截图，人工核对 OCR 与图标谁准
3. **`RECOVERED_CONTINUE` 打点**：恢复后继续的路径还没有状态落盘
4. **策略调参**：经济/阵容/站位已能跑通（第1名、第2名、第2名），可按 §13 的数据继续优化
   （例：日志里 `Refresh` 40 次 / `C_EXPERIENCE` 41 次，可核对升阶与刷新预算是否合理）
5. 如需核对赛季界面：`dev_tools/chess_roi_probe.py --live oas3 --scan-top`（只读实时帧）

**三局连跑复测**：
```powershell
cmd /c start "" /b "<repo>\toolkit\python.exe" dev_tools\chess_series_test.py oas3 3 04:00:00 --log D:\3D\YYS2\_recovery_070208\chess_series3_<日期>.log
```
（内存覆盖 `run_count/limit_time`，结束自动恢复配置；日志写 UTF-8）

---

## 18. 双 Agent 自动开发系统（2026-10-27 23:20 建成并启动）

**目标**：无人值守地持续改进百鬼棋局（Agent A 决策/审核，Agent B 改代码/测试）。

| 角色 | 模型（DeepSeek 映射） | 权限 | 职责 |
| --- | --- | --- | --- |
| Agent A | claude-opus-4-1 → deepseek-v4-pro | `--permission-mode plan`（只读 + 联网） | 读现状/日志/文档 → 规划任务卡 → 审核 B 的 diff → PASS/REWORK/BLOCKED |
| Agent B | claude-sonnet-4-5 → deepseek-flash | `acceptEdits` + Bash 白名单 | 改代码、补测试、输出 JSON 自述 |
| 调度器 | Python（本仓库自带解释器） | 自己跑验证命令 | 状态机、预算、持久化、返工、实机托管、验收 |

两者都通过 **Claude Code headless（`-p --output-format json`）** 调用，
凭证只存在于进程环境（`winreg` 从用户级读取，绝不打印）。

**状态机**：`PLAN(A) → EXECUTE(B) → VERIFY(机器) → REVIEW(A) → PASS/REWORK/BLOCKED`

**关键文件**
```
dev_tools/dual_agent/
├── scheduler.py      # 主状态机（--tasks N / --once / --review-task <key>）
├── cli_agent.py      # Claude Code headless 调用 + usage 计费 + max_turns 重试
├── state.py          # SQLite（tasks/runs/verifications/acceptance/meta）+ events.jsonl + 心跳
├── acceptance.py     # AC01~AC12 机器验收 + H1~H3 人工项
├── safety.py         # 危险 git/shell 黑名单、受保护路径、git 检查点、单实机名额
├── context.py        # 精简上下文（文件切片/diff 上限/日志命中行/digest）
├── watchdog.py       # 崩溃自动重启（读 state.db 判断终态）
├── status.py         # 查看状态/预算/验收（不调模型）
├── start.ps1 / run_forever.ps1 / autostart.cmd
└── tests/            # 5 个测试脚本 + run_all.py（63 项断言）
```

**预算与安全**（`config.json`）：费用 ¥12 / 160 次调用 / 7.5 小时硬上限；
价格表保守（input ¥4/M、cache ¥1/M、output ¥16/M）；连续失败 3 次 → `blocked`；
实机对局单进程 + 每会话 2 次 + 2 小时超时；`config/*.json`、`.credentials` 禁写。

**验收**：AC01 调度器自测 / AC02 真实 A→B→A / AC03 持久化恢复 / AC04 预算停止 /
AC05+AC05S 行为回归+静态 / AC06 棋局完整流程 / AC07 ADB+截图恢复 /
AC08 策略回归（过渡位+时间预算）/ AC09 符咒+御魂 / AC10 赛季数据 / AC11 稳定版本+文档 /
AC12 最终报告。**全部 PASS 才会写 `completed`**，否则保持 running/blocked/paused_*。

**运维**
```powershell
.\toolkit\python.exe dev_tools\dual_agent\status.py                     # 看状态
powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\start.ps1 -Status
.\toolkit\python.exe dev_tools\dual_agent\tests\run_all.py             # 自测
# 启动：看门狗（崩溃自恢复）
cmd /c start "" /b "<repo>\toolkit\python.exe" dev_tools\dual_agent\watchdog.py --hours 7
```

**已知边界**：`Register-ScheduledTask` 在本机返回 0x80070005（无权限），
所以用「Python watchdog + 启动文件夹 autostart.cmd」实现登录续跑；
电脑关机/休眠期间不运行（不假装在跑），重启登录后自动接着跑。

### 18.1 首夜实跑结果（2026-10-27 22:51 → 10-28 00:10）

| 指标 | 值 |
| --- | --- |
| 运行时长 | 3.3 小时（22:51→00:10，之后预算到顶自动 `paused_budget`） |
| 模型调用 | 26 次（A 15 次 ¥7.45 / B 11 次 ¥5.03），合计 **¥12.48**（上限 ¥12） |
| Token | in 897k / cached 4.51M / out 273k（缓存命中把有效单价压得很低） |
| 任务 | **5 个 accepted**（4 个 A 审核 PASS + 1 个操作员复核）、0 blocked、1 pending |

被接受的代码改动（均已回归验证）：

| 任务 | 实际改动 |
| --- | --- |
| `T-indexerror-boundary-fix` | `module/atom/ocr.py`：QUANTITY 模式空文本不再抛 IndexError（返回 0）+ `dev_tools/test_indexerror_boundary.py` 77 行 |
| `T-transition-slot` | `strategy/adaptive.py`：过渡位费用上限收紧到 2 费、主副羁绊无低费卡时兜底 |
| `T-grigri-conflict-rule` | 符咒冲突判定规则落地（前二 margin 判据）+ 测试 |
| `T-econ-deploy-budget` | `runtime/economy.py`：`ECONOMY_DEPLOY_RESERVE=25`，中后期给补上阵预留金币 |
| `T-soul-equip-reason-fix` | `runtime/hand_operations.py`：`failed_targets` 跳过无效站位、失败原因分类、mode 重拍；`chess_soul_target_skip_test.py` 21 项 |

回归：11 个测试套件全绿（ui_click/adb/level_cache/click_record/gold/screenshot/transition/
phase_budget/grigri/indexerror/soul_target）+ contract_scan 0 问题 + missing_method 62 任务 0 问题
+ season_data 41 项 PASS。

**未完成**：`T-nemu-switchback`（ADB 降级后自动探测切回 nemu_ipc）仍 pending——
需要新的预算额度才会被执行。

**预算机制实测**：¥12 上限触发时调度器把状态写成 `paused_budget` 并停止调用模型
（heartbeat 显示 stopped），watchdog 见终态退出——**没有继续扣费，也没有伪报完成**。
要续跑：把 `dev_tools/dual_agent/config.json` 的 `budget.max_cny_total` 调大，
再启动 `watchdog.py --hours N`。

## 19. 调度器 v2：Claude 总指挥 + DeepSeek 执行（2026-10-28 起）

**更正**：§18 的「Agent A = claude-opus-4-1」实际走 `claude.CMD`，而用户级环境
`ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic` → **v1 的规划与审核都是 DeepSeek**。
v2 把 Agent A 换成真 Claude（`claude-anthropic.cmd` → api.anthropic.com），v1 的 AC02 证据作废重算。

| 角色 | 模型 / CLI | 做什么 |
| --- | --- | --- |
| Claude 总指挥 | Opus 5.5（规划/疑难/高风险审核）、Sonnet 5.5（常规审核/研究） | 任务卡（目标/证据/文件/约束/验收/风险）、审核（diff + 机器验证 + 自己读代码）、返工用尽后亲自做疑难分析并生成带 `guidance` 的新任务、在独立 worktree（`D:\3D\YYS2\oas-wt-claude-aux`，分支 `claude/aux`）写研究文档 |
| DeepSeek 执行 | `deepseek-v4-pro` / `claude.CMD` | 改业务源码、写测试；没有终端，编译/回归/实机由调度器执行 |
| DSH DeepSeek 会话 | `deepseek_b.py next → record → review` | 可选的「带终端/模拟器」执行者；与调度器共用 **执行锁**（`state.db` 的 `lock:executor`，TTL 3h） |

**关键机制**（代码都在 `dev_tools/dual_agent/`）：
- 预算硬上限分供应商：Claude 总 $80 / 24h $25；DeepSeek 总 ¥300 / 24h ¥80；24h 调用 260 次。
  24h 窗口到顶 → `paused_daily`，看门狗到点自动续跑；总额到顶 → `paused_budget`（终态）。
  Claude 费用用 CLI 报告的 `total_cost_usd`（真实账单价），DeepSeek 按 token 单价保守估算。
- 环境故障（代理/VPN）连续 3 次 → `paused_env`，20 分钟后自动重试，不把任务判失败。
- 审核防虚报：机器验证没过时 Claude 给的 PASS 也会被调度器否决；审核记录进 `reviews` 表。
- 知识缓存 `state/knowledge.jsonl`：通过/疑难结论自动进提示词，避免重复分析。
- 实机评估：有新任务被接受且实战验收未过时，调度器在执行锁空闲时启动连续 3 局
  （`chess_eval_*.log`），24h 最多 4 次，与任何棋局进程互斥。
- 人工介入：`control.py pause|resume|start`；`state/PAUSE` 存在时下一轮安全暂停。
- 完成门禁：`acceptance.final_gate()` **重新全量**跑 20 项强制验收，全过才 `completed`
  并打 `stable-v2-*` tag、写 `state/FINAL_REPORT.md`。

**20 项强制验收**（阈值写死在 `acceptance.py`，不得放宽）：AC01 调度器自测；AC02 Claude 审核
→ DeepSeek 执行真实协作；AC02R 返工闭环；AC03 持久化；AC04 预算；AC05 行为回归；AC05S 静态检查；
AC06 连续 3 局完整流程；AC07 ADB/截图恢复；AC08 策略回归；AC09 符咒/御魂测试；AC10 赛季数据自洽；
AC10R 赛季联网核实记录（`docs/research/season_verification.md`，≥3 来源、14 天内）；AC11 tag + 本节；
AC13 前期（1~8 回合）早期购买≥2/局、早期上阵≥3/局、过渡位 ≥2/3 局；AC14 购买确认率≥90%、
动态预算每局生效、截图超时≤2/局；AC15 御魂装备≥3 次且成功率≥75%；AC16 等级 OCR 无效≤5/局、
商店未识别≤3/局、符咒冲突全有裁决；AC17 过程指标全面优于稳定基线（`chess_series3_run3_1248.log`）；AC17T 指标统计本身的测试（`dev_tools/chess_audit_metrics_check.py`）。
AC13~AC17 只看 v2 启动后**开局**的最近 3 局（`metrics.py`），名次只作人工参考（H1）。

**接手时发现的测量问题**（2026-10-28 10:20，Claude 核实）：`chess_decision_audit.py` 的逐回合
`timeline[r].buy_ok` 恒为 0（全局 `buy_ok` 有 37 次），且 `Chess soul equip summary` 日志被折行，
`soul_equipped` 永远统计为 0；`level_ocr_invalid` 在 validate4 每局 18~20 次（run3 为 0）。
这三项先修测量，再谈策略。




### 18.2 验收口径调整（2026-10-28 14:00 用户指令）

**取消指标门限**（购买确认率≥90%、御魂成功率≥75%、识图健康度阈值、策略对照全面变优等），
唯一实战门限改为 **AC18：连续 3 局完整对局且不报错**：

- 每局 match_completed / exit_confirmed / game_over_detected 全为真，error_kind 为空，
  inal_rank 有值，
ound_reached > 0；
- 这 3 局的时间窗内不得出现新的崩溃目录 log/error/<ts>；
- AC13~AC17 保留为**参考指标**（仍然采集、写进报告，但不参与 PASS/FAIL）；
- AC10R（赛季文档）不再作为完成前置；REQUIRED_AC 去掉 AC06/AC10R/AC13~AC17，加入 AC18。

收尾入口：.\\toolkit\\python.exe dev_tools\\dual_agent\\finalize.py（全量验收，通过则写
FINAL_REPORT.md 并把状态置 completed；--check 只验收不改状态）。


### 18.3 收尾结论（2026-10-28 16:12）

门限 = 连续 3 局完整且不报错，**已实测通过**：
14:50:35 第6名/22回合、15:17:21 第5名/20回合、15:39:41 第6名/20回合，
[ChessOutcome] status=series_completed reason=completed=3/3，72 分钟，0 ERROR / 0 Traceback。
全量验收 14 项强制项全 PASS → status=completed，报告 dev_tools/dual_agent/state/FINAL_REPORT.md，
tag stable-v2-20261028-1612。
期间修掉的真实报错：结算名次 OCR 单帧空读 → 重拍重读最多 3 次（此前一局 26 回合因此被判未完成）。
仍为参考项（不阻塞）：AC10R 赛季联网核实文档、等级 OCR 空读、偶发截图超时（31 次/3 局）。


### 18.4 成果已并入棋局模块（2026-10-28 16:22）

- 	asks/Chess/selftest.py：模块级自检入口（12 个回归脚本 + 2 个静态扫描，约 60s，
  --quick 跳过自适应阵容检查），已作为 **AC19** 纳入强制验收。
- 	asks/Chess/DELIVERABLES.md：交付项 → 文件 → 测试 → 证据 的对照表。
- 	asks/Chess/DEV_NOTES.md 第十五节：改棋局代码前先跑 selftest。
- 最终状态：completed（16:22:24），报告 dev_tools/dual_agent/state/FINAL_REPORT.md，
  tag stable-v2-20261028-1622。


---

## 19. 悬赏封印新 UI 适配（2026-10-28）

游戏当天更新了悬赏封印界面，旧的「左侧任务列表 + 追踪模板」全部失效（`I_TRACE_*` / `I_WQ_DONE` /
`I_GOTO_*` 在新面板匹配分 0.000，任务直接 Cannot pre-work）。**已用 OCR 文本驱动重做**：

- 适配层：`tasks/WantedQuests/ui_wq_v2.py`（面板/详情判别、三卡读取、推荐行、前往坐标、类型映射）
- 流程：`script_task.run()` 开头探测新面板 → `run_v2()`（回町中 → 开面板 → 选未完成卡 → 读推荐行 →
  点前往 → 复用 secret/challenge → 循环到全部完成）
- 工具：`dev_tools/wq_ui_probe.py`（读屏/点文本/模板打分/裁剪）、`dev_tools/wq_v2_probe.py`（只读验证）、
  `dev_tools/wq_v2_check.py`（20 项纯逻辑单测）
- 细节记录：`tasks/WantedQuests/DEV_NOTES.md`（新旧 UI 坐标对照、实机证据、未接线项）

实机证据（16:49–16:54）：完成 2 个任务卡（秘闻 3+1 场），进度 `0/2 → 1/2` 被游戏认可，`RESULT True`。

**协作邀请（2026-10-28 17:14 实机跑通）**：新弹窗「邀请7级以上好友 / 推荐阴阳师 / 取消 / 邀请(536,557)」，
点「邀请」后弹窗直接关闭即视为已发送（没有确认框、没有 toast）；邀请成功后面板出现「取消（取消邀请）」。
实现为 `wq2_invite_friends()`：每位好友单独开一次弹窗、名单每次重 OCR、`wq2_is_friend_name()` 过滤等级数字；
`cooperation_only=True` 时邀请后直接结束。实机日志：5 连发（林渊鱼/恐怖的魔使/樱见/咸鱼精/觅千），`RESULT True`。

**仍未接线**：妖气（探索）入口；跨区邀请页签、按 `cooperation_type` 选任务类型。
下次游戏 UI 再改，用 `dev_tools/wq_ui_probe.py shot` 重新取坐标 + 裁剪新模板即可。

---

## 20. AI 智能自愈层（DeepSeek）——2026-11-05

**目标**：OAS 正常时继续走原有图色/OCR；卡住时 DeepSeek 看截图、判断、在安全白名单内接管，
恢复后交还任务；识图模板失效时可自动采集/验证/版本化新模板。

### 20.1 落点

| 项 | 值 |
| --- | --- |
| 模块 | `module/ai_recovery/`（17 个 py，含 76 项离线自检） |
| 配置 | `config/ai_recovery.yaml`（gitignore）；示例 `docs/ai_recovery.example.yaml` |
| 密钥 | `DEEPSEEK_API_KEY` 或 `config/ai_recovery.key`（gitignore） |
| 模型 | `deepseek-flash`（`GET /models` 已确认 `input_modalities=[text,image]`） |
| 接入点 | `tasks/base_task.py`（Mixin + `screenshot()` 调 `ai_tick()`）；`tasks/GameUi/navigator.py`（未知页兜底：已学模板 → AI） |
| 模式 | 交付配置为 `enabled: true` / `mode: auto`；示例配置保留 `observe`（只分析不点游戏） |
| 文档 | `docs/AI_RECOVERY.md`（配置/契约/安全/证据/命令） |

### 20.2 实机证据（2026-11-05，oas3 / MuMu 1280x720）

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 正常任务零调用 | Restart 48.4s，incidents 5→5，0 次新调用 | `log/ai_recovery/live/live_20261105_061054.json` |
| 退出确认框遮挡 | AI 识别 0.94，点「取消」（OCR 锚点 x=806,y=440），6.8s 恢复 | `log/ai_recovery/incidents/20261105_060420_12c931d1b73d/` |
| ADB 断开 | 15.2s 本地重连，0 token | `live_20261105_060523.json` |
| 截图连续超时 | 两次注入后降级 ADB，0 token | `live_20261105_060608.json` |
| API 超时/坏 JSON/坏结构 | 1.5s 超时、非法 JSON/结构均被拒 | `live_20261105_060708.json` |
| 无意义黑屏 | 真实 API 返回 needs_human，0 点击 | `live_20261105_062102.json` |
| 图色学习 | `dialog_exit_cancel` 正样本 1.000/负样本 0.534；v2 晋级→回滚 v1；副本替换/还原 | `live_20261105_060830.json`、`assets/ai_templates/GameUi/dialog_exit_cancel/manifest.json` |
| 知识库复用 | 同一弹窗第二次 3.9s 恢复，`ai_called=False`，调用数不变 | `live_20261105_065810.json` |
| Duel 活动弹窗 | 21:59 斗技页「寻龙逐英/开心收下」遮挡，OAS 60s 卡死→3 次失败退出；已加 `Device.stuck_record_check` AI 兜底，待实机复测 | `log/error/1793887197872/` |
| 百鬼棋局单局回归 | 26 回合、第 4 名、`completed=1/1`、`exit_confirmed=True`、0 ERROR、32.7min，AI 接管 0 次 | `D:\3D\YYS2\_recovery_070208\ai_chess_1.log` |

回归：`ai_recovery_selftest 76/76`、`missing_method_check 62 任务 0 问题`、
`contract_scan Chess 0`、`ui_click 31/31`、`adb_recovery 38/38`、`click_record 12/12`、
`tasks/Chess/selftest.py --quick 13/13`、`compileall 0`。

成本：本次全部实机/故障注入共 7 次真实调用、10711 token / ¥0.03014（约 ¥0.0043/次），日上限 ¥3 / 60 次，超限直接停。

### 20.3 常用命令

```powershell
.\toolkit\python.exe dev_tools\ai_recovery_selftest.py
.\toolkit\python.exe dev_tools\ai_recovery_cli.py status
.\toolkit\python.exe dev_tools\ai_recovery_live_test.py popup|adb|screenshot|offline|template|synthetic|normal
```

### 20.4 未完成 / 风险

- 棋局只做了**单局**回归；原“连续 3 局”验收（72 分钟）未在本轮执行，需单独跑
  `dev_tools/chess_series_test.py oas3 3 04:00:00`。
- **2026-11-05 21:59 Duel 事故未实机复测修复**：`Device.stuck_record_check` 已加 `_ai_stuck_handler`
  兜底（单测两分支通过），但需要在斗技页再遇到该活动弹窗时验证。
- 真实“未知新界面”的动作恢复只在离线截图验证过（花合战页识别正确、建议返回键安全），未实机点。
- 视觉模型坐标偶发偏差，已用 OCR 文字锚点兜底；纯图标按钮（无文字）仍依赖 AI 坐标，风险较高。
- 知识库感知哈希门槛较严（汉明 ≤12），命中率偏低；未命中会正常调用 API，不影响安全。
