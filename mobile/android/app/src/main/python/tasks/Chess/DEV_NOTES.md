# 百鬼棋局模块开发记录

> 本文件记录百鬼棋局的架构、已知故障根因、修复与验证状态。改代码前后都请先读这里，
> 避免重复踩坑。最后更新：2026-10-27。

## 一、模块结构（27 个文件 / 约 8200 行）

| 文件 | 职责 |
| --- | --- |
| `script_task.py` | 任务入口、整局与回合编排（`ChessLegacyScriptTask` + `ScriptTask`） |
| `page.py` | 页面定义与跳转（`page_chess` / `page_chess_battle` / `page_chess_result`） |
| `assets.py` | 棋局专用模板 |
| `runtime/state_loop.py` | 状态机主循环（识模式 → 决策 → 执行） |
| `runtime/round_state.py` | 回合/阶段状态与缓存 |
| `runtime/economy.py` | 百鬼币、购买、出售、刷新、升级 |
| `runtime/hand_operations.py` | 手牌/商店操作（含符咒、御魂选择） |
| `runtime/frame_cards.py` | 单帧卡片识别（依赖 `runframe`） |
| `runtime/recognition.py` / `press_and_drag.py` | 界面识别、拖拽布阵 |
| `runtime/hyakki_draft.py` | 选将/羁绊相关 |
| `runtime/scout.py` | 对手阵容侦察 |
| `strategy/adaptive.py` | 随机应变策略 |
| `strategy/lineup*.py` / `shikigami_catalog.py` / `soul_catalog.py` / `grigri.py` | 阵容、式神、御魂、符咒知识库 |

继承关系要点：`ScriptTask(ChessStateLoopMixin, ChessLegacyScriptTask)`，
后者混入 6 个 runtime mixin + `GameUi` + `GeneralBattle` + `ChessAssets`。
**棋局页面标志在 `PageCheckAssets`，不在 `GameUiAssets`**（页面模块用别名引用）。

## 二、故障根因表（按 `log/error/*` 聚合，2026-10-24 ~ 10-27）

| 次数 | 报错 | 根因 | 处理 |
| --- | --- | --- | --- |
| 5 | `ScriptTask has no attribute 'chess_result_flow_visible'` | `GameUi` 基类丢失 `ChessBattleNavigationMixin` | 已补基类 |
| 5 | `FileNotFoundError ./tasks/GameUi/page/page_chess_exit.png` | 模板图片缺失 | 图片现已在仓库 |
| 2 | `ScriptTask has no attribute 'runframe'` | reset 把 `BaseTask.runframe` 冲掉 | 已从历史快照恢复 |
| 2 | `Device has no attribute 'image_frame_id'` | 服务版接口被回退 | 已恢复帧号属性 |
| 2 | `IndexError: string index out of range`（非棋局） | 文本解析越界 | 待查 |
| 1 | `ScriptTask has no attribute 'I_CHESS_EXIT'` | 资源定义丢失 | 已补 `GameUiAssets` |
| 1 | `RuleImage.match_all_any() unexpected kw 'frame_id'` | 匹配接口签名被回退 | 三个接口恢复 `frame_id` |
| 1 | `GeneralBattleAssets has no attribute 'I_EXIT_ENSURE'` | 资源定义丢失 | 已补回 |
| 1 | `ScriptTask has no attribute 'I_CHECK_CHESS'` | 用到 `PageCheckAssets` 上的标志 | 已确认在正确类 |
| 1 | `ui_click_until_disappear() unexpected kw 'click_t'` | 旧签名残留 | 当前签名 `(click, interval)`，调用已兼容 |
| 1 | `Device has no attribute '_format_action_duration'` | 拖拽日志辅助方法缺失 | 已在 control.py |
| 1 | `Device has no attribute '_invalidate_image_batch_cache'` | 拖拽后清缓存接口缺失 | 已补（自增帧号） |

**共同根因**：2026-10-26 OAS 自带更新器执行 `git reset --hard origin/master`，
把 `tasks/Component/*`、`module/atom/image.py`、`module/device/*` 等公共组件回退到旧版本，
而棋局代码按新接口调用。详见 `references/repo-reset-recovery.md`（技能库）。

## 三、验证状态

| 层级 | 方式 | 结果 |
| --- | --- | --- |
| 静态 | `dev_tools/missing_method_check.py` | 62 任务 0 问题 |
| 导入 | 62 个 `script_task` 全量 import | 0 失败 |
| 编译 | `compileall tasks module` | ok |
| 模板 | `dev_tools/asset_file_check.py` | 2145 引用，缺失 2（均误报） |
| 接口 | `dev_tools/oas_fix_smoke_test.py`（真实截图） | PASSED |
| 实机-登录 | `run_task_once.py oas3 Restart` | True（登录→庭院） |
| 实机-棋局 | `run_task_once.py oas3 Chess` | 3 分钟 0 错误，回合推进到第 4 回 |
| 连续 3 局 | 未做 | **未验证** |

## 四、参考资料与限制

- 教学视频 `https://www.bilibili.com/video/BV1jRZ9BtEVn/`：**本会话无法观看/取字幕**，
  未采用视频内容，规则以游戏内实际界面与代码已实现逻辑为准。
- 规则参考：`https://yys.huijiwiki.com/wiki/百鬼棋局`、`https://a.4399.cn/gg/54459041_99319.html`。
- 注意区分：**百鬼棋局**（自走棋）/ 百鬼弈（对战）/ 对弈竞猜（预测）/ 百鬼夜行（撒豆）。

## 五、待办

1. 连续 3 局实机运行 + 卡死/异常恢复验证。
2. `IndexError: string index out of range` 定位（非棋局路径）。
3. 棋局界面在新赛季（2026-10-21 更新）是否变化，需实机核对模板。
4. 策略层（经济/阵容/站位）仍依赖既有规则，未按新赛季做适配。
## 六、自动更新覆盖事故与防护（2026-10-27 补记）

**事故**：OAS 自带更新器执行 `git reset --hard origin/master` 两次，把整合版分支、未提交改动、
gitignore 的 `config/*.json` 一起冲掉；随后 3 天内棋局类任务连续报 12 类 AttributeError/TypeError。

**受影响**：`tasks/Component/*` 公共组件、`module/atom/image.py`（远程匹配版→本地版）、
`module/device/*`（`image_frame_id` 等接口）、多个 `assets.py` 的资源定义。

**防护（已落地）**：
1. `config/deploy.yaml`：`AutoUpdate: false`、`KeepLocalChanges: true`
2. `deploy/config.py`：`AutoUpdate` 默认值改为 `False`
3. `deploy/git.py`：新增 `_update_guard()`，更新前检查「当前分支 == 目标分支 / 无本地未推送提交 /
   无未提交改动」，任一不满足就拒绝更新并打日志
4. `.git/hooks/reference-transaction`：ref 变更时把旧 sha 存进 `refs/autobackup/`
5. `dev_tools/repo_autosave.py` + 计划任务：每 5 分钟自动提交、每日 bundle
6. 回归测试：`dev_tools/updater_guard_check.py`（三分支不一致/脏工作区/干净仓库）

**结论**：以后即使有人手动跑更新器，也不会再静默覆盖整合版代码；被拒绝时日志会写明原因。
## 七、首局完整对局通过（2026-10-27 09:59）

真实日志锚点（验证一局是否真的完成，缺一不可）：

```
Chess game-over rank OCR: [第三名] -> 第3名
Chess game ended: 第3名
Chess completed games: 1/1
Click (451,617) @ PAGE_CHESS_EXIT_TO_LOBBY     ← 退出结算页
Click (118,651) @ PAGE_CHESS_RANK_GOTO_LOBBY   ← 回到棋局大厅
Chess task loop finished: completed=1, target=1
[ChessOutcome] status=series_completed reason=completed=1/1
```

- 最高回合 **26**，全程 0 ERROR/Traceback
- `log/chess_outcome.jsonl` 追加 `{"status":"series_completed","reason":"completed=1/1"}`
- 判定门槛（提交 d66b61a8）：`completed>=target_count` **且** `_last_game_rank` 为 int（结算 OCR 证据），
  否则记 `failed`——避免被 Restart/中断接管的局被记成成功
- 该局使用的 `ui_click_until_disappear` 已从快照恢复完整实现（提交 21bef1e4），
  `**kwargs` 已删除（未知参数必须抛错）

## 八、逐局对局记录 `log/chess_matches.jsonl`（2026-10-27 新增）

`_mark_outcome()` 只在**整个任务结束**时写一条，回答不了「第几局、打到第几回合、
名次多少、有没有真的退回大厅、这局是被 Restart 接管还是跑完的」。因此每局单独
落一条 JSON：

| 字段 | 含义 | 证据来源 |
| --- | --- | --- |
| `index` / `target` | 第几局 / 目标局数 | 循环计数 |
| `mode` | `normal` / `rank_protection` | 保段位开关 |
| `round_reached` | 本局最高回合 | `run_one_round()` 每回合刷新 |
| `final_rank` | 结算名次 | `_read_game_rank()` OCR（`第N名`） |
| `game_over_detected` | 是否识别到结算页 | `I_GAME_OVER_1/2` |
| `exit_confirmed` | 是否真的回到棋局大厅 | `return_to_chess_lobby()` 返回 True（内部逐帧确认 `I_CHECK_CHESS`） |
| `match_completed` | 是否计入 `completed` | 见下 |
| `error_kind` / `error_reason` | 异常分类 | `device_disconnected` / `game_not_running` / `task_end` / `GameStuckError` / `rank_ocr_unavailable` |
| `duration_s` | 本局耗时 | 单调时钟 |

**计入完成的门槛（`match_completed=True`）**：结算页识别到 `final_rank`（1~8）
才 `completed += 1`。读不到名次的局记录 `rank_ocr_unavailable`，退回大厅后重开
一局；连续 `UNVERIFIED_MATCH_LIMIT=2` 局读不到名次则结束任务（避免白烧局数）。

**任务级成功门槛**（`[ChessOutcome]`）：`completed >= target_count` **且**
最近一局 `final_rank` 是 int **且** `exit_confirmed=True`。三者缺一记 `failed`。

## 九、ADB 掉线自动重连（2026-10-27 新增）

### 9.1 事实前提

- 棋局截图走 **nemu_ipc**、点击走 **minitouch**，两者都不完全依赖 adb；
  只有 `app_is_running()`（`app_current_adb/uiautomator2`）和部分 `adb_shell`
  依赖 adb。所以 **ADB 掉线时画面还在动、点击还能生效**，旧代码却把它当
  「Game died」→ Restart 重开，甚至 `RequestHumanTakeover` → `exit(1)`。
- MuMu 实例信息 `MuMuManager.exe info -v all` 是权威来源：
  `is_android_started` / `player_state` / `adb_host_ip` / `adb_port`。
  实例重启会换端口，配置里的旧 serial 永远连不上。

### 9.2 实现

| 文件 | 职责 |
| --- | --- |
| `module/device/mumu_state.py` | 查 `MuMuManager info -v all`（5s 缓存），按 serial 端口匹配实例，给出 `adb_endpoint` |
| `module/device/reconnect.py` | `ConnectionRecovery` mixin：状态分类、有限次重连阶梯、serial 换绑、事件落盘 |
| `module/exception.py` | 新增 `DeviceDisconnectedError(GameNotRunningError)`：区分「设备掉线」与「游戏真没跑」 |
| `module/device/device.py` | `Device` 混入 `ConnectionRecovery`；`stuck_record_check()` 先恢复再抛异常 |

**分类**（`classify_connection()`）：`ok` / `adb_server_down` / `adb_missing` /
`adb_offline` / `adb_unauthorized` / `emulator_stopped` / `unknown`。

**重连阶梯**（`recover_connection(stage=...)`）：

1. 查 adb 状态；adb 服务无响应 → `adb_restart()`
2. MuMu 报告实例未启动 → 直接放弃（交给 Restart，重连无意义）
3. 最多 3 轮，每轮依次尝试「配置 serial」→「MuMuManager 报告的 `host:port`」
4. 连通且状态 `device` → 若地址与配置不同则**换绑 serial**（写回配置、作废旧会话缓存）
5. 每次事件写 `log/device_connection.jsonl`（stage/adb_before/attempts/recovered/emulator）

**异常语义**：`stuck_record_check()` 里 `app_is_running()` 为假时

- adb 健康 → 仍是 `GameNotRunningError('Game died')`（游戏真没跑）
- adb 掉线且重连成功 + 游戏仍在前台 → 继续任务（不抛异常）
- adb 掉线且重连失败 → `DeviceDisconnectedError`（继承 `GameNotRunningError`，
  调度器照旧转 `Restart`，但对局层能区分原因）

### 9.3 验证状态

- 单元级：`dev_tools/adb_recovery_unit.py` **35 项全过**（分类/阶梯/换绑/异常类型）
- 实机级：`dev_tools/adb_cycle_check.py`（`status` / `cut` / `cut-server` / `port`）
  ——**本次会话待跑**（棋局空档才可执行）

## 十、回归测试清单（本次新增）

| 工具 | 覆盖 | 结果 |
| --- | --- | --- |
| `dev_tools/ui_click_regression.py` | `ui_click_until_disappear` 行为：click_target/visible/guard/timeout/max_clicks/confirm_frames、RuleClick 不被当图像、未知参数抛 TypeError | **31 项全过** |
| `dev_tools/adb_recovery_unit.py` | ADB 分类与重连阶梯、异常类型选择 | **35 项全过** |
| `dev_tools/contract_scan.py` | 任务对 BaseTask/GameUi 的调用契约 | Chess 0 问题 |
| `dev_tools/missing_method_check.py` | 62 任务 MRO 方法存在性 | 0 问题 |

## 十一、已发现的配置矛盾：`limit_time` 与 `run_count`

`oas3` 当前 `chess.chess_config.run_count=3`、`limit_time=00:30:00`。
一局要 25~40 分钟，30 分钟预算会在第 1 局结束后就跳出循环（时间检查只在
**开新局之前**），因此**永远测不到连续三局**，最后 `completed=1/3` 记 `failed`。

处置：`dev_tools/chess_series_test.py` 在**内存里**覆盖 `run_count`/`limit_time`
跑连测，`finally` 恢复原值并保存，不污染 `config/oas3.json`。

```powershell
cmd /c start "" /b "<repo>\toolkit\python.exe" dev_tools\chess_series_test.py oas3 3 04:00:00 --log D:\3D\YYS2\_recovery_070208\chess_series3.log
```

## 十二、2026-10-27 实机证据（本轮）

### 12.1 两局连跑（旧代码，pid 15112，10:24:40 → 11:26:23，61 分钟）

日志 `D:\3D\YYS2\_recovery_070208\chess7.out.log`，10,019 行，**0 ERROR / 0 Traceback**。
`dev_tools/chess_log_report.py` 自动判定：

```
 #  start     end      round rank ended exit lobby count verified
 1  10:52:34  10:52:34    25    1     Y     Y     Y    1/3   YES
 2  11:25:56  11:25:56    31    2     Y     Y     Y    2/3   YES
 outcome: status=failed reason=completed=2/3 settled=True
 Chess task time reached; stop before starting next game
```

- 第 1 局 **第 1 名**（25 回合）、第 2 局 **第 2 名**（31 回合），两局都完成
  「结算 OCR → 退出结算页 → 返回大厅」全流程，**退出后确实回到大厅并重新开局**
- 第三局被 `limit_time=00:30:00` 挡下（61 分钟 > 30 分钟），`completed=2/3` 记 `failed`
  ——这是**预算不足**，不是对局失败；第三局的连续运行需要 §十一 的连测工具
- 途中 `Chess level OCR invalid: []` 13 次、`lineup capacity could not be confirmed` 13 次
  （旧代码会整轮停止上阵，本轮已修，见 §7.1）

### 12.2 ADB 断连恢复三场景（实机，`dev_tools/adb_cycle_check.py`）

| 场景 | 做法 | 结果 |
| --- | --- | --- |
| `cut` | `adb disconnect 127.0.0.1:16416` | **PASSED**，0.17s 恢复，截图 62ms，前台=游戏 |
| `port` | serial 改成不存在的 16999 | **PASSED**，2.20s：先试 16999 失败 → 用 MuMuManager 报告的 16416 连上 → **换绑 serial 并写回配置** |
| `cut-server` | `adb kill-server` | **PASSED**，4.27s：检测 adb 服务不可用 → `adb_restart()` → 连上 |
| `guard` | 压卡死计时器 + 断 ADB | **PASSED**：① 掉线恢复后不误判 Game died（抛 GameStuckError，任务继续）；② ADB 正常但游戏没跑 → `GameNotRunningError('Game died')`；③ 重连失败 → `DeviceDisconnectedError` |

事件落盘 `log/device_connection.jsonl`（stage / adb_before / attempts / recovered / emulator）。

> 细节：真实日志显示 `app_is_running()` 内部 adb 方法自带 `@retry` 会先自行 `adb_reconnect()`
> 解决「同一 serial 掉线」；`ConnectionRecovery` 的价值在它解决不了的两类情况
> ——**实例换端口**（配置 serial 永久失效）和**adb 服务挂掉**，并把失败分类成
> `DeviceDisconnectedError` 而不是 `RequestHumanTakeover → exit(1)`。

### 12.3 单元级回归（全部通过）

| 工具 | 项数 | 结果 |
| --- | --- | --- |
| `dev_tools/ui_click_regression.py` | 31 | PASSED |
| `dev_tools/adb_recovery_unit.py` | 35 | PASSED |
| `dev_tools/chess_level_cache_check.py` | 9 | PASSED |
| `dev_tools/contract_scan.py Chess` | - | 0 问题 |
| `dev_tools/missing_method_check.py` | 62 任务 | 0 问题 |

### 12.4 新发现的识别情况（已核实，勿误判）

- **`[CHESS_MODE]` 读到数字不是 bug**：`_read_primary_round_layout()` 的设计是
  「主 mode ROI 读到数字 ⇒ 处于**前三回合专用布局**」，此时改用 `O_ROUND_2` /
  `O_CHESS_MODE_2` 读取。实测第 1~3 回合主 ROI 读到 `31/83`、备用 ROI 读到 `战`
  `1回目`，正是这套判据在起作用；第 5 回合主 ROI 直接读到 `5回目` / `待！`。
  用 `dev_tools/chess_roi_probe.py --live oas3` 对实时帧逐 ROI 验证过：
  ```
  round (469,0,67,30) -> [5回目]      mode (741,0,32,28) -> [待！]
  round_2 (507,0,62,30) -> [目]       mode_2 (706,2,32,28) -> []
  level (131,680,47,30) -> [三阶]     gold (1198,640,43,32) -> [43]
  ```
  → 结论：**布局判据工作正常，不需要重标 ROI**（先前"主布局错位"的判断已撤回）。
- 商店 `未识别` 比例 ~7%（183 次整页扫描中 47 格）**仍未定位**。已加诊断：低于阈值时输出
  最像的 3 个候选与分数（`Chess store portrait below threshold ... top candidates: ...`），
  下次连跑日志即可区分「图鉴缺条目」与「动画/遮挡」。
- 日志编码坑：shell 重定向产出的日志是 **UTF-16LE(BOM)**，按 UTF-8 读会一个字都匹配不到；
  `chess_log_report.py` 会自动识别，连测工具改为自写 UTF-8。

### 12.5 逐局记录实测发现并修复的缺陷

`log/chess_matches.jsonl` 第一条真实记录（2026-10-27 12:10:08，第 1 局 32 回合第 2 名）：

```json
{"index":1,"target":3,"mode":"normal","round_reached":0,"final_rank":2,
 "game_over_detected":true,"exit_confirmed":true,"match_completed":true,
 "error_kind":null,"duration_s":2186.0}
```

- `round_reached=0` 暴露了一个真实缺陷：实际执行的是 `ChessStateLoopMixin.run_one_round`
  （MRO 在 `ChessLegacyScriptTask.run_one_round` 之前），而 `_match_round_reached`
  只加在了 legacy 版本里。
- 修复：`state_loop.run_one_round` 同步维护；`_finish_match()` 额外取
  `_current_round_no` 兜底（double insurance）。
- 其余字段（名次/结算识别/退出确认/完成门控/耗时）均按预期写入 → 逐局记录机制可用。
- 日志版验收表不受此缺陷影响：`chess_log_report.py` 的回合数来自日志里的
  `[ROUND_2] N回目` / `[ROUND] N回目`，实测第 1 局 32 回合。
- 另修 `chess_log_report.py`：新日志同时有 `[ChessMatch]` 打点和锚点累加器会重复计数，
  现在有精确记录时只保留精确记录。

### 12.6 `GameTooManyClickError` 把整局跑挂（12:34 实测，已修）

第 2 局在第 22 回合被 `GameTooManyClickError: Too many click for a button: shikigami_1` 终止，
逐局记录正确写下 `match_completed=false / error_kind=GameTooManyClickError`（门控生效）。

- **根因**：`Control.click()` 往点击记录里塞的是 **`control_name` 字符串**（即 `RuleClick.name`），
  而代码里 `click_record_remove(click_rule)` 传的是规则对象，`str(obj)` 是
  `<module.atom.click.RuleClick object at 0x...>` —— **一条也删不掉**。同一商店格反复购买
  （每次最多 3 下）累计到 10 下就触发设备层的全局保护，整局被判卡死。
  同样的对象传参错误还出现在 `I_RESTART_AGAIN` / `I_BACK_RED` / 六界选择。
- **修复**：`economy._clear_slot_click_record()` 按 `.name` 清理，且**每次购买尝试前**都清；
  新增 `SHOP_BUY_MAX_ATTEMPTS=6` 上限（到限放弃该格而不是死循环）；
  其余三处改用 `.name` / `getattr(target,'name',target)`。
- **回归**：`dev_tools/click_record_check.py` 12 项（含「传对象是 no-op」反例与 10 次阈值验证）。

### 12.7 nemu_ipc 卡死导致任务启动即失败（12:43 实测，已修）

同一时刻：`adb exec-out screencap` 正常（2 MB PNG）、前台是游戏，但 `nemu_capture_display`
连续 0.15s 超时，重试 3 次后抛 `RequestHumanTakeover` → 任务直接退出（`status=failed`）。

- **修复**：`Device.screenshot()` 捕获 `RequestHumanTakeover` 后用 ADB 补一帧；成功则置
  `_screenshot_force_adb`，本会话后续帧直接走 ADB（不再每帧等超时），并 WARNING 记录原因；
  **不改写用户配置** `screenshot_method`。ADB 也失败时才向上抛。
- **实测**：`dev_tools/screenshot_fallback_check.py` 8 项通过；12:48 的第三轮连跑启动时
  真的走到这条降级（日志 `Screenshot method fell back to ADB`），任务继续跑而不是退出。

### 12.8 连续三局连跑通过（2026-10-27 13:59，新代码）

`dev_tools/chess_series_test.py oas3 3 04:00:00`，pid 36872，12:48 → 13:59（71.3 分钟），
日志 `D:\3D\YYS2\_recovery_070208\chess_series3_run3_1248.log`（UTF-8，6,834 行，0 ERROR / 0 Traceback）。

`dev_tools/chess_log_report.py` 判定：

```
 #  start     end       round rank ended exit lobby count verified
 1  12:48:54  13:14:16    22    6     Y    Y    Y    1/3   YES
 2  13:14:17  13:35:41    20    6     Y    Y    Y    2/3   YES
 3  13:35:42  13:59:24    20    7     Y    Y    Y    3/3   YES
 outcome: status=series_completed reason=completed=3/3 settled=True
```

```
[ChessMatch] end index=1 round=22 rank=6 game_over=True exit_confirmed=True completed=True
[ChessMatch] end index=2 round=20 rank=6 game_over=True exit_confirmed=True completed=True
[ChessMatch] end index=3 round=20 rank=7 game_over=True exit_confirmed=True completed=True
Chess task loop finished: completed=3, target=3
[ChessOutcome] status=series_completed reason=completed=3/3 settled=True exit_confirmed=True matches=3/3 verified limit_time=4:00:00
RESULT Chess -> True
```

- **每局都有独立证据**：结算名次 OCR、退出结算页、返回大厅；`round_reached` 正确（22/20/20）
- `level_ocr_invalid=0`、`capacity_blocked=0`（修复前同类日志是 13 次）
- 全程无 `GameTooManyClickError`（点击记录修复生效）
- 启动时 nemu_ipc 卡死 → **降级 ADB 截图后整轮继续跑**（`fell back to ADB` ×1），未中断
- 配置只被内存覆盖，结束后 `[ChessSeries] restored run_count=3 limit_time=00:30:00`

**观察（不下结论）**：本轮全程走 ADB 截图，三局名次 6/6/7，明显低于 nemu 正常时的 1/2；
同时 shop 扫描 142 次 / refresh 17~20 次，也远少于 nemu 那轮的 183 次 / 66 次。
ADB 单帧更慢 → 备阶段 15 秒窗口内能做的经济动作更少，是**可能**原因之一，
但对手随机、样本仅 3 局，需 nemu 正常时再跑一组对照才能定论。
**排查提示**：发现名次异常下降时，先 grep 日志有没有 `fell back to ADB`。

## 十三、决策审计与识图真相（2026-10-27 14:20 起）

新增证据工具：`dev_tools/chess_decision_audit.py`（逐局逐回合的买卖/刷新/升级/上阵
时间线 + 异常清单）、`dev_tools/capture_compare.py`（同屏 nemu vs ADB 对照）、
`dev_tools/shop_frame_scan.py`（离线重跑商店识别、按目录汇总 EMPTY/失败率）。

### 13.1 三局 6/6/7 的证据链（不是「策略参数没调好」）

| 指标（每局） | run3（ADB 帧） | chess7（nemu 帧，第1/2名） |
| --- | --- | --- |
| 上阵次数 | **4 / 1 / 7** | 8.5 |
| 购买尝试 / 成功 | 13 / 2 | 32 / 30 |
| `shop could not be closed` | 18 | 0 |
| `before soul phase: shop is open` | 20 | 2 |
| 刷新次数 | 16 | 48 |
| `purchase was not confirmed` | 32 | 4 |
| 金币读数 3s 内 +20 跳变 | 3 | 9 |

因果链（按时间顺序，全部有日志行）：

1. 备阶段先跑「刷新/升级」，把时间用到 `remaining < 15`，**商店仍然开着**；
2. 上阵/上魂要求商店关闭 → 阶段已切 `待` → `Stop Chess preparation before soul phase`
   （20 次）→ **上阵整段被跳过**；
3. 棋盘只有 1~7 个单位 → 战力不足 → 第 6/7 名；
4. 手牌长期偏满 → 购买更难确认（`purchase not confirmed` 11 次/局）→ 金币积压、
   还会为了腾位置卖掉阵容卡（`Sell Chess lineup card ... name=雪女/灵海蝶`）。

### 13.2 「商店 7% 未识别」的真相：是空格子

`shop_frame_scan.py --summary` 对 11 张「未识别」样本（55 个格子）离线复现：

```
EMPTY(空格子)      14  (25.5%)      ← 低阶商店只出 3~4 张卡，或卡被买走后留空
card+recognized    41  (74.5%)
card_but_unknown    0  (0.0%)      ← 真实识别失败率
```

→ 真实失败率 **0%**。已修：空格子记 `空`（不再计入失败、不再落样本）；
判定用 `_frame_card_scan('store')` 的卡框（未识别出名字的卡也有 roi）。

### 13.3 nemu_ipc vs ADB（同屏对照，`capture_compare.py`）

| 项 | nemu_ipc | ADB |
| --- | --- | --- |
| 单次 screenshot（含限流） | 213 ms | **912 ms（4.3×）** |
| 清晰度（Laplacian 方差） | 2295 | 2516 |
| 模板分数（I_REFRESH/I_MARKET/…） | 均远低于阈值 | 同 |
| OCR（回合/模式/等级/金币） | 一致 | 一致 |

→ ADB 的代价是**决策频率**（每秒动作数降到 1/4），不是识别质量。
ADB 降级是必要的兜底（nemu 真卡死时靠它跑完三局），但**不应长期停留**：
发现 `fell back to ADB` 且名次下滑时，应重启游戏/模拟器恢复 nemu。

### 13.4 本轮已修（含验证状态）

| 改动 | 依据 | 验证 |
| --- | --- | --- |
| 上阵优先于经济 + 经济结束关店 | 20 次上阵被跳过 | 单局实机（见 13.5） |
| 金币读数防抖（3s 内 +20 视为误读） | 12 例跳变（8→98 等） | 单测 12 项 |
| 空格子记 `空` | 55 格 0 真实失败 | 离线回归 |
| 商店失败/符咒冲突样本落盘 | 无法凭日志判真伪 | 已产出 11 份样本 |
| `click_record_remove_rule` 统一按名字删 | 3 处 no-op | 单测 12 项 |
| 六道不随人数加权（×0.35） | 官方公告 | 单测（边际 3.5 vs 10.0） |
| `season_data.py` 赛季数据 + 出处 | 官方 10-21 公告 | 数据核对 |

### 13.5 验证两轮：上阵优先后的单局（14:35–15:33）

`dev_tools/chess_series_test.py oas3 1 02:00:00` 各跑一局：

| 指标（每局） | run3 修前（3 局） | validate1 | validate2（+金币两帧确认） |
| --- | --- | --- | --- |
| 名次 | 6 / 6 / 7 | **3** | **4** |
| 回合 | 22/20/20 | 28 | 23 |
| 耗时 | 21~25 分钟 | 30.0 分钟 | 24.1 分钟 |
| 上阵次数 | 4 / 1 / 7 | **8** | **7** |
| 购买尝试/确认 | 13 / 2 | 34 / 33 | 19 / 19 |
| 购买未确认 | 10~11 | 1 | **0** |
| 刷新 | 16 | 46 | 33 |
| 升阶点击 | 18 | 23 | 20 |
| 到达阶数 | 6~7 | 8 | 8 |
| 商店扫描 | 47 | 92 | 73 |
| `unknown_cells`（旧口径） | 12~20 | 28 | **0**（空格子已区分） |
| 金币误读拦截 / 确认 | - | - | 8 / 6 |

→ 购买确认率 **15% → 100%**，上阵次数 **~4 → 7~8**，两局名次 3/4（修前 6/6/7）。
`level_ocr_invalid` 仍有 11~12 次，但 `capacity_unknown=0`（等级缓存兜底生效）。

**必须说明的局限**：
- 两局样本，名次受对手随机影响，不能宣称「策略已优化到第 3 名」；
  能确认的是**过程指标**（买得到、上得了、买得准）稳定改善。
- 下一步用 3 局连跑复核（§13.7），并补充「早期低费过渡位」。

### 13.6 金币两帧确认（15:08 起，实测生效）

`_stabilize_gold_reading()`：突增 ≥20 先记候选，**下一帧仍接近才承认**；
回落则采纳真值；候选 10 秒过期。validate2 实测拦截 8 次、确认真实收入 6 次
（`8→96→2` 这类来回跳不再被当成金币）。单测 13 项。

### 13.7 尚未解决（按优先级，均有证据）

1. **早期买不到东西**：自适应阵容取「最强 8 张」，多为 2~5 费；1~10 回合商店只出
   1~3 费 → 实测一局前 10 回合只有 0~3 次购买、0 次上阵。方案：给阵容加 1~2 个
   「同羁绊低费过渡位」并在上阵时动态分配空位（会动到 `shikigami_deploy_positions`
   与站位回收，需单独一轮实机验证）。
2. **经济吃掉补上阵的窗口**：经济停止阈值 `remaining < 15` 太晚，补上阵常因阶段切走被跳过。
   方案：阈值提到 25s，或给上阵预留固定预算。
3. **符咒 OCR/图标冲突**（朱印 vs 纹章，收益表分开）：已落样本，需人工核对一张图定规则。

## 十四、策略与识图优化（2026-10-27 22:20 起，目标从「能跑完」转「更会玩」）

稳定基线：`806e0352`（连续三局通过 + 两轮单局验证），记录在 `log/stable_baseline.txt`。

### 14.1 前期低费过渡位（解决「前期买不到、棋盘空着」）

**证据**：`dev_tools/chess_shop_cost_curve.py` 统计 5 局日志、约 1900 张商店卡：

```
阶1 → 只有 1 费          阶2~3 → 最高 2 费（1 费占 78~100%）
阶4 → 3 费（1 费 52~67%）阶5~6 → 4 费（1 费 29~53%）
阶7~8 → 5 费（1 费 15~26%）
按回合：round 1-5 最高 2 费、6-10 最高 3~4 费、11-16 最高 5 费
```

数据落在 `strategy/season_data.py`（`SHOP_MAX_COST_BY_LEVEL` / `shop_max_cost()`）。
自适应阵容原来取「最强 8 张」，多为 2~5 费 → 前 10 回合 0~3 次购买、0 次上阵。

**实现**：
- `adaptive._transition_candidates()`：阶 ≤4（`TRANSITION_MAX_LEVEL`）时从
  「当期买得到的同羁绊低费单位」里选 ≤2 个（`TRANSITION_SLOTS`），
  优先已拥有/已在手，其次费用低；`Plan.transition` 记录它们
- `recognition.collect_adaptive_observation()` 之前**没填 level**（恒为 0，过渡逻辑永不触发），
  现在真实读取，并把 level 纳入重算签名
- `build_strategy()` 把 `transition` 写进策略（romaji），站位仍由 `_assign_positions` 统一分配
- 人口统计改为 `max(阵容内已上阵, 已上阵站位)`：过渡位/离队单位也占位置，
  只数名单会超编上阵
- 淘汰：`_release_stale_transition_units()` 只收回「登记过且已不在阵容」的过渡位
  （`_recall_board_unit_to_hand` 拖回手牌 → 战阶段手牌清理卖掉回血）；
  满员或目标站位被占时先收过渡位，核心卡绝不触碰

**验证**：`dev_tools/chess_transition_check.py` 31 项；实机 validate3 第 1 回合即出现
`Chess transition unit deployed: 雪童子 -> set 6`。

### 14.2 动态阶段时间预算（替代写死 15s）

- `_handle_preparation()` 计时 deploy / soul 实测耗时，`_update_phase_cost()` 做 EMA
- `_phase_time_budget() = deploy + soul + 2s + margin(3s)`，下限 12s；
  无历史时退回 20s。ADB 降级（0.9s/帧）时实测耗时变大 → 预算自动上调
- 备/战两个经济循环都改用该预算，日志输出 `budget=` 与成本明细
- 测试：`dev_tools/chess_phase_budget_check.py` 7 项

### 14.3 符咒 OCR/图标冲突裁决（依据实盘样本）

`dev_tools/grigri_conflict_audit.py --replay` 复盘 12 份冲突样本：

- `朱印 / 纹章` 是两种不同道具（收益表分开），但**图标模板几乎无法区分**：
  同一张卡两种模板分数差只有 0.000~0.010（分裂纹章 0.867 vs 分裂朱印 0.857）
- OCR 文本的类型字可读（`锻刃朱印`、`强体纹章` 映射分 1.0）

→ 规则（`strategy/grigri.resolve_conflict()`）：
1. 图标前二差距 ≥0.03 且最高分 ≥0.80、同时文字映射 <0.90 → **信图标**
   （实盘：`剥金符咒·贰 0.836/m0.179` vs OCR `返金符咒 0.75`，原始文本丢了「剥」）
2. 其余一律**信文字**（覆盖所有 朱印/纹章 型冲突）
3. 冲突样本改为存**整帧**+名称条（旧样本只存面板，名字 ROI 在面板外，无法重跑 OCR）

测试：`dev_tools/grigri_conflict_check.py` 21 项（含 11 条实盘 fixture）。

### 14.4 御魂装配失败分类

`equip_souls_from_hand()` 由只报 "not confirmed" 改为输出 `reason=`：
`no_soul_in_hand` / `no_eligible_target` / `target_position_unknown` /
`hand_count_unchanged` / `hand_count_mismatch` / `mode_changed`，
每轮再打一条 summary（equipped / souls_left / verified_targets / skip_reasons）。
先看清原因，再决定是否改操作，而不是靠重复拖拽。

### 14.5 赛季数据核对

`dev_tools/season_data_check.py` 41 项：赛季元数据、新增 7 式神的羁绊标注、
六道/遗念/回响/常世 在强度表与图鉴里、新符咒 3 个、御魂表、费用曲线自洽，
以及 **5 份实机日志里出现过的 63 种式神 + 81 种符咒是否都能查到**（全部命中）。

### 14.6 「棋盘空位/错过上阵」前提被证据推翻（2026-10-28 复核）

**原假设**：`no deployable lineup hand card candidate` 出现很多 = 棋盘空着，应该改上阵时序
（阶段切换前补位、有空位就上）。

**实测（`chess_validate4_2234.log` 两局审计 + 逐回合统计）**：
- 第 1 局结束时棋盘上有 **10 个单位**（8 阶人口 8 + 特殊单位），即 `no_candidate` 大量出现时
  棋盘其实**已满**；`no_candidate=33` 里绝大部分属于正常现象。
- 上阵时序**已经是「先上阵再经济」**：`phase_cut=4 / skip_deploy=1`（run3 时期是 20 次），
  「商店没关导致上阵被跳过」这条链路已修好，不需要再改时序。
- 真正的浪费在**买卡**：`凤凰火 买 3 卖 3`、`雪童子 买 7 上 2`、`鲸汐千姬 买 8 上 2`
  —— 过渡位在阵容换血后变成非阵容卡，被战阶段手牌清理卖掉（金币白花）。

**结论**：不改上阵时序；改为
1. `economy.py`：过渡位只买一张（`TRANSITION_COPY_LIMIT=1`），正式阵容卡仍按升星需要买；
2. `hand_operations.py`：给 `no_candidate` 加原因分类
   （`hand_empty` / `hand_has_no_lineup_card` / `lineup_cards_already_deployed` / 其它），
   以后再出现「空位」可以直接看是「没牌」还是「已满」，不用再猜；
3. `chess_decision_audit.py`：修正过渡位上阵日志正则（`-> set N`）+ 统计 `deploy_gap`。

**验证**：`dev_tools/chess_buy_deploy_guard_test.py` 14 项（纯逻辑）；实机对照见 §14.7。

### 14.7 截图 IPC 卡死的真因：多进程抢 MuMu IPC（2026-10-28 10:30）

**现象**：09:50-10:28 一局里 `screenshot() call timeout` 累计 **102 次**，捕获耗时在
0.04s 与 4.5s/超时之间跳变；同一时刻 `adb exec-out screencap` 正常（0.93s）。

**实测**：
- 在一个进程里调用 `nemu_ipc_release()` 后，同进程随后 5 帧 **0.034~0.064s**；
- 只要有第二个 OAS 进程（探测脚本、Restart 任务、旧 series 残留）同时持有 IPC，
  第一帧就超时；进程退出后新进程重新 `NemuIpcImpl init` 又恢复正常；
- MuMu 实例整机重启后连续 3 局：**0 次截图超时**。

**结论与改动**：
1. 运行期纪律：**同一时间只允许一个 OAS 设备进程**——不在 series 跑的时候执行
   `run_task_once` / 探测脚本 / 另一个 series。
2. `module/device/device.py`：`screenshot()` 现在同时捕获裸 `TimeoutError` 并先
   `nemu_ipc_release()` 再降级 ADB；连续 3 帧 >1s 也会自动释放 IPC（下次重试重新初始化）。
3. `dev_tools/screenshot_fallback_check.py` 改为**合成帧**回归（17 项断言），
   不再依赖实机截图健康；实机健康用 `--live` 只做信息输出。
4. 实机健康自检用 `dev_tools/screen_text_probe.py`（整屏 OCR）确认游戏停在哪一屏，
   比猜「是不是登录卡住」可靠。

**证据等级**：IPC 释放前后耗时对比（4.5s→0.034s）+ 3 局 0 超时，均为本机实测。


## 十五、交付清单与模块自检（2026-10-28）

本轮所有改动的「改了什么 / 在哪 / 怎么验 / 证据」汇总在 	asks/Chess/DELIVERABLES.md；
模块级回归入口是 	asks/Chess/selftest.py：

`powershell
.\\toolkit\\python.exe tasks\\Chess\\selftest.py          # 14 项，约 1 分钟
.\\toolkit\\python.exe tasks\\Chess\\selftest.py --quick  # 跳过自适应阵容检查
`

以后改棋局代码，先跑它再提交；它覆盖过渡位/站位/淘汰、时间预算、限购与缺口分类、
御魂装配、符咒冲突、审计统计、赛季数据、等级缓存、点击记录、金币确认、OCR 边界，
以及公共接口契约与缺失方法扫描。
