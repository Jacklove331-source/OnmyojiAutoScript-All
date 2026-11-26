# 双 Agent 自动开发系统 v2：Claude 总指挥 + DeepSeek 执行（OAS 百鬼棋局）

## v2（2026-10-28 起，以本节为准）

| 角色 | 实际模型 | CLI | 职责 |
| --- | --- | --- | --- |
| Claude 总指挥 | 规划/疑难/高风险审核 `claude-opus-5-5`；常规审核/辅助 `claude-sonnet-5-5` | `claude-anthropic.cmd`（api.anthropic.com，经本机代理） | 规划任务卡、审核 diff+机器验证、返工用尽时亲自做疑难分析、独立 worktree 做研究类辅助 |
| DeepSeek 执行 | `deepseek-v4-pro` | `claude.CMD`（用户级 `ANTHROPIC_BASE_URL` = api.deepseek.com/anthropic） | 改业务源码、写测试（无终端，验证由调度器跑） |
| DSH 里的 DeepSeek 会话（可选） | DSH 当前模型 | `deepseek_b.py next/record/review` | 有终端/模拟器的执行者；与调度器共用**执行锁**，不会同时改主工作区 |
| 调度器 | Python | `scheduler.py` + `watchdog.py` | 状态机、预算、执行锁、实机评估、验收门禁、恢复 |

> v1 的「Agent A = claude-opus-4-1」其实也走 `claude.CMD` → DeepSeek，**不是 Claude**。v2 的 AC02
> 只承认 provider=anthropic 的审核。

常用命令：

```powershell
.\toolkit\python.exe dev_tools\dual_agent\status.py              # 状态/预算/验收/每任务 token 与费用
.\toolkit\python.exe dev_tools\dual_agent\tests\run_all.py       # 自测（6 组 104 项）
.\toolkit\python.exe dev_tools\dual_agent\control.py start       # 拉起看门狗（已在跑则跳过）
.\toolkit\python.exe dev_tools\dual_agent\control.py pause       # 安全暂停（写 state/PAUSE）
.\toolkit\python.exe dev_tools\dual_agent\control.py resume      # 去掉暂停并续跑
.\toolkit\python.exe dev_tools\dual_agent\metrics.py --baseline  # 实战过程指标（基线）
```

状态：`running` / `paused_daily`（24h 预算窗口到顶，看门狗到点自动续跑）/ `paused_env`（代理/网络
故障，20 分钟后重试）/ `blocked`（暂无可执行任务，45 分钟后重试）/ `paused_limit`（单次运行到时，
立即重启）/ 终态 `paused_budget`（总额到顶）、`paused_manual`、`completed`（全量复核 20 项强制验收通过）。

预算硬上限（`config.json → budget`）：Claude 总 $80 / 24h $25；DeepSeek 总 ¥300 / 24h ¥80；
24h 调用 ≤260 次；总计 ¥900、1500 次。Claude 费用取 CLI 报告的真实账单价（`total_cost_usd`）。

---

## v1 说明（历史）

两个真实 Agent + 一个调度器，持续改进 `D:\3D\YYS2\OnmyojiAutoScript-easy-install-1`：

| 角色 | 模型 | 权限 | 职责 |
| --- | --- | --- | --- |
| Agent A（总指挥/审核） | claude-opus-4-1 → deepseek-v4-pro | `--permission-mode plan`（只读 + 联网查资料） | 读现状/日志/文档 → 规划任务卡 → 审核 B 的改动 → PASS/REWORK/BLOCKED |
| Agent B（执行工程师） | claude-sonnet-4-5 → deepseek-flash | `--permission-mode acceptEdits` + Bash 白名单 | 改代码、补测试、自述证据 |
| 调度器 | 纯 Python | 自己执行验证命令 | 状态机、预算、持久化、返工、实机对局托管、验收 |

两者都通过 **Claude Code CLI（headless `-p`）** 调用，复用其成熟的文件/终端/权限能力，
DeepSeek 由 `ANTHROPIC_BASE_URL` + 模型映射接入（凭证只在进程环境里，不落盘、不打印）。

## 快速开始

```powershell
# 1) 看状态
.\toolkit\python.exe dev_tools\dual_agent\status.py

# 2) 自测（63 项断言，不花钱）
.\toolkit\python.exe dev_tools\dual_agent\tests\run_all.py

# 3) 后台启动（关掉终端也继续跑）
powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\start.ps1 -Hours 7

# 4) 带守护（异常退出自动重启）
powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\run_forever.ps1 -Hours 7

# 5) 可选：注册「登录后续跑」计划任务（电脑重启后自动继续）
powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\install_resume_task.ps1 -Install
powershell -ExecutionPolicy Bypass -File dev_tools\dual_agent\install_resume_task.ps1 -Remove
```

## 工作流

```
PLAN(A) ──任务卡──> EXECUTE(B) ──改动──> VERIFY(调度器跑测试/静态检查)
     ↑                                        │
     └──── REWORK(≤max_rework) ←── REVIEW(A) ←┘
                                   │
                             PASS ─> accepted ─> 下一任务 / 实机对局 / 验收
```

- **任务卡**：`{id,title,goal,files,constraints,verify[],max_rework,risk}`
- **机器验证**：`verify` 里只能填已注册的 AC id（AC05/AC05S/AC08/AC09/AC10），
  调度器自己跑脚本，B 无法伪造结果
- **实机对局**：调度器托管（同一时间只允许一个进程、会话内 2 次上限、
  2 小时超时），完成后写入 `log/chess_matches.jsonl` 作为 AC06 证据
- **返工**：`max_rework` 次机会用尽 → 任务 `blocked`，不再烧预算

## 持久化与恢复

| 文件 | 内容 |
| --- | --- |
| `state/state.db` | tasks / runs / verifications / acceptance / meta（SQLite） |
| `state/events.jsonl` | 全量事件流（任务、审核、git 检查点、实机对局、预算） |
| `state/heartbeat.json` | 心跳（进程是否真的在跑，不假装） |
| `state/FINAL_REPORT.md` | 终态报告：验收表 + 任务流水 + 人工复核项 + 剩余限制 |
| `logs/scheduler_*.log` | 每次运行的完整输出 |

进程被杀/电脑重启 → 重新启动后从 `state.db` 继续：未完成任务直接续跑，
已完成任务跳过，预算与实机次数继续累计（不会重复危险操作）。

## 预算与安全

- 费用硬上限：`config.json → budget.max_cny_total`（默认 ¥12）、`max_calls`（160）、`max_hours`（7.5）
- 价格表是**保守估计**（input ¥4/M、cache ¥1/M、output ¥16/M），宁可提前停
- 危险动作熔断：
  - git：`reset --hard` / `clean` / `push --force` 一律拒绝
  - shell：`rm -rf` / `del /s` / `format` / `shutdown` / `reg delete` 拒绝
  - 路径：`.credentials`、`config/*.json` 禁止写入
  - 实机：单进程 + 次数上限
- 连续失败 `max_consecutive_failures`(3) 次 → `blocked`，保存现场不再重试

## 验收项（机器可检查）

| ID | 内容 |
| --- | --- |
| AC01 | 调度器自测全绿（持久化/预算/安全/协议/验收门禁） |
| AC02 | 至少一次真实 A→B→A 协作（DB 里有 agent_a/agent_b 成功调用与已验收任务） |
| AC03 | 任务持久化与恢复 |
| AC04 | Token/费用预算与安全停止 |
| AC05 / AC05S | OAS 行为级回归 + 静态检查（contract/missing method） |
| AC06 | 百鬼棋局完整流程（进局→结算→退出，本会话内） |
| AC07 | ADB/截图异常恢复机制 |
| AC08 | 策略修复有回归测试（过渡位、时间预算） |
| AC09 | 符咒冲突与御魂操作验证 |
| AC10 | 赛季数据核对（含实机日志覆盖） |
| AC11 | 稳定版本与文档 |
| AC12 | 最终报告存在且含证据 |

**人工复核项**（不阻塞状态机，写进报告）：H1 策略是否真的更强、H2 双 Agent 决策质量、
H3 脚本无法发现的规则误判。

只有 AC01–AC12 全部 PASS 才会把状态写成 `completed`；否则保持
`running / paused_budget / blocked / paused_limit` 之一，**不会伪报完成**。

## 已知边界

- 电脑休眠/关机/断网时不会运行（不假装在跑）；重启后靠 `state.db` 恢复。
- 名次提升无法由脚本证明，只能给过程指标趋势（人工判断）。
- DeepSeek 侧若限流/超时，`cli_agent` 会重试 2 次后放弃；连续失败会触发熔断。
