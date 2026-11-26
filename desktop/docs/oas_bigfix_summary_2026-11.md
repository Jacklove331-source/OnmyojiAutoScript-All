# OAS 错误大修复 · 汇总（2026-11-17 ~ 11-20 的 78 个错误快照）

结论先说：**5 类错误、共 65 次失败，全部定位到根因并落地修复；离线全量验收 6/6 通过；真机跑通
`Restart`（RESULT True）+ `EvoZone` 三轮（11 场战斗、0 ERROR、0 新错误快照）。**
仍保留 4 条「按设计不修」与 2 条「需下次真机复现才能闭环」的项，见 §6。

修改规模：42 个文件、+6567/−425 行（基线 `75aabc08`），其中 9 个是新增自检
（`dev_tools/fix_*_test.py`、`dev_tools/verify_config_pages.py`、`verify_gbk_logger.py`、
`missing_method_selftest.py`）。

## 1. 失败次数 → 修复状态

| 类别 | 次数 | 根因（一句话） | 修复 | 状态 |
| --- | --- | --- | --- | --- |
| A 预设列表/预设选择 | 22 | 882fd7c2 新增的「按像素位移定位」把量不准当致命错 + 面板打开循环无上限 + 缺按钮直接抛错 | `preset_list.py` 4 带投票/回弹预判/保守返回；`preset_team.py` 降级跳过御魂预设、面板最多 4 次/12s | 离线 27 例 + 真机验证 |
| B 连点熔断 | 18 | 合法重复动作（滑动翻页/重复点入口）与点击共用同一记录；部分循环无上限 | 8 个触发点逐个定性：清记录/独立控制名/有界退出；新增 `SwitchSoul/click_guard.py` 统一自愈（清记录+关弹窗+重试一次，仍失败 soft-fail） | 离线 11 例 |
| C 未知浮层/页面 | 8 | 全屏浮层（悬赏封印）不在关闭清单；`O_EXIT_GAME_CONFIRM` 的 Full-OCR 逐字兜底把 '町中'/'15'/空结果判成弹窗 | `navigator.py` 加 `StrictKeywordOcr` + 浮层两套判据（标题 OCR / 右上角红叉几何）+ 关闭后复判 3 次 + 回退 2 轮；退出确认框只点取消 | 离线 49 例（含真实快照）+ 真机负例 |
| D 等待/停滞 | 10 | `BATTLE_STATUS_S` 在组队/战斗等待期就记录，300s 直接判死；另有进战斗没切自动、分享卡死、WQ2 面板 ROI 过期 | `device.py` 看门狗到点先截帧确认→续期（≤3 次）；`battle_wait*` 三条路径共用 `ensure_auto_battle_once()`；WeeklyTrifles 全部 `while 1` 有界；WQ2 多探针+空读熔断 | 离线 45+13 例 + 真机验证 |
| E 杂项 | 7 | GBK 控制台 emoji 崩溃、管道关闭、DailyTrifle 页签、Abyss/Meta 超时、ai_recovery 误判 | 日志层统一容错（文件日志保原文）、管道写前判 closed+降级、页签多形态+重试、超时降级跳过、验证改「重新截帧 + 连续 2 帧」、schema 逐动作丢弃 | 离线 20 例 + ai_recovery 79 + 67 |

## 2. 关键根因（均有证据与回归）

1. **A 类是 11-17 11:27 提交 882fd7c2 引入的回归**：该提交 3.5 小时后（14:59）开始成片失败。
   旧算法在「回弹/头像动画帧」上得 0.22~0.53 分直接 raise；`_top` 只 `movement >= 0` 就判失败。
2. **C 类的真正技术根因不是 ROI 太宽，而是 `RuleOcr(Full)` 的逐字兜底**：拼接串不命中时按单字匹配，
   于是 `'町中'`、`'15'`、空结果都能让 `appear()` 为真。`StrictKeywordOcr` 只认「单个文本框包含关键词」。
3. **D 类的「卡死」一半是等待语义错**：`log/error/1794977024777` 截图显示战斗仍是「手动」模式（2/8 回合等输入），
   代码里从来没有任何检查/切换自动战斗；`1795178295813` 则是首领战打了 5 分钟仍在打（1/8 回合、伞剑 33495/236397），
   被 300s 定时器误杀。
4. **E 类的 GBK 崩溃**：`logger.info(f'📋 …')` 在 GBK 控制台抛 UnicodeEncodeError 打断任务；
   选择「日志层容错」（文件日志保留原文）而不是删 emoji，一次覆盖全仓 17 处 + 未来新增。

## 3. 真机验收（2026-11-20 22:02–22:28，oas3 / MuMu 实例 1 / 16416）

| 步骤 | 结果 |
| --- | --- |
| `run_task_once.py oas3 Restart` | `RESULT Restart -> True`；登录→庭院→领地事务→关弹窗全通（`log/verify_restart.txt`） |
| EvoZone 第 1 轮（11 场战斗） | 预设日志 `[战斗预设分组位移] 方向-1 位移0 最高分1.00 列表静止/回弹` → `第1项已定位`；缺御魂按钮降级为 WARNING 并继续出战；0 ERROR；0 新错误快照（`log/verify_evozone.txt`） |
| EvoZone 第 2 轮 | 走到未知页恢复：`[EXIT_GAME_CONFIRM 0.232s] []` → `no text in ROI, treat as absent`；`[OVERLAY_TITLE_BOUNTY] no text` → 回退旧关闭动作 → 回到 page_main（`log/verify_evozone2.txt`） |
| EvoZone 第 3 轮 | `[AutoBattle] already auto` ×7，每场 17~20s 结束并 `Win battle`；0 ERROR（`log/verify_evozone3.txt`） |

## 4. 离线验收（verifier 独立复跑，全 PASS）

| 检查项 | 命令 | 结果 |
| --- | --- | --- |
| 编译 | `.\toolkit\python.exe -m compileall -q tasks module script.py` | exit 0 |
| 配置页 | `.\toolkit\python.exe dev_tools\verify_config_pages.py` | 63/63，0 失败 |
| 六份自检 | `fix_presets / fix_unknown_page / fix_click_guard / fix_misc / fix_longtail / fix_autobattle` | 27/49/11/20/45/13 全 PASS |
| 模板引用 | `.\toolkit\python.exe dev_tools\asset_file_check.py` | 2191 引用，致命缺失 0 |
| GBK 日志 | `.\toolkit\python.exe dev_tools\verify_gbk_logger.py` | 5 条 API 全 PASS，替换符 0 |
| 缺失方法 | `.\toolkit\python.exe dev_tools\missing_method_check.py` | 63 任务，problems 0 |
| Chess 自检 | `.\toolkit\python.exe tasks\Chess\selftest.py --quick` | 13/13 PASS（裸命令，GBK 下不再崩） |
| ai_recovery | `dev_tools\ai_recovery_selftest.py` + 两个 unittest 模块 | 79/79、67 OK |

完整逐项报告见 `docs/oas_bigfix_verification_2026-11.md`。

## 5. 影响面（两个全局共用文件）

- `module/device/device.py`（+164 行）：`stuck_long_wait_list` 只多一个 `LONG_WAIT` 占位符；
  续期逻辑只对「记录全部属于长等待表」时生效，`opt-in` 的任务（WeeklyTrifles、WantedQuests）才会续期；
  普通卡死语义未变。38 处打点、46 个调用文件、34 个继承类已逐个核对。
- `tasks/GameUi/navigator.py`（+339 行）：删除项逐条有替换（RuleOcr→StrictKeywordOcr、
  退出确认→`_handle_exit_game_confirm`、裸 adb 返回键→`_protected_page_visible`+`_press_back_key`），
  覆盖全部 63 个任务；庭院页禁止 BACK、只点取消的保护保留。

## 6. 未修 / 保持现状（明确列出，不假装修完）

1. `tasks/Component/SwitchSoul/switch_soul.py`：式神录里「该组该队确实不存在预设」仍是 `ScriptError`
   （真失败语义）。本轮把 OROCHI 那次**误判**从根上修掉了，真缺失没有降级——如需降级要 owner 另行决定。
2. `log/error/1795179473096`：第 60 回合、6/7 技能位阵亡的必败局。看门狗现在最多续期 3 次（≈15 分钟），
   之后仍会抛 GameStuckError；这是对局强度问题，不是脚本缺陷。
3. `EmulatorNotRunningError`：4 次重试后 `RequestHumanTakeover`，串口无设备 + 桌面窗口缺失 = 环境抖动，未改代码。
4. `O_EXIT_GAME_CONFIRM` 的**正例**（真实确认框截图）离线用例没有，只有注入文本 + 反例帧；
   真机未复现该弹窗。风险：若真框被 OCR 切成多框，会判 False 不点取消（安全但可能仍卡）。
5. `悬赏封印` 浮层的**正例**真机没复现（该弹窗要游戏触发）；负例真机已过。
6. `config/deploy.yaml` 的 mtime 变化来自运行中的服务自身写入（gitignore），非本次改动。

## 7. 复核命令

```powershell
cd D:\3D\YYS2\OnmyojiAutoScript-easy-install-1
.\toolkit\python.exe dev_tools\verify_config_pages.py
.\toolkit\python.exe dev_tools\fix_presets_test.py
.\toolkit\python.exe dev_tools\fix_unknown_page_test.py
.\toolkit\python.exe dev_tools\fix_click_guard_test.py
.\toolkit\python.exe dev_tools\fix_misc_test.py
.\toolkit\python.exe dev_tools\fix_longtail_test.py
.\toolkit\python.exe dev_tools\fix_autobattle_test.py
.\toolkit\python.exe dev_tools\missing_method_selftest.py
.\toolkit\python.exe tasks\Chess\selftest.py --quick
```

真机复核看这几条日志锚点：`[战斗预设分组位移]` / `跳过御魂装配并用当前御魂出战` /
`[OVERLAY_TITLE_BOUNTY]` / `OCR [EXIT_GAME_CONFIRM] no text in ROI, treat as absent` /
`[Stuck] … 第 N/3 次续期` / `[AutoBattle] already auto` 或 `manual detected -> toggle on`。
