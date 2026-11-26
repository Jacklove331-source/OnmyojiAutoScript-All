import 'package:flutter/material.dart';

import 'avatar_store.dart';
import 'widgets.dart';

/// 头像徽记：庭院账户卡与「我的」页共用这一个组件，读的是 [OasAvatarStore.i.state]。
///
/// 改头像只需要写 store，两处头像会在同一帧内一起变，不必各自 setState。
class OasAvatarBadge extends StatelessWidget {
  const OasAvatarBadge({
    super.key,
    required this.size,
    this.borderColor,
    this.borderWidth = 1.4,
    this.onTap,
  });

  final double size;
  final Color? borderColor;
  final double borderWidth;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return ValueListenableBuilder<OasAvatar>(
      valueListenable: OasAvatarStore.i.state,
      builder: (BuildContext context, OasAvatar avatar, Widget? _) {
        final Widget badge = Container(
          width: size,
          height: size,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: p.surfaceAlt.withOpacity(0.5),
            border: Border.all(
              color: borderColor ?? p.gold.withOpacity(0.55),
              width: borderWidth,
            ),
          ),
          child: Padding(
            padding: const EdgeInsets.all(2),
            child: Image.asset(
              avatar.imageAsset,
              fit: BoxFit.contain,
              errorBuilder: (_, __, ___) => Text(
                avatar.char,
                style: TextStyle(
                  fontFamily: OasFont.serif,
                  fontFamilyFallback: OasFont.serifFallback,
                  fontSize: size * 0.38,
                  height: 1,
                  fontWeight: FontWeight.w700,
                  color: p.goldHi,
                ),
              ),
            ),
          ),
        );
        if (onTap == null) return badge;
        return GestureDetector(onTap: onTap, child: badge);
      },
    );
  }
}

/// 公共头像选择器（庭院账户卡与「我的」页共用）：选中即保存，两处同步生效。
Future<void> showOasAvatarPicker(BuildContext context) async {
  final BuildContext host = context;
  await showModalBottomSheet<void>(
    context: context,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
    ),
    builder: (BuildContext c) {
      final OasPalette p = c.oas;
      return Padding(
        padding: const EdgeInsets.fromLTRB(18, 16, 18, 22),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Text('选择头像', style: OasFont.song(size: 16, color: p.ink)),
            const SizedBox(height: 4),
            Text(
              '游戏内素材头像，庭院与「我的」同步生效，不需要相册权限',
              style: TextStyle(fontSize: 11, color: p.inkDim),
            ),
            const SizedBox(height: 14),
            ValueListenableBuilder<OasAvatar>(
              valueListenable: OasAvatarStore.i.state,
              builder: (BuildContext context, OasAvatar active, Widget? _) {
                return GridView.count(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  crossAxisCount: 4,
                  mainAxisSpacing: 10,
                  crossAxisSpacing: 10,
                  childAspectRatio: 0.82,
                  children: <Widget>[
                    for (final OasAvatar item in OasAvatar.all)
                      InkWell(
                        onTap: () async {
                          final OasAvatarSaveResult result =
                              await OasAvatarStore.i.select(item);
                          if (!c.mounted) return;
                          Navigator.of(c).pop();
                          if (!host.mounted) return;
                          // 只有「真的回滚了」才说失败；被更新的选择覆盖时静默，
                          // 否则连点会连弹几条互相矛盾的 toast。
                          final String? message = switch (result) {
                            OasAvatarSaveResult.saved =>
                              '头像已换成「${item.label}」',
                            OasAvatarSaveResult.failedRolledBack =>
                              '保存失败，头像已恢复为上次保存的那款',
                            OasAvatarSaveResult.superseded => null,
                          };
                          if (message != null) OasToast.show(host, message);
                        },
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: <Widget>[
                            OasAvatarBadge(
                              size: 52,
                              borderWidth: item.id == active.id ? 2 : 1,
                              borderColor: item.id == active.id
                                  ? p.gold
                                  : p.inkSoft.withOpacity(0.3),
                            ),
                            const SizedBox(height: 6),
                            Text(
                              item.label,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: item.id == active.id
                                    ? FontWeight.w700
                                    : FontWeight.w400,
                                color: item.id == active.id ? p.gold : p.inkSoft,
                              ),
                            ),
                          ],
                        ),
                      ),
                  ],
                );
              },
            ),
          ],
        ),
      );
    },
  );
}
