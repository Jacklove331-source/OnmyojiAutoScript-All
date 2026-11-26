# OAS AI 智能自愈层（DeepSeek）使用与验收说明

> 版本：2026-11-05。适用仓库：`D:\3D\YYS2\OnmyojiAutoScript-easy-install-1`（分支 `port/xylolit-vendor`）。
> 原则：**DeepSeek 是辅助恢复层，不是主控制器**。正常跑图仍走 OAS 原有图色/OCR/点击；
> 只有原有逻辑失败且本地恢复也失败时才调用 AI，且 AI 只能提交受控动作计划，由安全执行器落地。

---

## 1. 已完成并实测的能力

| 能力 | 实现位置 | 状态 |
| --- | --- | --- |
| 统一异常检测（12 类信号） | `module/ai_recovery/detector.py` | 已实现；静态帧 + 点击/未知页/循环/显式信号综合判定 |
| DeepSeek 视觉分析 + 严格 JSON 校验 | `client.py`、`schema.py`、`prompt.py` | 已实测（真实 API，`deepseek-flash` 支持图像输入） |
| 安全动作执行（白名单/边界/禁点/OCR 危险词/逐动作验证） | `actions.py` | 已实测：实机点「取消」关闭退出确认框 |
| 两个运行模式（观察/自动） | `config.py` + `orchestrator.py` | 已实测：观察不点击；自动恢复成功 |
| 本地优先（ADB/截图失败先本地恢复，不花 token） | `orchestrator.py` + `integration.py` | 已实测：ADB 断开 15.2s 本地重连；截图超时降级 ADB |
| 本地异常知识库 + 方案复用/失效淘汰 | `kb.py` | 已实测：命中后复用；旧方案失效会自动重新分析 |
| 图色模板候选生成/负样本验证/版本化/回滚 | `templates.py` | 已实测：从真实截图学到 `dialog_exit_cancel`，正样本 1.000、负样本 0.534，v2 晋级后回滚 v1 |
| Token/费用预算硬上限 | `budget.py` | 已实测：2 次调用 ¥0.0083；预算为 0 时不发请求 |
| 证据落盘（截图/上下文/AI 结果/动作/验证） | `evidence.py` | 每次接管一份 `log/ai_recovery/incidents/<ts>_<id>/` |
| OAS 接入（不改原任务代码） | `integration.py` + `tasks/base_task.py` + `tasks/GameUi/navigator.py` | 已实测：Restart 正常跑完，零 AI 接管 |

---

## 2. 文件结构

```
module/ai_recovery/
├── config.py          # YAML + 环境变量配置，密钥解析（不打印）
├── types.py           # Anomaly / Diagnosis / Action / RecoveryResult
├── prompt.py          # 短结构化提示词（JSON schema + 安全边界）
├── schema.py          # 无第三方依赖的 JSON Schema 子集校验
├── client.py          # DeepSeek /chat/completions，重试/熔断/token 计费
├── budget.py          # 每次异常 / 每小时 / 每天调用数与费用上限
├── detector.py        # 进度监视器（帧哈希 + 点击 + 页面历史 + 显式信号）
├── imageutil.py       # dhash / 画面变化率 / JPEG 编码 / 裁剪 / 标注
├── kb.py              # 知识库（JSONL）+ 截图去重
├── actions.py         # 动作白名单 + 坐标/禁点/危险词 + 执行后验证
├── templates.py       # 候选裁剪、正负样本验证、版本、晋级/回滚、资源替换
├── evidence.py        # 证据仓库 + 负样本池
├── orchestrator.py    # 主流程：本地优先 → 知识库 → AI → 执行 → 验证 → 交手
├── integration.py     # TaskAdapter + AiRecoveryMixin（BaseTask/GameUi 接入）
├── anomaly_builder.py # 异常构造小工具
└── selftest.py        # 76 项离线自检（无设备、无网络）
```

新增/修改的 OAS 文件（仅 3 处接入）：

| 文件 | 改动 |
| --- | --- |
| `tasks/base_task.py` | `BaseTask` 增加 `AiRecoveryMixin` 基类；`screenshot()` 末尾调用 `ai_tick()`（关闭时仅一次布尔判断） |
| `tasks/GameUi/navigator.py` | `close_unknown_pages(..., allow_ai=True)`：本地关闭动作全失败后，AI 最后尝试一次；适配器调用时传 `allow_ai=False` 防递归 |
| `.gitignore` | 忽略 `config/ai_recovery.key` 与运行期 `assets/ai_templates/` |

---

## 3. 配置

主配置：`config/ai_recovery.yaml`（被 `.gitignore` 覆盖，不进 Git）。
**本次交付的实际配置为 `enabled: true` / `mode: auto`**（安全动作已通过实机验证）；
`docs/ai_recovery.example.yaml` 保留 `observe`，作为新环境/新游戏版本的保守模板。

```yaml
enabled: true          # 总开关；false = 零 API、零动作
mode: observe          # observe=只分析保存证据；auto=允许安全动作
api:
  base_url: https://api.deepseek.com
  model: deepseek-flash     # 官方 /models 确认 input_modalities 含 image
  reasoning_effort: low     # low 省钱；疑难界面可临时 high
  timeout_s: 75
  max_retries: 2
  max_tokens: 900
  image_max_side: 960
  image_quality: 68
  send_full_screen: true    # false = 一律只发目标区域
budget:
  per_incident_calls: 2
  per_hour_calls: 12
  per_day_calls: 60
  per_day_cny: 3.0
detector:
  stale_frame_s: 90         # 同一画面静止多久算无进展（还需叠加点击证据）
  unknown_page_s: 45        # 未知页持续多久（还需画面基本静止）
  repeated_click_n: 4
  cooldown_s: 180           # 同一异常冷却，防反复接管
  max_incidents_per_key: 3  # 同一异常最多接管次数
safety:
  max_actions: 4
  max_click_per_point: 2
  min_confidence: 0.6
  verify_change_ratio: 0.012
  forbidden_rects: ...      # 归一化禁点矩形
  forbidden_keywords: ...   # 充值/支付/购买/删除/退出游戏/注销...
knowledge:
  enabled: true
templates:
  enabled: true
  auto_apply_to_source: false   # true 才允许覆盖正式图色资源（会先备份）
```

环境变量覆盖：`OAS_AI_ENABLED`、`OAS_AI_MODE`、`OAS_AI_MODEL`、`OAS_AI_BASE_URL`；
密钥：`DEEPSEEK_API_KEY`（优先）或 `config/ai_recovery.key`（单行，勿提交）。
`config/ai_recovery.key` 只写权限可控的本地文件，代码与日志永不输出密钥。

---

## 4. 接入点与行为

1. **每帧（BaseTask.screenshot）**：`ProgressMonitor.observe()` 只做 dhash + 32×18 缩略图（<1ms），
   满足条件才产生异常事件。正常跑图不会调用 API。
2. **未知页面（navigator.close_unknown_pages）**：原有 local closers + 返回键全失败后，
   才调用 `ai_recover_unknown_page()`；恢复成功返回 `True`，导航继续。
3. **显式信号**：任务/导航可调用 `self.ai_signal('template_miss', detail)` /
   `self.ai_note_click(x, y, name)`，把业务层证据并入判定。
4. **设备卡死兜底**：OAS 的 60s `stuck_timer` 比异常检测器更早触发（Duel 这类“已知页 + 零点击等待”
   尤其明显）。`Device.stuck_record_check()` 抛 `GameStuckError` 前会调用 `_ai_stuck_handler`
   （`BaseTask` 初始化时注册）；AI 恢复成功则清计时器继续任务，否则保持原异常语义。

检测规则（避免误判的关键）：

| 异常 | 触发条件 |
| --- | --- |
| `unknown_page` | 未识别页持续 ≥45s **且** 画面基本静止 ≥36s，或持续 ≥180s |
| `no_progress` | 画面静止 ≥90s **且**（期间 ≥2 次点击无变化 / 处于 loading|login / 超过任务级超时） |
| `click_no_effect` | 同一坐标 30s 内 ≥4 次点击且期间帧哈希无变化 |
| `click_loop` | 90s 内两个页面来回跳 ≥8 次 |
| `template_miss` / `ocr_fail` / `state_mismatch` / `load_timeout` / `screen_changed` / `popup` / `recovery_failed` / `recognition_conflict` | 任务代码显式上报，120s 内有效 |

战斗/加载/动画期间帧持续变化，因此不会因“两张截图一样”被误判。

---

## 5. AI 契约

请求：`POST {base_url}/chat/completions`，`response_format=json_object`，截图以 data URL 发送，
prompt 内包含任务/页面/异常/最近动作/分辨率/已知页面/错误摘要。

返回结构（缺字段或越界即整条作废）：

```json
{
  "page": "退出游戏确认框",
  "page_known": false,
  "page_category": "dialog",
  "cause": "退出确认弹窗遮挡，模板识别失败",
  "confidence": 0.94,
  "risk": "low",
  "actions": [
    {"type": "click", "x": 0.618, "y": 0.655, "text": "取消", "reason": "关闭弹窗"}
  ],
  "expected_change": "弹窗消失，回到庭院",
  "needs_human": false,
  "template_update": {
    "needed": true, "name": "dialog_exit_cancel",
    "region": {"x": 0.583, "y": 0.628, "w": 0.07, "h": 0.055}
  }
}
```

`template_update` 是可选字段：单独校验，坏了只丢弃该字段，不影响恢复动作。

---

## 6. 安全边界（AI 拿不到无限制控制权）

- 动作白名单：`click / wait / back / close_popup / goto_page`；`goto_page` 只接受页面注册表里存在的 key。
- 坐标：归一化坐标 → 像素，必须落在屏幕内且离边缘 ≥12px；禁点矩形（右上充值区、右下菜单区）直接拒绝。
- 危险词：AI 给出的按钮文字、以及目标点周围 90px 的 OCR 文本，命中
  `充值/支付/购买/兑换/删除/分解/出售/退出游戏/注销/解绑/设置/清除数据` 即拒绝；
  例外：动作文字是 `取消/关闭/返回/放弃/不用/以后/否/稍后`（允许取消购买/关闭弹窗）。
- 次数：单次异常 ≤4 个动作、同一坐标 ≤2 次；执行前有 45s 总预算。
- 语义锚点：AI 给的按钮文字会先用整屏 OCR 找到同名文本框并点其中心；只有找不到时才用 AI 坐标
  （实测修正过一次 y 轴偏差 140px 的误点，见 `live_20261105_065643.json`）。
- 验证：每个动作后重新截图，画面变化 <1.2% 且页面未变 → 判失败并立即停手，绝不盲连点。
- 低置信（<0.6）、`needs_human=true`、`risk=high` → 不动作，保存证据。
- 失败隔离：验证不过 → 记录知识库失败、返回 `STOPPED`，OAS 走原有 Restart/失败流程。
- 庭院页禁止 `back`：否则会弹出「退出游戏」确认框；执行器直接拒绝该动作。
- 防循环：同异常 180s 冷却 + 进程内最多 3 次 + 相同截图 10 分钟内不重复送 API。

---

## 7. 省钱策略与实测成本

1. 正常不调用（Restart 实机 48.4s，0 次调用）。
2. 本地优先：ADB/截图故障先本地恢复（实测 0 token）。
3. 知识库命中直接重放（观察模式也只记录，不花钱）。
4. 相同截图哈希去重 + 异常指纹冷却。
5. 目标区域异常只发局部截图（`view=target_crop`），上下文日志限长。
6. 每次调用记录 prompt/cached/completion token 与费用，写入 `log/ai_recovery/budget_oas3.json`。

实测（2026-11-05，oas3 / MuMu 1280×720）：

| 场景 | prompt | cached | completion | 费用 |
| --- | --- | --- | --- | --- |
| 退出确认框（第一次） | 1207 | 384 | 316 | ¥0.004366 |
| 退出确认框（第二次） | 1251 | 512 | 281 | ¥0.003982 |

当天全部 7 次真实调用合计 **10711 token / ¥0.03014**，平均 **≈¥0.0043/次**。按每天 60 次上限 ≈¥0.26/天（实际远低于此）。

---

## 8. 图色模板自动维护

流程与代码：

1. AI 返回 `template_update.needed=true` 与 `region`；
2. `TemplateLibrary.create_candidate()` 从**当前真实截图**裁剪候选（不是猜坐标）；
3. `validate()`：正样本 `matchTemplate(TM_CCOEFF_NORMED)` 必须 ≥0.88；负样本来自历史真实截图
   （`log/error/**` + 既往证据），同一页面的负样本按整帧 dhash 剔除，避免把同元素当误匹配；
   误匹配率必须 ≤2%，负样本最高分必须低于 `0.88-0.06`；
4. 通过后 `promote()` 写入 `assets/ai_templates/<task>/<name>/vNNN.png` 并更新 manifest
   （分辨率、区域、来源、验证报告、时间、版本）；
5. 可 `rollback()` 回滚到上一版本；
6. 运行时优先复用：`TemplateLibrary.match_active()` 在未知页兜底时先匹配已晋级模板并点击中心，
   命中即恢复（不再调 API）；这一步在 `close_unknown_pages` 里排在调用 AI 之前；
7. `apply_to_source()` 才替换正式图色资源，且先把原文件备份到 `assets/ai_templates/_backups/`，
   写入 apply 日志；`restore_source()` 可还原。默认 `auto_apply_to_source: false`。

实机证据：`dialog_exit_cancel` v1（正样本 1.000、负样本 0.534、误匹配率 0），
新帧回归 1.000，v2 晋级后回滚到 v1，副本替换/还原均成功
（`log/ai_recovery/live/live_20261105_060830.json`、`assets/ai_templates/GameUi/dialog_exit_cancel/manifest.json`）。

---

## 9. 常用命令

```powershell
# 离线自检（72 项，无设备/无网络）
.\toolkit\python.exe dev_tools\ai_recovery_selftest.py

# 状态（开关/模式/预算/知识库/模板）
.\toolkit\python.exe dev_tools\ai_recovery_cli.py status

# 首次启用：生成配置 + 从 ~/.dsh/.credentials.yaml 导入密钥（不打印密钥）
.\toolkit\python.exe dev_tools\ai_recovery_cli.py init
.\toolkit\python.exe dev_tools\ai_recovery_cli.py key --from-credentials

# 对单张截图跑一次真实 AI 分析（不操作游戏）
.\toolkit\python.exe dev_tools\ai_recovery_cli.py analyze log\error\<ts>\xxx.png --mode observe

# 实机验收（每次只跑一个场景）
.\toolkit\python.exe dev_tools\ai_recovery_live_test.py state
.\toolkit\python.exe dev_tools\ai_recovery_live_test.py popup
.\toolkit\python.exe dev_tools\ai_recovery_live_test.py adb
.\toolkit\python.exe dev_tools\ai_recovery_live_test.py screenshot
.\toolkit\python.exe dev_tools\ai_recovery_live_test.py offline
.\toolkit\python.exe dev_tools\ai_recovery_live_test.py template
.\toolkit\python.exe dev_tools\ai_recovery_live_test.py synthetic   # 真实 API + 无意义画面，验证不盲点
.\toolkit\python.exe dev_tools\ai_recovery_live_test.py normal --task Restart
```

切换自动模式：编辑 `config/ai_recovery.yaml` 的 `mode: auto`，或临时
`$env:OAS_AI_MODE='auto'`（仅当前进程）。关闭：`enabled: false`（立即回到零开销）。

---

## 10. 验收证据（2026-11-05 实机）

| # | 验收项 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 正常任务不调用 AI | PASS：Restart 48.4s，incidents 5→5，API calls=2（均为前面的故障注入） | `log/ai_recovery/live/live_20261105_061054.json` |
| 2 | 模拟识图模板失效 | PASS：对 `I_CHECK_MAIN` 候选保守拒绝（负样本 0.883 高误匹配风险），未覆盖资源 | `live_20261105_060743.json` |
| 3 | 模拟未知游戏弹窗 | PASS：实机退出确认框，AI 识别 0.94 置信并点「取消」 | `incidents/20261105_060420_12c931d1b73d/` |
| 4 | 按钮点击无效 | 离线 PASS：`unverified -> stopped`（不盲点） | `selftest` 76/76 |
| 5 | 页面状态异常 | 离线 PASS：`state_mismatch` 信号 + 页面循环检测 | `selftest` |
| 6 | ADB 连接中断 | PASS：15.2s 本地重连，0 token | `live_20261105_060523.json` |
| 7 | 截图获取失败 | PASS：两次超时后降级 ADB，0 token | `live_20261105_060608.json` |
| 8 | DeepSeek API 超时 | PASS：1.5s 超时抛出，不阻塞 | `live_20261105_060708.json` |
| 9 | API 返回错误结果 | PASS：非法 JSON / 非法结构均被拒 | 同上 |
| 10 | 低置信度识别 | PASS：真实 API 对无意义黑屏返回 needs_human，0 点击 | `live_20261105_062102.json` |
| 11 | 图色自动生成与回归 | PASS：候选/验证/晋级/回滚/副本替换 | `live_20261105_060830.json` |
| 12 | AI 成功后交回 OAS | PASS：恢复到 `page_main`，导航可继续 | `incidents/20261105_060420.../result.json` |
| 13 | 无法安全恢复时停止 | PASS：`STOPPED` + 隔离，不反复接管 | `selftest` |
| 14 | 不反复接管循环 | PASS：第二次同异常 `SKIPPED`（冷却/次数） | `live_20261105_060902.json` |
| 15 | Token/费用限制生效 | PASS：预算 0 时 0 请求；日累计 2 次/¥0.0083 起 | `budget_oas3.json` |
| 16 | 正常棋局长跑不被干扰 | PASS：单局 26 回合/第 4 名，`completed=1/1`、`exit_confirmed=True`、0 ERROR，32.7 分钟，AI 接管 0 次 | `D:\3D\YYS2\_recovery_070208\ai_chess_1.log` |
| 17 | 知识库复用不再调 API | PASS：同一弹窗第二次 3.9s 恢复，`ai_called=False`，当日调用数不变 | `live_20261105_065810.json` |
| 18 | 设备 60s 卡死兜底 | 代码已加、单测两分支通过；**待实机复测**（见 21:59 Duel 事故） | `device.py` hook + `_test_stuck.py` |

回归（均通过）：

- `compileall tasks module script.py`
- `dev_tools/missing_method_check.py`：62 任务 0 问题
- `dev_tools/contract_scan.py Chess`：0 问题
- `dev_tools/ui_click_regression.py`：31/31
- `dev_tools/adb_recovery_unit.py`：38/38
- `dev_tools/click_record_check.py`：12/12
- `tasks/Chess/selftest.py --quick`：13/13（50.2s）

---

## 11. 已知限制

- 视觉模型会偶发误判/坐标偏差（实测一次 y 轴偏 140px，已用 OCR 文字锚点纠正；也出现过把有返回
  箭头的页面说成“无关闭按钮”）。执行层始终以安全为先，误判最多导致“停手/保存证据”，不会造成
  不可逆操作。
- 全屏感知哈希对大幅动画/换装敏感，知识库复用门槛较严（汉明 ≤12）；命中率会偏低，
  但宁可不复用也不乱点。
- `auto_apply_to_source` 默认关闭：模板先进入 AI 版本库；确认稳定后再开启自动替换或手动 apply。
- 复杂任务（棋盘拖拽、长按、输入）不在 AI 动作白名单内，AI 只能做“恢复”，不能执行业务流程。
- 2026-11-05 21:59 `Duel` 在斗技页被「寻龙逐英/开心收下」活动弹窗挡住，OAS 等待 60s 后
  `GameStuckError`，连续 3 次失败后退出并关模拟器。当时 AI 未介入的原因：页面是已知 `page_duel`
  且零点击，检测器的 `no_progress` 需 90s+点击证据；现已加 `Device.stuck_record_check` 兜底，
  **但这次修复还没有实机复测**。
- 本次只做了**单局**棋局回归（26 回合/第 4 名/0 ERROR）；原项目“连续 3 局”验收口径仍需按
  `dev_tools/chess_series_test.py oas3 3 04:00:00` 单独执行，不能拿本文件的单局结果代替。

---

## 12. 回滚 / 卸载

- 立即停用：`enabled: false`（不删代码也零调用）。
- 完全卸载：删除 `module/ai_recovery/`，移除 `BaseTask` 的 mixin 与 `ai_tick()` 调用、
  移除 `close_unknown_pages` 的 AI 分支；三处改动均为追加，不影响原有逻辑。
- 模板回滚：`ai_recovery_cli.py templates rollback <task> <name>`；
  正式资源恢复：用 `assets/ai_templates/_backups/` 里的备份覆盖原 png。

