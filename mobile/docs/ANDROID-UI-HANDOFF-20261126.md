# OASX 安卓端 UI 交接

2026 年 11 月 26 日，Gemini → Codex。当前工作停在新版任务图标完成后的布局审核。后续调整以这个 Flutter 工作区和可交互预览为依据。

## 接手入口

| 用途 | 位置 |
| --- | --- |
| 实际修改的 Flutter 工程 | `E:\OASX-Android\work\oasx-deepseek-20261119\full-handoff-20261120-evening\workspace` |
| 在电脑上点开四页和任务配置 | [OASX 手机端交互预览](<D:/3D/YYS2/OASX-Mobile-UI真机预览.html>) |
| 查看全部任务图标及对应任务名 | [63 项任务图标总览 v2](<C:/Users/XIAXUDONG/.gemini/antigravity/brain/a6dc05ab-00ad-486c-8753-79f40184cbb1/all_63_task_icons_v2.png>) |
| 上一版安卓安装包 | [0.3.4+80，ARM64](<D:/3D/YYS2/OASX-Mobile-0.3.4-b80-arm64-v8a.apk>) |

原始 Gemini 会话编号为 `a6dc05ab-00ad-486c-8753-79f40184cbb1`。本机记录在 `%USERPROFILE%\.gemini\antigravity\brain\a6dc05ab-00ad-486c-8753-79f40184cbb1\.system_generated\logs\transcript_full.jsonl`。工作区分支为 `master`。本轮布局、组件和图片包含已修改与未跟踪的文件，当前工作目录保留了这些改动。

## 已确认的界面方向

| 位置 | 用户已提出的要求和当前实现 | 源码入口 |
| --- | --- | --- |
| 导航与外观 | 底栏依次为庭院、绘卷、监控、我的。五款庭院背景可切换或随机使用。底栏采用人物图，「我的」用晴明。 | [应用外壳](../lib/ui/shell.dart)、[皮肤配置](../lib/ui/skin_store.dart) |
| 庭院 | 首页显示任务执行顺序，支持展开和置顶。资产卡展示勾玉、金币、体力、神秘符咒、皮肤券、魂玉。神秘符咒已按上次反馈换成蓝票。 | [庭院](../lib/ui/pages/home.dart)、[资产卡](../lib/ui/pages/assets_card.dart) |
| 绘卷 | 列表支持搜索、启停任务、打开参数。庭院队列和绘卷列表共用任务图标映射。图案按玩法区分，用户接受 Q 版素材。 | [任务列表](../lib/ui/pages/scripts.dart)、[任务参数](../lib/ui/pages/task_args.dart)、[图标映射](../lib/ui/task_names.dart) |
| 监控 | 画面与运行日志位于同一页。预览画布和全屏画布都关闭状态文字叠层。「最近画面」留帧区已移除，画布下有暂停、刷新、全屏接管。 | [监控页](../lib/ui/pages/monitor.dart)、[日志面板](../lib/ui/pages/monitor_log_panel.dart) |
| 我的 | 包含外观、账号、护航和在线更新设置。更新源可选 GitHub 或 GitCode。 | [我的](../lib/ui/pages/me.dart)、[在线更新](../lib/ui/pages/ota_card.dart) |

## 本次接手复核

- 安卓端 63 个包含 `script_task.py` 的任务目录、Dart 专属图标表和 [任务图片目录](../assets/onmyoji/tasks/) 的标识完全一致。63 张 PNG 均为 128 × 128，逐张解码后的像素内容互不相同。Flutter 已 [声明该资源目录](../pubspec.yaml)。其中 20 项采用最后一轮 Q 版素材，包括石距、道馆和花车巡游。
- 两份交互预览内嵌的图片表一致，都含 63 个任务图标。浏览器演示列表提供 24 个示例任务，每项都引用同名图标。示例配置仅用于预览交互。
- 在隔离的 Edge 实例中触发了四个页签、庭院资产详情及「年兽」参数页。四页均有内容，详情显示六项资产，参数页可打开。此轮检查屏蔽了外部网络请求，验证范围是交互逻辑。预览的部分排版仍依赖在线 Tailwind 脚本。
- 在实际 Flutter 工程运行 `flutter test --no-pub`，17 项全部通过。本轮验证覆盖源码、资源和浏览器预览。新布局在手机上的尺寸、图标辨识度和触控表现待真机验收。

## 版本与下一步

Gemini 最后记录的双源发布版本为 **0.3.4+80**。本机同版本 APK 中，新任务图标和六张新命名的货币图片条目均为零。工程 [pubspec.yaml](../pubspec.yaml) 保留 `0.3.4+36`，此前发版脚本使用 `--build-number` 指定构建号。

可交互预览和图标总览是这一轮的审稿入口。后续按用户指定的页面、控件和顺序同步调整 Flutter 和预览，再检查窄屏与真机显示。**0.3.4+81** 是原会话讨论的候选构建号，等待布局定稿。
