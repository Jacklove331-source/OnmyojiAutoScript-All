// The Flutter starter counter test is replaced by actual OAS home-page behavior.
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/ui/api.dart';
import 'package:oasx/ui/pages/home.dart';

class HomeAdapter implements HttpClientAdapter {
  HomeAdapter({this.lastPlan = const <String>[]});

  final List<String> lastPlan;

  @override
  Future<ResponseBody> fetch(RequestOptions options,
      Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    const List<String> tasks = <String>[
      'Restart', 'AreaBoss', 'GoldYoukai', 'Nian', 'RealmRaid', 'KekkaiUtilize',
      'KekkaiActivation', 'DemonEncounter', 'DailyTrifles', 'TalismanPass', 'Pets',
      'Delegation', 'WantedQuests', 'Tako', 'Orochi', 'MetaDemon', 'BondlingFairyland',
      'EvoZone', 'RichMan', 'WeeklyTrifles', 'MysteryShop', 'CollectiveMissions',
      'Hunt', 'AbyssShadows'
    ];
    final Map<String, Object> responses = <String, Object>{
      '/health': <String, Object>{'ok': true},
      '/state': <String, Object>{'ok': true, 'virtual_display': 2},
      '/scheduler/status': <String, Object>{
        'running': false,
        'config': 'oas-lg14-qa',
        'tasks': lastPlan
      },
      '/task/status': <String, Object>{'running': false},
      '/config_tasks': <String, Object>{
        'ok': true,
        'tasks': tasks
            .map((String name) =>
                <String, Object>{'name': name, 'enabled': true})
            .toList()
      },
      '/script_menu': <String, Object>{'Tasks': tasks},
      '/log/tail': <String, Object>{
        'ok': options.uri.queryParameters['path'] ==
            'log/2026-11-18_17-00-00_oas1_WantedQuests.txt',
        'lines': <String>['Selected task output'],
      },
      '/log/files': <String, Object>{
        'files': <Object>[
          <String, Object>{
            'path': 'log/2026-11-18_17-00-00_oas1_WantedQuests.txt',
            'size': 1234
          }
        ]
      },
    };
    return ResponseBody.fromString(
        jsonEncode(responses[options.uri.path] ?? <String, Object>{}), 200);
  }

  @override
  void close({bool force = false}) {}
}

Future<void> showHome(WidgetTester tester, {HomeAdapter? adapter}) async {
  final Dio dio = Dio(BaseOptions(
      baseUrl: 'http://127.0.0.1:22267', responseType: ResponseType.plain));
  dio.httpClientAdapter = adapter ?? HomeAdapter();
  await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: HomePage(api: OasApi.withClient(dio)))));
  await tester.pumpAndSettle();
  for (int attempt = 0; attempt < 20; attempt++) {
    await tester.pump(const Duration(milliseconds: 100));
    if (find.text('已连接').evaluate().isNotEmpty) break;
  }
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('imported plan is visible before a scheduler run has started',
      (WidgetTester tester) async {
    await showHome(tester);
    expect(find.textContaining('已启用 24 项', findRichText: true), findsOneWidget);
    expect(find.text('后台'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('old scheduler history does not hide the selected plan',
      (WidgetTester tester) async {
    await showHome(tester, adapter: HomeAdapter(lastPlan: <String>['Pets']));
    expect(find.textContaining('已启用 24 项', findRichText: true), findsOneWidget);
  });

  testWidgets('default home shows task execution queue order and pin controls',
      (WidgetTester tester) async {
    await showHome(tester);
    expect(find.text('任务排序'), findsOneWidget);
    expect(find.text('#1'), findsOneWidget);
    expect(find.text('置顶'), findsWidgets);
    expect(find.text('最近日志'), findsNothing);
    expect(find.text('已完成'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('expanding queue reveals all 24 scheduled tasks',
      (WidgetTester tester) async {
    await showHome(tester);
    final Finder expand = find.textContaining('展开全部 24 项排队顺序');
    await tester.drag(find.byType(ListView).first, const Offset(0, -450));
    await tester.pumpAndSettle();
    expect(expand, findsOneWidget);
    await tester.tap(expand);
    await tester.pumpAndSettle();
    expect(find.text('收起排队列表 ▴'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

}
