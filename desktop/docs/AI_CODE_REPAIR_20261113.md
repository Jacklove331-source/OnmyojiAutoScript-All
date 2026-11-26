# OAS 自动代码维修

已启用 AI 且处于 auto 模式的账号会自动启动代码维修服务，沿用原有 DeepSeek 凭证及每日调用/费用上限。原有截图自愈、模板复用与累计学习继续工作。

## 故障处理流程

1. ScriptError、普通代码异常及任务导入/初始化异常留下任务名、脱敏错误、真实源码路径、堆栈位置与源码版本，进入持久维修队列。
2. 独立维修进程读取相关日志及源码，在不包含账号配置、凭证和游戏设备的临时副本中工作。AI 可以实际调用源码读取、搜索、精确修改、编写回归和运行测试工具。
3. 同一份行为回归必须在原版出现断言失败、修复版全部通过，并执行到修改行；没有测试、测试自身出错、只检查源码字符串、正常输入被破坏，均不能应用。
4. 应用前核对源码版本及协作黑板，获取现有 executor 锁。所有运行账号必须在空闲窗口，通过维护握手确认暂停在输入动作之前；正在战斗或心跳过期时继续等待。
5. 保存原始字节、补丁、测试和部署日志，按文件原子替换，仅重新加载原先运行的账号。原来未启动的账号保持未启动。新进程必须提交新实例心跳才算重载通过。
6. 页面恢复和工作进程启动都只是中间结果，原任务正常完成后才标记代码修复成功。原任务仍失败、重载失败或观察期内未确认完成，会安排安全回滚。回滚遇到人工后续修改时保留修改并标记需要处理。
7. 同一故障/源码版本最多维修两轮，失败后退避并保存原因；失败的相同补丁不会再次应用。程序重启后继续使用这些记录。源码已改变的历史故障作废，避免改坏已经修好的版本。

## 接入范围

- 自动修改范围是实际堆栈涉及的业务 Python 文件及该任务目录，默认最多 3 文件、180 行改动。
- 维修系统自身、服务器控制、账号配置、密钥、dual_agent 协作代码及其他人进行中的认领均受保护。模型没有任意 shell 或直接操作真实设备的工具；补丁不能新增网络、进程、写文件或游戏输入能力。
- 行为回归采用真实函数 AST 与模拟依赖，在单独 Python 进程内运行，限制导入、禁网/进程和文件写入，不导入真实游戏任务。它是受控离线回放器，并非通用操作系统安全沙箱。
- 无法建立有效回归的复杂问题会保留证据，达到轮数上限后停止该维修请求。模板图片更新仍使用原有模板库和验证流程。

## 空正文兼容修复

DeepSeek 界面诊断显式使用 `thinking: disabled`，提示词提供完整最小 JSON 示例。遇到空白正文或截断结果时，最多再进行一次受原预算限制的纠正请求，取消接口 JSON 模式但保留本地结构及动作校验。思考内容不会被当成答案或执行计划。

官方文档说明：[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[JSON 空白输出](https://api-docs.deepseek.com/guides/json_mode/)、[工具调用](https://api-docs.deepseek.com/guides/tool_calls/)。历史空正文存在这些可能原因；一次对照请求不能确定过去每一次的原因。

## 查看与控制

OASX 原有 AI 修复队列会显示代码维修进度，原任务确认完成后标记解决。更完整的状态及证据可通过命令查看：

```powershell
.\toolkit\python.exe -X utf8 -B dev_tools\ai_recovery_cli.py code-repair status oas1
.\toolkit\python.exe -X utf8 -B dev_tools\ai_recovery_cli.py code-repair doctor oas1
.\toolkit\python.exe -X utf8 -B dev_tools\ai_recovery_cli.py code-repair run-once oas1
.\toolkit\python.exe -X utf8 -B dev_tools\ai_recovery_cli.py code-repair rollback oas1 <维修编号>
```

`doctor` 使用纯黑自检图验证真实接口，计入现有调用预算，不执行游戏动作。`run-once` 处理一轮，仍遵守测试、锁、认领和空闲维护条件。

数据位于 `log/ai_recovery/code_repair/`：`jobs.sqlite3` 保存任务及审计事件；`work/<维修编号>/` 保存源码副本、`change.patch`、`regression.py`、`test-report.json`、部署日志和原始备份；`runtime/` 保存账号与维修进程心跳。

可在原 `config/ai_recovery.yaml` 加入 `code_repair` 分组覆盖默认值。`enabled: false` 关闭代码维修；`auto_apply: false` 保留定位、生成和测试，只准备补丁。观察模式不自动维修。
