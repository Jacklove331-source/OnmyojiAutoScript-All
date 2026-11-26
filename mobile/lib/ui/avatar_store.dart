import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

/// 头像样式：只做「内置几款字符头像」，不申请相册权限、不弹任何系统框。
class OasAvatar {
  const OasAvatar(this.id, this.char, this.label);

  final String id;
  final String char;
  final String label;

  static const List<OasAvatar> all = <OasAvatar>[
    OasAvatar('fox', '狐', '狐面'),
    OasAvatar('whale', '鲸', '小鲸'),
    OasAvatar('sakura', '樱', '樱花'),
    OasAvatar('moon', '月', '月读'),
  ];

  static OasAvatar byId(String id) {
    for (final OasAvatar item in all) {
      if (item.id == id) return item;
    }
    return all.first;
  }
}

/// 头像选择落地到 app 私有目录 avatar.json（一个字符串，够用且不占空间）。
class OasAvatarStore {
  OasAvatarStore._();

  static Future<File> _file() async {
    final Directory dir = await getApplicationSupportDirectory();
    return File('${dir.path}/avatar.json');
  }

  static Future<OasAvatar> load() async {
    try {
      final File file = await _file();
      if (!await file.exists()) return OasAvatar.all.first;
      final Object? decoded = jsonDecode(await file.readAsString());
      if (decoded is Map && decoded['id'] is String) {
        return OasAvatar.byId(decoded['id'] as String);
      }
    } catch (_) {}
    return OasAvatar.all.first;
  }

  static Future<bool> save(OasAvatar avatar) async {
    try {
      final File file = await _file();
      await file.writeAsString(jsonEncode(<String, String>{'id': avatar.id}));
      return true;
    } catch (_) {
      return false;
    }
  }
}
