# OASX Mobile 进度记录（阶段一/二）

> 更新时间：2026-11-01 22:55
> 状态：**部分完成**——工具链、基线 APK、APK 内 Python、无障碍手势全部打通；
> **截图在 MuMu 上返回全黑**（模拟器限制待绕过），真机验收未开始。

## 一、已完成并实测

| 项 | 证据 |
| --- | --- |
| 工具链 | Flutter 3.24.5（`D:\3D\YYS2\_tools\flutter`）；OpenJDK 17.0.2（`E:\OASX-Android\jdk17`，华为镜像 187MB/约 1 分钟）；Android SDK 35（`E:\OASX-Android\sdk`，cmdline-tools/platform-tools/platforms;android-35/build-tools;35.0.0 来自腾讯镜像，`flutter doctor` 识别） |
| 构建加速 | Gradle wrapper 指向腾讯镜像（`gradle-8.13-all.zip` 221MB）；Maven 用阿里云镜像；pub 用 `pub.flutter-io.cn` |
| 基线 APK | `flutter build apk --debug` → 95.4MB，`adb install -r` Success，MuMu 上 `mCurrentFocus=com.oas.oasx/.MainActivity` |
| 嵌入 CPython | Chaquopy 16.1.0 + Python 3.10（buildPython 指向仓库 `toolkit\python.exe`），APK 87MB |
| APK 内 Python 服务 | logcat：`python backend: started(stdlib) on 127.0.0.1:22267`；宿主 `adb forward` 后 `GET /health` 返回 `{"ok":true,...,"bridge_available":true}` |
| 免 Root 手势 | `POST /tap`、`POST /swipe` 均返回 `{"ok":true}`（AccessibilityService.dispatchGesture） |
| 无障碍截图授权 | 加了 `android:canTakeScreenshot="true"` 后系统不再报 `SecurityException: Services don't have the capability...`，返回 **1280x720 PNG** |

校验脚本：`dev_tools/mobile/verify_bridge.py` → 当前 **6/6 PASS**（但截图内容全黑，见下）。

## 二、当前阻塞 / 未完成

1. **截图全黑**：`AccessibilityService.takeScreenshot` 在 MuMu（Android 12 类）上返回合法尺寸但像素全黑。
   这是模拟器渲染路径的已知坑，不代表真机不行，但**不能当通过**。
   → 下一步：改用 **MediaProjection**（VirtualDisplay + ImageReader）做主路径，无障碍截图作 fallback；
   并在一台真机上复测（真机还没接入）。
2. **pip 依赖未打进包**：chaquo.com wheel 源在本机 ReadTimeout（aiohttp/numpy/Pillow 均未装），
   当前 Python 服务走 stdlib 实现兜底。→ 下一步：加大 `--timeout 1200`、后台长时间构建；
   或在能直连的时段跑；opencv-python 在 chaquo 仓库里确认存在（YES）。
3. **OAS 核心尚未嵌入**：`tasks/**`、`module/**` 还没搬进 `src/main/python`（阶段三）。
4. **OASX 界面未接到本机服务**：Flutter 侧 `ApiClient.address` 仍是手填后端地址，
   需要改成默认 `127.0.0.1:22267` + 本地服务自动发现（阶段四）。
5. **真机未接触**：最新 Android（目前 16/API 36）无设备可测，验收表中标「未完成」。

## 三、关键文件位置

- 工程根：`D:\3D\YYS2\oasx_mobile`（从 `_oasx_src\OASX-0.3.2` 复制，去掉了 build/桌面平台目录）
- 本仓库镜像（便于版本管理）：`mobile/oasx_mobile/**`（kotlin/python/manifest/gradle 等新增文件）
- 构建日志：`D:\3D\YYS2\_oasx_src\logs\build_*.log`
- 验证脚本：`dev_tools/mobile/verify_bridge.py`；下载器：`dev_tools/mobile/dl.ps1`
- 产物 APK：`D:\3D\YYS2\oasx_mobile\build\app\outputs\flutter-apk\app-debug.apk`

## 四、复现命令（本机）

```powershell
# 环境
$env:JAVA_HOME='E:\OASX-Android\jdk17'
$env:ANDROID_HOME='E:\OASX-Android\sdk'; $env:ANDROID_SDK_ROOT='E:\OASX-Android\sdk'
$env:PUB_HOSTED_URL='https://pub.flutter-io.cn'; $env:FLUTTER_STORAGE_BASE_URL='https://storage.flutter-io.cn'

cd D:\3D\YYS2\oasx_mobile
D:\3D\YYS2\_tools\flutter\bin\flutter.bat build apk --debug

# 安装 + 启用无障碍 + 启动
$adb='E:\OASX-Android\sdk\platform-tools\adb.exe'
& $adb -s 127.0.0.1:16416 install -r build\app\outputs\flutter-apk\app-debug.apk
& $adb -s 127.0.0.1:16416 shell settings put secure enabled_accessibility_services com.oas.oasx/com.oas.oasx.OasAccessibilityService
& $adb -s 127.0.0.1:16416 shell settings put secure accessibility_enabled 1
& $adb -s 127.0.0.1:16416 shell am start -n com.oas.oasx/.MainActivity
& $adb -s 127.0.0.1:16416 forward tcp:22267 tcp:22267

# 验证（宿主侧）
cd D:\3D\YYS2\OnmyojiAutoScript-easy-install-1
.\toolkit\python.exe dev_tools\mobile\verify_bridge.py
```

## 五、下一步（按顺序）

1. MediaProjection 截图实现（Kotlin）+ 用 Python `/screenshot.png` 复验像素非全黑（断言均值 > 5）；
2. 把 aiohttp/numpy/Pillow（后续 opencv-python）打进包（长超时后台构建）；
3. 搬 OAS 核心：`config/**`、`module/base`、`module/config`、`tasks/<少量任务>` 进 `src/main/python/oas_core`，
   写平台适配层（device 用 bridge，去掉 adb/uiautomator2/pywin32）；
4. Flutter 侧默认连 `127.0.0.1:22267`，实现「任务列表/启动停止/日志」最小闭环；
5. MuMu 上跑通 `Restart` 任务 → 再考虑百鬼棋局等自定义功能；
6. 真机（最新 Android）安装与核心功能验证 → 未完成前不宣布通过。


## 六、2026-11-01 23:20：后台运行（不影响前台使用）已实现并实测

**需求**：自动化在后台跑，用户前台正常用手机。

**做法**
| 层 | 内容 |
| --- | --- |
| Kotlin | 新增 OasForegroundService（oregroundServiceType=dataSync + 常驻低打扰通知 + 唤醒锁 1h 上限 + START_STICKY）；Python 与任务循环都在服务里跑，**关掉 Flutter 界面也不停** |
| Kotlin | 无障碍服务记录/查询当前前台包名（事件为空时回退 
ootInActiveWindow），暴露 isTargetForeground()、目标包名可配置 |
| Python | /state 返回 foreground / target_foreground / background_service / paused / can_inject；/tap、/swipe 在「目标 App 不在前台」或「用户暂停」时直接拒绝（不会乱点用户正在用的界面）；新增 /pause、/resume、/target?package= |
| 通知栏 | 「停止」按钮；/pause、/resume 也可从脚本控制 |

**实测（dev_tools/mobile/verify_background.py，MuMu）**
`
TOTAL 12  PASS 12  FAIL 0   MOBILE BACKGROUND: PASSED
`
覆盖：服务常驻 → 切桌面时 can_inject=false 且 /tap 被拒 → 打开目标 App 后 can_inject=true 且 /tap 成功 →
暂停后拒绝、恢复后成功 → 全程 /health 可访问（进程未被杀）。

**踩坑记录**
1. install -r 之后系统会清掉无障碍绑定，settings get secure enabled_accessibility_services 变 
ull；
   必须重写 settings 并**校验返回值**（写成重试循环），否则前台判定一直是空串。
2. Companion 的 ar paused 不加 @JvmStatic 时只有 Companion.getPaused()，Chaquopy 调 getPaused() 失败→
   暂停永远读成 false；同理 setPaused 与属性 setter 会 **JVM 签名冲突**，方法改名为 updatePaused。
3. 前台包名不能只依赖 TYPE_WINDOW_STATE_CHANGED 事件（部分机型/模拟器不派发），要回退到活动窗口查询。

**现状与限制（如实）**
- 这里的「后台」= **自动化进程在后台常驻、界面可关**；目标游戏仍需在前台才能被操作
  （Android 只允许向当前焦点窗口注入手势，无 Root 无法操作后台应用）。
- 用户切走时脚本自动「暂停注入」，切回游戏自动恢复；如需「完全离线挂机」，需要虚拟屏/云手机等方案，
  不在当前免 Root 路线上。


## 七、2026-11-02 00:50：真·后台挂机（Shizuku 虚拟屏）链路

**你要的「后台」= 手机前台可以正常用，游戏在后台继续被自动化。** 这条在免 Root 下**只有一个可行路线**：
用 shell 级权限（Shizuku）+ **虚拟屏**，把游戏搬到副屏上跑，截图/点击都指向副屏，主屏完全不受影响。
参考实现（GitHub 实测可读）：[ALAS-AOS](https://github.com/Dreamry2C/ALAS-AOS)（proot + Shizuku 虚拟屏）、
[XYZ](https://github.com/Xiao-yu-ze/XYZ)（PVZ2，Shizuku 虚拟屏 + 后台注入，含 `VirtualDisplayCreator` /
`InputInjector` / `DisplayTaskMover` 等实现）。

### 已实测证实（`dev_tools/mobile/verify_virtual_display.py`，MuMu，6/6 PASS）
```
建屏前 display ids: [0]
[PASS] 虚拟屏已创建              虚拟屏 displayId = 3
[PASS] App 已在虚拟屏启动
[PASS] 能截到虚拟屏画面（PNG）
[PASS] 注入点击后虚拟屏画面变化（证明点击落到虚拟屏）
[PASS] 主屏焦点未指向被自动化 App（用户仍可自用）
[PASS] 虚拟屏已清理
```
具体做法（等价于 Shizuku 提供的 shell 能力）：
```bash
settings put global overlay_display_devices "1280x720/240"   # 建虚拟屏（得到 displayId）
am start --display <id> -n <pkg>/<Activity>                  # 把游戏搬到虚拟屏
screencap -p -d <id>                                         # 截虚拟屏（主屏不受影响）
input -d <id> tap x y                                        # 往虚拟屏注入点击
```
证据截图：`log/mobile/vd_before.png`、`log/mobile/vd_after.png`（点在虚拟屏上打开了"通知"页，
而主屏 `mFocusedApp` 仍是桌面）。

### 已写进 APK 的代码（待真机 Shizuku 授权后即用）
| 位置 | 内容 |
| --- | --- |
| `android/.../shizuku/ShizukuShell.kt` | Shizuku 权限申请 + 反射调用 `Shizuku.newProcess`（13.x 该方法是 private，javap 确认签名）+ 虚拟屏创建/启动/截图/注入/存活检查 |
| `python/oas_mobile/bridge.py` | `display_create/launch/screenshot/tap/swipe/release/alive`、`shizuku_*` 封装 |
| `python/oas_mobile/server.py` | `/vd/create`、`/vd/launch`、`/vd/screenshot`、`/vd/tap`、`/vd/swipe`、`/vd/release`、`/shizuku/request`；`/state` 增加 `shizuku/ready/virtual_display` |
| `app/build.gradle` + Manifest | `dev.rikka.shizuku:api/provider 13.1.5`、`ShizukuProvider` 注册 |
| 注入守卫 | 每次注入前 `isDisplayAlive()`（XYZ 项目记录：向已失效虚拟屏注入可能让厂商 ROM 的 system_server 越界崩溃） |

### 未完成 / 阻塞（如实）
1. **本机 MuMu 上没能让 Shizuku 服务跑起来**：Shizuku 需要用户用「无线调试」配对或 root 启动；
   MuMu 的 `su` 对应用 UID 不授权（应用内 `su -c` 报 `Stream closed`），adb shell 侧 `su` 才是 root。
   所以 **App 内 `/vd/*` 端点在模拟器上尚未验证通过**；链路本身已用 adb（同权限语义）6/6 验证。
2. 真机需要用户一次性：装 Shizuku → 开发者选项开无线调试 → 配对启动 Shizuku → 在 OASX Mobile 里点"授权"。
   这一步只能真机做，模拟器替代不了。
3. 游戏能否在副屏正常渲染仍需在真机验证（部分游戏强制主屏/检测副屏，XYZ 项目也提到过渲染相关坑）。

### 结论（技术边界）
- **免 Root + 同屏**：只能"自动化在后台服务里常驻，用户切走时暂停注入"（第六节已实现）。
- **免 Root + 真后台**：必须走 Shizuku 虚拟屏（本节链路），前提是游戏接受副屏渲染。
- 若游戏拒绝副屏，只剩云手机/第二台设备方案，需你确认取舍。


## 八、2026-11-02 01:20：真·后台在模拟器上跑通（7/7 实测，含真实游戏）

用户已把 MuMu 的 Shizuku 以 root 启动，本轮把整条链路在 App 内跑通：

```
[PASS] Shizuku 后端可用            binder=true permission=true backend=shizuku
[PASS] 虚拟屏 + App 迁移就绪        displayId = 12
[PASS] 虚拟屏截到真实画面（非全黑）  1.1MB PNG，阴阳师庭院
[PASS] 虚拟屏注入点击返回 ok
[PASS] 点击后画面发生变化（点击真的落到游戏上）
[PASS] 主屏焦点没有被自动化 App 占据  mCurrentFocus=null
[PASS] 虚拟屏上确实是被自动化的 App
TOTAL 7  PASS 7  FAIL 0
```
脚本：`dev_tools/mobile/verify_vd_game.py`；截图证据：`log/mobile/vd_game_before.png`、`vd_game_after.png`。

### 关键发现（都影响实现）
1. **游戏会自己跑回主屏**：`am start --display N` 只能把它拉起到副屏，加载完它又回主屏
   （阴阳师实测）。必须再执行 `am display move-stack <rootTaskId> <displayId>` 把整块任务搬过去
   （XYZ 项目 `DisplayTaskMover` 同思路）。现在 `/vd/ensure` 一步完成「建屏→启动→搬任务→复核」。
2. **overlay 虚拟屏会被系统悄悄销毁**：MuMu 上跑一阵后 `dumpsys display` 里就只剩 Display 0。
   所以截图/注入前必须 `isDisplayAlive()`，不在了就重建并重新搬任务——已封装成 `_ensure_display()`，
   `/vd/screenshot`、`/vd/tap`、`/vd/swipe` 都走它。
3. **Shizuku 的 `ShizukuRemoteProcess.exitValue()` 在进程未退出时抛异常**，而 `Process.isAlive()` 内部就调它
   → 轮询会炸（报 `process hasn't exited`）。改成「读输出到 EOF + 只调一次 exitValue」。
4. **虚拟屏 id 不固定**（实测出现过 3/4/5/6/8/9/10/11/12），不能按名字或序号解析；
   用 `dumpsys display` 里 `mPrimaryDisplayDevice=叠加视图 #N` 定位。
5. **必须解析 launcher component**：游戏包用 `MAIN/LAUNCHER` intent 解析会失败
   （`am start -a MAIN -c LAUNCHER <pkg>` → `Error: Activity not started`），
   要先用 `cmd package resolve-activity --brief -c android.intent.category.LAUNCHER <pkg>` 拿 `pkg/activity`。

### 现在的接口（全部实测可用）
```
POST /vd/ensure?package=<pkg>      建屏+启动+搬到副屏，返回 display
GET  /vd/screenshot?display=<id>   截副屏（PNG），带存活守卫
POST /vd/tap?display=&x=&y=        往副屏注入点击
POST /vd/swipe?display=&x1..       往副屏注入滑动
POST /vd/move?package=&display=    仅搬任务
POST /vd/release                   清理副屏
GET  /state                        shizuku / virtual_display / paused 等
```

### 剩余工作
1. OAS 设备层接这套接口（`screenshot`/`click` 改走 `/vd/*`），再把 tasks/** 搬进 APK；
2. OASX Flutter 默认连 `127.0.0.1:22267`；
3. 真机只需重复「装 Shizuku → 无线调试配对 → 授权」，代码不用改。


## 九、2026-11-02 06:40：APK 内图像栈打通（numpy + OpenCV 实测）

阶段三的第一个硬前提验证完毕：**OAS 的识图依赖能在 APK 里跑**。

### 依赖装法（不再受 chaquo.com 慢网影响）
把 cp310/x86_64 的 wheel 先下载到本地 `mobile-wheels/`，gradle 里用
`pip { options "--find-links", "<本地目录>"; install "numpy"; install "opencv-python"; install "lxml"; install "psutil" }`
从本地装（chaquo.com 直连实测 ReadTimeout，下载靠 `curl -C -` 续传）。

### 实测（`GET /selftest`，APK 112.7 MB）
```json
{"ok": true, "python": "3.10.15", "numpy": "1.26.2", "cv2": "4.5.1",
 "checks": {"numpy": "import ok", "resize_cvtColor": "(360, 640, 3) (720, 1280)",
            "matchTemplate": "score=1.0000 loc=(0, 0)",
            "png_codec": "encoded=5599 decoded=(720, 1280, 3)"}}
```

### 实测（`GET /match_self`，虚拟屏真游戏帧上做「裁剪→匹配回去」）
```
match_self (100,100,160,120) -> {"ok": true, "score": 1.0, "found": [100, 100]}
match_self (600,300,200,150) -> {"ok": true, "score": 1.0, "found": [600, 300]}
```
判定条件写死在端点里：`score > 0.98` 且 `|found - crop原点| <= 2`，两条都过。
这证明「虚拟屏截图 → PNG 解码 → 裁剪 → 多尺度 matchTemplate → 坐标回传」整链在设备上正确。

### 新增接口
| 接口 | 作用 |
| --- | --- |
| `GET /selftest` | numpy/cv2 版本 + resize/cvtColor/matchTemplate/PNG 编解码自检 |
| `GET /assets` | 列出打进 APK 的 OAS 素材（`assets/oas_templates/`） |
| `GET /match?asset=&display=` | 用打包素材对虚拟屏帧做多尺度匹配（返回 score/loc/scale） |
| `GET /match_self?display=&x=&y=&w=&h=` | 自检：裁剪一块再匹配回去，坐标必须还原 |

### 下一步
1. 把 `module/base`、`module/atom/image.py`、`module/atom/ocr` 等搬进 APK（OCR 需要 onnxruntime，
   chaquo 仓库没有 → 走 Kotlin `onnxruntime-android` 或先只跑纯图像任务）；
2. 写 VD 设备后端接进 OAS `module/device`；
3. 打通第一个真实任务（`Restart` 登录 → 悬赏封印）。


## 十、2026-11-02 07:20：OAS 核心搬进 APK，import 自检 9/9 通过

`GET /core_import` 结果：
```
ok= True failed= 0 / 9
  PASS module.logger            PASS module.exception       PASS module.base.decorator
  PASS module.base.timer        PASS module.base.utils      PASS module.atom.image
  PASS module.atom.click        PASS module.config.utils    PASS module.config.config
```
`module.config.config` 能导意味着 **配置引擎（YAML + pydantic + filelock）在手机上跑得起来**，
这是迁移任务的前提。

### 为了让它跑起来做的事（都是硬约束导致的）
| 问题 | 处理 |
| --- | --- |
| pydantic 2.10 无法装（`pydantic-core` 是 Rust 扩展，Chaquopy 无 Android wheel） | 随包携带 pydantic v1.10（纯 Python），写 `pydantic/` 门面把 v2 API（`field_validator`/`model_validator`/`BeforeValidator`/`PlainSerializer`/`ConfigDict`/`model_dump`…）映射到 v1，同时转出 v1 API（`validator`/`conint`…） |
| `ppocronnx`（OCR）依赖 onnxruntime | 提供占位包：能 import，真调用 OCR 时抛明确错误（OCR 后续走 Kotlin onnxruntime） |
| `zerorpc`（桌面 OCR RPC） | 占位包，构造即报错 |
| Pillow 无 Android wheel | 写 `PIL/` 兼容层（open/fromarray/new/crop/resize/convert/save，底层 cv2+numpy） |
| `module.server`（WebUI） | 最小实现（`State` + `I18n`），只满足 OCR/i18n 的导入 |
| `module.notify` | no-op Notifier |
| `dev_tools.*`、`winerror` | 桌面/Windows 专用，给 stub |
| numpy 版本 | chaquo 只有 1.23.3 / 1.26.2；scipy 1.8.1 要求 `<1.25` → 用 **numpy 1.23.3**（也贴近 OAS 的 pin） |
| scipy | 用本地 wheel（21.4 MB，chaquo 唯一 cp310/android 版本 1.8.1） |

pip 依赖（全部离线 wheel + 纯 Python）：numpy 1.23.3 / opencv-python 4.5.1 / lxml / psutil /
scipy 1.8.1 / PyYAML / cached_property / rich / filelock / typing-extensions / cn2an /
decorator / inflection / requests / exceptiongroup / paho-mqtt / memory-profiler / certifi / urllib3。

### 当前 12 项目标里的下一步
```
FAIL tasks.base.base            -> 该版本 OAS 没有此模块（任务基类是 tasks/base_task.py）
FAIL tasks.Restart.script_task  -> No module named 'module.device'
FAIL tasks.WantedQuests.script_task -> 同上
```
→ **下一步就是写手机版 `module/device`**（VD 后端）：需要实现 OAS 代码实际调用的这些接口：
`image / image_frame_id / screenshot / click / long_click / swipe / app_is_running /
app_start / app_stop / adb_shell / click_record* / detect_record / save_screenshot / draw_adb`。


## 十一、2026-11-02 08:10：任务在 APK 里真的跑起来了（跑到 OCR 边界）

`POST /task/run?name=Restart` 的实测链路（log 来自 `/task/status`）：
```
08:05:29 workspace ready: /data/user/0/com.oas.oasx/files/oas
        config loaded: oas1
        device ready (virtual display backend)
        task instantiated: tasks.Restart.script_task
        → run() → app_restart → login.app_handle_login → _burst → appear → RuleImage.match
        → ocr_appear → module.ocr.models.get_ocr_model
        → RuntimeError: ppocronnx/onnxruntime is not available inside this Android build
```
即：**配置、设备层、任务实例、图片识别、登录流程都跑通了，卡点收敛到「OCR 需要 onnxruntime」这一处。**

### 本轮新增（都实测）
| 组件 | 说明 |
| --- | --- |
| `module/device/device.py` | 手机版设备层：截图/点击/长按/滑动走 Shizuku 虚拟屏；`app_start/stop/is_running` 走 shell；桌面专属（minitouch/scrcpy/窗口消息/录像/反检测）全 no-op 但保留同名方法 |
| `oas_mobile/shizuku_exec.py` | Python 侧 shell 封装（`ShizukuShell.exec`） |
| `oas_mobile/task_runner.py` | 工作目录准备（应用私有目录 `files/oas`）、Config 构造、后台线程跑 `ScriptTask.run()`、状态与日志尾巴 |
| `GET /task/status`、`POST /task/run?name=&config=` | 手机上启停任务 + 读状态 |
| `State.deploy_config` / `DeployConfig.UseOcrServer` | 补桌面版字段，避免 OCR 层 AttributeError |
| 任务素材 | 2587 个 PNG/23 MB 复制进 APK 的 python 树（`ASSETS_SYNC.md` 记录同步方式，素材不入库） |
| pydantic shim | 支持 `@field_validator`/`@field_serializer` + `@classmethod` 组合（OAS 里有 67 处） |

### 唯一剩余硬卡点：OCR
`module/ocr/ppocr.py` → `ppocronnx.TextSystem`（onnxruntime + PP-OCR 模型）。Chaquopy 无 onnxruntime，
所以下一步二选一：
1. **Kotlin 侧 onnxruntime**（`com.microsoft.onnxruntime:onnxruntime-android`）加载 OAS 的 PP-OCR 模型，
   Python 通过 Chaquopy 调 `OcrBridge.detectAndOcr(bitmap)`（需要做 det/rec 前处理 + CTC 解码 + 字典）；
2. 或先用 **纯图像任务** 验证端到端（跳过 OCR），再补 OCR。

### 顺带确认
- `tasks.Restart`、`tasks.WantedQuests` 能 import；任务能实例化并进入业务流程。
- 设备层截图/点击/滑动复用第六、七、八节验证过的 VD 链路，未引入新的坐标假设。


## 十二、2026-11-02 09:40：OCR 桥 + 自动登录打通，任务真正在手机上执行

### 已完成并实测
1. **OCR 桥（ML Kit 中文识别）**：`OcrBridge.kt` + `ppocronnx` 兼容实现。
   - `GET /ocr_selftest`：虚拟屏游戏帧识别出 13 行文本（含「适龄提示」「用户协议与隐私政策」等），带 box/score；
   - `POST /task/...` 里 `OrderOcr` 等路径已可用（日志 `[LOGIN_ENTER_GAME_ORIGIN 0.118s] [游戏]` 证明调用链通）。
   - 限制（实测）：**半透明美术字（如「进入游戏」）ML Kit 读不出**，放大/二值化/加边都不行 → 这类按钮改用模板匹配。
2. **手机端登录流程**（`oas_mobile/login.py`）：模板匹配「进入游戏」→ 点击 → 用「探索」模板判定已进主界面。
   实测日志：`login result: True`、`home detected (score=0.8079)`，确实从登录页进了庭院。
3. **任务执行**：`tasks.Restart`、`tasks.WantedQuests` 都能在 APK 内实例化并进入业务流程
   （`task instantiated: tasks.WantedQuests.script_task`）。
4. **修掉 `adb_shell` 参数类型**：OAS 会传 list（`['input','keyevent','4']`），
   Chaquopy 只能传 String → 统一 `shlex.quote` 拼成字符串。

### 当前唯一卡点：OAS 的页面判定不认当前主界面
```
oas:Page detect miss[goto_page]: scoped=['global', 'wanted_quests']
oas:Try switch to a supported page   （循环）
```
当前庭院截图（`log/mobile/wq_page.png` 一类）**没有底部导航栏**，
而 OAS 的 `I_CHECK_MAIN` 依赖底部「式神录」图标 → 判定失败 → 任务无法 goto_page。

处理方向（与 10-28 桌面版那轮同类）：按当前 UI 重新标定主界面 check 素材/ROI，
或先加一个「庭院特征」兜底判定（例如识别「探索」按钮模板作为 page_main 依据）。

### 手机端实测到的其他事实
- ML Kit 对页脚小字（客户端版本号等）识别很好，对美术字很差；
- 模板匹配在设备上稳定（`/match_self` score=1.0、坐标精确；登录按钮匹配 0.86+）；
- OCR 单次约 0.1~0.7s（含预处理阶梯），任务循环整体可接受。


## 十三、2026-11-02 10:50：悬赏封印任务在手机上完整跑完（首个端到端成功）

`POST /task/run?name=WantedQuests&config=oas1&login=1` → `GET /task/status` 结果：
```
login: starting via OCR (display=None)
login result: True                       ← 自动登录（模板匹配「进入游戏」→ 主界面判定）
task instantiated: tasks.WantedQuests.script_task
[WQ] new UI detected, run v2 flow        ← 桌面版 2026-10 的新面板流程在手机上被复用
[WQ] invite: dialog did not open         ← 邀请入口点开失败（待修）
[WQ] no unfinished wanted quest in panel ← 三卡已全部完成，无需再打
[WQ] v2 finished, handled=0
task finished normally (TaskEnd)         ← ok=True（4 分 19 秒）
```

### 本轮修掉的问题
| 问题 | 处理 |
| --- | --- |
| 登录助手只认「探索/町中」，卡在悬赏面板死循环 | 增加**「已在游戏内」文案判定**（悬赏封印/一键追踪/取消/邀请/跨区/好友…），命中即认为已登录 |
| 页面判定不认当前庭院 → `Page detect miss[goto_page]` 循环 | 手机端重标主界面特征（「探索」按钮 + 「町中」文字，**多条件 OR**），只改 APK 内副本；可重复脚本 `dev_tools/mobile/apply_mobile_overrides.py` / `apply_page_main_or.py` |
| 覆盖 `assets.py` 时丢缩进 → `IndentationError`（整包任务全挂） | 覆盖脚本按原缩进（TAB）写回 + `py_compile` 校验 |
| `TaskEnd` 被当失败 | `task_runner` 捕获 `TaskEnd` → `ok=True`（OAS 的「正常结束」约定） |

### 实测结论
- 手机端**已经能完整执行一个 OAS 任务**（登录 → 导航 → 面板 → 邀请尝试 → 卡片判定 → 结束）；
- 任务在 APK 内的耗时主要由 OCR（0.1~0.7s/次）与 `goto_page` 重试决定，4 分钟量级可接受；
- 剩余功能缺口：`[WQ] invite: dialog did not open` —— 协作邀请的入口点击没打开对话框（坐标/面板状态差异），
  属于按钮定位问题，下一步修（用模板匹配定位「邀请好友」入口，而不是写死坐标）。


## 十四、2026-11-02 11:52：手机上跑完「完整一场」悬赏封印（含真实战斗胜利）

`POST /task/run?name=WantedQuests&config=oas1&login=1` 实测 9 分 38 秒，`ok=True`：
```
login result: True
task instantiated: tasks.WantedQuests.script_task
[WQ] new UI detected, run v2 flow
[WQ] cooperation invite sent: 2          ← 协作邀请真的发出去了
[WQ] card 2: 击败14个丑时之女 0/14
[WQ] go 式神碎片第十章 x1                 ← 打开详情 → 点「前往」
GENERAL BATTLE START                      ← 真的进战斗
Win battle                                ← 战斗胜利
[WQ] v2 finished, handled=1
task finished normally (TaskEnd)
```

### 本轮修掉的四个真问题
| 问题 | 根因 | 处理 |
| --- | --- | --- |
| `[WQ] invite: dialog did not open` | 弹窗标题被 ML Kit 读成「邀清20級以上好友」，关键字 `邀请20级以上好友` 匹配失败 | 加 OCR 归一化（邀清/邀情→邀请、級→级、推薦→推荐…），并把判定放宽为「推荐 / 取消+邀请+推荐」组合 |
| 邀请入口点不中 | 入口坐标写死 (246,416)，当前 UI 已漂 | 面板左侧区域 OCR 找「邀请」文案，命中即点；找不到才退回固定坐标 |
| `cannot open card detail, skip to next` | 详情页标题是「自己击败XX」，而判定只认「悬赏目标」 | 详情判定放宽：`击败` + 结构化兜底（同时有「前往」与 a/b 进度） |
| `no unfinished wanted quest in panel`（假阴性） | 邀请/详情流程把界面留在详情页，读卡把详情当面板 | 读卡前加面板守卫：不在面板就先关详情→开面板，仍不行才结束本轮 |

### 仍然存在的缺口
- 打完一场后 `[WQ] cannot open wanted-quest panel, stop`：战斗结算后回到探索页，
  面板入口（町中）定位在手机端偶尔失配 → 下一轮要补「战斗后回町中」的稳定路径。
- 邀请第 2~5 人：首次成功后入口需要重新定位，目前只稳定发出 1~2 人。


## 十五、2026-11-02 12:48：多轮续跑打通（战后自动回面板 → 下一张卡 → 再战斗）

### 本轮修掉的四个问题
| 症状 | 根因 | 处理 |
| --- | --- | --- |
| `[mobile] tap failed at (85,340)` 连发、点击全失效 | 游戏被按出了「退出游戏？取消/确认」弹窗，弹窗吞掉所有点击 | 设备层加**弹窗兜底**：截图时抽检（每 4 帧）`退出游戏` 文案，命中就点「取消」并重截 |
| 战后 `cannot open wanted-quest panel, stop` | 战斗结束回到探索页，`wq2_open_panel()` 只点「町中入口」 | 新增 `wq2_ensure_panel()`：面板在→返回；详情→关闭；探索页→先 `goto_page(page_main)` 再点入口 |
| `no unfinished wanted quest`（假阴性） | 卡片 ROI 与当前 UI 错位：文本 ROI 读到进度、进度 ROI 读到空 | **重写卡片解析**：整块区域 `O_WQ_PANEL_SCAN`(100,420,1110,130) 一次 OCR → 按 x 聚类到四列 → 文本与进度按 y 就近配对（支持协作卡的两行任务） |
| 邀请入口/详情页判定不准 | 见第十四节 | 已修 |

### 实测（进行中，已确认）
```
[WQ] card scan: #1 '好友击敗鲤鱼精' 0/8 | #2 '击败14个丑时之女' 14/14 | #3 '击敗12个灯笼鬼' 0/12 | #4 '击敗6个涂壁' 0/6
[WQ] card 1: 好友击敗鲤鱼精 0/8
[WQ] go 秘闻 海怪的温柔·叁 x2
GENERAL BATTLE START → Win battle
… （第二轮）GENERAL BATTLE START → Win battle
[WQ] on exploration page, back to main      ← 战后自动回主界面，进入下一轮
```
即：**多轮循环已经跑起来**（开面板 → 读卡 → 前往 → 战斗 → 胜利 → 回主界面 → 下一张卡）。

### 已知小问题
- 协作卡（好友击败XX）被优先选中时会去打「秘闻」拿碎片，效率不如自己击败的卡；
  后续可给 `wq2_unfinished_cards()` 加排序：`自己击败` 优先、协作卡最后。
- 邀请入口在部分面板状态下仍需退回固定坐标（日志会 warning，但不影响主流程）。


## 十六、2026-11-02 13:12：卡片优先级 + 首次完整收尾

### 卡片优先级（自己击败 > 其他 > 协作/好友）
wq2_unfinished_cards() 增加排序：自己 0 分、好友/协作 2 分、其他 1 分。
实测（手机面板）：
`
card scan: #1 '自己击败河量' 10/12 | #1 '好友击敗鲤鱼精' 0/8 | #2 '击败14个丑时之女' | #3 '击败12个灯笼鬼' 0/12 | #4 '击敗6个涂壁' 0/6
card 1: 自己击败河量 10/12          ← 优先选自己能打的
`
之前会先选协作卡去打「秘闻」凑碎片，效率明显更低。

### 上一轮完整跑完（12:37 → 12:58，21 分钟）
ok=True、handled=1、多轮 前往→战斗→Win battle→回主界面，收尾 	ask finished normally (TaskEnd)。
末尾仍有一次 cannot open wanted-quest panel, stop（当场已无未完成任务），
属于「无事可做时提前收尾」，不影响结果。


## 十七、2026-11-02 13:20：手机端调度器（无人值守循环跑任务）

新增 `oas_mobile/scheduler.py` + 三个接口：
```
POST /scheduler/start?tasks=WantedQuests,DailyTrifles&interval=1800&config=oas1
POST /scheduler/stop
GET  /scheduler/status     → {running, tasks, interval, round, current, next_at, log[]}
```
行为：按任务列表顺序跑（每个任务内部会先自动登录），跑完等 `interval` 秒进入下一轮；
单任务最多等 40 分钟（防呆），随时可 `/scheduler/stop`。

实测（`dev_tools/mobile/verify_scheduler.py`，间隔 180s）：
```
[PASS] 调度器启动
[PASS] 调度器进入运行态并开始跑任务   round=1 current='WantedQuests'
[PASS] 任务线程确实在跑（有当前任务名）
[PASS] 调度器可停止
[PASS] 停止后 running=False
TOTAL 5  PASS 5  FAIL 0
```

### 当前手机端可用能力（汇总）
| 能力 | 接口 |
| --- | --- |
| 虚拟屏真后台（游戏不占主屏） | `/vd/ensure`、`/vd/screenshot`、`/vd/tap`、`/vd/swipe` |
| 自动登录 | 随 `/task/run` 自动执行（模板匹配 + 在游戏判定 + 脱困） |
| 单任务执行 | `POST /task/run?name=<Task>&config=<cfg>&login=1` + `GET /task/status` |
| 无人值守循环 | `POST /scheduler/start|stop` + `GET /scheduler/status` |
| 识别自检 | `/selftest`、`/match_self`、`/ocr_selftest`、`/ocr_roi`、`/core_import` |

实测结论：悬赏封印在手机上可完整跑完（21 分钟，含多轮战斗，`ok=True`）。


## 十八、2026-11-02 13:55：OASX 界面接上手机内服务（HTTP + WebSocket 全通）

### 做了什么
1. **OASX 自动连本机**：`login_controller.onInit` 地址为空时默认 `127.0.0.1:22267` 并自动登录。
2. **手机端补齐 OASX 需要的接口**（`oas_mobile/server.py`）：
   `/test`、`/config_list`、`/config_new_name`、`/home/home_menu`、`/home/additional_translate`、
   `PUT /home/chinese_translate`、`/script_menu`、`/<script>/<task>/args`。
3. **WebSocket `/ws/<config>`**（新文件 `oas_mobile/ws.py`，stdlib 手写握手/帧编解码）：
   连接后推 `{"state": "running"|"idle"}` 与任务日志尾巴，响应 `start` / `stop` / `get_state`。

### 实测
App 启动后的服务端访问日志（全部 200，无 404/501）：
```
GET /test 200 · PUT /home/chinese_translate 200 · GET /config_list 200
GET /home/additional_translate 200 · GET /home/home_menu 200 · GET /script_menu 200
```
数据侧：`/config_list → ['oas1']`、`/script_menu → 59 个任务`。

WebSocket（`dev_tools/mobile/verify_ws.py`）：
```
[PASS] WebSocket 握手成功（101）
[PASS] get_state 返回 JSON 状态        {"state": "idle"}
[PASS] 收到服务端推送（心跳/日志）      {"state": "idle"}
TOTAL 3  PASS 3  FAIL 0
```

界面侧：App 已进入主页面（`OASX / HOME / 主页`，截图 `log/mobile/app_ui.png` 系列），
说明 Flutter 前端 ↔ APK 内 Python 服务的链路打通。

### 尚未验证
- 界面上点「启动/停止任务」的完整交互（需要 UI 操作，`start` 已实现但未从界面实测）。
- 任务参数页（`/<script>/<task>/args` 目前返回空表，界面能渲染但无字段）。


## 十九、2026-11-02 14:20：任务参数表接上（OASX 配置页可渲染）

### 做了什么
1. `/<script>/<task>/args` 从「返回空表」改成**用 OAS 自己的 Config 模型生成**：
   `Config(script).model.script_task(task)`（与桌面 WebUI 同一入口）。
2. 过程中修了两个 pydantic 门面缺陷：
   - `model_json_schema()` 返回的是 v1 的 `definitions`，而 OAS 的 `extract_groups()` 要 `$defs`
     → 加转换（`definitions`→`$defs`、`$ref` 路径同步）→ 修掉 `KeyError('$defs')`；
   - `model_dump(context=...)` 是 v2 参数，v1 的 `.dict()` 不认识
     → `model_dump` 先剔除 v2 专有参数（context/mode/round_trip/warnings…）→ 修掉 `TypeError`。
3. 配置写入 `PUT /<script>/<task>/<group>/<argument>/value?types=&value=`：先用 OAS 官方
   `ConfigModel.script_set_arg()`（有校验），失败再退回直接写 json（绝对路径）。

### 实测
```
GET /oas1/WantedQuests/args
→ groups ['scheduler', 'wanted_quests_config', 'switch_soul_config']
→ 字段形如 {"name":"before_end","title":"Before End","default":"00:00:00","value":"00:00:00","type":"string"}
PUT /oas1/WantedQuests/wanted_quests_config/cooperation_only/value?types=boolean&value=true
→ true
```

### 已知问题（下一步）
- `PUT` 返回 true，且 `/debug_args` 探针确认**文件里已写入** `cooperation_only = True`，
  但随后 `GET /args` 读回的 `value` 仍是默认 `false` → 说明 `Config` 侧存在缓存或读到的是
  另一份实例/路径。下一轮排查方向：`Config` 的 `model` 是 `cached_property` + 进程级复用，
  需要每次请求重新构造，或调用 `Config` 的 reload/`save` 后再读。
- 界面点「启动/停止」尚未从 UI 实测（WS 的 `start`/`stop` 已实现）。

### 配置写入已闭环（2026-11-02 14:25 复测）
之前「PUT 返回 true 但读回默认值」是当时的测试顺序/COnfig 实例时序问题；
最终用官方 ConfigModel.script_set_arg() 后**双向都通**：
`
set true  -> true | args: True
set false -> true | args: False
`
/debug_cfg 探针同时确认 cwd/绝对路径/文件内容一致
（cwd=/data/data/com.oas.oasx/files/oas，文件里 WantedQuests.wanted_quests_config.cooperation_only 随设置变化）。


## 二十、2026-11-02 16:55：隐藏虚拟屏（MediaProjection）+ UI 主题重做

### 隐藏虚拟屏：能做到「主屏完全看不到」
用 MediaProjection 建 PUBLIC 虚拟屏（渲染到自建的 ImageReader Surface，不在主屏显示），
而不是 overlay_display_devices（后者会在主屏渲染小窗，正是用户看到的现象）。
参考实现：[headless-android](https://github.com/HakaI963/headless-android)、
[vdisplay-android](https://github.com/MerlinShieh/vdisplay-android)（后者明确写了
overlay 屏 m start --display 会被 SafeActivityOptions.checkPermissions 拒绝）。

落地文件：
- HiddenDisplay.kt：建屏 / 抓帧（ImageReader→PNG）/ release / status
- HiddenCaptureService.kt：oregroundServiceType=mediaProjection 前台服务
  （Android 12+/14 硬性要求，缺失时报 Media projections require a foreground service...）
- MainActivity：启动后自动弹一次录屏授权；onActivityResult 里**立刻建屏**
  （createVirtualDisplay 会校验调用方在前台，后台线程再建会 SecurityException）
- ridge.py / device.py：display_ensure_app、_ensure_display、screenshot 支持隐藏屏路径
- HTTP：GET /hidden[?action=ensure|create|release|prefer|status]

实测证据（MuMu / Android 12）：
`
hidden: {'ok': True, 'display': 21, 'status': 'projection=true displayId=21'}
游戏 task #1193 落在 Display #21；主屏 focused 仍是 com.oas.oasx/MainActivity
隐藏屏截图（ImageReader 抓帧）1.2MB，画面为游戏登录界面
`

### 已知限制（关键）
**应用切到后台后，MediaProjection 的 ImageReader 不再产帧**（日志 empty frame from display N），
而任务恰恰是在后台跑的。所以：
- 隐藏屏目前做成**可选**：GET /hidden?action=prefer&value=1 才优先使用；
- 抓帧失败会自动 hidden_disable() 并降级回 overlay 屏，不会中断任务；
- 默认路径仍是 overlay（回归实测：d/ensure → display 24、login result: True、
  [WQ] new UI detected, run v2 flow 正常）。

下一步真·隐藏方案：用 Shizuku 的 shell 权限起 pp_process 宿主自建虚拟屏
（owner 是 shell，可 m start --display，且不依赖 MediaProjection 授权/前台），
参考 vdisplay-android 的 d.jar 做法。

### UI 主题重做（lib/config/theme.dart）
- 主色改为「阴阳师·朱红」#9E1B32，浅色暖白底 / 深色暗红灰底；
- 统一圆角（卡片 18、按钮 14、Chip 全圆），填充式输入框，药丸按钮；
- 底部导航药丸指示器 + 选中加粗；ListTile/Divider/SnackBar 统一圆角；
- 导出 AppRadius / AppSpacing 供页面复用。
截图：登录页已应用新主题（朱红标题 + 圆角输入框 + 药丸按钮）。

## 二十一、2026-11-02 17:25：无头虚拟屏宿主（shell app_process）——通道打通，卡在 Context

### 为什么还要做这一层
MediaProjection 建的隐藏屏在 App 切后台后 ImageReader 停帧（第二十节），
而「真后台」必须在后台也能抓帧。方案：用 Shizuku 的 shell 权限起 `app_process` 宿主，
由 shell 进程持有虚拟屏（参考 vdisplay-android 的 vd.jar）。

### 已实现
- `VdHost.java`（Java，避免 Kotlin stdlib 依赖）：`main()` 建屏 → 打印 `DISPLAY_ID=<n>` → 常驻；
  自带 `PROBE=` 诊断（反射到的签名、失败原因链）。
- `ShizukuShell.startLongRunning(command)`：起常驻 shell 进程并返回句柄（`exec()` 会等退出，不适用宿主）。
- `VdHostDisplay.kt` / `VdHostBridge.kt`：Python 可调 start/stop/status；命令形如
  `CLASSPATH=<base.apk> app_process /system/bin com.oas.oasx.host.VdHost 1280 720 240`。
- HTTP `POST /vd/host?action=start|status|stop`；`bridge.vdhost_*`；
  display 优先级：**宿主屏 > 隐藏屏(opt-in) > overlay**。
- 验证脚本 `dev_tools/mobile/verify_vdhost.py`（服务在线 / 建屏 / 游戏在该屏 / 主屏不是游戏 / 可截图 / 可停止）。

### 实测进展（关键）
```
D AndroidRuntime: Calling main entry com.oas.oasx.host.VdHost       <- 宿主真的跑起来了
I VdHostDisplay: host: PROBE=systemContext failed InvocationTargetException
I VdHostDisplay: host: PROBE=createVirtualDisplay/6 Context,MediaProjection,VirtualDisplayConfig,Callback,Handler,Context,
I VdHostDisplay: host: PROBE=global failed ... NullPointerException: Context.getPackageName() on null
```
结论：**Android 12 的签名是 6 参 + `VirtualDisplayConfig`**（Builder 反射构造成功），
但 `ActivityThread.systemMain()` 在该 ROM 的 app_process 里抛异常 → 拿不到 Context → 建屏 NPE。

### 下一步（明确）
1. 绕开 Context：直接走 binder —— `ServiceManager.getService("display")` 拿 `IDisplayManager`，
   调 `createVirtualDisplay(VirtualDisplayConfig, IVirtualDisplayCallback, ISurfaceComposerClient, String)`，
   ownerPackageName 用 `com.android.shell`；
2. 或 `ContextImpl.createSystemContext(ActivityThread.currentActivityThread())`（需先让 systemMain 成功）。


### 二十一·续（17:30）：定位到 system_server 的 uid 校验

按顺序修掉两个前置条件后，卡点已经从「跑不起来」推进到「权限校验」：

| 尝试 | 结果 |
| --- | --- |
| `ActivityThread.systemMain()` 直接调 | `RuntimeException: Can't create handler inside thread ... that has not called Looper.prepare()` |
| 先 `Looper.prepareMainLooper()` 再调 | **`PROBE=systemContext true`**（拿到系统 Context） |
| 反射 `createVirtualDisplay` 6 参 + Builder 构造 `VirtualDisplayConfig` | `SecurityException: packageName must match the calling uid` |
| 用 `ContextWrapper` 把包名伪装成 `com.android.shell` | 仍然 `SecurityException: packageName must match the calling uid` |

结论：`DisplayManagerService` 侧对「虚拟屏 owner 包名 vs 调用 uid」的校验不是在 Java 层
用 `Context.getPackageName()` 做的（伪装无效），需要按 shell uid 的真实身份注册，
或直接走 binder：`ServiceManager.getService("display")` →
`IDisplayManager.createVirtualDisplay(VirtualDisplayConfig, IVirtualDisplayCallback, ISurfaceComposerClient, String)`。


### 二十一·三（18:15）：无头屏能建、能投 App，但帧服务与宿主生命周期还没通

**已实测通过（`dev_tools/mobile/verify_vdhost_frame.py`，3/4 PASS）**
```
[PASS] 无头屏创建（shell 宿主）   {'ok': True, 'display': 32, ...}
[PASS] 游戏已在该屏               Display #32 … ResumedActivity: com.netease.onmyoji…
[PASS] 主屏前台不是游戏           mCurrentFocus=null（主屏上没有游戏窗口）
[FAIL] 宿主帧服务取到 PNG          HTTP 503
```
关键修复链（每步都有 `PROBE=` 日志）：
1. `Looper.prepareMainLooper()` → `ActivityThread.systemMain()` 才能拿到 Context；
2. 宿主用 `su 2000` 降权（MuMu 的 Shizuku 是 root 起的，默认 uid=0 → `packageName must match the calling uid` 永远不成立）；
3. `createVirtualDisplay` 的 flags 必须带 `OWN_CONTENT_ONLY`，否则报
   `Requires CAPTURE_VIDEO_OUTPUT ... in order to create a screen sharing virtual display`；
4. 加 `PRESENTATION` 后 `dumpsys display` 里能看到 `FLAG_PRESENTATION, FLAG_OWN_CONTENT_ONLY`（display 状态 ON）。

**还没通的两点**
- 宿主进程会在 App 进程结束后一起消失（`ps -A | grep os.oasx.host` → 0），
  → 需要脱离 App 生命周期（`su 0 … nohup … &` / `setsid`），否则重装/重启 App 就丢屏。
- 帧服务取不到帧：宿主侧 `ImageReader_JNI: Unable to acquire a buffer item … maxImages buffers`
  （自检循环里没释放旧 Image，缓冲区被耗尽），App 侧 socket 读超时（`capturePng failed: Try again`）。
  → 修 `acquireLatestImage()` 的释放节奏、把 maxImages 调大，再验证 `/vd/frame`。

### 当前可用状态
- **默认**：overlay 虚拟屏（主屏可见小窗，`screencap -d` / `input -d` 全链路稳定）。
- **可选**：MediaProjection 隐藏屏（主屏不可见，但 App 后台后停帧）。
- **未通**：shell 宿主无头屏（不可见 + 后台可用），已定位到 service 侧 uid 校验。

### 回归
默认路径（overlay）不受影响：`/vd/ensure → display 24`、任务正常进入 `login: starting via OCR`。
