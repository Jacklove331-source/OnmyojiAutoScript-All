import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'api.dart';
import 'pages/home.dart';
import 'pages/me.dart';
import 'pages/monitor.dart';
import 'pages/scripts.dart';
import 'skin_store.dart';
import 'theme.dart';

/// 应用外壳：阴阳师 5 款可换庭院皮肤全屏背景 + 4 格官方纯人物透明底栏
/// （庭院·不知火 / 绘卷·花鸟卷 / 监控日志合一·抱竹茨球 / 我的·晴明）
class OasShell extends StatefulWidget {
  const OasShell({super.key});

  /// 让别的页面（如庭院快捷入口）能切到指定页签：0庭院 1绘卷 2监控 3我的
  static void Function(int index)? goTab;

  @override
  State<OasShell> createState() => _OasShellState();
}

class _OasShellState extends State<OasShell> with WidgetsBindingObserver {
  int _index = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    OasShell.goTab = (int index) {
      if (!mounted) return;
      final int clamped = index.clamp(0, _OasBottomBar._items.length - 1);
      setState(() => _index = clamped);
    };
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<OasSkinState>(
      valueListenable: OasSkinController.i.state,
      builder: (BuildContext context, OasSkinState skinState, Widget? _) {
        final OasCourtyardSkin skin = skinState.skin;
        final bool isDark = skin.isDark;

        return AnnotatedRegion<SystemUiOverlayStyle>(
          value:
              (isDark ? SystemUiOverlayStyle.light : SystemUiOverlayStyle.dark)
                  .copyWith(
            statusBarColor: Colors.transparent,
            systemNavigationBarColor: skin.dockBg,
            systemNavigationBarIconBrightness:
                isDark ? Brightness.light : Brightness.dark,
          ),
          child: Stack(
            fit: StackFit.expand,
            children: <Widget>[
              // 1. 全屏官方庭院皮肤 / 用户自定义相册壁纸
              _CourtyardBackdrop(skinState: skinState),
              // 2. 透明 Scaffold + 悬浮磨砂 4 格底栏
              Scaffold(
                backgroundColor: Colors.transparent,
                extendBody: true,
                resizeToAvoidBottomInset: false,
                body: ValueListenableBuilder<String>(
                  valueListenable: OasApi.i.activeConfigChanges,
                  builder: (BuildContext context, String config, Widget? _) {
                    final List<Widget> pages = <Widget>[
                      HomePage(key: ValueKey<String>(config)),
                      ScriptsPage(key: ValueKey<String>(config)),
                      const MonitorPage(),
                      const MePage(),
                    ];
                    return IndexedStack(
                      index: _index,
                      children: <Widget>[
                        for (int i = 0; i < pages.length; i++)
                          TickerMode(
                            enabled: i == _index,
                            child: Padding(
                              padding: const EdgeInsets.only(bottom: 76),
                              child: pages[i],
                            ),
                          ),
                      ],
                    );
                  },
                ),
                bottomNavigationBar: _OasBottomBar(
                  index: _index,
                  skin: skin,
                  onTap: (int i) => setState(() => _index = i),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _CourtyardBackdrop extends StatelessWidget {
  const _CourtyardBackdrop({required this.skinState});

  final OasSkinState skinState;

  @override
  Widget build(BuildContext context) {
    final OasCourtyardSkin skin = skinState.skin;
    Widget image;
    if (skinState.hasCustomWallpaper &&
        File(skinState.customWallpaperPath!).existsSync()) {
      image = Image.file(
        File(skinState.customWallpaperPath!),
        fit: BoxFit.cover,
        gaplessPlayback: true,
      );
    } else {
      image = Image.asset(
        skin.assetPath,
        fit: BoxFit.cover,
        gaplessPlayback: true,
      );
    }

    return Stack(
      fit: StackFit.expand,
      children: <Widget>[
        AnimatedSwitcher(
          duration: const Duration(milliseconds: 350),
          child: SizedBox.expand(
            key: ValueKey<String>(
              skinState.hasCustomWallpaper
                  ? skinState.customWallpaperPath!
                  : skin.id,
            ),
            child: image,
          ),
        ),
        DecoratedBox(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              stops: const <double>[0.0, 0.48, 1.0],
              colors: <Color>[
                skin.overlayTop,
                skin.overlayMid,
                skin.overlayBottom,
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _OasBottomBar extends StatelessWidget {
  const _OasBottomBar({
    required this.index,
    required this.skin,
    required this.onTap,
  });

  final int index;
  final OasCourtyardSkin skin;
  final ValueChanged<int> onTap;

  static const List<({String asset, String label})> _items =
      <({String asset, String label})>[
    (asset: 'assets/onmyoji/dock_home.png', label: '庭院'),
    (asset: 'assets/onmyoji/dock_tasks.png', label: '绘卷'),
    (asset: 'assets/onmyoji/dock_monitor.png', label: '监控'),
    (asset: 'assets/onmyoji/dock_me.png', label: '我的'),
  ];

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
        child: Container(
          height: 66,
          decoration: BoxDecoration(
            color: skin.dockBg,
            borderRadius: BorderRadius.circular(22),
            border: Border.all(color: skin.cardBorder, width: 1.1),
            boxShadow: <BoxShadow>[
              BoxShadow(
                color: Colors.black.withOpacity(skin.isDark ? 0.45 : 0.16),
                blurRadius: 18,
                offset: const Offset(0, 6),
              ),
            ],
          ),
          child: Row(
            children: <Widget>[
              for (int i = 0; i < _items.length; i++)
                Expanded(child: _item(p, i)),
            ],
          ),
        ),
      ),
    );
  }

  Widget _item(OasPalette p, int i) {
    final bool selected = index == i;
    final Color labelColor = selected ? skin.gold : p.inkDim;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () => onTap(i),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 220),
        margin: const EdgeInsets.symmetric(horizontal: 5, vertical: 5),
        decoration: BoxDecoration(
          color: selected
              ? skin.accent.withOpacity(skin.isDark ? 0.22 : 0.13)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(16),
        ),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            AnimatedScale(
              duration: const Duration(milliseconds: 220),
              curve: Curves.easeOutBack,
              scale: selected ? 1.18 : 0.96,
              child: Container(
                width: 34,
                height: 34,
                decoration: selected
                    ? BoxDecoration(
                        shape: BoxShape.circle,
                        boxShadow: <BoxShadow>[
                          BoxShadow(
                            color: skin.gold.withOpacity(0.45),
                            blurRadius: 10,
                            spreadRadius: 0.5,
                          ),
                        ],
                      )
                    : null,
                child: Image.asset(
                  _items[i].asset,
                  fit: BoxFit.contain,
                  filterQuality: FilterQuality.medium,
                ),
              ),
            ),
            const SizedBox(height: 2),
            Text(
              _items[i].label,
              style: TextStyle(
                fontSize: 10.5,
                height: 1.05,
                letterSpacing: 0.6,
                fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                color: labelColor,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
