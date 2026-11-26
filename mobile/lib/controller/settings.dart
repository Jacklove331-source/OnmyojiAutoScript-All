import 'dart:io';

import 'package:get/get.dart';
import 'package:get_storage/get_storage.dart';
import 'package:oasx/api/api_client.dart';
import 'package:oasx/service/script_service.dart';
import 'package:oasx/config/translation/i18n_content.dart';
import 'package:oasx/views/nav/view_nav.dart';
import 'package:path_provider/path_provider.dart';

import 'package:oasx/utils/check_version.dart';
import 'package:oasx/config/global.dart';

/// language: String ["en-US", "zh-CN", "zh-TW", "ja-JP"]
/// 关于桌面分辨率的适配： 不知道如何下手
class SettingsController extends GetxController {
  GetStorage storage = GetStorage();
  late String temporaryDirectory;

  @override
  void onInit() {
    updateTemporaryDirectory();
    getCurrentVersion().then((value) {
      GlobalVar.version = value;
    });
    super.onInit();
  }

  void updateTemporaryDirectory() {
    temporaryDirectory = storage.read('temporaryDirectory') ?? './';
    printInfo(info: 'Old TemporaryDirectory : $temporaryDirectory');
    getTemporaryDirectory().then((value) {
      temporaryDirectory = value.path;
      printInfo(info: 'New TemporaryDirectory : $temporaryDirectory');
      storage.write('temporaryDirectory', temporaryDirectory);
    });
  }

  bool get aiEnabled => storage.read('aiRecoveryEnabled') ?? false;
  String get aiMode => storage.read('aiRecoveryMode') ?? 'observe';
  String get aiKey => storage.read('aiRecoveryKey') ?? '';

  /// 把设置写到 App 私有 files 目录；Python 端 config.py 会从这里读。
  Future<void> saveAiConfig({
    required bool enabled,
    required String mode,
    required String key,
  }) async {
    final dir = await getApplicationSupportDirectory();
    final keyFile = File('${dir.path}/ai_recovery.key');
    final cfgFile = File('${dir.path}/ai_recovery.yaml');
    final trimmed = key.trim();
    await keyFile.writeAsString(trimmed, flush: true);
    await cfgFile.writeAsString(
      'enabled: ${enabled ? 'true' : 'false'}\n'
      'mode: ${mode == 'auto' ? 'auto' : 'observe'}\n'
      'api:\n'
      '  model: deepseek-flash\n'
      '  key_file: ${keyFile.path.replaceAll('\\', '/')}\n',
      flush: true,
    );
    storage.write('aiRecoveryEnabled', enabled);
    storage.write('aiRecoveryMode', mode);
    storage.write('aiRecoveryKey', trimmed);
    Get.snackbar('AI 自愈', '已保存，下个任务生效');
  }

  Future<void> killServer() async {
    final success = await ApiClient().killServer();
    if (success) {
      Get.snackbar(I18n.kill_server_success.tr, '');
      Get.offAllNamed('/login');
      await Future.wait([
        Get.delete<ScriptService>(force: true),
        Get.delete<NavCtrl>(force: true),
      ]);
    } else {
      Get.snackbar(I18n.kill_server_failure.tr, '');
    }
  }
}
