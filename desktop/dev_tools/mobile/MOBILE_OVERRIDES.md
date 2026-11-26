# APK 内对 OAS 文件的手机端覆盖清单

同步 `module/**`、`tasks/**` 到 APK 后，需要重新应用这些覆盖（桌面仓库不受影响）：

| 文件 | 覆盖内容 | 原因 |
| --- | --- | --- |
| `tasks/GameUi/assets.py` | `I_CHECK_MAIN` → 「探索」悬浮按钮模板 `page_main_check_mobile.png`（roi_front=(330,25,100,105), threshold=0.75） | 当前庭院没有底部导航栏，旧检查项（底部式神录图标）永远失配，导致 `Page detect miss[goto_page]` |
| `tasks/GameUi/page/page_main_check_mobile.png` | 由当前版本真实截图裁出的「探索」按钮 | 同上 |

重新生成：`python dev_tools/mobile/apply_mobile_overrides.py`（需先有最新庭院截图）
