import 'dart:math' as math;
import 'dart:ui' show PathMetric;

import 'package:flutter/material.dart';

import 'skin_store.dart';
import 'theme.dart';

export 'skin_store.dart';
export 'theme.dart';

/// ── 共用小组件：卡片 / 页头 / 徽记 / 胶囊 / 按钮 ──
/// 卡片自动随当前阴阳师庭院皮肤呈现半透磨砂质感。

class OasCard extends StatelessWidget {
  const OasCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(14),
    this.onTap,
    this.radius = kRadiusCard,
    this.color,
    this.gradient,
    this.border = true,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final VoidCallback? onTap;
  final double radius;
  final Color? color;
  final Gradient? gradient;
  final bool border;

  @override
  Widget build(BuildContext context) {
    final OasCourtyardSkin skin = OasSkinController.i.current;
    final Widget box = Container(
      padding: padding,
      decoration: BoxDecoration(
        color: gradient == null ? (color ?? skin.cardBg) : null,
        gradient: gradient,
        borderRadius: BorderRadius.circular(radius),
        border: border ? Border.all(color: skin.cardBorder) : null,
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: Colors.black.withOpacity(skin.isDark ? 0.24 : 0.08),
            blurRadius: 12,
            offset: const Offset(0, 4),
          ),
        ],
      ),
      child: child,
    );
    if (onTap == null) return box;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: box,
    );
  }
}

/// 阴阳师官方立绘/式神头像徽记（替代旧版单字方块）
class OasAssetBadge extends StatelessWidget {
  const OasAssetBadge({
    super.key,
    required this.asset,
    this.size = 34,
    this.radius = 10,
    this.transparentCutout = false,
  });

  final String asset;
  final double size;
  final double radius;
  final bool transparentCutout;

  @override
  Widget build(BuildContext context) {
    final bool isCutout = transparentCutout ||
        asset.contains('/dock_') ||
        asset.contains('/cut_');
    if (isCutout) {
      return SizedBox(
        width: size,
        height: size,
        child: Image.asset(
          asset,
          fit: BoxFit.contain,
          filterQuality: FilterQuality.medium,
        ),
      );
    }
    final OasCourtyardSkin skin = OasSkinController.i.current;
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius),
        border: Border.all(color: skin.gold.withOpacity(0.65), width: 1.2),
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: Colors.black.withOpacity(0.28),
            blurRadius: 6,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      clipBehavior: Clip.antiAlias,
      child: Image.asset(
        asset,
        fit: BoxFit.cover,
        filterQuality: FilterQuality.medium,
      ),
    );
  }
}

/// 页头：官方立绘徽记（或兜底朱印） + 宋体标题 + 副标题 + 右侧状态胶囊
class OasPageHeader extends StatelessWidget {
  const OasPageHeader({
    super.key,
    required this.badge,
    required this.title,
    required this.subtitle,
    this.trailing,
    this.badgeColor,
    this.badgeAsset,
  });

  final String badge;
  final String title;
  final String subtitle;
  final Widget? trailing;
  final Color? badgeColor;
  final String? badgeAsset;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return Row(
      children: <Widget>[
        if (badgeAsset != null && badgeAsset!.isNotEmpty)
          OasAssetBadge(asset: badgeAsset!, size: 38, radius: 10)
        else
          OasBadge(text: badge, color: badgeColor ?? p.vermilion, size: 34),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(title, style: OasFont.heading(size: 21, color: p.ink)),
              const SizedBox(height: 2),
              Text(
                subtitle,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11.5, color: p.inkDim, height: 1.25),
              ),
            ],
          ),
        ),
        if (trailing != null) ...<Widget>[const SizedBox(width: 8), trailing!],
      ],
    );
  }
}

/// 朱印式方块徽记（御 / 魂 / 觉 / 活 …）
class OasBadge extends StatelessWidget {
  const OasBadge({
    super.key,
    required this.text,
    required this.color,
    this.size = 32,
    this.radius = 9,
  });

  final String text;
  final Color color;
  final double size;
  final double radius;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: <Color>[
            Color.lerp(color, Colors.white, 0.16) ?? color,
            color,
          ],
        ),
        borderRadius: BorderRadius.circular(radius),
        boxShadow: <BoxShadow>[
          BoxShadow(
            color: color.withOpacity(0.20),
            blurRadius: 6,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Text(
        text,
        style: TextStyle(
          fontFamily: OasFont.serif,
          fontFamilyFallback: OasFont.serifFallback,
          fontSize: size * 0.46,
          height: 1,
          fontWeight: FontWeight.w700,
          color: const Color(0xFFFFF7EE),
        ),
      ),
    );
  }
}

/// 小胶囊：状态点 + 文字
class OasPill extends StatelessWidget {
  const OasPill({
    super.key,
    required this.text,
    this.color,
    this.dot = false,
    this.outlined = false,
    this.onTap,
  });

  final String text;
  final Color? color;
  final bool dot;
  final bool outlined;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    final Color c = color ?? p.inkDim;
    final Widget label = Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: outlined ? Colors.transparent : c.withOpacity(0.10),
        borderRadius: BorderRadius.circular(kRadiusPill),
        border: outlined ? Border.all(color: p.line) : null,
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (dot) ...<Widget>[
            Container(
              width: 6,
              height: 6,
              decoration: BoxDecoration(color: c, shape: BoxShape.circle),
            ),
            const SizedBox(width: 4),
          ],
          Text(
            text,
            style: TextStyle(
              fontSize: 11,
              height: 1.1,
              fontWeight: FontWeight.w600,
              color: c,
            ),
          ),
        ],
      ),
    );
    if (onTap == null) return label;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: label,
    );
  }
}

/// 分区标题 + 右侧说明
class OasSectionHeader extends StatelessWidget {
  const OasSectionHeader({super.key, required this.title, this.trailing});

  final String title;
  final String? trailing;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return Row(
      children: <Widget>[
        Text(
          title,
          style: TextStyle(
            fontSize: 13.5,
            height: 1.2,
            fontWeight: FontWeight.w700,
            letterSpacing: 1,
            color: p.ink,
          ),
        ),
        const Spacer(),
        if (trailing != null)
          Text(
            trailing!,
            style: TextStyle(fontSize: 11, color: p.inkDim),
          ),
      ],
    );
  }
}

/// 唯一的主按钮：漆红渐变，文字字距拉大
class OasPrimaryButton extends StatelessWidget {
  const OasPrimaryButton({
    super.key,
    required this.label,
    this.suffix,
    this.onTap,
    this.height = 48,
  });

  final String label;
  final String? suffix;
  final VoidCallback? onTap;
  final double height;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Container(
        height: height,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: const Alignment(-0.6, -1),
            end: const Alignment(0.6, 1),
            colors: <Color>[
              Color.lerp(p.vermilion, Colors.white, 0.06) ?? p.vermilion,
              p.vermilion,
              Color.lerp(p.vermilion, Colors.black, 0.20) ?? p.vermilion,
            ],
          ),
          borderRadius: BorderRadius.circular(kRadiusBtn),
          boxShadow: <BoxShadow>[
            BoxShadow(
              color: p.vermilion.withOpacity(0.28),
              blurRadius: 12,
              offset: const Offset(0, 4),
            ),
          ],
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(
              label,
              style: OasFont.song(
                size: 15.5,
                spacing: 3,
                weight: FontWeight.w600,
                color: const Color(0xFFFFF7EE),
              ),
            ),
            if (suffix != null) ...<Widget>[
              const SizedBox(width: 8),
              Text(
                suffix!,
                style: TextStyle(
                  fontSize: 11.5,
                  color: const Color(0xFFFFF7EE).withOpacity(0.72),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// 次级按钮（暂停 / 停止 / 看画面 …）：淡色底 + 同色描边
class OasSoftButton extends StatelessWidget {
  const OasSoftButton({
    super.key,
    required this.label,
    this.color,
    this.onTap,
    this.height = 40,
  });

  final String label;
  final Color? color;
  final VoidCallback? onTap;
  final double height;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    final Color c = color ?? p.inkSoft;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Container(
        height: height,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: c.withOpacity(0.09),
          borderRadius: BorderRadius.circular(kRadiusBtn),
          border: Border.all(color: c.withOpacity(0.30)),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 12.5,
            height: 1.1,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
            color: c,
          ),
        ),
      ),
    );
  }
}

/// 三格状态卡里的一格：大数字 + 小单位 + 底部说明
class OasStatCell extends StatelessWidget {
  const OasStatCell({
    super.key,
    required this.value,
    required this.label,
    this.unit,
    this.valueColor,
  });

  final String value;
  final String label;
  final String? unit;
  final Color? valueColor;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return OasCard(
      padding: const EdgeInsets.fromLTRB(10, 9, 10, 9),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text.rich(
            TextSpan(
              children: <InlineSpan>[
                TextSpan(
                  text: value,
                  style: TextStyle(
                    fontFamily: OasFont.serif,
                    fontFamilyFallback: OasFont.serifFallback,
                    fontSize: 17,
                    height: 1.15,
                    fontWeight: FontWeight.w700,
                    color: valueColor ?? p.ink,
                  ),
                ),
                if (unit != null)
                  TextSpan(
                    text: ' $unit',
                    style: TextStyle(
                      fontFamily: null,
                      fontSize: 10.5,
                      fontWeight: FontWeight.w400,
                      color: p.inkDim,
                    ),
                  ),
              ],
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          const SizedBox(height: 5),
          Text(
            label,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 10.5, color: p.inkDim, height: 1.1),
          ),
        ],
      ),
    );
  }
}

/// 据点四宫格里的一格
class OasQuickTile extends StatelessWidget {
  const OasQuickTile({
    super.key,
    required this.icon,
    required this.label,
    this.iconColor,
    this.onTap,
  });

  final IconData icon;
  final String label;
  final Color? iconColor;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Container(
            width: 36,
            height: 36,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: p.surface,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: p.line),
            ),
            child: Icon(icon, size: 17, color: iconColor ?? p.gold),
          ),
          const SizedBox(height: 6),
          Text(
            label,
            style: TextStyle(fontSize: 11, color: p.inkSoft, height: 1.1),
          ),
        ],
      ),
    );
  }
}

/// 列表行：官方头像徽记 + 主副标题 + 右侧尾部
class OasRowItem extends StatelessWidget {
  const OasRowItem({
    super.key,
    required this.badge,
    required this.badgeColor,
    required this.title,
    this.subtitle,
    this.trailing,
    this.onTap,
    this.badgeAsset,
  });

  final String badge;
  final Color badgeColor;
  final String title;
  final String? subtitle;
  final Widget? trailing;
  final VoidCallback? onTap;
  final String? badgeAsset;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return OasCard(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      onTap: onTap,
      child: Row(
        children: <Widget>[
          if (badgeAsset != null && badgeAsset!.isNotEmpty)
            OasAssetBadge(asset: badgeAsset!, size: 34, radius: 9)
          else
            OasBadge(text: badge, color: badgeColor, size: 30, radius: 8),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 13,
                    height: 1.2,
                    fontWeight: FontWeight.w600,
                    color: p.ink,
                  ),
                ),
                if (subtitle != null) ...<Widget>[
                  const SizedBox(height: 3),
                  Text(
                    subtitle!,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 10.5,
                      height: 1.15,
                      color: p.inkDim,
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (trailing != null) ...<Widget>[const SizedBox(width: 8), trailing!],
        ],
      ),
    );
  }
}

/// 紧凑开关（跟样图一样小一号）
class OasSwitch extends StatelessWidget {
  const OasSwitch({super.key, required this.value, this.onChanged});

  final bool value;
  final ValueChanged<bool>? onChanged;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return Transform.scale(
      scale: 0.74,
      child: Switch(
        value: value,
        onChanged: onChanged,
        activeColor: const Color(0xFFFFF7EE),
        activeTrackColor: Color.lerp(p.vermilion, p.surface, 0.30) ?? p.vermilion,
        inactiveThumbColor: p.surface,
        inactiveTrackColor: p.line,
        trackOutlineColor: const WidgetStatePropertyAll<Color>(Colors.transparent),
        materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
    );
  }
}

/// 细分割线
class OasDivider extends StatelessWidget {
  const OasDivider({super.key, this.indent = 0, this.endIndent = 0});

  final double indent;
  final double endIndent;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return Padding(
      padding: EdgeInsets.only(left: indent, right: endIndent),
      child: Divider(height: 1, thickness: 1, color: p.line),
    );
  }
}

/// 轻提示（Phase 1 占位用：说明哪一步会接真接口）
abstract final class OasToast {
  static void show(BuildContext context, String text) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(text),
          duration: const Duration(milliseconds: 1400),
          margin: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        ),
      );
  }
}

/// 虚线描边（监控页识别框）
class OasDashedBorder extends StatelessWidget {
  const OasDashedBorder({
    super.key,
    required this.child,
    required this.color,
    this.radius = 8,
    this.dash = 5,
    this.gap = 4,
    this.strokeWidth = 1.2,
  });

  final Widget child;
  final Color color;
  final double radius;
  final double dash;
  final double gap;
  final double strokeWidth;

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      painter: _DashedPainter(
        color: color,
        radius: radius,
        dash: dash,
        gap: gap,
        strokeWidth: strokeWidth,
      ),
      child: child,
    );
  }
}

class _DashedPainter extends CustomPainter {
  _DashedPainter({
    required this.color,
    required this.radius,
    required this.dash,
    required this.gap,
    required this.strokeWidth,
  });

  final Color color;
  final double radius;
  final double dash;
  final double gap;
  final double strokeWidth;

  @override
  void paint(Canvas canvas, Size size) {
    final Paint paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth;
    final Path path = Path()
      ..addRRect(
        RRect.fromRectAndRadius(
          Offset.zero & size,
          Radius.circular(radius),
        ),
      );
    for (final PathMetric metric in path.computeMetrics()) {
      double d = 0;
      while (d < metric.length) {
        final double next = math.min(d + dash, metric.length);
        canvas.drawPath(metric.extractPath(d, next), paint);
        d = next + gap;
      }
    }
  }

  @override
  bool shouldRepaint(covariant _DashedPainter oldDelegate) {
    return oldDelegate.color != color ||
        oldDelegate.radius != radius ||
        oldDelegate.dash != dash ||
        oldDelegate.gap != gap ||
        oldDelegate.strokeWidth != strokeWidth;
  }
}

/// AI 自愈状态条（纯展示）。
///
/// 数据来自桥接 `GET /ai/health`，只有四个字段：enabled / mode / model / has_key。
/// 读不到就直说「状态未知」，不拿开关字样糊弄——用户明确要求禁止虚报。
class OasAiStatusStrip extends StatelessWidget {
  const OasAiStatusStrip({
    super.key,
    this.data,
    this.error,
    this.loading = false,
  });

  /// `/ai/health` 的原始返回；null 表示还没读到。
  final Map<String, dynamic>? data;

  /// 读取失败的原文；非 null 时优先于 [data] 展示。
  final String? error;

  final bool loading;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    final Map<String, dynamic>? d = data;

    final String title;
    final String detail;
    final Color dot;
    final Color tone;

    if (error != null) {
      title = 'AI 修复状态未知';
      detail = '桥接没连上，读不到 /ai/health';
      dot = p.vermilion;
      tone = p.inkDim;
    } else if (d == null || loading) {
      title = 'AI 修复';
      detail = '正在读取桥接状态…';
      dot = p.inkDim;
      tone = p.inkDim;
    } else if (d['enabled'] != true) {
      title = 'AI 修复 · 已关闭';
      detail = '在「我的 → AI 修复设置」里开启后才会介入';
      dot = p.inkDim;
      tone = p.inkDim;
    } else if (d['has_key'] != true) {
      title = 'AI 修复 · 缺 Key';
      detail = '开关已开，但没配 DEEPSEEK_API_KEY，出错时不会真的介入';
      dot = p.vermilion;
      tone = p.vermilion;
    } else {
      final String mode = (d['mode'] ?? 'auto').toString();
      final String model = (d['model'] ?? 'deepseek').toString();
      title = 'AI 修复 · ${mode == 'auto' ? '自动执行' : '仅观察'}';
      detail = '$model · 失败时先查知识库，再问模型';
      dot = p.jade;
      tone = p.inkSoft;
    }

    return OasCard(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 11),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(shape: BoxShape.circle, color: dot),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(title, style: OasFont.song(size: 14, color: p.ink)),
                const SizedBox(height: 3),
                Text(
                  detail,
                  style: TextStyle(fontSize: 11, height: 1.3, color: tone),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
