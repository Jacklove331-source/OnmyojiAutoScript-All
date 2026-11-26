# release 包会开 R8；Chaquopy 从 Python 侧按名字调 Kotlin（jclass / callAttr），
# 名字被混淆就会静默返回 false（表现为"无障碍未连接""后台服务未起"，但 dumpsys 看服务其实在跑）。
-keep class com.oas.oasx.** { *; }
-keepclassmembers class com.oas.oasx.** { *; }
-keep class com.chaquo.python.** { *; }
-keepclassmembers class com.chaquo.python.** { *; }

# ShizukuShell 用反射调 Shizuku.newProcess（13.x 把它声明成 private），
# R8 一旦把方法名/类名混淆，反射就 resolve 不到 → exec 静默失败（/vd/create、/vd/host 全返回 -1）。
-keep class rikka.shizuku.** { *; }
-keepclassmembers class rikka.shizuku.** { *; }
-dontwarn rikka.shizuku.**

-keep class ai.onnxruntime.** { *; }
