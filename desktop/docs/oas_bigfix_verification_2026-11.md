# OAS 错误大修复 · 独立验收报告（2026-11-20）

验收员：`verifier`（独立复跑，不写业务代码，未做任何真机动作：无 adb、无实例启停、无游戏点击）
任务：`t5 — 独立验收：四类修复的离线全量复跑与报告`
团队：`oas-error-bigfix`（t1 presets-fixer / t2 ui-recovery / t3 click-guards / t4 misc-hardening / t6 longtail-fixer / t7 presets-fixer）

## 0. 结论摘要

| 维度 | 结果 |
| --- | --- |
| `compileall` 全量编译 | **PASS**（exit 0，无输出） |
| 全部任务菜单配置页生成 | **PASS**（63/63，0 失败，exit 0） |
| 四份必跑自检（presets / unknown_page / click_guard / misc） | **PASS**（27+49+11+20 全过，exit 0） |
| 两份追加自检（longtail / autobattle）+ preset_index_check + ai_recovery_selftest | **PASS**（45+9+47+79 全过，exit 0） |
| 模板引用缺失分级扫描 `asset_file_check` | **PASS**（2191 引用，致命缺失 0，仅坐标用缺失 6） |
| GBK 控制台 emoji 日志离线复现 | **PASS**（对照组裸流确实抛 `UnicodeEncodeError`，logger 全路径不抛，文件日志保留原文） |
| 改动范围（diff）核对 | **PASS 但 1 项越界**：35 个代码文件全部落在某位成员的 inScope 内；`docs/oas_error_inventory_2026-11.md` 无人申报（见 F-2） |
| 静态核查（调试 print / 硬编码路径 / 空 except） | **PASS**（生产代码 0 命中） |
| 既有 Chess 自检 `--quick` | **FAIL**（exit 1，FAIL 1，见 F-1；判定为**静态检查器误报**，非运行时回归） |

一句话：**六位成员的离线交付全部可复现通过；唯一的红灯是 `missing_method_check` 的误报把 Chess 自检长期挂在 FAIL，本次由 t2 新增的两个方法名把误报从 ~61 条抬到 183 条。**

## 1. 环境与基线

| 项 | 值 |
| --- | --- |
| 仓库 | `D:\3D\YYS2\OnmyojiAutoScript-easy-install-1` |
| 分支 | `port/xylolit-vendor` |
| 基线提交 | `36afd9b5`（`[autosave] 2026-11-20 21:45:03`，即 t7 最后一次落盘） |
| 本次修复的提交区间 | `3c83bf7d^..36afd9b5`（4 个 autosave：21:30 / 21:35 / 21:40 / 21:45） |
| 解释器 | `.\toolkit\python.exe` = Python 3.10.11 (MSC v.1929 64bit) |
| 工作树 | `git status --short` 开始与结束均为空（除本报告与两个 `dev_tools/verify_*.py` 新增件） |
| 验收时间窗 | 2026-11-20 21:45 ~ 22:0x |

> 说明：仓库每 5 分钟 autosave 自动提交，因此成员改动不在 `git status` 里，只能按提交区间核对（见 §5）。

## 2. 验收总表（命令 / 退出码 / 输出摘要 / 结论）

| # | 检查项 | 命令 | 退出码 | 输出摘要 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | 全量字节码编译 | `.\toolkit\python.exe -m compileall -q tasks module script.py` | 0 | 无任何输出（`-q` 只报错） | PASS |
| 2 | 配置页生成（全部任务菜单） | `.\toolkit\python.exe dev_tools\verify_config_pages.py` | 0 | `config_name=template \| menu tasks=63 \| pages ok=63 \| failures=0 + init 0` / `CONFIG PAGES: PASSED` | PASS |
| 3 | 预设链路自检（t1） | `.\toolkit\python.exe dev_tools\fix_presets_test.py` | 0 | `{"passed": 27, "failed": 0, "report": "...log/fix-presets-check.json"}` | PASS |
| 4 | 未知页/浮层自检（t2） | `.\toolkit\python.exe dev_tools\fix_unknown_page_test.py` | 0 | `[PASS]` 全绿，收尾 `49/49 全部通过` | PASS |
| 5 | 连点熔断自检（t3） | `.\toolkit\python.exe dev_tools\fix_click_guard_test.py` | 0 | `TOTAL 11 PASS 11 FAIL 0` / `FIX CLICK GUARD TEST: PASSED` | PASS |
| 6 | 杂项/编码自检（t4） | `.\toolkit\python.exe dev_tools\fix_misc_test.py` | 0 | `Ran 20 tests ... OK` / `TOTAL 20 FAIL 0 ERROR 0` / `FIX MISC TEST: PASSED` | PASS |
| 7 | 长尾看门狗自检（t6） | `.\toolkit\python.exe dev_tools\fix_longtail_test.py` | 0 | `合计 45 项断言：通过 45，失败 0` | PASS |
| 8 | 自动战斗自检（t7） | `.\toolkit\python.exe dev_tools\fix_autobattle_test.py` | 0 | `{"passed": 9, "failed": 0, ...}` | PASS |
| 9 | 既有预设索引回归 | `.\toolkit\python.exe dev_tools\preset_index_check.py` | 0 | `{"passed": 47, "failed": 0, ...}` | PASS |
| 10 | ai_recovery 自检 | `.\toolkit\python.exe dev_tools\ai_recovery_selftest.py` | 0 | `TOTAL 79 PASS 79 FAIL 0` / `AI RECOVERY SELFTEST: PASSED` | PASS |
| 11 | 模板引用缺失分级 | `.\toolkit\python.exe dev_tools\asset_file_check.py` | 0 | `引用图片 2191 个，致命缺失 0 个，仅坐标用途缺失 6 个` | PASS |
| 12 | 既有 Chess 自检 | `.\toolkit\python.exe tasks\Chess\selftest.py --quick` | **1** | 13 组里 12 组 PASS；`[FAIL] 缺失方法扫描（全任务）: ... checked 63 tasks, problems 183` / `CHESS SELFTEST: FAILED -> dev_tools/missing_method_check.py rc=1` | **FAIL**（F-1） |
| 13 | GBK 控制台 emoji 复现 | `$env:PYTHONIOENCODING='gbk'; .\toolkit\python.exe dev_tools\verify_gbk_logger.py` | 0 | 对照组 `raw sys.stdout raises UnicodeEncodeError (encoding=gbk)`；`logger.info/warning/print/hr/error` 全 `[PASS]`；`file log keeps original emoji: log\2026-11-20_verify.txt` | PASS |
| 14 | 改动范围核对 | `git diff --name-status 3c83bf7d^ HEAD` + 与 team.json 各 inScope 比对 | 0 | 36 个文件：35 代码全命中 inScope，1 文档越界 | PASS（附 F-2） |
| 15 | 静态核查（新增行） | 见 §6 复现命令 | 0 | 生产代码 `print(` 0、硬编码绝对路径 0、`except: pass` 0、`breakpoint/pdb` 0 | PASS |

原始输出留档（`log/` 下，均为本次运行新生成）：`verify_fix_presets_test.txt`、`verify_fix_unknown_page_test.txt`、`verify_fix_click_guard_test.txt`、`verify_fix_misc_test.txt`、`verify_fix_longtail_test.txt`、`verify_fix_autobattle_test.txt`、`verify_preset_index_check.txt`、`verify_ai_recovery_selftest.txt`、`verify_asset_file_check.txt`、`verify_config_pages.txt`、`verify_gbk_logger.txt`、`verify_chess_selftest.txt`、`verify_missing_method_check.txt`。

## 3. GBK emoji 复现细节（对应 E 类根因）

脚本：`dev_tools/verify_gbk_logger.py`（新增，只写 stdout，不碰 device/adb）。

```
[info] PYTHONIOENCODING='gbk' sys.stdout.encoding='gbk'
[ok] control: raw sys.stdout raises UnicodeEncodeError (encoding=gbk)   ← 证明这条流确实是窄编码
[PASS] logger.info(emoji)
[PASS] logger.warning(emoji)
[PASS] logger.print(emoji)
[PASS] logger.hr(emoji)
[PASS] logger.error(emoji)
[PASS] file log keeps original emoji: ...\log\2026-11-20_verify.txt
GBK LOGGER VERIFY: PASSED   (exit 0)
```

- 对照组不可省：它证明「裸 sys.stdout 在 GBK 下写 📋 必抛」这一前提成立，否则实验组通过没有意义。
- 实验组覆盖 `module/logger.py` 的三条写路径：`logging` 的 RichHandler（`logger.info/warning/error`）、`logger.print`（经函数式 logger）、`logger.hr`（`logger.print` 的另一入口）。
- 文件日志用 UTF-8 写，原文未被替换符污染（`📋 🏆 ⚠️ ✅` 四个字符都能在文件里找到，替换符计数 0）。
- 控制台上看到的第一行提示「控制台编码 gbk 无法显示以下字符，已替换为占位符（文件日志保留原文）」= t4 声明的首次替换提示，行为与申报一致。

## 4. 共用文件影响面核对（重点项）

### 4.1 `module/device/device.py`（t6 longtail-fixer，全局共用）

diff 规模：`+164 / -1`，**唯一一条删除**是把类属性 `stuck_long_wait_list` 的定义行替换成加入 `'LONG_WAIT'` 的新定义（即无能力被移除）。

新增 API（全部带默认值，纯增量）：
`stuck_record_remove(button)`、`register_stuck_state_prober(prober, name='')`、`stuck_state_probe()`、`stuck_try_renew()`、`_stuck_capture_frame()`、`_stuck_snapshot_reference()`、`static _frame_motion(prev, curr)`。

行为影响面（实测计数，命令见 §6）：

| 指标 | 数量 | 含义 |
| --- | --- | --- |
| 调用 `stuck_record_add/check/clear` 的文件 | 46 | 几乎所有跑战斗/长流程的任务都会走到改动后的 `stuck_record_check` |
| 直接 `stuck_record_add(` 的调用点所在文件 | 38 | 每次打点多一张 `self.image.copy()` 快照（≈2.7MB / 1280×720×3，只留最新一张） |
| 使用 `LONG_WAIT` 占位符的文件 | 3（`device.py` + `tasks/WantedQuests/ui_wq_v2.py` + `tasks/WeeklyTrifles/script_task.py`） | **只有主动注册 `LONG_WAIT` 的流程**会进入「先截帧确认再续期」分支 |
| 继承 `GeneralBattle`（t7 的 `ensure_auto_battle` 生效面） | 34 个类 | 每场战斗多 1 次 OCR + 最多 2 次点击 |

结论：普通卡死语义未变（不注册 `LONG_WAIT` 的任务行为零变化），长等待最多多等约 15 分钟（3 次续期）；无公开方法被删。**风险点**：`_stuck_capture_frame` 每次续期多 2 张截图 + 1 秒等待，若真机截图本身变慢，会把「卡死判定」推迟；这属于真机验收项（§7）。

### 4.2 `tasks/GameUi/navigator.py`（t2 ui-recovery，全局共用）

diff 规模：`+339 / -14`。删除项逐条核对：

| 删除内容 | 替换为 | 判定 |
| --- | --- | --- |
| `O_EXIT_GAME_CONFIRM = RuleOcr(...)`（Full 模式） | `StrictKeywordOcr(...)`（去掉逐字兜底，keyword 仍为「退出游戏」） | 语义收紧，符合 D 类误判根因 |
| `if self.appear(self.O_EXIT_GAME_CONFIRM): ...click cancel...` 老实现 | `_handle_exit_game_confirm()`：只点 `C_EXIT_GAME_CANCEL`、点完重新截图复判、最多 2 次 | 更安全（点前点后都复判） |
| `if not self.appear(self.I_CHECK_MAIN): adb_shell(['input','keyevent','4'])` | `_protected_page_visible()`（同时看 `I_CHECK_MAIN` 与 `I_CHECK_TOWN`）+ `_press_back_key()`（按前 `click_record_clear`，总量预算 4） | 保护面扩大，且解决了「按了也算有进展」导致上层永不超时 |

新增：`_strict_full_ocr`、`_protected_page_visible`、`_press_back_key`、`_detect_overlay_close_cross`、`_detect_fullscreen_overlay`、`_close_fullscreen_overlay`、`_exit_confirm_button_panel_visible`、`_handle_exit_game_confirm`、`_recover_to_usable_page`；常量 `OVERLAY_CLOSE_ATTEMPTS=3`、`OVERLAY_CLOSE_CROSS_ROI=(1170,60,110,100)`、`MAX_UNKNOWN_PAGE_BACK_PRESSES=4`。

影响面：`GameUi` 是 `BaseTask` 的基类 → 覆盖全部 63 个任务；直接使用未知页恢复的文件 5 个（`close_unknown_pages` / `_recover_to_usable_page` 的调用点）。

**我在代码层面确认到的两个残留风险（属真机验收项，不是缺陷判定）**：

1. `O_EXIT_GAME_CONFIRM` 的 keyword 是「退出游戏」、ROI 是 `(330,300,620,90)`，判据要求「单个文本框包含该词」。`dev_tools/fix_unknown_page_test.py` 里这一条是用**注入 OCR 文本**（`'是否退出游戏？'`）验证的（该文件 L499-528），**没有真实确认框截图做素材**；全屏浮层那条则用了真实截图 `log/error/1795179872096/2026-11-20_21-04-31-591471.png`。也就是说：浮层几何已在真图验证，退出确认框的关键词尚未在真图/真机上验证。
2. 浮层关闭 ROI `(1170,60,110,100)`（红色关闭叉重心，真实快照定位 (1209,109)）与兜底坐标 `C_OVERLAY_CLOSE_X=(1204,95,18,18)` 只在 1280×720 的悬赏封印浮层上验证过；换分辨率或换浮层（活动弹窗、公告）未见证据。

## 5. 改动范围核对（36 文件 → 申报人）

命令：`git diff --name-status 3c83bf7d^ HEAD`，逐条对照 team.json 的 `inScope`。

| 申报人 (task) | inScope 目录/文件 | 实际被改的文件 | 命中 |
| --- | --- | --- | --- |
| t1 presets-fixer | `tasks/Component/preset_list.py`、`preset_team.py`、`tasks/Component/GeneralBattle/`、`dev_tools/fix_presets_test.py`、`preset_index_check.py` | `preset_list.py`、`preset_team.py`、`GeneralBattle/battle_wait.py`、`GeneralBattle/general_battle.py`、`fix_presets_test.py`（`preset_index_check.py` 未改） | 全中 |
| t2 ui-recovery | `tasks/GameUi/navigator.py`、`tasks/GameUi/default_pages.py`、`dev_tools/fix_unknown_page_test.py` | `navigator.py`、`fix_unknown_page_test.py`（`default_pages.py` 未改，与申报一致） | 全中 |
| t3 click-guards | `SwitchSoul/`、`Pets/`、`MemoryScrolls/`、`RealmRaid/`、`EvoZone/`、`ReplaceShikigami/`、`GeneralBuff/`、`fix_click_guard_test.py` | `SwitchSoul/click_guard.py`(A)、`SwitchSoul/switch_soul.py`、`Pets/script_task.py`、`MemoryScrolls/script_task.py`、`RealmRaid/script_task.py`、`EvoZone/script_task.py`、`ReplaceShikigami/replace_shikigami.py`、`GeneralBuff/general_buff.py`、`fix_click_guard_test.py`(A) | 全中 |
| t4 misc-hardening | `module/logger.py`、`module/server/`、`tasks/KekkaiUtilize/script_task.py`、`tasks/DailyTrifles/script_task.py`、`tasks/AbyssShadows/script_task.py`、`tasks/MetaDemon/`、`module/ai_recovery/`、`dev_tools/fix_misc_test.py` | `logger.py`、`server/script_process.py`、`DailyTrifles/script_task.py`、`AbyssShadows/script_task.py`、`MetaDemon/hzw_2026.py`、`ai_recovery/{hardening_test,integration,orchestrator,schema,selftest,types,verification}.py`、`fix_misc_test.py`(A) | 全中（`KekkaiUtilize/script_task.py` 未改，因为改的是日志层；与其申报的「不删 emoji、日志层容错」一致） |
| t6 longtail-fixer | `module/device/device.py`、`tasks/AreaBoss/`、`tasks/WeeklyTrifles/`、`tasks/WantedQuests/`、`dev_tools/fix_longtail_test.py` | `device.py`、`WeeklyTrifles/script_task.py`、`WantedQuests/script_task.py`、`WantedQuests/ui_wq_v2.py`、`fix_longtail_test.py`(A) | 全中（`tasks/AreaBoss/` 未改） |
| t7 presets-fixer | `tasks/Component/GeneralBattle/`、`dev_tools/fix_autobattle_test.py` | `GeneralBattle/battle_wait.py`、`GeneralBattle/general_battle.py`、`fix_autobattle_test.py`(A) | 全中 |
| **无人申报** | — | `docs/oas_error_inventory_2026-11.md`(A) | **越界**（F-2） |

补充：`battle_wait.py` / `general_battle.py` 同时落在 t1 与 t7 的 inScope 里，两方都改了同一文件（t1 记降级结果、t7 加 `ensure_auto_battle`），提交时间 t7 在后（21:45），未见互相覆盖（两处改动都在，已由 fix_presets_test 27 例 + fix_autobattle_test 9 例分别覆盖）。

## 6. 复现命令全集（含 FAIL 项）

```powershell
cd D:\3D\YYS2\OnmyojiAutoScript-easy-install-1

# 1 编译
.\toolkit\python.exe -m compileall -q tasks module script.py

# 2 配置页（仓库原本没有该检查脚本，本次新增 dev_tools/verify_config_pages.py）
.\toolkit\python.exe dev_tools\verify_config_pages.py

# 3-6 四份必跑自检
.\toolkit\python.exe dev_tools\fix_presets_test.py
.\toolkit\python.exe dev_tools\fix_unknown_page_test.py
.\toolkit\python.exe dev_tools\fix_click_guard_test.py
.\toolkit\python.exe dev_tools\fix_misc_test.py

# 7-10 追加回归
.\toolkit\python.exe dev_tools\fix_longtail_test.py
.\toolkit\python.exe dev_tools\fix_autobattle_test.py
.\toolkit\python.exe dev_tools\preset_index_check.py
.\toolkit\python.exe dev_tools\ai_recovery_selftest.py

# 11 模板引用
.\toolkit\python.exe dev_tools\asset_file_check.py

# 13 编码面（必须 GBK）
$env:PYTHONIOENCODING='gbk'; .\toolkit\python.exe dev_tools\verify_gbk_logger.py

# 12 FAIL 项复现（F-1）
.\toolkit\python.exe tasks\Chess\selftest.py --quick
.\toolkit\python.exe dev_tools\missing_method_check.py
```

静态核查与影响面计数（本次实际执行的两段命令）：

```powershell
# 新增行里的 调试 print / 硬编码绝对路径 / 空 except / pdb
.\toolkit\python.exe -c "import subprocess,re; ..."   # 逐条规则见 §7；本次结果：生产代码 0 命中
# 影响面计数
git grep -l -e "stuck_record_add(" -e "stuck_record_check(" -e "stuck_record_clear(" -- tasks module | Measure-Object -Line
git grep -l -e "LONG_WAIT" -- tasks module
git grep -l -e "GeneralBattle)" -e "GeneralBattle," -- tasks module | Measure-Object -Line
```

## 7. Findings（未通过项 / 需 Lead 决策项）

### F-1【medium】`missing_method_check` 误报把 Chess 自检长期挂在 FAIL，本次被 t2 放大到 183 条

- 现象：`tasks\Chess\selftest.py --quick` → `TOTAL 13 PASS 12 FAIL 1`、`CHESS SELFTEST: FAILED -> dev_tools/missing_method_check.py rc=1`；单独跑 `dev_tools/missing_method_check.py` 得 `checked 63 tasks, problems 183`，exit 1。
- 归因（已定位到具体行，不是猜）：
  - 该检查器按 **MRO 里的整个源文件** 收集 `self.xxx(` 调用，再用 `hasattr(ScriptTask, name)` 判定，因此把**同文件里其它类**的方法算到任务类头上。
  - `current_page`（约 61 条）：来自 `module/ai_recovery/integration.py:152`（`TaskAdapter.current_page`），该文件通过 `AiRecoveryMixin` 进入每个任务的 MRO。**基线 `3c83bf7d^` 已存在**（`git grep self.current_page( 3c83bf7d^` 命中同一行），即 FAIL 早于本轮修复。
  - `_strict_full_ocr`（`tasks/GameUi/navigator.py:58`）与 `detect_and_ocr`（`navigator.py:64`）：**本轮 t2 新增**，都在 `class StrictKeywordOcr(RuleOcr)` 内，方法本身存在（`_strict_full_ocr` 定义于同文件 L60；`detect_and_ocr` 由 OCR 基类提供），属于新增误报。
- 证据（MRO 归因实测）：`tasks.AreaBoss.script_task.ScriptTask.__mro__` 含 `AiRecoveryMixin`；`_strict_full_ocr -> tasks/GameUi/navigator.py:58`、`detect_and_ocr -> navigator.py:64`、`current_page -> module/ai_recovery/integration.py:152`；三者 `hasattr(ScriptTask, ...)` 全为 False。
- 影响：**不影响运行时**（三处调用都在 `RuleOcr`/`TaskAdapter` 自己的实例上），但会让 `tasks/Chess/selftest.py` 这条既有回归常年红着，掩盖真正的 `AttributeError` 类回归（这正是该脚本存在的理由）。
- 精确复现：
  ```powershell
  .\toolkit\python.exe tasks\Chess\selftest.py --quick     # exit 1
  .\toolkit\python.exe dev_tools\missing_method_check.py   # exit 1, problems 183
  ```
- 建议修法（**不在我的 inScope，交 Lead 决定是否开单**）：给 `missing_method_check.py` 的 `IGNORE` 加 `{'current_page', 'detect_and_ocr', '_strict_full_ocr'}`；或把 `collect_calls` 限定到「类体内」再判（根治误报，但要改检查器本体）。
- 归属：`dev_tools/missing_method_check.py` 本轮**未被任何人修改**（36 文件清单里没有它）；新增的 2 条误报可归 t2 的 `navigator.py` 新增类，但**不能算 t2 的代码缺陷**。

### F-2【low】`docs/oas_error_inventory_2026-11.md` 不在任何成员 inScope 内

- 现象：`git diff --name-status 3c83bf7d^ HEAD` 中 `A docs/oas_error_inventory_2026-11.md`（+40 行）；六份 inScope 都没有 `docs/`。
- 但该文件是本轮任务的**指定输入**（验收指引第一条要求先读它），内容是错误清单（A/B/C/D/E 类），不含业务代码。
- 精确复现：`git show 36afd9b5 --stat | Select-String docs`，或 `git diff --name-status 3c83bf7d^ HEAD -- docs`
- 结论：无害越界，登记备查；若要求严格 inScope，需 Lead 事后补认（或让 t4 在其 inScope 里补一条 `docs/`）。

### F-3【low，非缺陷，属验收覆盖缺口】退出确认框关键词判据没有真图/真机证据

- 见 §4.2 风险 1：`dev_tools/fix_unknown_page_test.py` L499-528 用注入 OCR 文本（`'是否退出游戏？'`）而非真实确认框截图验证 `O_EXIT_GAME_CONFIRM`。
- 影响：若真机上「退出游戏」四个字被拆成两个文本框（或带问号/换行被 OCR 切分），StrictKeywordOcr 会判 False → 不点取消 → 交给 `_recover_to_usable_page` 与上层超时。**这是「安全但可能仍卡」的降级，不是误点确认**，但需要真机确认命中率。
- 建议：Lead 真机验收时专门制造一次退出确认框（主界面按返回键），确认日志出现 `Exit-game confirm dialog: click cancel`；若三次都只出现 `keyword matched ... geometry missing` 或干脆没有该行，则需放宽 keyword 或改用「任取一个框包含『退出』」的策略。

### 已核对但不构成 finding 的项（避免误伤）

- `git status --short` 全程为空；`git diff` 无 inScope 之外**代码**文件。
- 新增行里没有任何生产代码 `print(`（36 条 `print(` 全部在 `dev_tools/fix_*_test.py` 的输出语句里）。
- 新增行里没有硬编码绝对路径（`[A-Za-z]:\` 0 命中）、没有 `except: pass` / `except Exception: pass`、没有 `breakpoint()/pdb`。
- `config/deploy.yaml` 的 mtime 在 21:47 变过，但它被 `.gitignore` 覆盖（`config/*.yaml`），且不在 diff 里；推测为运行中的服务进程写入，非本次修复改动。

## 8. 未验证事项（按铁律不做真机动作，交 Lead）

本轮我**没有**执行：启动/停止 OAS 实例、adb 连接或点击、`run_task_once.py`、`oas_fix_smoke_test.py`（t2 曾跑过它，只读截一张图）。因此以下必须真机验收：

1. **A 类**预设列表：进战斗队伍预设面板，选一次「阵容」预设 + 一次「御魂」预设，看日志 `[战斗预设队伍位移]` 是否稳定出分、面板打开是否 ≤4 次尝试，且不再出现 `ScriptError → exit(1)`。
2. **B 类**连点熔断：连续跑 Pets / EvoZone / RealmRaid，确认日志出现 `[xxx] 连点熔断：...` 后是「清记录 + 关弹窗 + 重试一次」，失败才软失败，且**实例不重启**。
3. **C 类**未知页：制造一次悬赏封印浮层（或等它自然出现），确认 `Fullscreen overlay detected by OCR keyword` / `Close fullscreen overlay [1/3]` 与 `overlay_x` 日志；同时确认 `_protected_page_visible` 在庭院/町中时不再点右上角（日志 `Fullscreen overlay check skipped: main page or town is visible`）。
4. **D 类**等待：AreaBoss 打满 5 分钟以上的战斗，确认 `第 1/3 次续期` 出现而不是 `Wait too long`；确认 `[AutoBattle]` 分支在有「手动」按钮的战斗里把模式切回自动（t7 的 34 个继承类覆盖面）。
5. **E 类**编码：真实实例日志里若出现 emoji，控制台应出现一行「控制台编码 gbk 无法显示以下字符…」提示而**不中断任务**；`log/2026-11-20_oas3.txt` 内仍保留原文 emoji。
6. **F-1**：决定是否修 `missing_method_check.py`（否则 Chess 自检持续红）。
7. **F-3**：退出确认框判据的真机命中验证（见上）。

## 9. 我新增的两个只读脚本（都在 `dev_tools/`，不改业务代码）

| 脚本 | 作用 | 只读性 |
| --- | --- | --- |
| `dev_tools/verify_config_pages.py` | 用 `ConfigModel.gui_args/gui_task` 的**原始实现**给 63 个任务菜单生成配置页，校验返回非空且是合法 JSON、schema 含 `properties`；缺 model 或抛异常即 FAIL | 不实例化 `Config('oas3')`（避免 ConfigWatcher 线程与 `__setattr__` 里的 `save()` 改写运行中实例配置），用 shim 挂 `config/template.json` 的默认值 |
| `dev_tools/verify_gbk_logger.py` | GBK 下复现 emoji 日志：对照组裸流必须抛 `UnicodeEncodeError`，实验组 `logger.info/warning/print/hr/error` 必须全通，文件日志必须保留原文 | 不碰 device/adb/实例 |

两份脚本都是 `verify_*.py`，符合我的 inScope（只允许写 `dev_tools\verify_*.py` 与 `docs\` 报告）。
