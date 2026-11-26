import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// 无人值守的「硬上限」：每日最多跑多少轮，到顶就不再自动开新一轮。
///
/// 存在 App 自己的应用目录里（`path_provider` 已随项目引入，不额外拉依赖），
/// 不写进 OAS 配置——这是 App 侧的护栏，跟游戏配置分开才好解释。
class OasSafety {
  OasSafety._();

  static final OasSafety i = OasSafety._();

  /// 0 表示不限制。
  int maxRoundsPerDay = 0;

  bool _loaded = false;

  File? _file;

  Future<File?> _target() async {
    if (_file != null) return _file;
    try {
      final Directory dir = await getApplicationSupportDirectory();
      _file = File('${dir.path}/safety_limits.json');
      return _file;
    } catch (_) {
      return null;
    }
  }

  Future<void> load() async {
    if (_loaded) return;
    _loaded = true;
    try {
      final File? file = await _target();
      if (file == null || !await file.exists()) return;
      final Object? data = jsonDecode(await file.readAsString());
      if (data is Map && data['maxRoundsPerDay'] is num) {
        maxRoundsPerDay = (data['maxRoundsPerDay'] as num).toInt();
      }
    } catch (_) {}
  }

  Future<bool> save() async {
    try {
      final File? file = await _target();
      if (file == null) return false;
      final File pending = File('${file.path}.tmp');
      await pending.writeAsString(
        jsonEncode(<String, int>{'maxRoundsPerDay': maxRoundsPerDay}),
        flush: true,
      );
      await pending.rename(file.path);
      return true;
    } catch (_) {
      return false;
    }
  }

  String get label =>
      maxRoundsPerDay > 0 ? '每日 $maxRoundsPerDay 轮' : '不限制';
}
