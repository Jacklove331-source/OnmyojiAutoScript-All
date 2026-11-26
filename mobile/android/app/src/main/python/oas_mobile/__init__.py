"""OAS Mobile 的 Python 侧入口包。

本包运行在 Chaquopy（Android 内嵌 CPython）里，职责：
1. 用 `java` 模块调用 Kotlin 执行层（截图/点击/滑动），拿到 PNG 字节；
2. 起一个本地 HTTP/WS 服务（aiohttp），让 Flutter(OASX) 界面连 `127.0.0.1:<port>`；
3. 后续阶段在这里逐步挂上 OAS 的任务引擎（tasks/**）。

设计约束：
- 不依赖 adb / uiautomator2 / pywin32 等桌面端东西；
- 所有 Android 能力都通过 `bridge` 走 Kotlin，方便以后换实现（MediaProjection/无障碍）。
"""

__version__ = "0.3.4"
