# 工作台补全独立验收报告（wb-verify）

- 验收时间：2026-11-20 22:56 – 23:05（Asia/Shanghai）
- 验收对象：工作台后端 11 组新接口 / 老路由回归 / 工作台 UI 信息架构与截图 / 写操作边界 / 藏宝阁残留
- 仓库：`D:\3D\YYS2\OnmyojiAutoScript-easy-install-1`，解释器 `.\toolkit\python.exe`（3.10）
- 基线提交（补全前）：`46829740`（2026-11-20 22:30:02 autosave）；本轮核验工作树：`bac54329` + 未提交改动
- UI 核验对象（工作树文件）：`module/server/web/workbench/index.html`
  sha256 `be9bde3c7c8525bfb23a4364aeefa225ec38dc578266e69bcda7f86685dd1866`（112,945 B，mtime 23:01:47）
  HEAD 版本 sha256 `0b0d25ae64e4d7b93c2ea6e9a2ba591c339b3c523b52f3c6c5e4269b496703e2`（111,507 B）
- 独立验收脚本：`dev_tools/verify_workbench_ui.py`（本轮新写，不使用实现方自检的断言）
  结果：**TOTAL 162 / PASS 162 / FAIL 0**，退出码 0
- 临时服务：只挂 `/workbench` 路由，`127.0.0.1:23334`（新接口）+ `127.0.0.1:23335`（补全前 router，用于老路由对比），进程内 marker 校验确认请求确实打到本轮临时服务；**未重启、未触碰 22267 上正在跑的 OAS 服务**。
- 写操作全部打到 `%TEMP%` 下复制出来的 OAS store / 备份目录，仓库 `config/workbench` 与平安志目录均未被本脚本写入。
- 未做任何真机动作（未起停实例、未 adb）。

## 结论

全部 7 条验收标准通过，无阻塞项、无 FAIL 项。发现 3 条低风险事项与 2 条交付提醒，见「八、发现与风险」。

| # | 验收标准 | 结果 |
|---|---|---|
| 1 | compileall exit 0 + Workbench 六份 selftest 全绿 | PASS |
| 2 | 11 组新接口逐条实测，字段与契约一致；无效参数 4xx；平安志不可用优雅降级 | PASS |
| 3 | 12 条老路由回归，结构与补全前一致 | PASS |
| 4 | UI 1440/720 两张截图真实生成；导航顺序/锚点/三态/无外网 CDN | PASS |
| 5 | 写操作只落 OAS store；平安志 sqlite mtime 不变；代码无写 SQL | PASS |
| 6 | 藏宝阁残留为 0 | PASS |
| 7 | 本报告落地，FAIL 项带复现命令 | PASS（本次 0 FAIL，复现命令见第九节） |

---

## 一、编译与自检

命令（仓库根）：

```powershell
.\toolkit\python.exe -m compileall -q tasks module script.py           # exit 0，无输出
.\toolkit\python.exe tasks\Workbench\selftest.py                       # TOTAL 15  PASS 15  FAIL 0
.\toolkit\python.exe tasks\Workbench\selftest_web.py                   # TOTAL 79  PASS 79  FAIL 0
.\toolkit\python.exe tasks\Workbench\selftest_features.py              # TOTAL 61  PASS 61  FAIL 0
.\toolkit\python.exe tasks\Workbench\selftest_domains.py               # TOTAL 9   PASS 9   FAIL 0
.\toolkit\python.exe tasks\Workbench\selftest_io.py                    # TOTAL 20  PASS 20  FAIL 0
.\toolkit\python.exe tasks\Workbench\selftest_paz.py                   # TOTAL 20  PASS 20  FAIL 0
```

六份自检合计 204 项全绿；`selftest_paz` 真实读到本机平安志库：`souls=3547 owned=3201`。
`selftest_web` 已包含 `web.new.no_cbg` 这类负向断言，本轮除复跑外，另用独立脚本重做了同类检查（见第七节）。

## 二、11 组新接口（14 条路由，真实 HTTP）

服务：本轮临时服务 `http://127.0.0.1:23334`（marker 校验通过），数据源为本机平安志库 + 临时 OAS store 副本。

| 组 | 路由 | 断言到的字段 / 结果 | 实测样本 |
|---|---|---|---|
| 1 | `GET /workbench/souls/{id}` | `ok, source, soul{internal_id,soul_key,set_name,slot,quality,level,score,main_attribute,main_value,equipped,locked,recommendation,category}, attrs[{name,value,is_fixed,rolls,kind}], score{value,rule,contributions,formula,source}, radar[8]{axis,value}` | 取 `/souls` 首行 `soul_id` 打开，200；`attrs` 非空；`score.source=analysis_todo`；`radar` 8 轴 |
| 2 | `GET /workbench/todos` | `ok, source, items[], counts{open,done,ignored}, total, filtered`；单条 `todo_id,soul_id,set_name,score,rule,status,note,labels,soul_internal_id,slot,level,quality,main_attribute,category,recommendation,updated` | `counts={open:3547,done:0,ignored:0} total=3547`，`limit=5` 返回 5 条 |
| 3 | `POST /workbench/todos/decision` | `decision{todo_id,action,status,note,at}`；done→restore 状态流转 | done 返回 `status=done`，restore 返回 `status=open`；处置出现在 `/todos/history` 首条 |
| 4 | `GET /workbench/todos/history` | `ok, source, items[]` | 审计行含 `note="wb-verify 验收"` |
| 5 | `GET /workbench/standards/versions` | `ok, source, items[{id,name,active,rule_count,updated,version,preset,status}], active, preference` | `source=rule_version`，4 套标准，`active=builtin:builtin.owner.default-manual` |
| 6 | `POST /workbench/standards/activate` | `ok, active, updated, note`；只改 OAS 偏好 | 激活首套版本 200，`active` 与传入一致 |
| 7 | `GET /workbench/roster/{name}` | `name,rarity,level,star,skills[],souls[],owned,id,awakened,skin,icon,panel,instances` | `丑时之女`：`owned=true, souls=6, skills=3` |
| 8 | `GET /workbench/snapshots` | `ok, source, items[{snapshot_id,time,items,souls,source,integrity,integrity_message,checked_at,completeness}], note` | 1 条快照 `1ed777ca-…`，`integrity=unknown`（库无 integrity 表时按「未记录」） |
| 9 | `GET /workbench/backups` + `POST /workbench/backups/create` | `ok, source=oas-store, items[], dir, note, pinganzhi{ok,items,reason}`；备份行 `{id,time,size,path,note}` | 创建 200，文件落在临时备份目录，清单 +1，`pinganzhi` 只读展示平安志 `backup_record` |
| 10 | `GET /workbench/acquisitions` | `ok, source, items[{id,time,source,format,result,snapshot_id,completeness,parser,profile_id}], total` | 1 条采集记录（`format=mumu-snapshot-v1`） |
| 11 | `GET /workbench/meta` | `ok, data_source, pinganzhi{available,path}, counts{souls,shikigami,todos,oas_souls,oas_owned}, last_sync` | `data_source=paz-db`，`counts={3547,3201,3547,3547,3201}`，`last_sync=2026-11-19T17:07:39.446Z` |
| 附 | `GET /workbench/panel/history`、`GET /workbench/speed/history` | `ok, source, items[], total, note` | 本机 `pve_panel_cache` / `speed_preview_cache` 表存在但 0 行 → `items=[], total=0` + 中文 note，不报错 |
| 附 | `GET /workbench/souls`（t4 增量） | `standard,count,by_decision,results[],action_batch,total,offset,limit,returned`；行含 `soul_id,set_name,slot,quality,level`，旧字段 `matched,score,decision,rule_id,idx` 保留 | `total=3547 returned=20 count=3547`，`by_decision={discard:2316,keep:265,observe:966}` |

### 无效参数 / 错误码（全部 4xx，无 500）

| 请求 | 期望 | 实测 |
|---|---|---|
| `GET /souls/__wb_verify_missing__` | 404 | 404 `找不到御魂` |
| `GET /souls?offset=-1` / `?limit=0` / `?slot=9` | 400 | 400 |
| `GET /todos?status=bogus` / `?limit=0` | 400 | 400 |
| `POST /todos/decision`（空 body / 非法 action / 不存在 todo_id） | 400 / 400 / 404 | 400 / 400 / 404 |
| `GET /todos/history?limit=0` | 400 | 400 |
| `POST /standards/activate`（空 body / 未知版本） | 400 / 404 | 400 / 404 |
| `GET /standards/activate` | 405 | 405 |
| `GET /acquisitions?limit=0` | 400 | 400 |
| `GET /panel/history?limit=501`、`GET /speed/history?limit=501` | 400 | 400 |
| `GET /workbench/__wb_verify_unknown__` | 404 | 404 |

### 平安志不可用时的优雅降级

模拟方式：`WORKBENCH_PAZ_DB=<不存在的库>`（与实现方 `--no-paz` 等价；该环境变量是 `paz_extra.db_path()` 的官方覆盖入口，仅测试用），请求打到同一临时服务。

| 接口 | 降级表现 | 结果 |
|---|---|---|
| `/meta` | 200，`pinganzhi.available=false` + 中文 `reason`，`data_source=oas-store`，`counts` 仍给 OAS 侧数字 | PASS |
| `/snapshots`、`/acquisitions`、`/todos`、`/panel/history` | 200 + `ok:false` + `reason` + 空结构 / counts | PASS |
| `/standards/versions` | 回退本模块标准包：`source=local-standard`，`items` 非空 + `reason` | PASS |
| `/backups` | 200，OAS 备份仍可用；`pinganzhi.ok=false` + `reason` | PASS |
| `/souls/<不存在>`、`/roster/<不存在>` | 200 + `ok:false` + `reason`（不是 500） | PASS |
| `POST /todos/decision`（平安志不可用） | 200，仍写 OAS store 并回 `reason` 说明平安志不可用 | PASS |

## 三、老路由回归

- 补全前 router（`git show 46829740:module/server/workbench_router.py`）在内存中 exec 后单独起 `127.0.0.1:23335`，与新服务逐条对比。
- 补全前 **33** 条 `/workbench` 路由全部仍然注册；新增 **14** 条，合计 47 条（另加双方各自的验收 marker 路由）。
- 12 条指定老路由逐条实测（新/旧同状态 200 + 新响应 **包含** 旧响应全部顶层字段；列表型响应对首行字段做同样包含检查）：
  `overview / souls / roster / shards / panel / headtail / activities / tasks / monitor / domains / admission / instances`
  其中 `roster / shards / admission` 的 `rows` 行字段也逐字段比对，均无删改。
- 函数级差异（`git show 46829740:…` vs 工作树）：old_funcs=42 / new_funcs=61，**removed=[]**；`changed=['souls','decision']`。
  - `souls`：t4 的纯增量（新增 `offset/limit/q/set/slot` 参数与 `total/offset/limit/returned` 及行内 `soul_id/set_name/slot/quality/level`），旧字段全部保留；非法参数从「500/忽略」变为 400，属修复。
  - `decision`：逐行对比函数体 **完全一致**（675 B == 675 B）；此前差异只是其后追加了新代码块导致边界误判。
- router 文件 diff：`+372 / -3`，删除的 3 行均为 import/签名行的替换（`Body` → `Body, Query` 与 `souls` 签名扩展），无老路由逻辑删除。

## 四、UI 信息架构与真实截图

浏览器：`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`，`--headless=new` + DevTools 协议；**等真实数据渲染完**（`#soulsBox` 表格出现、stamp 不再是「加载中」）后才截图。截图对象即上文工作树 `index.html`。

真实 DOM 断言（1440×2400，桌面）：

- 导航分组顺序：`['总览','御魂','式神','养成','数据']` —— PASS。
- 13 个老 hash 锚全部存在且有高度：`ov, mon, soul-sec, panel-sec, ht-sec, act-sec, roster-sec, asset-sec, shard-sec, guild-sec, profiles-sec, tasks-sec, speed-sec`；页面内 21 条导航链接全部指向存在的 id；13 个锚点逐个改 hash 实测可滚动到顶部附近（页面末尾 2 个滚到底部即视为可达）—— PASS。
- 三态：`.state.loading / .state.empty / .state.error` 三套模板在 DOM 中存在，中文文案为「加载中… / 还没有… / …失败」—— PASS。
- 运行时资源全部同源：`performance.getEntriesByType('resource')` 无任何非 `location.origin` 的 URL（无外网 CDN 请求，静态扫描 `https?://` 也是 0 条）—— PASS。
- 页面无 JS 运行时错误（`error`/`unhandledrejection` 采集为空）—— PASS。
- 顶部状态条 5 项常驻且有值：`档案 平安志档案 / 御魂 3547 / 式神 266 · 281 / 待办 3547 / 数据源 paz-db`，更新时间 `23:03:53` —— PASS。
- 关键面板真实渲染出行：御魂 50 行、待办 200 行、快照 1、标准版本 4、备份 3、采集 1、任务 14、元信息 189 字符 —— PASS。
- 720×1600 移动端：真实渲染出行；`scrollWidth(720) <= innerWidth(720)` 无横向溢出；分组顺序不变；无 JS 错误 —— PASS。

截图（真实渲染产物，未做任何后处理）：

| 文件 | 尺寸 | 字节 |
|---|---|---|
| `log/workbench/ui/verify_desktop.png` | 1440×2400 | 579,076 |
| `log/workbench/ui/verify_desktop_full.png`（全页） | 1440×9295 | 1,200,804 |
| `log/workbench/ui/verify_mobile.png` | 720×1600 | 257,046 |
| `log/workbench/ui/verify_mobile_full.png`（全页） | 720×9832 | 921,875 |

人工目视核对（本人查看原图）：左栏五组标题顺序正确；顶部状态条与「刷新全部」；概览卡片、监控、御魂分析（筛选/分页/行内「详情」）、待办处置（完成/忽略/御魂 + 处置历史）、式神/养成/数据各段都有内容或明确空态；中文标签正常（属性已汉化为「攻击加成/暴击/暴击伤害/效果命中」等），无乱码、无空白面板、无横向滚动条。

与 `git status` 的对照：本轮截图对应的工作树 `index.html` 相对 HEAD 有 **41 行未提交改动**（`+30/-11`，属 wb-ui 的 t5 收尾：720px 待办卡片式、属性键归一化），工作树与 HEAD 两个版本静态扫描均 **0 条外网 URL、0 处藏宝阁**。

## 五、写操作边界

对比对象：`%APPDATA%\com.yys.analysis` 整棵目录（49 个文件，含 `current-analysis.sqlite3` 471,064,576 B、`-wal`、`-shm` 与 `monitor-history.sqlite3` 等）。

- 目录无新增/删除；**除 `current-analysis.sqlite3-shm` 外的所有数据文件大小 + mtime 完全不变** —— PASS。
- 平安志主库：`POST /todos/decision` 与 `POST /backups/create` 前后 `size`、`mtime` 一致（size=471,064,576；mtime=1795112616.789）—— PASS。
- 隔离生效：本脚本所有写入落在 `%TEMP%` 的 OAS store 副本；仓库 `config/workbench/{todo_decisions.json,todo_decisions.log,decisions.json,standards.json}` 前后指纹一致 —— PASS。
- `-shm` 说明：SQLite WAL 模式下，**只读连接的 SELECT 也会更新 `-shm` 里的 read mark**（用最小实验复现：单独一条 `select count(*)` 前后 `-shm` mtime 变化、主库与 `-wal` 不变）。这是 WAL 索引的只读副作用，不是写库；主库 `mtime/size` 是判断是否被写的可靠依据，本轮以主库 + `-wal` 为准，`-shm` 仅作说明记录。
- 代码层：`tasks/Workbench/ingest/paz_extra.py` 全文无 `INSERT/UPDATE/DELETE/REPLACE/DROP/ATTACH`；`_ro()` 使用 `mode=ro` + `query_only=1`；`store.py` 只写 JSON（无 sqlite 写）；router 内无「平安志路径 + 写语句」组合 —— PASS。

## 六、老路由现场复跑

同一轮验收内，通过临时服务实际请求（非直调函数）：`overview / souls / roster / shards / panel / headtail / activities / tasks / monitor / domains / admission / instances` 全部 200，且与补全前 router 的响应结构一致（新为旧超集）。`/workbench/souls` 默认响应遵守 t4 分页契约（200 行、`total=3547`、`by_decision` 口径不变）。

## 七、藏宝阁

- 路由：`@app.get/post` 字符串里无 `cbg/cangbao/藏宝阁` —— 0。
- 菜单/导航：`index.html` 所有 `<a>` 行无相关字样 —— 0。
- 代码字段标识符：扫描 `module/server/workbench_router.py`、`index.html`、`tasks/Workbench/**/*.py`（排除 selftest 的负向断言）—— 0。
- 备注：`features.py` / `selftest_features.py` 里的「藏宝阁已剔除 / 除外」是说明性注释，`selftest_web.py` 的 `web.new.no_cbg` 是「不得出现 cbg」的负向断言，均**不是残留**，已在检查中排除。

## 八、发现与风险

| 编号 | 等级 | 内容 | 建议 |
|---|---|---|---|
| F1 | 中（交付前必做） | 22267 上正在跑的 OAS 进程仍是补全前启动的，**新路由要等下次 OAS 服务启动才生效**；本轮所有接口验证走的是独立临时服务（23334/23335），没有重启 22267。 | 交付/演示前重启一次 OAS 服务，再在 22267 上抽查 `/workbench/meta`、`/workbench/todos`、`/workbench/souls/{id}`。 |
| F2 | 低 | `index.html` 工作树有 41 行未提交改动（wb-ui t5 收尾），本次截图对应工作树版本；若之后又改 UI，本轮截图/断言即过期。 | 把工作树改动随下次 autosave/提交固化；如再改 UI，重跑 `dev_tools/verify_workbench_ui.py`。 |
| F3 | 低（信息） | WAL 只读读会更新 `current-analysis.sqlite3-shm` 的 mtime（主库 / `-wal` 不变）。用「整目录 mtime 全等」做监控会误报。 | 只对主库与 `-wal` 做写监控；把 `-shm` 排除。 |
| F4 | 低（信息） | 本机 `pve_panel_cache` / `speed_preview_cache` 为 0 行，两个历史面板只能验证空态，验证不到有数据的渲染分支。 | 有历史数据后再补一次带数据的目视核对（接口契约已覆盖）。 |
| F5 | 低（信息） | 验收脚本/截图落在 `dev_tools/` 与 `log/workbench/ui/`；`dev_tools/wb_api_selftest.py`、`wb_shot.py` 属草稿区工具，但核心断言已并入 `tasks/Workbench/selftest_web.py`。 | 可接受；如要求「成果只留在模块内」，可把独立验收命令写进 `tasks/Workbench/deliverables/交付清单.md`。 |

无 FAIL 项，无阻塞项。

## 九、复现命令

完整独立验收（约 2–3 分钟，含 Edge 截图；需要 23334/23335 空闲，脚本会自动挑下一个空闲端口并做 marker 校验）：

```powershell
cd D:\3D\YYS2\OnmyojiAutoScript-easy-install-1
.\toolkit\python.exe dev_tools\verify_workbench_ui.py
# 期望末行：WB VERIFY: PASSED   TOTAL 162  PASS 162  FAIL 0，退出码 0
```

只跑接口（不起浏览器）：`.\toolkit\python.exe dev_tools\verify_workbench_ui.py --no-shots`
指定端口/浏览器：`--port 23340 --old-port 23341 --edge "<msedge.exe>"`
临时目录会在结束时打印（`%TEMP%\wbverify_*`），可手动删除。

单项复现：

```powershell
# 编译 + 六份自检
.\toolkit\python.exe -m compileall -q tasks module script.py
.\toolkit\python.exe tasks\Workbench\selftest.py
.\toolkit\python.exe tasks\Workbench\selftest_web.py
.\toolkit\python.exe tasks\Workbench\selftest_features.py
.\toolkit\python.exe tasks\Workbench\selftest_domains.py
.\toolkit\python.exe tasks\Workbench\selftest_io.py
.\toolkit\python.exe tasks\Workbench\selftest_paz.py

# 藏宝阁残留（期望 0 行）
Select-String -Path module\server\workbench_router.py,module\server\web\workbench\index.html -Pattern 'cbg|cangbao|藏宝阁'

# 平安志读取层无写 SQL（期望 0 行）
Select-String -Path tasks\Workbench\ingest\paz_extra.py -Pattern 'INSERT|UPDATE|DELETE|REPLACE|DROP TABLE|ATTACH'

# 老路由函数级差异（期望 removed=[]、changed=['souls','decision']，decision 函数体一致）
# 见本报告第三节的命令片段，或直接重跑 dev_tools\verify_workbench_ui.py
```

本次 0 个 FAIL，故无「FAIL 项复现命令」；若以后出现 FAIL，脚本会在汇总处逐条打印「失败项复现命令」对应的检查名，并对每项给出实际 status/响应片段。

## 十、限制与声明

- 未重启 22267 服务、未起停模拟器/游戏、未执行 adb；本轮没有修改任何业务代码。
- 本轮新增/改动文件仅：`dev_tools/verify_workbench_ui.py`、`log/workbench/ui/verify_*.png`、本报告（`docs/workbench_completion_verification.md`）。工作树里 `index.html` 的未提交改动来自 wb-ui 的 t5，不是本轮改的。
- 所有数字均来自本轮真实命令输出；截图来自真实 Edge 渲染，非合成。
