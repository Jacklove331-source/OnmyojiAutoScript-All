import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show MethodCall, MethodChannel, rootBundle;
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/ui/avatar_picker.dart';
import 'package:oasx/ui/avatar_store.dart';
import 'package:oasx/ui/theme.dart';

/// 从当前目录向上找包根（有 pubspec.yaml 的那层），别无脑假设 cwd。
Directory findPackageRoot() {
  Directory dir = Directory.current;
  for (int i = 0; i < 8; i++) {
    if (File('${dir.path}${Platform.pathSeparator}pubspec.yaml').existsSync()) {
      return dir;
    }
    final Directory parent = dir.parent;
    if (parent.path == dir.path) break;
    dir = parent;
  }
  fail('找不到 pubspec.yaml，无法定位包根：${Directory.current.path}');
}

/// 测试环境没有 path_provider 原生实现：用它模拟「写盘可用 / 写盘失败」。
const MethodChannel kPathProviderChannel =
    MethodChannel('plugins.flutter.io/path_provider');

void mockSupportDir(Directory dir) {
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(kPathProviderChannel, (MethodCall call) async {
    if (call.method == 'getApplicationSupportDirectory') return dir.path;
    return null;
  });
}

void unmockSupportDir() {
  TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
      .setMockMethodCallHandler(kPathProviderChannel, null);
}

/// 头像预设的资源路径必须是真实存在的文件、且真的被打进 assets，
/// 否则线上只会静默回落到字形。
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  final OasAvatarStore store = OasAvatarStore.i;
  final OasAvatar fallback = OasAvatar.all.first;
  final OasAvatar kagura = OasAvatar.byId('kagura');
  final OasAvatar orochi = OasAvatar.byId('orochi');

  setUp(() async {
    // 单例是全局的：每个用例都从「刚启动、无 mock、无残留写盘队列」的干净状态开始
    unmockSupportDir();
    await store.resetForTest();
  });
  tearDown(unmockSupportDir);

  test('every preselected avatar points at a real bundled asset', () async {
    final String root = findPackageRoot().path;
    final String pubspec =
        File('$root${Platform.pathSeparator}pubspec.yaml').readAsStringSync();
    expect(pubspec.contains('- assets/onmyoji/'), isTrue,
        reason: 'pubspec.yaml 必须声明 assets/onmyoji/ 目录');

    for (final OasAvatar item in OasAvatar.all) {
      expect(item.imageAsset, startsWith('assets/onmyoji/'),
          reason: '${item.id} 的资源不在 assets/onmyoji 下');
      final String path = '$root${Platform.pathSeparator}'
          '${item.imageAsset.replaceAll('/', Platform.pathSeparator)}';
      expect(File(path).existsSync(), isTrue,
          reason: '${item.id} 指向的资源不存在：${item.imageAsset}');
      final int bytes = (await rootBundle.load(item.imageAsset)).lengthInBytes;
      expect(bytes, greaterThan(0),
          reason: '${item.id} 的资源没有打进包：${item.imageAsset}');
    }
  });

  test('ids are unique and byId round-trips, unknown id falls back', () {
    final Set<String> ids = <String>{};
    for (final OasAvatar item in OasAvatar.all) {
      expect(ids.add(item.id), isTrue, reason: '重复 id：${item.id}');
      expect(OasAvatar.byId(item.id).id, item.id);
      expect(item.label, isNotEmpty);
      expect(item.char, isNotEmpty);
    }
    expect(OasAvatar.byId('已下线的旧头像').id, OasAvatar.all.first.id);
  });

  test('select() persists to avatar.json and load() reads it back', () async {
    final Directory dir = Directory.systemTemp.createTempSync('oas_avatar_');
    addTearDown(() {
      unmockSupportDir();
      if (dir.existsSync()) dir.deleteSync(recursive: true);
    });
    mockSupportDir(dir);

    expect(await store.select(kagura), OasAvatarSaveResult.saved);
    expect(store.state.value.id, kagura.id, reason: '内存状态应立即生效');

    final File file = File('${dir.path}${Platform.pathSeparator}avatar.json');
    expect(file.existsSync(), isTrue, reason: 'avatar.json 必须落盘');
    expect(jsonDecode(file.readAsStringSync())['id'], kagura.id);

    // 模拟重启：把内存打回默认，再 load() 应读回 kagura
    store.state.value = fallback;
    await store.load();
    expect(store.state.value.id, kagura.id, reason: '重启后应读回上次选择');
  });

  test('failed write rolls back to what is really on disk', () async {
    final Directory dir = Directory.systemTemp.createTempSync('oas_avatar_');
    addTearDown(() {
      unmockSupportDir();
      if (dir.existsSync()) dir.deleteSync(recursive: true);
    });

    // 1) 正常写盘：kagura 落盘成功
    mockSupportDir(dir);
    expect(await store.select(kagura), OasAvatarSaveResult.saved);
    await store.load();
    expect(store.state.value.id, kagura.id);

    // 2) 外部把磁盘改成 kaguya，再让「写」失败但「读」仍可用
    //    （avatar.json.tmp 被一个目录占住）；回滚若读的是磁盘就该是 kaguya，
    //    而内存里已知的旧真值还是 kagura —— 这条断言能区分两条路径。
    final File json = File('${dir.path}${Platform.pathSeparator}avatar.json');
    json.writeAsStringSync(jsonEncode(<String, String>{'id': 'kaguya'}));
    final Directory blocker =
        Directory('${dir.path}${Platform.pathSeparator}avatar.json.tmp');
    blocker.createSync(recursive: true);

    final OasAvatarSaveResult result = await store.select(orochi);
    expect(result, OasAvatarSaveResult.failedRolledBack);
    expect(store.state.value.id, 'kaguya',
        reason: '落盘失败应按磁盘真值回滚（外部改成 kaguya），而不是内存缓存或刚点的那款');

    // 3) 连读盘都不可用时，退回内存里已知的磁盘真值（此时已被第 2 步纠正为 kaguya）
    unmockSupportDir();
    expect(await store.select(orochi), OasAvatarSaveResult.failedRolledBack);
    expect(store.state.value.id, 'kaguya',
        reason: '读盘不可用时不能把上一次确认过的磁盘真值丢掉');
  });

  test('a slower earlier select is reported as superseded, not saved', () async {
    // 时序约定：两次 select 都在任何一次落盘完成前发起（store 内部按调用顺序排队），
    // 所以先发起的那次一定先完成、且此时 _selectSeq 已被第二次递增 ⇒ superseded。
    unmockSupportDir(); // 测试环境写盘必失败，两次选择都会走到失败分支
    final Future<OasAvatarSaveResult> first = store.select(kagura);
    final Future<OasAvatarSaveResult> second = store.select(orochi);

    expect(await first, OasAvatarSaveResult.superseded,
        reason: '被更新选择接管的那次不该报 saved/失败');
    expect(await second, OasAvatarSaveResult.failedRolledBack);
    expect(store.state.value.id, fallback.id,
        reason: '写盘全失败且磁盘无文件时，回滚到默认款');
  });

  testWidgets('one avatar state drives every badge (courtyard + me) at once',
      (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: buildOasTheme(Brightness.light),
        home: const Scaffold(
          body: Row(
            children: <Widget>[
              OasAvatarBadge(size: 44),
              OasAvatarBadge(size: 52),
            ],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    for (final OasAvatar item in OasAvatar.all) {
      store.state.value = item;
      await tester.pump();
      final List<Image> images =
          tester.widgetList<Image>(find.byType(Image)).toList();
      expect(images.length, 2, reason: '两处头像都应该在页面上');
      for (final Image image in images) {
        expect((image.image as AssetImage).assetName, item.imageAsset,
            reason: '${item.id}: 两处头像必须同时切到同一张图');
      }
    }
    expect(tester.takeException(), isNull);
  });
}
