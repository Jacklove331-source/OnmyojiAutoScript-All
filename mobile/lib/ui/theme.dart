import 'package:flutter/material.dart';

/// ─────────────────────────────────────────────────────────────
/// OASX Mobile · 和纸浅色设计令牌
/// 定稿样图：dist/mockup/v8-light.png   规范：dist/DESIGN-SPEC-v1.md
/// 页面里不要硬编码颜色，统一用 `context.oas.xxx` 取。
/// ─────────────────────────────────────────────────────────────
class OasPalette {
  const OasPalette({
    required this.bg,
    required this.surface,
    required this.surfaceAlt,
    required this.line,
    required this.ink,
    required this.inkSoft,
    required this.inkDim,
    required this.vermilion,
    required this.vermilionHi,
    required this.gold,
    required this.goldHi,
    required this.jade,
    required this.purple,
  });

  /// 页面底（和纸）
  final Color bg;

  /// 卡片
  final Color surface;

  /// 次级面：图标底、分段选中、进度槽
  final Color surfaceAlt;

  /// 描边 / 分隔
  final Color line;

  /// 正文
  final Color ink;

  /// 次级正文
  final Color inkSoft;

  /// 说明文字
  final Color inkDim;

  /// 主色 · 漆红
  final Color vermilion;

  /// 主按钮渐变上沿
  final Color vermilionHi;

  /// 描金 / 强调数字
  final Color gold;

  /// 深色块高亮
  final Color goldHi;

  /// 成功 / 正常
  final Color jade;

  /// 分支强调（御魂 / 觉醒徽记）
  final Color purple;

  /// 和纸浅色（默认，用户定稿版）
  static const OasPalette light = OasPalette(
    bg: Color(0xFFF6EFE3),
    surface: Color(0xFFFFFDF8),
    surfaceAlt: Color(0xFFFBF3E6),
    line: Color(0xFFE6D6BD),
    ink: Color(0xFF2A201A),
    inkSoft: Color(0xFF5C4A3A),
    inkDim: Color(0xFF8A7561),
    vermilion: Color(0xFFB03A2C),
    vermilionHi: Color(0xFFD9503F),
    gold: Color(0xFFB0803C),
    goldHi: Color(0xFFEAC98A),
    jade: Color(0xFF2F8F6B),
    purple: Color(0xFF6C4B9B),
  );

  /// 深色版（我的 → 外观 备选）
  static const OasPalette dark = OasPalette(
    bg: Color(0xFF171210),
    surface: Color(0xFF281E19),
    surfaceAlt: Color(0xFF221A16),
    line: Color(0xFF3A2C24),
    ink: Color(0xFFF1E9DC),
    inkSoft: Color(0xFFD8C9B8),
    inkDim: Color(0xFFA99585),
    vermilion: Color(0xFFC0493A),
    vermilionHi: Color(0xFFDC5B49),
    gold: Color(0xFFC89A55),
    goldHi: Color(0xFFEAC98A),
    jade: Color(0xFF4CAE86),
    purple: Color(0xFF8B6BC0),
  );
}

extension OasPaletteContext on BuildContext {
  OasPalette get oas => Theme.of(this).brightness == Brightness.dark
      ? OasPalette.dark
      : OasPalette.light;
}

/// 圆角：卡片 16 / 按钮 12 / 胶囊 999
const double kRadiusCard = 16;
const double kRadiusBtn = 12;
const double kRadiusPill = 999;

/// 字体：标题走宋体（明朝体感），正文走系统黑体
abstract final class OasFont {
  static const String serif = 'serif';

  /// Android 上 'serif' 一般解析到 Noto Serif CJK；下面是兜底链
  static const List<String> serifFallback = <String>[
    'Noto Serif CJK SC',
    'Noto Serif SC',
    'Source Han Serif SC',
    'SimSun',
    'Songti SC',
  ];

  /// 标题：粗、字距 2
  static TextStyle heading({
    double size = 21,
    Color? color,
    double spacing = 2,
    FontWeight weight = FontWeight.w700,
  }) {
    return TextStyle(
      fontFamily: serif,
      fontFamilyFallback: serifFallback,
      fontSize: size,
      height: 1.25,
      fontWeight: weight,
      letterSpacing: spacing,
      color: color,
    );
  }

  /// 卡片标题 / 数字（宋体，字距小一点）
  static TextStyle song({
    double size = 15,
    Color? color,
    double spacing = 0.5,
    FontWeight weight = FontWeight.w600,
  }) {
    return TextStyle(
      fontFamily: serif,
      fontFamilyFallback: serifFallback,
      fontSize: size,
      height: 1.3,
      fontWeight: weight,
      letterSpacing: spacing,
      color: color,
    );
  }

  /// 日志等宽字
  static const TextStyle mono = TextStyle(
    fontFamily: 'monospace',
    fontSize: 11.5,
    height: 1.45,
  );
}

/// 主题模式开关：默认和纸浅色（我的 → 外观 切换）
final ValueNotifier<ThemeMode> oasThemeMode =
    ValueNotifier<ThemeMode>(ThemeMode.light);

ThemeData buildOasTheme(Brightness brightness) {
  final bool isDark = brightness == Brightness.dark;
  final OasPalette p = isDark ? OasPalette.dark : OasPalette.light;

  final ColorScheme scheme = ColorScheme.fromSeed(
    seedColor: p.vermilion,
    brightness: brightness,
  ).copyWith(
    primary: p.vermilion,
    onPrimary: const Color(0xFFFFF7EE),
    secondary: p.gold,
    onSecondary: const Color(0xFF2A201A),
    surface: p.surface,
    onSurface: p.ink,
    surfaceContainerHighest: p.surfaceAlt,
    outline: p.line,
    outlineVariant: p.line,
    error: p.vermilion,
    onError: const Color(0xFFFFF7EE),
  );

  final ThemeData base = ThemeData(brightness: brightness, useMaterial3: true);
  final TextTheme tt = base.textTheme
      .apply(bodyColor: p.ink, displayColor: p.ink)
      .copyWith(
        bodyLarge: TextStyle(fontSize: 15, height: 1.45, color: p.ink),
        bodyMedium: TextStyle(fontSize: 13, height: 1.45, color: p.ink),
        bodySmall: TextStyle(fontSize: 11, height: 1.4, color: p.inkDim),
        titleMedium: OasFont.song(size: 16, color: p.ink),
        titleLarge: OasFont.heading(size: 20, color: p.ink),
      );

  return base.copyWith(
    colorScheme: scheme,
    scaffoldBackgroundColor: p.bg,
    canvasColor: p.bg,
    textTheme: tt,
    dividerColor: p.line,
    dividerTheme: DividerThemeData(color: p.line, thickness: 1, space: 1),
    appBarTheme: AppBarTheme(
      backgroundColor: p.bg,
      surfaceTintColor: Colors.transparent,
      foregroundColor: p.ink,
      elevation: 0,
      centerTitle: false,
      titleTextStyle: OasFont.heading(size: 19, color: p.ink),
    ),
    cardTheme: CardTheme(
      color: p.surface,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(kRadiusCard),
        side: BorderSide(color: p.line),
      ),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: p.ink,
      contentTextStyle: TextStyle(color: p.bg, fontSize: 12.5),
      behavior: SnackBarBehavior.floating,
    ),
    progressIndicatorTheme: ProgressIndicatorThemeData(
      color: p.vermilion,
      linearTrackColor: p.surfaceAlt,
      linearMinHeight: 6,
    ),
    splashColor: p.gold.withOpacity(0.08),
    highlightColor: p.gold.withOpacity(0.04),
  );
}
