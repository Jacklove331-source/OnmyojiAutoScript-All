# AI 自愈：累计学习与重复错误退避

已启用 AI 自愈的账号会自动使用本功能，账号配置和 API 密钥沿用原设置。

- 按任务、异常类型、页面、错误原因和代码版本区分故障，历史记录保存在本机，重启后继续使用。
- 同一成功动作方案累计到同一条知识记录。复用前仍校验账号、游戏版本、分辨率、画面和风险限制。
- 页面恢复先标记为临时成功；原任务正常结束后，才增加“确认解决”次数。原任务确认完成前，同一错误再次出现或原任务仍失败，会给该方案记录负反馈；延期和重启不会丢掉这条待确认记录。
- 同一方案连续失败 2 次后停用。历史累计成功率很高也不能绕过此限制。AI 重新给出同一失败点击、仅微调坐标，也会被拦截。
- 同一错误连续失败 3 次后退避，默认从 10 分钟逐次翻倍，最多 6 小时。其他正常任务继续。
- 模型接口连续失败 3 次，默认暂停模型请求 10 分钟，跨任务与工作进程重启有效；已有本地方案仍可使用。
- ScriptError 会进入受控自愈。无法处理的代码异常进入修复记录并延期当前任务，防止整个 OAS 因同一任务反复退出。
- 工作进程在空闲时重载，直接使用原生模拟器关闭接口；避免为了“关闭”而先初始化设备、提前启动模拟器，仍按原定时间预热。
- 需要人工接管的异常仍停机。新的学习机制不绕过购买、充值、删除等动作限制，不自动执行任意代码或修改业务源码。

配置字段位于原 `config/ai_recovery.yaml` 的可选 `learning` 分组；旧配置未填写时按以上默认值生效。总调用次数与费用仍受原预算限制。

查看本机学习情况：

```powershell
.\toolkit\python.exe -X utf8 -B dev_tools\ai_recovery_cli.py learning oas1
.\toolkit\python.exe -X utf8 -B dev_tools\ai_recovery_cli.py health oas1
```

数据位置：`log/ai_recovery/learning/<账号哈希>.sqlite3`。原截图证据、知识库和修复队列继续保留。

离线验证：

```powershell
.\toolkit\python.exe -X utf8 -B -m module.ai_recovery.learning_test
.\toolkit\python.exe -X utf8 -B -m module.ai_recovery.selftest
.\toolkit\python.exe -X utf8 -B -m module.ai_recovery.hardening_test
```

这些验证使用临时目录与替身设备/模型，不操作真实游戏，也不调用收费模型。
