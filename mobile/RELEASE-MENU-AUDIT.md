# 发布菜单可用性审计（0.3.4+36 源码轮）

- 时间：**2026-11-21 02:07:48**（`release-menu-audit.json` mtime；上一版为 0.3.4+34 / 2026-11-21 00:20）。
- +37 / +39 两轮只改登录守卫与菜单 `unsupported` 元数据，**未各自重跑审计**；62/62 来自本轮 + 事后独立复跑。
- 范围：`android/app/src/main/python/tasks` 中带 `script_task.py` 的发布菜单入口，排除内部 `GotoMain`。
- 结论：**62 个入口，62 个可导入**（02:07:48 的 +36 轮跑出：`SUMMARY total=62 import_ok=62 import_fail=0
  missing_asset_tasks=8`，日志 `release-menu-audit-36.log`）。缺图规则的活跃引用仍带降级保护，
  大神签到继续是运行期依赖检查而不是导入期抛错。
- 机器可读结果：`release-menu-audit.json`（本轮重跑）、`missing-assets-usage.log`（缺图引用分类）。

## +35 … +39 的登录期改动（影响所有菜单入口，不只某一个任务）

| 问题 | 修复 | 验证 |
| --- | --- | --- |
| 业务失败被记成"完成"（+35）：`TaskEnd` 一律当成功 | `BaseTask.mark_failure()` + `task_runner.task_end_result()`：任务显式声明失败则 `ok=false`、带回 `error`、`phase=failed` | `test_task_end_failure.py` 6 项；实机 `AutoCheckinBigGod` 变成 `phase=failed ok=false` |
| 登录守卫对"亮、静止、认不出"的画面零动作，空转到超时（+36） | 64×36 灰度帧间均差 <1.5 且均值亮度 ≥20 判静止；30s 补点、150s 重启客户端一次 | `test_mobile_login.py` 新增 3 项；真机 run `b40f1c63…` 触发 `login screen frozen for 64s (motion=0.81)` |
| 进庭院后被「是否打开…被自动关闭的加成？」卷轴框挡住，庭院识别全落空（+37） | 登录期点「取消/关闭/知道了/返回」关框（不点确认），静止时每轮 OCR；`LOGIN_TIMEOUT` 300s→480s | `test_mobile_login.py` 新增 1 项（`test_courtyard_confirm_dialog_is_dismissed_with_cancel`）；真机帧 `evidence/kekkai/k-20.jpg` |


## 本轮修复

| 问题 | 修复 | 验证 |
| --- | --- | --- |
| `AutoCheckinBigGod` 在导入期 `raise ImportError`，连带拖垮菜单加载与静态审计 | 改为可导入：缺 `oas-checkin-biggod` 时置 `BIGGOD_DEPENDENCY_ERROR`，`run()` 第一步报错并 `TaskEnd`（`success=False`），其余任务不受影响 | `audit-release-menu.py` 62/62 通过；`test_missing_assets.py` |
| `ActivityShikigami` 轮询 `I_PASS_5..9`，其中 `I_PASS_8` 模板图从未提交，轮询到即 `FileNotFoundError` 打断任务 | `RuleImage.available` + `available_rules()` 过滤缺图规则，缺图记 warning 跳过；全缺才失败退出 | 单元测试 `test_pass_8_is_dropped_from_challenge_rotation` |
| `WantedQuests` 邀请分支连点 `I_WQ_FRIEND_1..5`，这 5 张图全部缺失 | `invite_friend_rules_available()` 前置检查，缺图只跳过邀请分支，单人悬赏路径照常 | `test_friend_avatar_templates_are_reported_missing`、`test_invite_branch_is_guarded` |

## 不可导入

无。`AutoCheckinBigGod` 现在可导入，但**依赖官方网易大神客户端与 `oas-checkin-biggod` 包**，
本机缺依赖时运行会被立即判定失败并写清原因，不影响其余 61 项。

## 缺失素材（逐条核过引用，不再按「有引用」一律算活跃）

22 条规则引用 22 张缺失图片；其中 **6 条在活跃路径**（已加保护）、**2 条是被注释掉的死引用**、
**14 条无人引用**。分类依据 `audit-missing-assets-usage.py` 输出（`missing-assets-usage.log`）。

| 任务 | 缺失规则 | 引用情况 | 判断 |
| --- | --- | --- | --- |
| ActivityShikigami | `I_PASS_8`（另有 `fire_pass_10/11.png` 缺失但无规则引用） | 活跃 1 处（挑战入口轮询） | 已加缺图跳过 |
| WantedQuests | `I_WQ_FRIEND_1..5` | 活跃 5 处（邀请表单连点） | 已加分支前置保护；隔离验收固定 NoInvite |
| SixRealms | `I_SKILL103`、`I_SKILL104` | **注释掉的死引用**（`moon_sea/skills.py` 里 `# elif ... I_SKILL103`） | 上游从未提交该技能图标；活跃引用为零，不阻塞 |
| SixRealms | `I_NPC_COMMON`（`l104/l104_npc_common.png`） | 无引用 | 死引用 |
| AbyssShadows | `I_OPEN_ABYSS_SHADOWS`、`I_RYOU_ABYSS_SHADOWS` | 无引用 | 死引用 |
| BudokaiTournament | `I_ITEM_5..7` | 无引用 | 死引用 |
| KekkaiActivation | `I_A_CARDS_REALM` | 无引用 | 死引用 |
| MysteryShop | `I_MS_CHECK_EXP`、`I_MS_CHECK_TAIKO_6`、`I_MS_EXP`、`I_MS_TAIKO_6` | 无引用 | 死引用 |
| Restart | `I_LOGIN_SCROLL_CLOSE_AREA` | 无引用 | 死引用 |

## OAS1 的 24 项启用任务

- 24 项均能导入、均有配置，计划与角色历次核对一致。
- 已在实机取得完整业务结果（实机跑到任务自身收尾分支）：Restart、Pets、RealmRaid、TalismanPass、Nian、DailyTrifles、GoldYoukai、
  Orochi、EvoZone、BondlingFairyland、RichMan、WeeklyTrifles、KekkaiActivation、Tako、
  CollectiveMissions、AreaBoss、WantedQuests。
- **部分验证（只到中途分支，不算完整业务结果）**：
  - KekkaiUtilize：+35 run `fb4937c6…` / +39 run `bf5115c5…` 都只到「进好友结界 / 读剩余时间（3:59:48、5:03:26）/ 卡槽被占排下次」，**未执行选友→寄养**；
  - Delegation：只到「无可派遣条目」并排次日计划；
  - DemonEncounter：奖励识别超时，领取结果未核对。
  - MysteryShop：4 次营业日 run（`1e692bd5…` / `8743ac67…` / `48369ca6…` / `0fc3a659…`）都成功进店
    （`f-057` 帧：`I_MS_SHARE` 0.99、神秘商人 Lv1、商店数量 1/6、剩余 20:29）并完成「检查收获 + 排下次」，
    但当日商店 1/6 页无蓝票/黑碎，**实际购买动作（`buy_more`/`max_price`）未触发**；
    翻页模板 `ms_ms_next`/`ms_ms_before` 在真实帧只有 0.567/0.576（阈值 0.8），真伪待验。
- 仍需实机业务结果：MetaDemon（活动依赖）、Hunt（+34 入口与长战斗修复待装机复测）、
  AbyssShadows（寮未开启）。
- 计数口径（2026-11-21 14:05 后）：24 项 = **完整 17 + 部分 4**（KekkaiUtilize / Delegation / DemonEncounter / MysteryShop）
  **+ 未取得 3**（MetaDemon / Hunt / AbyssShadows）。

## 结论

发布菜单 62 个入口全部可导入；`大神签到` 是唯一需要官方客户端依赖的入口，缺依赖时明确失败而不是崩菜单。
缺图规则已不再能打断任务；六道之门 `I_SKILL103/104` 属上游死引用，需要真机补图才能启用对应分支。
