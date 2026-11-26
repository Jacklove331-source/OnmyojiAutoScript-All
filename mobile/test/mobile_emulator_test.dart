import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/ui/api.dart';
import 'package:oasx/ui/widgets_live.dart';

class Adapter implements HttpClientAdapter {
  Adapter(this.handle);
  final Future<ResponseBody> Function(RequestOptions, Future<void>?) handle;
  @override
  Future<ResponseBody> fetch(RequestOptions options,
          Stream<Uint8List>? requestStream, Future<void>? cancelFuture) =>
      handle(options, cancelFuture);
  @override
  void close({bool force = false}) {}
}

OasApi client(
    Future<ResponseBody> Function(RequestOptions, Future<void>?) handle) {
  final Dio dio = Dio(BaseOptions(
      baseUrl: 'http://127.0.0.1:22267', responseType: ResponseType.plain));
  dio.httpClientAdapter = Adapter(handle);
  return OasApi.withClient(dio);
}

ResponseBody image(Uint8List bytes,
        {int frame = 1, DateTime? at, int width = 1600, int height = 900}) =>
    ResponseBody.fromBytes(bytes, 200, headers: <String, List<String>>{
      'content-type': <String>['image/png'],
      'x-oas-frame-id': <String>['$frame'],
      'x-oas-display-id': <String>['7'],
      'x-oas-width': <String>['$width'],
      'x-oas-height': <String>['$height'],
      'x-oas-captured-at': <String>[
        '${(at ?? DateTime.now()).millisecondsSinceEpoch}'
      ],
    });

void main() {
  final Uint8List png =
      File('test/fixtures/mobile_frame.png').readAsBytesSync();

  test('backend rejection containing ok and true does not become success',
      () async {
    final OasApi api = client((_, __) async => ResponseBody.fromString(
        '{"ok":false,"reason":"true is not success"}', 200));
    expect(await api.startScheduler(), isFalse);
  });

  test('explicit successful JSON is accepted', () async {
    final OasApi api =
        client((_, __) async => ResponseBody.fromString('{"ok":true}', 200));
    expect(await api.startScheduler(), isTrue);
  });

  test('stop requests both scheduler and active task cancellation', () async {
    final List<String> paths = <String>[];
    final OasApi api = client((RequestOptions request, _) async {
      paths.add(request.path);
      return ResponseBody.fromString('{"ok":true}', 200);
    });
    expect(await api.stopScheduler(), isTrue);
    expect(paths, <String>['/scheduler/stop', '/task/stop']);
  });

  test('task switches read the selected account', () async {
    String? config;
    final OasApi api = client((RequestOptions request, _) async {
      config = request.uri.queryParameters['config'];
      return ResponseBody.fromString(
          '{"ok":true,"tasks":[{"name":"WantedQuests","enabled":true}]}', 200);
    });
    expect(await api.taskEnabled('oas3'), <String, bool>{'WantedQuests': true});
    expect(config, 'oas3');
  });

  test('monitor uses background JPEG endpoint and preserves native metadata',
      () async {
    String? path;
    final OasApi api = client((RequestOptions request, _) async {
      path = request.path;
      return image(png);
    });
    final MobileMonitorFrame? frame = await api.monitorFrame();
    expect(path, '/monitor/frame');
    expect(frame?.frameId, 1);
    expect(frame?.displayId, 7);
    expect(frame?.capturedAt, isNotNull);
    expect(frame?.bytes, png);
  });

  test('HTML and truncated JPEG are rejected', () async {
    final OasApi html = client((_, __) async =>
        ResponseBody.fromBytes('<html>bad response</html>'.codeUnits, 200));
    expect(await html.monitorFrame(), isNull);
    final OasApi broken = client((_, __) async =>
        ResponseBody.fromBytes(<int>[255, 216, 1, 2, 3, 4, 5, 6], 200));
    expect(await broken.monitorFrame(), isNull);
  });

  testWidgets('slow monitor response does not accumulate overlapping requests',
      (WidgetTester tester) async {
    int calls = 0;
    final Completer<ResponseBody> pending = Completer<ResponseBody>();
    final OasApi api = client((_, __) {
      calls++;
      return pending.future;
    });
    await tester.pumpWidget(
        MaterialApp(home: SizedBox(height: 300, child: LiveFrame(api: api))));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    expect(calls, 1);
    await tester.pumpWidget(const SizedBox());
    pending.complete(image(png));
    await tester.pump();
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'pause discards a late response and keeps the last observed frame',
      (WidgetTester tester) async {
    final GlobalKey<LiveFrameState> key = GlobalKey<LiveFrameState>();
    int calls = 0;
    final Completer<ResponseBody> pending = Completer<ResponseBody>();
    final OasApi api = client((_, __) async {
      calls++;
      if (calls == 1) return image(png, frame: 1);
      return pending.future;
    });
    await tester.pumpWidget(MaterialApp(
        home: LiveFrame(key: key, api: api, showStatusOverlay: true)));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));
    await tester.pump(const Duration(milliseconds: 250));
    expect(calls, 2);
    key.currentState!.setPaused(true);
    pending.complete(image(png, frame: 99));
    await tester.pump();
    expect(key.currentState!.lastFrame?.frameId, 1);
    expect(find.textContaining('画面已暂停'), findsOneWidget);
    await tester.pump(const Duration(seconds: 2));
    expect(calls, 2);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('old capture time is labeled waiting for a new frame',
      (WidgetTester tester) async {
    final OasApi api = client((_, __) async =>
        image(png, at: DateTime.now().subtract(const Duration(seconds: 20))));
    await tester.pumpWidget(
        MaterialApp(home: LiveFrame(api: api, showStatusOverlay: true)));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));
    expect(find.textContaining('等待新帧'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('default LiveFrame hides all text overlays on the video surface',
      (WidgetTester tester) async {
    final OasApi api = client((_, __) async => image(png));
    await tester.pumpWidget(MaterialApp(home: LiveFrame(api: api)));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));
    expect(find.byType(Text), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('hidden monitor does not poll', (WidgetTester tester) async {
    int calls = 0;
    final OasApi api = client((_, __) async {
      calls++;
      return image(png);
    });
    await tester.pumpWidget(MaterialApp(
        home: TickerMode(enabled: false, child: LiveFrame(api: api))));
    await tester.pump();
    await tester.pump(const Duration(seconds: 2));
    expect(calls, 0);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('interactive LiveFrame maps tap coordinates to virtual display pixels',
      (WidgetTester tester) async {
    final List<String> posts = <String>[];
    final OasApi api = client((RequestOptions request, _) async {
      if (request.path == '/monitor/frame') {
        return image(png, width: 1600, height: 900);
      }
      posts.add(request.uri.toString());
      return ResponseBody.fromString('{"ok":true}', 200);
    });
    await tester.pumpWidget(MaterialApp(
        home: Center(
            child: SizedBox(
                width: 320,
                height: 180,
                child: LiveFrame(api: api, interactive: true)))));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));
    await tester.tap(find.byType(LiveFrame));
    await tester.pump(const Duration(milliseconds: 50));
    expect(posts.any((String u) => u.contains('/vd/tap?x=800&y=450&display=7')),
        isTrue,
        reason: 'expected center tap at 800,450; got $posts');
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpWidget(const SizedBox());
  });
}
