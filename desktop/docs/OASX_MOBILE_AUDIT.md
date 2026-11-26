# OASX Mobile 审计报告（阶段一）

> 生成时间：2026-11-01 21:30
> 作者：DeepSeek（执行工程师）
> 结论先行：**现有机遇很大，但工具链（Flutter/JDK/Android SDK）本机缺失，正在安装；真机（最新 Android）尚未接入，最终真机验收标记为「未完成」。**

## 一、已发现的现有资产

| 资产 | 位置 | 状态 |
| --- | --- | --- |
| OAS 整合修改版（本仓库） | `D:\3D\YYS2\OnmyojiAutoScript-easy-install-1`（分支 `port/xylolit-vendor`） | ✅ 可用，任务/识别/调度/百鬼棋局/悬赏封印新 UI 适配均在跑 |
| OASX Flutter 客户端 | `D:\3D\YYS2\_oasx_src\OASX-0.3.2` | ✅ 源码完整（73 个 dart 文件，GetX + dio + websocket），已有 `android/` 工程（`applicationId com.oas.oasx`，gradle 8.13 wrapper） |
| MuMu 模拟器 | `E:\MuMuPlayer`（实例 1，adb `127.0.0.1:16416`） | ⚠️ 目前未启动（`is_android_started=false`），可按需拉起 |
| OASX 与 OAS 的通信方式 | `lib/api/api_client.dart` + `lib/service/websocket_service.dart` | HTTP + `ws://<address>/ws/...` → **移动端只要把 `address` 指向本机 127.0.0.1 即可复用全部界面** |

**关键结论**：OASX 是**瘦客户端**，本身不做识别/点击。要「手机上独立运行」，必须让 **OAS 的 Python 核心跑在手机里**，OASX 仍作为前端连 `127.0.0.1`。这决定了整体架构。

## 二、工具链现状（阻塞项）

| 项 | 需求 | 现状 | 处理 |
| --- | --- | --- | --- |
| Flutter SDK | 3.24.x（pubspec 要求 Dart `>=3.5.0`） | ❌ 未安装 | 正在下载 `flutter_windows_3.24.5-stable.zip` → `E:\dev\flutter` |
| JDK | 17（AGP 8.x 要求） | ❌ 未安装 | 正在 `winget install Microsoft.OpenJDK.17` |
| Android SDK | cmdline-tools + platform-tools + `platforms;android-35` + `build-tools;35.0.0` | ❌ 未安装 | 正在下载 `commandlinetools-win` → `E:\dev\android-sdk` |
| adb | 测试用 | ✅ 仓库自带 `toolkit\Lib\site-packages\adbutils\binaries\adb.exe`，另有 BAAH/MAA 自带 adb | 直接用 |
| 磁盘 | 需 ~8GB | C 11.6G / D 13.2G / E 186.8G | 工具链一律装 **E:** |
| 包管理 | 安装用 | `winget` ✅、git ✅、python 3.12 ✅ | 使用 winget + 官方 zip |

## 三、目标架构（决定后续所有实现）

```
┌────────────────────────── Android APK ──────────────────────────┐
│ Flutter (OASX UI, 复用 0.3.2 全部界面)                            │
│   └─ dio/websocket → 127.0.0.1:<port>                            │
│                                                                   │
│ Kotlin 原生层（AndroidX + 前台服务）                              │
│   ├─ MediaProjection 截图（VirtualDisplay + ImageReader）          │
│   ├─ AccessibilityService 手势（点击/滑动/长按）                   │
│   ├─ 前台服务（foregroundServiceType=mediaProjection|dataSync）    │
│   ├─ 通知/电池优化引导、权限流程                                   │
│   └─ OpenCV(Java AAR) 模板匹配 + onnxruntime-android 做 OCR        │
│                                                                   │
│ Chaquopy 嵌入 CPython 3.12                                        │
│   ├─ OAS 核心：tasks/**、module/**、config/**（尽量原样）           │
│   ├─ 平台适配层：device 改为调用 Kotlin（截图/点击/OCR/匹配）       │
│   └─ OAS WebUI/WS 服务：监听 127.0.0.1，供 Flutter 连              │
└───────────────────────────────────────────────────────────────────┘
```

### 为什么这样选
1. **复用面最大**：OAS 的 60+ 任务全在 Python，重写成 Kotlin 不现实；Chaquopy 能把 `tasks/`、`module/base`、调度器原样跑。
2. **OASX 界面零改动复用**：它只是 HTTP/WS 客户端，改一个地址常量即可。
3. **免 Root 的唯一合法组合**就是 MediaProjection + AccessibilityService（无 root、无 shizuku 依赖）。

### 已知硬约束（必须提前解决）
| 约束 | 影响 | 方案 |
| --- | --- | --- |
| Chaquopy 无 `opencv-python` wheel | `cv2` 无法 import | 写 `cv2` 兼容 shim：`matchTemplate/resize/cvtColor/threshold` 等转发 Kotlin OpenCV；其余用 numpy 实现 |
| Chaquopy 无 `onnxruntime` | OAS 的 ppocr 无法直接跑 | Kotlin 侧用 `onnxruntime-android` 加载同一份 onnx 模型，Python 通过桥调用 |
| `PIL` 可用但 `cv2` 语义不同 | 图像格式转换 | 统一走 RGB numpy + Kotlin 位图互转（`Bitmap` ↔ `byte[]`） |
| MediaProjection 每次会话需用户授权 | 长时间运行可能被系统回收 | 前台服务保持会话；`onStop` 自动重建并向 UI 提示「需重新授权」 |
| Android 14/15 前台服务类型与后台启动限制 | 服务起不来 | `foregroundServiceType="mediaProjection"`，启动必须由 Activity 前台触发 |
| OAS 依赖 zerorpc（Python<->GUI） | 手机上不需要 | WebUI 用 aiohttp/fastapi 起 HTTP+WS，OASX 直接连；断掉 zerorpc 依赖 |

## 四、OAS 依赖清单（需在 Chaquopy 侧验证）

先做**依赖可安装性探测**（后续阶段二立马跑）：`numpy / Pillow / aiohttp / msgpack / cached-property / uiautomator2? / pywin32(否) / adbutils(否)`。
> `adbutils`、`uiautomator2`、`pywin32`、`pygetwindow` 这些 Windows/ADB 专属依赖必须**在移动端替换掉**，这正是「平台适配层」要做的事。

## 五、分阶段计划与验收口径

| 阶段 | 内容 | 完成标志 | 当前 |
| --- | --- | --- | --- |
| 一 | 审计 | 本文件 + 工具链可用 | 🔄 工具链安装中 |
| 二 | Android 基础能力 | 一个最小 APK 在 MuMu 上：截图 → 匹配 → 点击 → 滑动 全链路通 | ⬜ |
| 三 | OAS 核心迁移 | 手机上能跑 `Restart`（进游戏）+ 至少 1 个真实任务 | ⬜ |
| 四 | OASX 整合 | OASX 界面连 127.0.0.1，能看到任务列表/日志/启动停止 | ⬜ |
| 五 | 自定义功能 | 百鬼棋局、自动阵容、御魂配置在手机端可用 | ⬜ |
| 六 | MuMu 自动测试 | 一键 `build → install → test → log` 脚本 | ⬜ |
| 七 | 最新 Android 真机 | 真机安装+核心功能验证 | ❌ 未接入真机 |
| 八 | 交付 | APK + 源码 + 构建脚本 + 测试报告 | ⬜ |

## 六、风险与如实声明

1. **真机验收未完成**：本机只有 MuMu（Android 12 类），最新正式版 Android 真机尚未接入，
   因此「最新 Android 兼容性」只能标注为**未验证**，不会用模拟器结果冒充。
2. **性能不确定**：MediaProjection 截图在 1080p 下约 50~150ms/帧，比 nemu_ipc(30~80ms) 慢；
   OAS 的部分节奏（百鬼棋局购买/上阵窗口）需要重新调参，这在阶段五要实测。
3. **Chaquopy 版本与 Python 3.12**：需先验证 Chaquopy 是否支持 3.12；否则把 OAS 侧降到 3.11/3.10
   可接受的语法范围（纯语法层面，风险低）。
4. **不承诺「所有安卓设备」**：只承诺「在实测过的设备/系统版本上通过」。

## 七、下一步（本会话继续执行）

1. 工具链装完 → `flutter --version`、`sdkmanager` 装 `platform-tools/platforms;android-35/build-tools;35.0.0`；
2. 用 OASX 现有 `android/` 先做一次**基线构建**（`flutter build apk --debug`），证明链路通；
3. 新建 `android_host/`（Kotlin + Chaquopy 外壳），先跑通「Python 打印 + 调用 Kotlin 截图」；
4. 再按阶段二~六推进。
