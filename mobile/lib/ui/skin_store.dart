import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';

import 'theme.dart';

class OasCourtyardSkin {
  final String id;
  final String name;
  final String subtitle;
  final String assetPath;
  final bool isDark;
  final Color accent;
  final Color gold;
  final Color overlayTop;
  final Color overlayMid;
  final Color overlayBottom;
  final Color cardBg;
  final Color cardBorder;
  final Color dockBg;

  const OasCourtyardSkin({
    required this.id,
    required this.name,
    required this.subtitle,
    required this.assetPath,
    required this.isDark,
    required this.accent,
    required this.gold,
    required this.overlayTop,
    required this.overlayMid,
    required this.overlayBottom,
    required this.cardBg,
    required this.cardBorder,
    required this.dockBg,
  });

  static const List<OasCourtyardSkin> all = [
    OasCourtyardSkin(
      id: 'night_sakura',
      name: '夜樱满月庭',
      subtitle: '紫金夜樱 · 经典庭院',
      assetPath: 'assets/onmyoji/skin_1_night_sakura.jpg',
      isDark: true,
      accent: Color(0xFFE04F5F),
      gold: Color(0xFFF3C87B),
      overlayTop: Color(0x8A120A1E),
      overlayMid: Color(0x7A160D26),
      overlayBottom: Color(0xE810081B),
      cardBg: Color(0xCC1E1430),
      cardBorder: Color(0x55F3C87B),
      dockBg: Color(0xEB150C24),
    ),
    OasCourtyardSkin(
      id: 'spring_courtyard',
      name: '暖春庭院扫除',
      subtitle: '官方和风 · 暖阳治愈',
      assetPath: 'assets/onmyoji/skin_2_spring_courtyard.jpg',
      isDark: false,
      accent: Color(0xFFB83B2E),
      gold: Color(0xFFB0803C),
      overlayTop: Color(0x99FBF3E4),
      overlayMid: Color(0x88F8EDDB),
      overlayBottom: Color(0xE8F6EBD8),
      cardBg: Color(0xE6FFFDF7),
      cardBorder: Color(0x99D4AF6A),
      dockBg: Color(0xF0FFFBF0),
    ),
    OasCourtyardSkin(
      id: 'crimson_sakura',
      name: '绯樱幻夜庭',
      subtitle: '官方绯樱 · 暗绯鎏金',
      assetPath: 'assets/onmyoji/skin_3_crimson_sakura.jpg',
      isDark: true,
      accent: Color(0xFFE11D48),
      gold: Color(0xFFF5C17A),
      overlayTop: Color(0x8C1E0B14),
      overlayMid: Color(0x7A240C17),
      overlayBottom: Color(0xEB19070F),
      cardBg: Color(0xCC2A111D),
      cardBorder: Color(0x59F43F5E),
      dockBg: Color(0xEB1D0A13),
    ),
    OasCourtyardSkin(
      id: 'jade_garden',
      name: '苍翠茶花庭',
      subtitle: '青绿水墨 · 萤火清雅',
      assetPath: 'assets/onmyoji/skin_4_jade_garden.jpg',
      isDark: true,
      accent: Color(0xFF10B981),
      gold: Color(0xFFA7F3D0),
      overlayTop: Color(0x8A081916),
      overlayMid: Color(0x780C221D),
      overlayBottom: Color(0xEB071412),
      cardBg: Color(0xCC0F2621),
      cardBorder: Color(0x5934D399),
      dockBg: Color(0xEB091A17),
    ),
    OasCourtyardSkin(
      id: 'heian_room',
      name: '平安京屏风室',
      subtitle: '经典和室 · 沉稳墨金',
      assetPath: 'assets/onmyoji/skin_5_heian_room.jpg',
      isDark: true,
      accent: Color(0xFFD97706),
      gold: Color(0xFFE8C27C),
      overlayTop: Color(0x9416120E),
      overlayMid: Color(0x821B1611),
      overlayBottom: Color(0xED130F0B),
      cardBg: Color(0xCC241D17),
      cardBorder: Color(0x59E8C27C),
      dockBg: Color(0xEB18130E),
    ),
  ];

  static OasCourtyardSkin byId(String id) {
    for (final skin in all) {
      if (skin.id == id) return skin;
    }
    return all.first;
  }
}

class OasSkinState {
  final String skinId;
  final bool randomOnLaunch;
  final String? customWallpaperPath;

  const OasSkinState({
    required this.skinId,
    this.randomOnLaunch = false,
    this.customWallpaperPath,
  });

  OasCourtyardSkin get skin => OasCourtyardSkin.byId(skinId);

  bool get hasCustomWallpaper =>
      customWallpaperPath != null && customWallpaperPath!.isNotEmpty;
}

class OasSkinController {
  OasSkinController._();

  static final OasSkinController i = OasSkinController._();

  final ValueNotifier<OasSkinState> state = ValueNotifier<OasSkinState>(
    const OasSkinState(skinId: 'night_sakura', randomOnLaunch: false),
  );

  final Random _random = Random();

  OasCourtyardSkin get current => state.value.skin;

  Future<void> load() async {
    var next = const OasSkinState(skinId: 'night_sakura', randomOnLaunch: false);

    try {
      final dir = await getApplicationSupportDirectory();
      final file = File('${dir.path}/courtyard_skin.json');

      if (await file.exists()) {
        final raw = await file.readAsString();
        final decoded = jsonDecode(raw);

        if (decoded is Map<String, dynamic>) {
          final skinId = decoded['skinId'] as String? ?? next.skinId;
          final randomOnLaunch =
              decoded['randomOnLaunch'] as bool? ?? next.randomOnLaunch;
          final customRaw = decoded['customWallpaperPath'] as String?;
          final customWallpaperPath = (customRaw == null || customRaw.isEmpty)
              ? null
              : customRaw;

          if (randomOnLaunch && customWallpaperPath == null) {
            final skin = _pickRandomSkin();
            next = OasSkinState(
              skinId: skin.id,
              randomOnLaunch: true,
            );
          } else {
            next = OasSkinState(
              skinId: skinId,
              randomOnLaunch: randomOnLaunch,
              customWallpaperPath: customWallpaperPath,
            );
          }
        }
      }
    } catch (_) {
      next = const OasSkinState(skinId: 'night_sakura', randomOnLaunch: false);
    }

    state.value = next;
    oasThemeMode.value =
        next.skin.isDark ? ThemeMode.dark : ThemeMode.light;
  }

  Future<void> selectSkin(String id) async {
    final skin = OasCourtyardSkin.byId(id);
    state.value = OasSkinState(
      skinId: skin.id,
      randomOnLaunch: state.value.randomOnLaunch,
    );
    oasThemeMode.value =
        skin.isDark ? ThemeMode.dark : ThemeMode.light;
    await _persist();
  }

  Future<OasCourtyardSkin> randomSkin() async {
    final picked = _pickDifferentSkin(state.value.skinId);
    state.value = OasSkinState(
      skinId: picked.id,
      randomOnLaunch: state.value.randomOnLaunch,
    );
    oasThemeMode.value =
        picked.isDark ? ThemeMode.dark : ThemeMode.light;
    await _persist();
    return picked;
  }

  Future<void> setRandomOnLaunch(bool enabled) async {
    state.value = OasSkinState(
      skinId: state.value.skinId,
      randomOnLaunch: enabled,
      customWallpaperPath: state.value.customWallpaperPath,
    );
    await _persist();
  }

  Future<bool> setCustomWallpaperFromFile(String sourcePath) async {
    try {
      final source = File(sourcePath);
      if (!await source.exists()) return false;

      final dir = await getApplicationSupportDirectory();
      if (!await dir.exists()) {
        await dir.create(recursive: true);
      }

      final destPath = '${dir.path}/custom_courtyard_bg.jpg';
      await source.copy(destPath);

      state.value = OasSkinState(
        skinId: state.value.skinId,
        randomOnLaunch: state.value.randomOnLaunch,
        customWallpaperPath: destPath,
      );
      await _persist();
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> clearCustomWallpaper() async {
    state.value = OasSkinState(
      skinId: state.value.skinId,
      randomOnLaunch: state.value.randomOnLaunch,
    );
    await _persist();
  }

  OasCourtyardSkin _pickRandomSkin() {
    return OasCourtyardSkin.all[_random.nextInt(OasCourtyardSkin.all.length)];
  }

  OasCourtyardSkin _pickDifferentSkin(String currentId) {
    final candidates =
        OasCourtyardSkin.all.where((skin) => skin.id != currentId).toList();
    if (candidates.isEmpty) return OasCourtyardSkin.all.first;
    return candidates[_random.nextInt(candidates.length)];
  }

  Future<void> _persist() async {
    try {
      final dir = await getApplicationSupportDirectory();
      final file = File('${dir.path}/courtyard_skin.json');
      final data = <String, dynamic>{
        'skinId': state.value.skinId,
        'randomOnLaunch': state.value.randomOnLaunch,
        'customWallpaperPath': state.value.customWallpaperPath,
      };
      await file.writeAsString(jsonEncode(data), flush: true);
    } catch (_) {
      // Silently ignore persistence failures (for example, in widget tests).
    }
  }
}
