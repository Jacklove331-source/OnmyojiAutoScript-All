import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/ui/api.dart';

/// `/ai/recovery` 的解析口径：历史文件是外部写的，字段可能缺失/类型漂移，解析不能炸。
void main() {
  test('正常记录：summary 与条目都读出来', () {
    final OasAiRecovery data = OasAiRecovery.fromJson(<String, dynamic>{
      'ok': true,
      'total': 3,
      'summary': <String, dynamic>{
        'scope': 'all',
        'total': 3,
        'scanned': 3,
        'recovered': 1,
        'needs_human': 1,
        'stopped': 1,
        'ai_calls': 2,
        'tokens': 1200,
        'cost_cny': 0.0024,
      },
      'entries': <Object>[
        <String, dynamic>{
          'id': 'aaaa1111',
          'at': '2026-11-26 10:10:10',
          'task': 'WeeklyPurchase',
          'type': 'device_stuck',
          'outcome': 'recovered',
          'message': 'ok',
          'cause': '点到导航栏边缘',
          'detail': '卡死 60s',
          'ai_called': true,
          'verified': true,
          'tokens': 1200,
          'cost_cny': 0.0024,
          'actions': 2,
          'actions_ok': 1,
        },
      ],
    });
    expect(data.total, 3);
    expect(data.recovered, 1);
    expect(data.needsHuman, 1);
    expect(data.stopped, 1);
    expect(data.aiCalls, 2);
    expect(data.costCny, closeTo(0.0024, 1e-9));
    expect(data.incidents, hasLength(1));

    final OasAiIncident it = data.incidents.single;
    expect(it.id, 'aaaa1111');
    expect(it.outcomeLabel, '已自愈');
    expect(it.headline, 'WeeklyPurchase · 点到导航栏边缘');
    expect(it.actions, 2);
    expect(it.actionsOk, 1);
  });

  test('脏数据兜底：缺字段 / tokens 是字符串 / cost 是 null / entries 不是列表', () {
    final OasAiRecovery data = OasAiRecovery.fromJson(<String, dynamic>{
      'summary': <String, dynamic>{'total': '12', 'cost_cny': null},
      'entries': <Object>[
        <String, dynamic>{'id': 'x', 'tokens': '1,234', 'cost_cny': 'abc'},
        'not-a-map',
        <String, dynamic>{'outcome': 'mystery'},
      ],
    });
    expect(data.total, 12, reason: '字符串数字要能读成 int');
    expect(data.costCny, 0.0);
    expect(data.incidents, hasLength(2), reason: '非 Map 的条目直接跳过');

    final OasAiIncident broken = data.incidents.first;
    expect(broken.tokens, 0, reason: '"1,234" 解析失败按 0，不抛异常');
    expect(broken.costCny, 0.0);
    expect(broken.outcomeLabel, '未记录结果');

    final OasAiIncident unknownOutcome = data.incidents.last;
    expect(unknownOutcome.outcomeLabel, '未记录结果');
    expect(unknownOutcome.headline, '异常', reason: 'task/cause/message 全空时给个兜底标题');
  });

  test('老后端没有这条路由：空页 + ok=false 都退化成空', () {
    expect(OasAiRecovery.empty.isEmpty, isTrue);
    final OasAiRecovery noSummary = OasAiRecovery.fromJson(<String, dynamic>{});
    expect(noSummary.total, 0);
    expect(noSummary.incidents, isEmpty);
    expect(noSummary.isEmpty, isTrue);
  });

  test('outcome 标签映射', () {
    OasAiIncident withOutcome(String outcome) =>
        OasAiIncident.fromJson(<String, dynamic>{'outcome': outcome});
    expect(withOutcome('recovered').outcomeLabel, '已自愈');
    expect(withOutcome('needs_human').outcomeLabel, '需人工');
    expect(withOutcome('stopped').outcomeLabel, '已停下');
    expect(withOutcome('').outcomeLabel, '未记录结果');
  });
}
