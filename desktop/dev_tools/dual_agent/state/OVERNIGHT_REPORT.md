# 双 Agent 首夜运行报告（非最终完成报告）

生成时间：2026-10-28 01:20
会话：2026-10-27 22:51:32 起
状态：**paused_budget**（费用上限触发，非完成；AC12 的 FINAL_REPORT 只在真正完成时生成）

## 1. 运行数据

| 指标 | 值 |
| --- | --- |
| 运行时长 | 3.3 小时（22:51 → 00:10） |
| 模型调用 | 26 次：Agent A 15 次（¥7.445，含 1 次失败）、Agent B 11 次（¥5.032，含 2 次失败） |
| 费用 | ¥12.48（上限 ¥12，保守价表：input ¥4/M、cache ¥1/M、output ¥16/M） |
| Token | input 896,684 / cached 4,514,432 / output 273,442 |
| 停止原因 | `budget.check_budget()` 检测到费用超上限 → 写 `paused_budget`，watchdog 见终态退出 |

## 2. 任务结果

| 任务 | 状态 | 判定 | 花费 | 实际改动 |
| --- | --- | --- | --- | --- |
| T-indexerror-boundary-fix | accepted | A: PASS | ¥3.43 | `module/atom/ocr.py` QUANTITY 空文本防护 + 77 行测试 |
| T-econ-deploy-budget | accepted | A: PASS | ¥0.94 | `runtime/economy.py` `ECONOMY_DEPLOY_RESERVE=25` |
| T-grigri-conflict-rule | accepted | A: PASS | ¥2.05 | 符咒冲突「前二 margin」判定规则 + 测试 |
| T-transition-slot | accepted | A: PASS | ¥1.49 | `adaptive.py` 过渡位收紧到 ≤2 费 + 无低费卡兜底 |
| T-soul-equip-reason-fix | accepted | 操作员复核 | ¥2.79 | `hand_operations.py` failed_targets/失败分类/mode 重拍 + 21 项测试 |
| T-nemu-switchback | pending | — | ¥0.00 | 未执行（预算到顶） |

## 3. 验证证据（操作员复核，非 Agent 自述）

```
compileall tasks module dev_tools                 -> 0
ui_click_regression / adb_recovery_unit / chess_level_cache_check /
click_record_check / gold_and_record_check / screenshot_fallback_check /
chess_transition_check / chess_phase_budget_check / grigri_conflict_check /
test_indexerror_boundary / chess_soul_target_skip_test   -> 11/11 PASSED
contract_scan Chess                                -> problems 0
missing_method_check                               -> 62 tasks, problems 0
season_data_check                                  -> 41/41 PASS
```

## 4. 验收状态（机器项）

| AC | 结果 | 说明 |
| --- | --- | --- |
| AC02 真实 A→B→A | PASS | accepted=5，agent_a 成功 14 次、agent_b 成功 9 次 |
| AC06 棋局完整流程 | PASS | 本会话完成 1 局（25 回合 / 第 5 名，完整结算+退出） |
| AC11 稳定版本与文档 | PASS | tag `stable-20261027-chess-strategy` + 文档 §18 |
| AC12 最终报告 | FAIL | 未到完成状态，故未生成 FINAL_REPORT（符合设计） |
| 其余 AC | 未本轮刷新 | 需要下一次运行由调度器统一跑（预算已停） |

## 5. 结论与下一步

- 双 Agent 系统**真实可用**：3.3 小时内产出 5 项被验证的代码改动，且审核抓出过编排层自身 bug。
- 费用集中在 Agent A（¥7.4/15 次 ≈ ¥0.5/次，审核要读 digest+diff+验证结果）。要降本：
  ① 降低 A 的 `max_turns`；② 审核 diff 进一步压到 4000 字符；③ 同一任务只允许 1 次审核 + 1 次返工。
- 继续运行需要提高 `budget.max_cny_total`（或等新的额度），然后：
  `cmd /c start "" /b "<repo>\toolkit\python.exe" dev_tools\dual_agent\watchdog.py --hours 7`
- 待办任务：`T-nemu-switchback`（截图从 ADB 降级后自动探测并切回 nemu_ipc）。
