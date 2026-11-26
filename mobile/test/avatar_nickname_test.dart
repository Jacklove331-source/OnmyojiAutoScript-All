import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart' show MethodCall, MethodChannel;
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/ui/avatar_store.dart';

/// 称呼（昵称）口径：能自己改、留空回落到「<头像名>大人」、和头像一起落盘。
const MethodChannel kPathProviderChannel =
    MethodChannel('plugins.flutter.io/path_provider');

void mockSupportDir(Directory dir) {
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(kPathProviderChannel, (MethodCall call) async {
    if (call.method == 'getApplicationSupportDirectory') return dir.path;
    return null;
  });
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('displayName：留空用默认「<头像名>大人」，设过就用昵称', () {
    final OasAvatar seimei = OasAvatar.all.first;
    expect(OasAvatarStore.displayName(seimei, ''), '晴明大人');
    expect(OasAvatarStore.displayName(seimei, '   '), '晴明大人');
    expect(OasAvatarStore.displayName(seimei, '娘娘'), '娘娘');
  });

  test('sanitizeNickname：去首尾空白、折叠内部空白、最多 12 个字', () {
    expect(OasAvatarStore.sanitizeNickname('  晴明  '), '晴明');
    expect(OasAvatarStore.sanitizeNickname('晴\t明\n大人'), '晴 明 大人');
    expect(OasAvatarStore.sanitizeNickname('一二三四五六七八九十十一十二'),
        '一二三四五六七八九十十一');
    expect(OasAvatarStore.sanitizeNickname(''), '');
  });

  test('昵称与头像存同一个 avatar.json，重启后都读回来', () async {
    final Directory dir = Directory.systemTemp.createTempSync('oas_nick_');
    addTearDown(() {
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(kPathProviderChannel, null);
      if (dir.existsSync()) dir.deleteSync(recursive: true);
    });
    final OasAvatarStore store = OasAvatarStore.i;
    await store.resetForTest();
    mockSupportDir(dir);

    expect(await store.setNickname('  阿爸 '), isTrue);
    expect(store.nickname.value, '阿爸');
    expect(await store.select(OasAvatar.all[1]), OasAvatarSaveResult.saved);

    final File file = File('${dir.path}${Platform.pathSeparator}avatar.json');
    final Map<String, dynamic> saved =
        jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
    expect(saved['id'], OasAvatar.all[1].id, reason: '换头像不能把昵称冲掉');
    expect(saved['nickname'], '阿爸');

    // 模拟重启
    store.nickname.value = '';
    store.state.value = OasAvatar.all.first;
    await store.load();
    expect(store.nickname.value, '阿爸');
    expect(store.state.value.id, OasAvatar.all[1].id);

    // 留空 = 删掉昵称
    expect(await store.setNickname(''), isTrue);
    expect(store.nickname.value, '');
    await store.load();
    expect(store.nickname.value, '');
  });

  test('落盘失败时称呼回滚，界面不会显示一个没保存上的名字', () async {
    final OasAvatarStore store = OasAvatarStore.i;
    await store.resetForTest();
    // 没 mock → path_provider 不可用，写盘必失败
    expect(await store.setNickname('临时'), isFalse);
    expect(store.nickname.value, '', reason: '失败要回到改之前的值');
  });
}
