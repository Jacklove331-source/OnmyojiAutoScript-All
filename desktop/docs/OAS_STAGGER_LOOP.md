# 错峰执行「循环卡住」诊断与修复（2026-10-28）

## 现象

oas1 / oas3 都开启 `stagger_enable=True`（group=`oas1,oas3`, gap=0）后，日志里不断重复：

```
[Queue] 'oas1' waiting for execution token...
[Queue] Idle while waiting token, emulator closed to save resources
Emulator/game not running, run `Restart` before task
Scheduler: Start task `Restart`      （60~80 秒）
[InstanceGuard] 'oas1' released token → 'oas3'
[Queue] 'oas1' waiting for execution token...   ← 回到开头
```

真正要跑的任务（KekkaiUtilize / KekkaiActivation …）只在两次 Restart 之间跑几十秒，
看起来就是「一直在重启和等待，任务轮不到」。

## 实测证据（log/2026-10-28_oas1.txt，17:22–17:42）

```
17:22:22 waiting for execution token...
17:24:46 Emulator/game not running, run `Restart` before task   ← 模拟器被关过
17:26:03 released token → oas3 ; End task Restart
17:26:03 waiting for execution token...                          ← 又等
17:27:33 Idle while waiting token, emulator closed               ← 90 秒后关机
17:28:16 Start task KekkaiUtilize                                ← 拿到令牌但要冷启动
17:29:16 released → oas3 ; 17:29:16 Start task Restart           ← 立刻又要重启
17:30:23 End Restart ; 17:30:23 waiting...
17:31:18 Start task KekkaiUtilize
17:32:10 released → oas3 ; waiting...
17:33:40 emulator closed again                                   ← 又来一遍
17:35:20 Emulator/game not running → Restart
...
```

同一天 oas1 日志里 `waiting for execution token` 7 次、`emulator closed to save resources` 3 次；
两个实例的 `Start task` 之间几乎总夹着一次 `Restart`。

## 根因（三处叠加）

1. **等令牌时关模拟器**（`script.py` 等待循环）：等满 90s 就 `emulator_stop()`。
   错峰交接本来就是「你跑完我接手」，但对方一个任务动辄几分钟，等 90s 就关机；
   关机后拿到令牌必须先冷启动 + Restart 登录，Restart 结束又把令牌让给对方 → 循环。
2. **占着执行权等自己的下一个任务**（`get_next_task`）：先 `_try_acquire_queue_token()`
   再判断 `task.next_run`，所以持有者在「等自己下一个任务」（可能几分钟）期间一直占着令牌，
   同组实例永远抢不到 → 它也会走第 1 条（等 90s → 关机）。
3. **错峰空闲关模拟器门槛太低**（`_wait_close_game`）：错峰时只要空闲 ≥3 分钟就关机，
   而两个实例的任务间隔常常就是 2~5 分钟 → 每次都冷启动。

## 修复

| 位置 | 改动 |
| --- | --- |
| `module/config/instance_guard.py` | 新增 `should_close_while_waiting()`：**错峰模式下等令牌时不关模拟器**；新增 `should_release_before_idle_wait()`：错峰且任务未到点时先让出执行权 |
| `script.py` 等待循环 | 关机判定改用 `should_close_while_waiting()` |
| `script.py get_next_task` | 任务未到点 → `should_release_before_idle_wait()` 成立时先 `release()` 再等 |
| `script.py _wait_close_game` | 错峰空闲关机门槛由 3 分钟改为 `close_emulator_wait`（当前 25/30 分钟） |
| `dev_tools/stagger_loop_check.py` | 20 项回归：错峰不关机、交接不丢令牌、心跳过期可接管、未到点让出执行权、真实日志签名检查 |

## 生效方式

代码改动需要**重启两个 OAS 实例**才生效（进程内是旧代码）。
重启后日志里应不再出现 `Idle while waiting token, emulator closed to save resources`
与「Start task 前必然夹一个 Restart」的组合；验证命令：

```powershell
.\toolkit\python.exe dev_tools\stagger_loop_check.py      # 20 项全绿
.\toolkit\python.exe dev_tools\stagger_status.py          # 看当前令牌/队列
```
