import 'package:flutter/services.dart';

/// 打开系统设置页的桥：只跳转，不代替用户点授权、不弹我们自己的框。
class OasSystem {
  OasSystem._();

  static const MethodChannel _channel = MethodChannel('oasx/system');

  /// page: accessibility / notification / app_detail / shizuku
  static Future<bool> open(String page) async {
    try {
      final bool? ok = await _channel.invokeMethod<bool>(
        'open',
        <String, String>{'page': page},
      );
      return ok ?? false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      return false;
    }
  }

  /// 调用系统安装器安装本地已下载校验通过的 APK（首次若缺未知来源权限会跳系统授权页）。
  static Future<bool> installApk(String path) async {
    try {
      final bool? ok = await _channel.invokeMethod<bool>(
        'installApk',
        <String, String>{'path': path},
      );
      return ok ?? false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      return false;
    }
  }
}
