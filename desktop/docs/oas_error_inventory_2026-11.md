# OAS 错误清单（2026-11-17 ~ 2026-11-20）

数据来源：`log/error/<epoch_ms>/`（78 个快照，含 log.txt 与截图）+ `log/2026-11-20_oas{1,3}.txt` +
`log/ai_recovery/incidents.jsonl`（341 条）。聚合命令：`toolkit\python.exe dev_tools\check_oas_errors.py --hours 72`。

## 按根因聚类的失败次数

| 类别 | 次数 | 代表错误 | 首次出现 |
| --- | --- | --- | --- |
| A 预设列表/预设选择 | 22 | `预设列表未向顶部移动，停止选择`、`预设列表滚动位置不明确（匹配0.50），停止选择`、`战斗预设面板未打开，停止选择`、`选中的队伍未找到一键预设御魂按钮…` | 11-17 14:59（`882fd7c2` 之后 3.5h） |
| B 连点熔断 | 18 | `GameTooManyClickError`：`ss_group_swipe_down`×5、`PET_PET_FEAST`×4、`UI_UI_BACK_YELLOW`×3、`shikigami_left_7`×2、`MS_MS_ENTER`/`buff_up`/`RS_RS_LEVEL_MAX`/`partition_7`/`O_EVOZONE_LOCK` 各 1 | 11-17 12:49 |
| C 未知页面/弹窗未关 | 8 | `Game page unknown`（`Current page checks: []`、`Unknown close history: ['None']`） | 11-17 12:01 |
| D 等待超时 | 10 | `GameStuckError: Wait too long` | 11-17 11:37 |
| E 杂项 | 7 | `切换狭间区域超时`×2、`Daily gift tab not recognized`、`祸至无日：进入活动超时`、`UnicodeEncodeError 'gbk' \U0001f4cb`（2~3 次）、`EmulatorNotRunningError`、`BrokenPipeError [WinError 232]` | 11-17 16:49 |

另：`log/ai_recovery/incidents.jsonl` 中 `outcome=stopped/needs_human` 的验证失败原因集中在
`page_not_freshly_recognized`、`wait_without_progress`、`诊断置信度不足`，多数对应 C/A 两类真失败，
但其中「AI 已把页面恢复，仅因验证口径过严而记失败」是独立的误判源。

## 证据锚点（可直接复现）

- A 类：`log/error/1795179676393`（21:01 OROCHI）、`1795179130467`（20:52 匹配0.50）、`1795179852457`
  （21:04 一键预设御魂按钮）、`1795178774126`（20:46 战斗预设面板未打开）。
  引入提交 `882fd7c2 fix(presets): scroll to arbitrary group and team indices`（11-17 11:27）新增
  `tasks/Component/preset_list.py` 的按像素位移定位算法；失败率与提交时间强相关。
- B 类：`S_SS_GROUP_SWIPE_DOWN` 是 `RuleSwipe`（`tasks/Component/SwitchSoul/assets.py:83`），
  却和点击共用 `Device.handle_control_check` 的记录（`control_name` 相同），
  连续滑动第 10 次触发熔断；`C_SHIKIGAMI_LEFT_7`、`PET_PET_FEAST`、`MS_MS_ENTER` 等为正常的重复点击循环。
- C 类：`log/error/1795179872096` 截图 = 全屏「悬赏封印」浮层压在 `page_main` 上，
  `navigator.close_unknown_pages` 的未知关闭动作没有覆盖这类浮层，
  右上角红 X 约 `(1213, 104)`；日志随后 `Current page checks: []` → `CRITICAL Game page unknown`。
- D 类：需要在各自快照里逐条看最后一帧（部分为结算/弹窗卡住，部分是页面根本没到达）。
- E 类：`tasks/KekkaiUtilize/script_task.py:601,631` 的 `logger.info(f'📋 …')` 在 GBK 控制台抛
  `UnicodeEncodeError`；`tasks/DailyTrifles/script_task.py:228`；`tasks/AbyssShadows/script_task.py:329`。

## 修复纪律

- 每条修复必须附离线回归（`dev_tools/fix_*_test.py`，假帧/假设备，不碰真机）。
- 实机只有一个设备通道：修复期任何成员都不得启动 OAS 实例、不得 adb、不得跑 `run_task_once.py`。
- 验收：`compileall` + 全部 60+ 任务配置页生成 + 各修复自检脚本 + 至少一轮真机连跑（由 Lead 在实例空闲时执行）。
