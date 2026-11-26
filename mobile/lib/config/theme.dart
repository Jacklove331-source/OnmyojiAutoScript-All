import 'package:flutter/material.dart';
import 'package:chinese_font_library/chinese_font_library.dart';

/// 主题配色（枚举方便 UI 里做「换主题色」入口）。
enum ColorSeed {
  baseColor('阴阳师·朱红', Color(0xFF9E1B32)),
  indigo('Indigo', Colors.indigo),
  blue('Blue', Colors.blue),
  teal('Teal', Colors.teal),
  green('Green', Colors.green),
  yellow('Yellow', Colors.yellow),
  orange('Orange', Colors.orange),
  deepOrange('Deep Orange', Colors.deepOrange),
  pink('Pink', Colors.pink);

  const ColorSeed(this.label, this.color);
  final String label;
  final Color color;
}

const Map<String, Color> colorSeedMap = {
  '阴阳师·朱红': Color(0xFF9E1B32),
  'M3 Baseline': Color(0xff6750a4),
  'Indigo': Colors.indigo,
  'Blue': Colors.blue,
  'Teal': Colors.teal,
  'Green': Colors.green,
  'Yellow': Colors.yellow,
  'Orange': Colors.orange,
  'Deep Orange': Colors.deepOrange,
  'Pink': Colors.pink,
};

/// 统一的圆角与间距，UI 里引用它保证「同一套视觉语言」。
class AppRadius {
  static const double card = 18;
  static const double button = 14;
  static const double chip = 999;
}

class AppSpacing {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 16;
  static const double lg = 24;
}

/// 浅色/深色主题：暖白（深色为暗红灰）底 + 朱红主色 + 柔和圆角。
ThemeData lightTheme = _buildTheme(Brightness.light);
ThemeData darkTheme = _buildTheme(Brightness.dark);

ThemeData _buildTheme(Brightness brightness) {
  final bool isLight = brightness == Brightness.light;
  final Color seed = colorSeedMap['阴阳师·朱红']!;
  final ColorScheme scheme = ColorScheme.fromSeed(
    seedColor: seed,
    brightness: brightness,
  );

  final Color surface = isLight ? const Color(0xFFFFFBF9) : const Color(0xFF1A1719);
  final Color surfaceVariant =
      isLight ? const Color(0xFFF4EDEA) : const Color(0xFF2A2427);

  final TextTheme textTheme = const TextTheme(
    bodyLarge: TextStyle(),
    bodyMedium: TextStyle(),
    bodySmall: TextStyle(),
    labelLarge: TextStyle(),
    labelMedium: TextStyle(),
    labelSmall: TextStyle(),
    titleLarge: TextStyle(fontWeight: FontWeight.w600),
    titleMedium: TextStyle(fontWeight: FontWeight.w600),
    titleSmall: TextStyle(fontWeight: FontWeight.w600),
  ).apply(fontFamily: 'LatoLato').useSystemChineseFont(brightness);

  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
    textTheme: textTheme,
    scaffoldBackgroundColor: surface,
    visualDensity: VisualDensity.adaptivePlatformDensity,

    // 顶栏：低调不抢内容
    appBarTheme: AppBarTheme(
      backgroundColor: surface,
      surfaceTintColor: Colors.transparent,
      foregroundColor: scheme.onSurface,
      elevation: 0,
      scrolledUnderElevation: 0.5,
      centerTitle: false,
      titleTextStyle: textTheme.titleLarge?.copyWith(
        color: scheme.onSurface,
        fontWeight: FontWeight.w600,
      ),
    ),

    // 底部导航：选中项用圆角药丸指示器
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: isLight ? Colors.white : const Color(0xFF211C1E),
      indicatorColor: scheme.primaryContainer,
      elevation: 1,
      height: 68,
      labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
      labelTextStyle: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return (textTheme.labelMedium ?? const TextStyle()).copyWith(
          color: selected ? scheme.primary : scheme.onSurfaceVariant,
          fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
        );
      }),
    ),

    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: surface,
      indicatorColor: scheme.primaryContainer,
      selectedIconTheme: IconThemeData(color: scheme.primary),
      labelType: NavigationRailLabelType.all,
    ),

    // 主按钮：圆角更柔和
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(0, 46),
        padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 12),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.button),
        ),
        textStyle: textTheme.labelLarge?.copyWith(fontWeight: FontWeight.w600),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(0, 46),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.button),
        ),
        side: BorderSide(color: scheme.outlineVariant),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(AppRadius.button),
        ),
      ),
    ),

    // 输入框：填充式 + 圆角
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: surfaceVariant,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.button),
        borderSide: BorderSide.none,
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.button),
        borderSide: BorderSide.none,
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(AppRadius.button),
        borderSide: BorderSide(color: scheme.primary, width: 1.4),
      ),
    ),

    chipTheme: ChipThemeData(
      backgroundColor: surfaceVariant,
      selectedColor: scheme.primaryContainer,
      side: BorderSide.none,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.chip),
      ),
      labelStyle: textTheme.labelMedium,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
    ),

    listTileTheme: ListTileThemeData(
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.card),
      ),
      iconColor: scheme.onSurfaceVariant,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
    ),

    dividerTheme: DividerThemeData(
      color: scheme.outlineVariant.withOpacity(0.5),
      thickness: 0.8,
      space: 1,
    ),

    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppRadius.button),
      ),
    ),

    cardColor: isLight ? Colors.white : const Color(0xFF241F21),
    shadowColor: Colors.black.withOpacity(isLight ? 0.06 : 0.4),
  );
}
