import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/ui/api.dart';

/// 手机端参数页的分组口径：
/// - 电脑专属五组**不显示**（用户明确要求去掉）
/// - 调度组折叠进「高级」
/// - 手机真正能改的 error / optimization / anti_ban 留在主界面
void main() {
  OasArgGroup group(String key) =>
      OasArgGroup(key: key, title: key, fields: const <OasArgField>[]);

  test('电脑专属五组在手机端完全不显示', () {
    for (final String key in <String>[
      'device',
      'desktop',
      'auto_lineup',
      'roster_scan',
      'workbench',
    ]) {
      expect(group(key).isHidden, isTrue, reason: '$key 不该出现在手机参数页');
      expect(group(key).isAdvanced, isFalse, reason: '$key 是隐藏，不是折叠');
    }
  });

  test('只有调度组折叠进高级', () {
    expect(group('scheduler').isAdvanced, isTrue);
    for (final String key in <String>['error', 'optimization', 'anti_ban']) {
      expect(group(key).isAdvanced, isFalse, reason: '$key 应该在主界面');
      expect(group(key).isHidden, isFalse, reason: '$key 不该被隐藏');
    }
  });

  test('其它任务的普通分组既不该隐藏也不该折叠', () {
    for (final String key in <String>[
      'soul',
      'kekkai',
      'scheduler_extra',
      'battle',
      'orochi',
    ]) {
      expect(group(key).isHidden, isFalse, reason: '$key 被误加进隐藏表');
      expect(group(key).isAdvanced, isFalse, reason: '$key 被误加进折叠表');
    }
  });

  test('两张表都是精确匹配，不是前缀/后缀', () {
    expect(OasArgGroup.kHiddenArgGroups.contains('device_extra'), isFalse);
    expect(OasArgGroup.kAdvancedArgGroups, <String>{'scheduler'});
    expect(OasArgGroup.kHiddenArgGroups.length, 5);
  });
}
