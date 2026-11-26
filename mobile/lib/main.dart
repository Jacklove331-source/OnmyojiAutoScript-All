import 'package:flutter/foundation.dart' show kReleaseMode;
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:device_preview/device_preview.dart';
import 'package:get_storage/get_storage.dart';
import 'package:oasx/service/locale_service.dart';
import 'package:oasx/service/theme_service.dart';
import 'package:oasx/service/websocket_service.dart';
import 'package:oasx/service/window_service.dart';
import 'package:oasx/utils/logger.dart';
import 'package:responsive_builder/responsive_builder.dart';
import 'package:get/get.dart';

import 'package:oasx/views/routes.dart';
import 'package:oasx/utils/platform_utils.dart';
import 'package:oasx/controller/settings.dart';
import 'package:oasx/config/theme.dart' show lightTheme, darkTheme;

// 新前端（阴阳师庭院皮肤 · 4 页签纯人物底栏）开关
import 'package:oasx/ui/api.dart';
import 'package:oasx/ui/shell.dart';
import 'package:oasx/ui/skin_store.dart';
import 'package:oasx/ui/theme.dart' as ui;

/// Phase 1 开关：true = 新的和纸浅色外壳（lib/ui/shell.dart）
/// false = 旧 GetX 界面（views/layout + views/nav，代码保留未删）
const bool kUseNewUi = true;

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // 竖屏锁定：只在竖屏下用，禁止横屏
  await SystemChrome.setPreferredOrientations(<DeviceOrientation>[
    DeviceOrientation.portraitUp,
  ]);

  if (kUseNewUi) {
    await OasApi.i.loadActiveConfig();
    await OasSkinController.i.load();
    runApp(const OasApp());
    return;
  }

  await initService();

  runApp(
    DevicePreview(
      enabled: !kReleaseMode && (PlatformUtils.isWindows),
      builder: (context) => const OASXApp(), // Wrap your app
    ),
  );
}

/// 新前端根：Material 3 + 和纸浅色主题（深色版作为备选主题）
class OasApp extends StatelessWidget {
  const OasApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<ThemeMode>(
      valueListenable: ui.oasThemeMode,
      builder: (BuildContext context, ThemeMode mode, Widget? _) {
        return MaterialApp(
          debugShowCheckedModeBanner: false,
          title: 'OASX',
          scrollBehavior: GlobalBehavior(),
          themeMode: mode,
          theme: ui.buildOasTheme(Brightness.light),
          darkTheme: ui.buildOasTheme(Brightness.dark),
          home: const OasShell(),
        );
      },
    );
  }
}

class OASXApp extends StatelessWidget {
  const OASXApp({super.key});

  // This widget is the root of your application.
  @override
  Widget build(BuildContext context) {
    final localeService = Get.find<LocaleService>();

    return ResponsiveApp(builder: (context) {
      return GetMaterialApp(
        // useInheritedMediaQuery: true,
        debugShowCheckedModeBanner: false,
        // locale: DevicePreview.locale(context),
        builder: DevicePreview.appBuilder, // 上面三个是使用device_preview
        scrollBehavior: GlobalBehavior(),
        translations: localeService.messages,
        locale: localeService.currentLocale,
        fallbackLocale: localeService.fallbackLocale, //语言选择无效时，备用语言
        title: 'OASX',
        onInit: onInit,
        initialRoute: Routes.initial,
        getPages: Routes.routes,
        theme: lightTheme,
        darkTheme: darkTheme,
      );
    });
  }

  /// 但是我不能确定 Getx 框架这个时候是否成功初始化
  void onInit() {
    Get.put(SettingsController());
  }
}

class GlobalBehavior extends MaterialScrollBehavior {
  @override
  Set<PointerDeviceKind> get dragDevices => {
        PointerDeviceKind.touch,
        PointerDeviceKind.mouse,
        // etc.
      };
}

Future<void> initService() async {
  await initLogger();
  await GetStorage.init();

  await Future.wait([
    Get.putAsync(() async => LocaleService()),
    Get.putAsync(() async => ThemeService()),
    Get.putAsync(() async => WindowService()),
  ]);

  Get.lazyPut(() => WebSocketService());
}
