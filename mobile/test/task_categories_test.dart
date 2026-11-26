import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/ui/api.dart';
import 'package:oasx/ui/task_categories.dart';
import 'package:oasx/ui/task_names.dart';

void main() {
  test('分类顺序与电脑端 config_menu.py 的菜单一致', () {
    expect(
      kTaskCategories.map((OasTaskCategory c) => c.label).toList(),
      <String>['脚本设置', '御魂', '日常', '肝帝专属', '阴阳寮', '每周', '活动'],
    );
    expect(kTaskCategories.map((OasTaskCategory c) => c.id).toSet().length,
        kTaskCategories.length);
  });

  test('同一个任务不会被归到两个大类', () {
    final Set<String> seen = <String>{};
    for (final OasTaskCategory c in kTaskCategories) {
      for (final String dir in c.members) {
        expect(seen.add(dir), isTrue, reason: '$dir 被重复归类');
      }
    }
  });

  test('分类表里没有任务表对不上的目录（防拼错/已删任务残留）', () {
    expect(kTaskCategories, isNotEmpty, reason: '分类表被清空了，这条测试会空过');
    // 电脑端菜单里有、本机任务表里没有的目录，只能在这里白名单登记；
    // 白名单本身也要校验：它必须真的不在任务表里，否则说明该任务已接上，白名单该删。
    const Set<String> menuOnly = <String>{};
    for (final String dir in menuOnly) {
      expect(kTaskDisplayNames.containsKey(dir), isFalse,
          reason: '$dir 已经有显示名了，把它从 menuOnly 白名单里删掉');
    }
    final List<String> unknown = <String>[
      for (final OasTaskCategory c in kTaskCategories)
        for (final String dir in c.members)
          if (!kTaskDisplayNames.containsKey(dir) && !menuOnly.contains(dir)) dir,
    ];
    expect(unknown, isEmpty, reason: '分类表里有任务表不存在的目录：${unknown.join(', ')}');
  });

  test('任务表里每个目录都能落到唯一一个分组', () {
    expect(kTaskDisplayNames, isNotEmpty);
    // 只断言"恰好一个分组"会漏掉"落进其他"的漏配，这里把漏配也当失败
    final List<String> uncategorized = <String>[];
    for (final String dir in kTaskDisplayNames.keys) {
      final List<OasTaskGroup> groups = groupTaskDirs(<String>[dir]);
      expect(groups.length, 1, reason: '$dir 分组结果不是恰好一个');
      expect(groups.single.dirs, <String>[dir], reason: '$dir 分组内容不对');
      if (groups.single.id == kOtherCategoryId) uncategorized.add(dir);
    }
    expect(uncategorized, isEmpty,
        reason: '这些任务没进分类表（会显示在「其他」）：${uncategorized.join(', ')}');
  });

  test('分组保序、空组丢弃、未归类落到「其他」、重复目录去重', () {
    final List<OasTaskGroup> groups = groupTaskDirs(<String>[
      'Orochi',
      'DailyTrifles',
      'UnknownTask',
      'DailyTrifles',
      '',
      'Orochi',
    ]);
    expect(groups.map((OasTaskGroup g) => g.label).toList(),
        <String>['御魂', '日常', '其他']);
    expect(groups[0].dirs, <String>['Orochi']);
    expect(groups[1].dirs, <String>['DailyTrifles']);
    expect(groups[2].dirs, <String>['UnknownTask']);
    expect(groups[2].id, kOtherCategoryId);
  });

  test('组内顺序按分类表排，不随后端返回顺序变化', () {
    final List<OasTaskGroup> groups = groupTaskDirs(<String>[
      'MetaDemon', // activity 里排第 3
      'ActivityShikigami', // activity 里排第 1
      'Quiz', // activity 里排第 8
    ]);
    expect(groups.single.label, '活动');
    expect(groups.single.dirs,
        <String>['ActivityShikigami', 'MetaDemon', 'Quiz']);
  });

  test('groupTasks 按分类顺序回填 OasTask 对象', () {
    final List<OasTask> tasks = <OasTask>[
      const OasTask(dir: 'WeeklyTrifles', title: '每周琐事'),
      const OasTask(dir: 'Orochi', title: '八岐大蛇'),
      const OasTask(dir: 'GlobalGame', title: '全局配置'),
    ];
    final List<({OasTaskGroup group, List<OasTask> tasks})> grouped =
        groupTasks(tasks);
    expect(grouped.map((e) => e.group.label).toList(),
        <String>['脚本设置', '御魂', '每周']);
    expect(grouped[0].tasks.single.dir, 'GlobalGame');
    expect(grouped[1].tasks.single.title, '八岐大蛇');
    expect(grouped[2].tasks.single.dir, 'WeeklyTrifles');
  });

  test('空任务表不产生分组', () {
    expect(groupTaskDirs(<String>[]), isEmpty);
    expect(groupTasks(<OasTask>[]), isEmpty);
  });
}
