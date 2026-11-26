# 百鬼棋局：交付清单（2026-10-28）

本文件属于**棋局模块自身**，用于回答「这轮到底往棋局里加了什么、怎么验」。

一条命令跑完本模块全部回归：

```powershell
cd D:\3D\YYS2\OnmyojiAutoScript-easy-install-1
.\toolkit\python.exe tasks\Chess\selftest.py          # 14 项，约 1 分钟，结尾 CHESS SELFTEST: PASSED
.\toolkit\python.exe tasks\Chess\selftest.py --quick  # 跳过多花时间的自适应阵容检查
```

## 一、玩法正确性

| 交付 | 位置 | 验证 | 证据 |
| --- | --- | --- | --- |
| 前期低费过渡位（阶≤4 时给 1~2 个当期买得到的同羁绊低费单位，后期自然退出） | `strategy/adaptive.py`、`strategy/season_data.py` | `dev_tools/chess_transition_check.py` 33 项 | 商店费用曲线取自 5 局日志 1900 张卡：阶1→1费、阶2~3→2费、阶4→3费、阶5~6→4费、阶7+→5费 |
| 过渡位淘汰（后期把已退出阵容的过渡位收回手牌再卖掉，不占核心位） | `runtime/hand_operations.py` | 同上 | 实机 3 局：过渡位上阵 3 次、回收 2 次 |
| 过渡位限购（只买一张，避免换血后当杂卡卖掉） | `runtime/economy.py` | `chess_buy_deploy_guard_test.py` 14 项 | 修前「凤凰火 买 3 卖 3、雪童子 买 7 上 2」 |
| 上阵缺口原因分类（hand_empty / 无阵容卡 / 已上阵） | `runtime/hand_operations.py` | 同上 | 证实 `no_candidate` 多数是「棋盘已满」而非空位 |
| 经济预留上阵金币（中后期 `ECONOMY_DEPLOY_RESERVE=25`） | `runtime/economy.py` | 回归 | 修前中后期只留 10~23 金，常出现「钱花光、没上阵」 |
| 阶段动态时间预算（按实测 deploy/soul 耗时 EMA 决定经济何时收手） | `script_task.py`、`runtime/settings.py` | `chess_phase_budget_check.py` 7 项 | 实机每局触发 6~12 次 budget stop |

## 二、识图与操作可靠性

| 交付 | 位置 | 验证 | 证据 |
| --- | --- | --- | --- |
| 符咒 OCR/图标冲突裁决（图标前二 margin<0.03 时信文字） | `strategy/grigri.py`、`runtime/round_state.py` | `grigri_conflict_check.py` 21 项（含 11 条实盘 fixture） | 朱印/纹章 两模板分差仅 0.000~0.010 |
| 御魂装配：无效目标跳过 + 落点偏移重试 + 剩余<6s 不开始 | `runtime/hand_operations.py` | `chess_soul_target_skip_test.py` 28 项 | 修前确认率 71%（24 次尝试仅 17 次成功），失败原因 `hand_count_unchanged` / `mode_changed_after_drag` |
| 结算名次 OCR 动画期空读 → 重拍重读最多 3 次 | `script_task.py` | 实机 | 修前 1 局 26 回合被判 `rank_ocr_unavailable`；修后 3 局名次全部读出 |
| 截图 IPC 卡死自愈（连续失败释放 IPC 重连，60s 冷却）+ 前台不是游戏时报 `GameNotRunningError` | `module/device/device.py` | `dev_tools/screenshot_fallback_check.py` 19 项 | 加载态/退到 launcher 时成片超时，加载完成后 0.04~0.08s |
| 金币两帧确认（突增需下一帧一致才承认） | `runtime/economy.py` | `gold_and_record_check.py` 13 项 | 实测 `8→96→2` 来回跳 |

## 三、数据与审计

| 交付 | 位置 | 验证 |
| --- | --- | --- |
| 六道之门赛季数据（式神/羁绊/符咒/御魂/费用曲线，含来源与核对日期） | `strategy/season_data.py` | `season_data_check.py` 41 项（日志里 63 种式神 + 81 种符咒全部命中） |
| 决策审计（逐回合买/刷/升/上阵/未确认 + 逐步指标归属到发起回合） | `dev_tools/chess_decision_audit.py` | `chess_audit_metrics_check.py` 20 项 |
| 逐局记录与验收门限（连续 3 局完整且不报错） | `log/chess_matches.jsonl`、`dev_tools/dual_agent/acceptance.py` | 实机 3 局连跑：第6名/22回合、第5名/20回合、第6名/20回合，`completed=3/3` |

## 四、当前已知限制（不属于门限）

- 偶发截图超时（3 局共 31 次，集中在加载/切页），会让购买确认率在 0.9 附近波动；
- 等级 OCR 偶发空读（实测 0 次/局后仍保留缓存兜底逻辑）；
- 结算页 3 选 1 幻境仍按随机点选（全房投票，个体选择不一定生效）。
