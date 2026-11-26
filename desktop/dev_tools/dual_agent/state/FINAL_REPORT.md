# 双 Agent 自动开发（调度器 v2）：最终报告

- 生成时间: 2026-10-28 16:22:24
- v2 会话开始: 2026-10-28 10:43:31
- Claude: 10 次调用，$3.6669
- DeepSeek: 4 次调用，¥3.1542
- Token: input=147815 cache_read=1575169 cache_write=273414 output=285693

## 机器验收（完成前全量复核）

| 项 | 结果 | 说明 |
| --- | --- | --- |
| AC01 双 Agent 调度器自测 | PASS | dev_tools/dual_agent/tests/run_all.py rc=0 :: [PASS] test_v2.py :: TOTAL 42  PASS 42  FAIL 0 / DUAL AGENT V2: PASSED / TOTAL 6  PASS 6  FAIL 0 / DUAL AGENT TESTS: PASSED |
| AC02 Claude 规划/审核 → DeepSeek 执行 真实协作（非模拟、非人工转发） | PASS | claude_ok_calls=5 deepseek_ok_calls=21 tasks_passed_by_claude=1 |
| AC02R 规划→执行→审核→返工→再审核通过 的完整闭环 | PASS | closed_rework_cycles=['T-audit-metrics-fix'] |
| AC03 任务持久化与恢复 | PASS | dev_tools/dual_agent/tests/test_persistence.py rc=0 :: [PASS] 验收记录按 id 覆盖更新 / TOTAL 17  PASS 17  FAIL 0 / DUAL AGENT PERSISTENCE: PASSED |
| AC04 Token/费用预算与安全停止 | PASS | dev_tools/dual_agent/tests/test_budget.py rc=0 :: [PASS] B 失败 → REWORK / TOTAL 9  PASS 9  FAIL 0 / DUAL AGENT BUDGET: PASSED |
| AC05 OAS 行为级回归 | PASS | dev_tools/ui_click_regression.py rc=0 :: [PASS] 未知参数: 抛 TypeError / [PASS] RuleClick 缺 visible: 抛 TypeError / [PASS] 非法 confirm_frames: 抛 ValueError / [PASS] 非法 max_clicks: 抛 ValueError / TOTAL 31  PASS 31  FAIL 0 / UI CLICK REGRESSION: PASSED; dev_tools/adb_recovery_unit.py rc=0 :: WARNING  2026-10 |
| AC05S 静态检查无问题 | PASS | contract/method scan ok |
| AC06 百鬼棋局连续 3 局完整流程（进局→结算→退出） | PASS | 基线后完成 4 局，最长连续 3/3 （基线行 9/14）: 2026-10-28 14:50:35 rank=6 rounds=22; 2026-10-28 15:17:21 rank=5 rounds=20; 2026-10-28 15:39:41 rank=6 rounds=20 |
| AC07 ADB/截图异常恢复机制 | PASS | device_connection_recovered=0; dev_tools/adb_recovery_unit.py rc=0 :: WARNING  2026-10-28 16:19:27.798 │ [DeviceRecovery] adb server query failed: RuntimeError('cannot connect to adb / WARNING  2026-10-28 16:19:27.804 │ [DeviceRecovery] adb server query failed: RuntimeError('cannot connect to adb /  |
| AC08 策略修复有回归测试 | PASS | dev_tools/chess_transition_check.py rc=0 :: [PASS] _forget_board_unit 清空四处登记 / TOTAL 33  PASS 33  FAIL 0 / CHESS TRANSITION: PASSED; dev_tools/chess_phase_budget_check.py rc=0 :: [PASS] 正常耗材下预算落在 12~25s 的合理区间 / TOTAL 7  PASS 7  FAIL 0 / CHESS PHASE BUDGET: PASSED; dev_tools/chess_buy_deploy_guard_te |
| AC09 符咒冲突与御魂操作有验证 | PASS | dev_tools/grigri_conflict_check.py rc=0 :: [PASS] 文字映射 分裂朱印 -> 分裂朱印 / TOTAL 21  PASS 21  FAIL 0 / GRIGRI CONFLICT: PASSED; dev_tools/chess_soul_target_skip_test.py rc=0 :: [PASS] mode changed -> no drag / TOTAL 28  PASS 28  FAIL 0 / SOUL TARGET SKIP: PASSED |
| AC10 赛季数据核对 | PASS | dev_tools/season_data_check.py rc=0 :: [PASS] 日志里的符咒全部在符咒表里 / TOTAL 41  PASS 41  FAIL 0 / SEASON DATA: PASSED |
| AC10R 当前赛季数据联网核实记录 | FAIL | 缺少 docs\research\season_verification.md |
| AC11 稳定版本与文档（含调度器 v2 交接） | PASS | tags=['stable-20261027-chess-strategy', 'stable-v2-20261028-1612'] handoff(v2)=True notes=True |
| AC12 最终报告（含证据与剩余限制） | PASS | FINAL_REPORT.md size=7697 |
| AC13 参考指标：前期过渡/购买/动态上阵（不设门限） | PASS | 窗口(≥2026-10-28 10:43:31) 完成局 3/3 / early_buy=4.67 early_deploy=4.0 transition_games=3 |
| AC14 参考指标：经济/时间预算（不设门限） | PASS | 窗口(≥2026-10-28 10:43:31) 完成局 3/3 / confirm=0.913 unconfirmed=0.67 shot_timeout=39.0 |
| AC15 参考指标：御魂装备（不设门限） | PASS | 窗口(≥2026-10-28 10:43:31) 完成局 3/3 / equipped=10 failed=4 rate=0.714 |
| AC16 参考指标：识图健康度（不设门限） | PASS | 窗口(≥2026-10-28 10:43:31) 完成局 3/3 / level_ocr_invalid=0.0 unknown=5.34 grigri=0/0 |
| AC17 参考：策略对照证据（不设门限） | PASS | base={'avg_early_buy_ok': 0.0, 'avg_early_deploy': 0.67, 'buy_confirm_rate': 0.179} window={'avg_early_buy_ok': 4.67, 'avg_early_deploy': 4.0, 'buy_confirm_rate': 0.913} report=strategy_compare.md |
| AC17T 实战指标统计正确性测试 | PASS | dev_tools/chess_audit_metrics_check.py rc=0 :: PASS  B: 逐回合 buy_ok 之和 == events.buy_ok / TOTAL 20 PASS 20 FAIL 0 / CHESS AUDIT METRICS: PASSED |
| AC18 连续 3 局完整且不报错 | PASS | 最近 3 局: 2026-10-28 14:50:35 rank=6 rounds=22; 2026-10-28 15:17:21 rank=5 rounds=20; 2026-10-28 15:39:41 rank=6 rounds=20 |
| AC19 棋局模块自检（tasks/Chess/selftest.py） | PASS | tasks/Chess/selftest.py rc=0 :: [PASS] 缺失方法扫描（全任务）: ════════════════════════════════════════════════════════════════════════════════ / checked 62 tasks, problems 0 / TOTAL 13  PASS 13  FAIL 0  (78.0s) / CHESS SELFTEST: PASSED |

## 任务流水（token / 费用 / 返工）

| 任务 | 状态 | 判定 | 调用 | ¥Claude | ¥DeepSeek | 返工 |
| --- | --- | --- | --- | --- | --- | --- |
| T-soul-equip-rate | accepted | PASS | 3 | 4.53 | 0.63 | 1 |
| T-season-verify | blocked | BLOCKED | 0 | 0.00 | 0.00 | 0 |
| T-level-ocr-empty | blocked | REWORK_EXHAUSTED | 5 | 5.04 | 1.79 | 2 |
| T-audit-metrics-fix | accepted | PASS | 4 | 17.02 | 0.73 | 1 |
| T-ac05-hermetic | accepted | PASS | 2 | 0.64 | 0.00 | 0 |
| T-deploy-empty-fix | accepted | PASS | 2 | 1.62 | 0.00 | 0 |
| T-recovered-continue-tag | blocked | BLOCKED | 1 | 0.38 | 0.00 | 0 |
| T-recovered-continue-mark | accepted | PASS | 4 | 0.91 | 0.87 | 0 |
| T-season-data-check | accepted | PASS | 2 | 0.12 | 1.68 | 0 |
| T-grigri-soul-conflict | accepted | PASS | 2 | 0.17 | 0.97 | 0 |
| T-chess-transition-regression | accepted | PASS | 2 | 0.12 | 0.83 | 0 |
| T-completion-gate-deadlock | accepted | PASS | 2 | 0.28 | 0.40 | 0 |
| T-recovered-continue-log | accepted | PASS | 2 | 0.51 | 0.65 | 0 |
| T-nemu-switchback | accepted | PASS | 2 | 0.44 | 0.85 | 0 |
| T-econ-deploy-budget | accepted | PASS | 2 | 0.32 | 0.63 | 0 |
| T-grigri-conflict-rule | accepted | PASS | 4 | 1.15 | 0.90 | 0 |
| T-transition-slot | accepted | PASS | 2 | 0.53 | 0.96 | 0 |
| T-indexerror-boundary-fix | accepted | PASS | 6 | 2.15 | 1.28 | 0 |
| T-soul-equip-reason-fix | accepted | OPERATOR_VERIFIED | 8 | 1.52 | 1.27 | 0 |

## 策略对照

# 策略对照（自动生成，`acceptance.check_strategy_compare`）

- 基线: chess_series3_run3_1248.log（3 局）
- 评估窗口: v2 启动后最近 3 局

| 指标 | 基线 | 窗口 |
| --- | --- | --- |
| avg_rank | 6.33 | 5.67 |
| avg_early_buy_ok | 0.0 | 4.67 |
| avg_early_deploy | 0.67 | 4.0 |
| buy_confirm_rate | 0.179 | 0.913 |
| avg_buy_unconfirmed | 10.67 | 0.67 |
| soul_equipped | 0 | 10 |
| soul_success_rate | 0.0 | 0.714 |
| avg_level_ocr_invalid | 0.0 | 0.0 |
| avg_unknown_cells | 16.0 | 5.34 |

## 窗口逐局

```
log | idx | start | rank | rnd | eBuy | eDep | buy | unconf | trans | soulOK | soulX | lvlX | unk
chess_clean3_1450.log | 1 | 10-28 14:50 | 6 | 22 | 4 | 4 | 14/15 | 0 | 1 | 4 | 0 | 0 | 0
chess_clean3_1450.log | 2 | 10-28 15:17 | 5 | 20 | 6 | 5 | 18/19 | 0 | 3 | 5 | 3 | 0 | 2
chess_clean3_1450.log | 3 | 10-28 15:39 | 6 | 20 | 4 | 3 | 10/12 | 2 | 1 | 1 | 1 | 0 | 14
```

## 需要人工复核

- H1: 策略是否真的更强（名次/回合/资源利用趋势，不能只凭 1~2 局）
- H2: 双 Agent 的任务选择与审核质量是否合理
- H3: 是否存在脚本无法发现的游戏规则误判

## 剩余限制
- 名次提升无法由脚本证明，只给过程指标对照（人工判断 H1）。
- 电脑休眠/关机/断网期间不运行，靠 state.db 持久化恢复。