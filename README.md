<div align="center">

# OnmyojiAutoScript-All (OAS 电脑端 + 手机端一体仓库)

<br>

<div>
    <img alt="platform" src="https://img.shields.io/badge/Platform-Windows%20%2B%20Android%2011%2B-3DDC84?logo=android&logoColor=white">
    <img alt="flutter" src="https://img.shields.io/badge/Mobile%20UI-Flutter%20%2B%20Kotlin-02569B?logo=flutter&logoColor=white">
    <img alt="python" src="https://img.shields.io/badge/Core-Python%203.10%20(Chaquopy%20%2B%20ONNX)-3776AB?logo=python&logoColor=white">
</div>
<div>
    <img alt="license" src="https://img.shields.io/github/license/Jacklove331-source/OnmyojiAutoScript-All">
    <img alt="releases" src="https://img.shields.io/github/v/release/Jacklove331-source/OnmyojiAutoScript-All">
    <img alt="stars" src="https://img.shields.io/github/stars/Jacklove331-source/OnmyojiAutoScript-All?style=social">
</div>

<br>

阴阳师自动化脚本 · **电脑端 (`desktop/`) + 纯手机免 Root 后台独立运行版 (`mobile/`)**

无需电脑 · 免 Root · Shizuku 独立虚拟屏后台托管 · 电脑端与手机端同源核心与配置互通

#### 上游项目致谢：[runhey/OnmyojiAutoScript](https://github.com/runhey/OnmyojiAutoScript) · [runhey/OASX](https://github.com/runhey/OASX)

</div>

---

## 目录结构 Repository Layout

```text
OnmyojiAutoScript-All/
├── desktop/   # 电脑端 OAS（Windows / 模拟器 / 完整桌面任务与界面）
├── mobile/    # 手机端 OASX Mobile（Flutter + Kotlin + Chaquopy 内嵌 Python + Shizuku 后台虚拟屏）
├── LICENSE    # GNU General Public License v3.0
└── README.md
```

---

## 功能覆盖 Features

双端共享 OAS Python 自动化引擎与本地 ONNX OCR 识别，覆盖绝大多数日常与周常玩法：

- **日常任务**：自动重启登录、庭院收菜、悬赏封印、每日琐事、花合战、小猫咪、式神委派、地域鬼王、逢魔之时、金币/经验妖怪、年兽
- **阴阳寮**：结界挂卡、结界蹭卡、个人突破、寮突破、狩猎战（麒麟/阴界之门）、集体任务、道馆、狭间暗域、宴会
- **御魂与刷图**：八岐大蛇、魂土（悲鸣）、业原火、日轮之陨、永生之海、六道之门、觉醒副本、探索、契灵之境、御灵、石距
- **每周相关**：每周琐事、每周购买、神秘商店、大富翁、真·八岐大蛇、秘闻副本、斗技等
- **限时活动**：爬塔、超鬼王、百鬼棋局等（随引擎同步更新）

---

## 手机端亮点 Highlights (`mobile/`)

- **纯手机本地运行（免电脑、免 Root）**：APK 内嵌 Python 3.10 与本地 ONNX OCR，手机随时随地独立托管。
- **Shizuku 独立后台虚拟屏**：在系统后台创建独立横屏虚拟屏运行阴阳师，**不改主屏分辨率/DPI，不干扰你前台刷视频或聊微信**；支持 Android 14~16 `VirtualDevice` 虚拟屏与传统显示回退。
- **实时画面监控与随时接管**：内置「监控」页支持实时查看后台虚拟屏画面、彩色运行日志、随时暂停/手动点按接管或一键切回主屏。
- **首页任务排序与一键置顶**：直观展示「运行中 / 待执行 / 等待中」队列顺序，支持将任意任务一键置顶优先执行。
- **设备安全守护**：实时监测电池电量、电池温度、充电状态与 Wi-Fi 网络状态，支持温度/电量阈值自动停机保护，保护手机硬件。
- **海量真机实测与稳定性修复**：
  - 适配阴阳师新版开屏底图与角色选择页；
  - 兼容移动端 PP-OCR 常见形近字/红底艺术字截断（如结界卡「勾玉/幻玉」、年兽单字标题、悬赏繁体数量等）；
  - 针对安卓后台墓碑冻结解冻后增加截屏超时与卡死计时自动恢复，防止误报断帧失败；
  - 修复空邮件循环、集体任务 30 次上限弹窗遮挡神社导航、安卓端跳过外部微信分享卡死等问题；
  - 全量中文化界面与菜单（对齐桌面端 OAS/OASX 官方译名）。

---

## 快速上手 Quick Start

### 1. 手机端（推荐直接下载 APK）

#### 环境要求
- Android 11 及以上系统（已在 Android 16 / OriginOS 6 真机实测全套日常与周常通过）
- 安装并启动 [Shizuku](https://shizuku.rikka.app/)（无线调试即可激活，无需 Root）

#### 使用步骤
1. 前往本仓库 [Releases](https://github.com/Jacklove331-source/OnmyojiAutoScript-All/releases) 下载最新 `arm64-v8a` APK 并安装；
2. 打开 **Shizuku** 并完成激活，再打开 **OASX** 授予 Shizuku 权限与通知权限；
3. 建议在手机 **设置 → 电池 → 后台耗电管理** 中将 **OASX** 与 **阴阳师** 设为 **「允许后台高耗电 / 无限制」**，避免息屏或切后台时被系统冻结；
4. 在「脚本」页按需开启/配置任务（也支持直接导入桌面端导出的 `.json` 配置文件）；
5. 回到「首页」点击 **启动调度**，脚本将自动在后台虚拟屏拉起阴阳师、验证角色并按队列依次执行；
6. 随时切到「监控」页查看实时后台画面与日志。

### 2. 电脑端 (`desktop/`)

1. 克隆本仓库并进入 `desktop/` 目录；
2. 安装 Python 3.10 依赖：
   ```bash
   pip install -r requirements.txt
   ```
3. 启动脚本或图形界面：
   ```bash
   python gui.py
   # 或运行调度服务端
   python server.py
   ```
4. 配置文件模板位于 `desktop/config/template.json`（个人账号配置已从公开库彻底剔除，请按需在界面中新建配置）。

---

## 从源码构建手机端 Build Mobile from Source

- **环境要求**：Flutter SDK、JDK 17、Android SDK（`compileSdk 36` / `targetSdk 36`）、Python 3.10（Chaquopy 构建环境）

```powershell
cd mobile
flutter pub get
$env:ORG_GRADLE_PROJECT_abi = 'arm64-v8a'
flutter build apk --release --no-tree-shake-icons --target-platform android-arm64
```

运行主机侧单元测试（无需连接真机）：

```powershell
python test/host/test_android_contract.py android/app/src/main/python
python test/host/test_navigation_recovery.py android/app/src/main/python
python test/host/test_mobile_login.py android/app/src/main/python
python test/host/test_restart_login.py android/app/src/main/python
flutter test
```

---

## 免费开源声明 Announcement

本软件完全免费、开源，仅供技术学习与个人研究交流使用。因使用本软件产生的任何后果均与本项目及作者无关。  
**This project is completely free and open-source. If you paid for it from any channel, please request a refund immediately.**

---

## 相关项目与鸣谢 Credits

- [runhey/OnmyojiAutoScript](https://github.com/runhey/OnmyojiAutoScript)：阴阳师自动化脚本核心引擎
- [runhey/OASX](https://github.com/runhey/OASX)：跨平台客户端与界面设计参考
- [Shizuku](https://github.com/RikkaApps/Shizuku)：免 Root 特权 API 桥接

---

## 开源协议 License

本项目遵循 [GNU General Public License v3.0](LICENSE) 开源协议。
