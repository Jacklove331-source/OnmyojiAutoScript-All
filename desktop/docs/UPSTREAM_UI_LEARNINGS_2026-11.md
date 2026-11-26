# OAS 上游/社区「新 UI 适配」学习笔记（2026-11-01）

> 目的：游戏 10-28 改版后，先看别人怎么做，避免自己重复造轮子。
> 结论速览：**官方 master 已包含悬赏封印适配（PR #1973 已合并 2026-10-28）**；
> **社区还有一份更完整的草稿 PR #1987（62 文件、Draft）**，把悬赏封印 + 庭院主题 + 结界皮肤一起适配了。

## 一、10-29 之后的相关提交/PR 清单

| 时间 | 类型 | 内容 | 对我们有用吗 |
| --- | --- | --- | --- |
| 10-28 11:32 | PR #1973 **已合并** | Wanted quests 新 UI 适配（我们已抄素材） | ✅ 已采用 |
| 10-29 00:03 | commit `eddc6171` | `fix(GameUi): 兼容商店两种招牌图片，修复反复进出商店` | ✅ 值得抄（商店来回进出） |
| 10-29 13:43 | PR #1977 已合并 | 同上（商店招牌兼容） | ✅ |
| 10-30 | PR #1988 已合并 | `地域鬼王准备阶段自动开战导致切预设死循环` | ⚠️ 与点击死循环同类，值得读 |
| 10-30 15:34 | commit `8d499033` + PR #1991 已合并 | `GlobalGame: 更新协作邀请识别素材与坐标` | ✅ 我们的 GlobalGame 已与之等价（diff 仅缩进差异） |
| 10-30 | PR #1987 **open / Draft** | **新语明霄主题 + 月海游星结界 + 新版悬赏封印界面**（62 文件 +1380/−383） | ⭐ 重点学习对象 |
| 10-31 | PR #1994/#1993 已合并 | 桌面版空闲策略关游戏、杀进程重启 | ➖ 与我们无关（安卓） |
| 10-31 | PR #1984 open | 花合战偶发未跳转领奖界面 | ➖ |

## 二、PR #1987 的方法论（最值得学的 8 条）

作者 Ghostuel，分支 `feat/xinyu-mingxiao-theme`，自带 15 项离线单测 + 55 项截图正负例。

1. **面板判定不靠标题 OCR，靠「控件存在 + 弹窗不存在」**
   ```python
   def wanted_board_open(self):
       # 模板匹配对亮度不敏感：把详情/邀请弹窗排除掉才算面板可见
       return (not self.appear(self.I_WQ_TRACE_ONE_CHECK_OPENED)
               and not self.appear(self.I_WQ_INVITE_ENSURE)
               and (self.appear(self.I_TRACE_ENABLE) or self.appear(self.I_TRACE_DISABLE)))
   ```
2. **打开面板要有「多入口 + 允许从探索页开始」**：`I_WQ_LIST_ENTRY`（新列表入口）→ `I_WQ_SEAL`
   → `I_WQ_DONE`(multi_scale) 依次尝试；`if not appear(I_CHECK_EXPLORATION): goto_page(page_main)`。
3. **新 UI 有直达入口就别绕庭院**：`goto_wanted_exploration()` 只点「前往探索」并**校验探索页标志**，
   超时抛 `ScriptError`（不允许静默成功）。
4. **「全部已追踪」用目标态判定**：等 `I_TRACE_DISABLE` 出现，而不是数点击次数。
5. **详情关闭用右上角关闭按钮**（`I_WQ_TRACE_ONE_CHECK_OPENED`），不再点旧版空白区域。
6. **协作邀请有 4 个入口**（`I_WQ_INVITE_1..4`，对应四张任务卡），`wanted_invite_buttons()` 统一取。
7. **邀请「假成功」防护**（重点）：
   ```python
   # 模板缺失不等于发送成功：必须 (a) 点过邀请按钮 (b) 弹窗关闭 (c) 回到面板
   if clicked and not self.appear(self.I_WQ_INVITE_ENSURE) and self.wanted_board_open():
       return True
   ```
   反过来，任何一步不满足就 `raise ScriptError`。
8. **素材 JSON 是唯一源头**：`assets.py` 由 `dev_tools.assets_extract.AssetsExtractor` 生成
   （我们仓库也有这个工具，**别再手改 assets.py**）。
9. **测试写成「object.__new__(ScriptTask) + Mock」的离线单测**，不建 Device、不连游戏：
   `test_wanted_ui.py` 174 行覆盖「直达探索 / 必须点邀请 / 弹窗必须关闭 / 四卡邀请 / 来源 OCR 解析」。

## 三、PR #1987 的已知缺陷（别照抄的地方）

- **Draft 状态**，作者自述「不打指定名字怪物」在**新版详情页 OCR 仍失败**：
  配置跳过「天邪鬼绿」，OCR 读成「自己击败天邪电绿」，精确匹配不命中 → 仍会去打。
- 来源面板**只处理前四行**，无滚动策略；妖气/年兽战斗未接。
- 只在离线验证过（截图正负例 + Mock 流程），**没有完整实机验证**；
  部分旧素材（现世任务标志、跨区高亮）无新版正例截图。
- 与上游 master 存在合并冲突（作者 TODO 未完成）。

## 四、对照我们自己的实现（2026-11-01 现状）

| 能力 | 我们（port/xylolit-vendor） | PR #1987 |
| --- | --- | --- |
| 面板判定 | 标题 OCR「悬赏封印」+「下次刷新」 | 控件存在性（更稳，亮度无关） |
| 打开面板 | 固定点町中入口 (85,340) + OCR 可见性 | 多入口模板 + 允许从探索页开始 |
| 战斗入口 | 三卡任务文本 OCR → 详情「前往」 | 面板「前往探索」直达 → 校验探索页 |
| 邀请 | OCR 找底栏/弹窗按钮，要求弹窗关闭 | 4 个邀请模板 + 三条件确认 |
| 素材来源 | 手改 `assets.py`（易与 json 漂移） | json → 生成器 |
| 测试 | `dev_tools/wq_v2_check.py`（纯逻辑 38 项） | `tests/tasks/WantedQuests/test_wanted_ui.py`（流程 15 项） |
| 实机验证 | ✅ 多次（面板/卡片/邀请/战斗） | ❌ 仅离线 |

## 五、采纳建议（按风险从低到高）

1. **立刻可做（低风险）**：把「面板判定加控件兜底」和「直达探索」两个思路补进我们的 `ui_wq_v2.py`
   （OCR 判定失败时用模板兜底；协作模式走「前往探索」而不是绕庭院）。
2. **待办（中风险）**：等 #1987 修完黑名单 OCR 并离开 Draft 后，整体替换 `tasks/WantedQuests/**`
   并一起引入它的 `tests/tasks/WantedQuests/`（届时用 `python -m unittest discover` 验证）。
3. **不要做**：直接抄 Draft 的 `script_task.py` 覆盖我们已实测跑通的流程
   （会把「不打指定名字怪物」的已知 bug 引进来，而且没有实机验证）。
4. **顺手可抄**：`eddc6171`（商店招牌兼容）如果我们的商店任务出现「反复进出商店」，直接搬；
   `PR #1988`（地域鬼王切预设死循环）与我们的点击死循环同源，值得读它的修法。

## 六、抓取脚本

- `dev_tools/fetch_pr1987.py`：把 PR #1987 的文件下到 `dev_tools/upstream_pr1987/`
  （支持 `--only-prefix tasks/WantedQuests` 之类按目录筛选，逐文件重试）。
- `dev_tools/fetch_pr1973.py` + `dev_tools/apply_pr1973_assets.py`：上一轮抄 #1973 素材用。
