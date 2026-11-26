import 'api.dart';

/// 任务大分类：与电脑端 OAS 的 `module/config/config_menu.py` 一一对应，
/// 只把分组名换成中文（桌面端那份是英文键名）。
///
/// 顺序即界面顺序；组内顺序也按这里的 members 排，找不到的任务落到最后的「其他」。
class OasTaskCategory {
  const OasTaskCategory(this.id, this.label, this.hint, this.members);

  final String id;
  final String label;

  /// 一句话说明（桌面端没有，手机端用来解释这类任务在肝什么）。
  final String hint;

  /// 任务目录名（= `/script_menu` 返回的 Tasks 项），顺序即展示顺序。
  final List<String> members;
}

const String kOtherCategoryId = 'other';
const String kOtherCategoryLabel = '其他';

const List<OasTaskCategory> kTaskCategories = <OasTaskCategory>[
  OasTaskCategory('script', '脚本设置', '启动/重开/全局设置这类地基项', <String>[
    'Script',
    'Restart',
    'GlobalGame',
  ]),
  OasTaskCategory('soul', '御魂', '各类御魂副本，最吃体力的地方', <String>[
    'Orochi',
    'Sougenbi',
    'FallenSun',
    'EternitySea',
    'SixRealms',
    'OtherWorldTwilight',
  ]),
  OasTaskCategory('daily', '日常', '每天必刷的常规项', <String>[
    'DailyTrifles',
    'AreaBoss',
    'AutoCheckinBigGod',
    'GoldYoukai',
    'ExperienceYoukai',
    'Nian',
    'TalismanPass',
    'DemonEncounter',
    'Pets',
    'SoulsTidy',
    'Delegation',
    'WantedQuests',
    'Tako',
  ]),
  OasTaskCategory('liver', '肝帝专属', '经验/金币/探索类，跑起来很久', <String>[
    'BondlingFairyland',
    'EvoZone',
    'GoryouRealm',
    'Exploration',
    'Hyakkiyakou',
    'HeroTest',
    'FindJade',
    'MemoryScrolls',
  ]),
  OasTaskCategory('guild', '阴阳寮', '寮活动与结界，多数需要寮友配合', <String>[
    'KekkaiUtilize',
    'KekkaiActivation',
    'RealmRaid',
    'RyouToppa',
    'Dokan',
    'CollectiveMissions',
    'Hunt',
    'AbyssShadows',
    'GuildBanquet',
    'DemonRetreat',
    'GuildActivityMonitor',
  ]),
  OasTaskCategory('weekly', '每周', '一周只能做几次的限次项', <String>[
    'TrueOrochi',
    'RichMan',
    'WeeklyPurchase',
    'Secret',
    'WeeklyTrifles',
    'MysteryShop',
    'Duel',
    'Chess',
    'DraftDuel',
  ]),
  OasTaskCategory('activity', '活动', '跟着当期活动走的临时项', <String>[
    'ActivityShikigami',
    'MartialArts',
    'MetaDemon',
    'FrogBoss',
    'FrogChallenge',
    'FloatParade',
    'LBS',
    'Quiz',
    'KittyShop',
    'DyeTrials',
    'GuguArtStudio',
    'IbukiArena',
    'BudokaiTournament',
  ]),
];

/// 一个分组 + 它实际命中的任务（按分类表顺序排好）。
class OasTaskGroup {
  const OasTaskGroup(this.id, this.label, this.hint, this.dirs);

  final String id;
  final String label;
  final String hint;
  final List<String> dirs;
}

/// 把 `/script_menu` 给的任务目录按大分类归档。
///
/// 规则：分类表顺序 → 组内 members 顺序；空组丢弃；表里没有的任务进「其他」
/// （保持传入顺序，去重），保证不丢任务、不重复显示。
List<OasTaskGroup> groupTaskDirs(Iterable<String> dirs) {
  final Set<String> present = <String>{};
  final List<String> order = <String>[];
  for (final String d in dirs) {
    if (d.isEmpty) continue;
    if (present.add(d)) order.add(d);
  }

  final List<OasTaskGroup> groups = <OasTaskGroup>[];
  final Set<String> used = <String>{};
  for (final OasTaskCategory c in kTaskCategories) {
    final List<String> hit = <String>[
      for (final String m in c.members)
        if (present.contains(m) && used.add(m)) m,
    ];
    if (hit.isNotEmpty) {
      groups.add(OasTaskGroup(c.id, c.label, c.hint, hit));
    }
  }

  final List<String> rest = <String>[
    for (final String d in order)
      if (!used.contains(d)) d,
  ];
  if (rest.isNotEmpty) {
    groups.add(OasTaskGroup(kOtherCategoryId, kOtherCategoryLabel, '还没归类的任务', rest));
  }
  return groups;
}

/// 分类查找（给测试和"这个任务属于哪一类"用）；未归类返回 null。
OasTaskCategory? categoryOf(String dir) {
  for (final OasTaskCategory c in kTaskCategories) {
    if (c.members.contains(dir)) return c;
  }
  return null;
}

/// 分组后的任务对象（按目录查回 OasTask，保持分组顺序）。
List<({OasTaskGroup group, List<OasTask> tasks})> groupTasks(List<OasTask> tasks) {
  final Map<String, OasTask> byDir = <String, OasTask>{
    for (final OasTask t in tasks) t.dir: t,
  };
  return <({OasTaskGroup group, List<OasTask> tasks})>[
    for (final OasTaskGroup g in groupTaskDirs(byDir.keys))
      (
        group: g,
        tasks: <OasTask>[
          for (final String d in g.dirs)
            if (byDir[d] != null) byDir[d]!,
        ],
      ),
  ];
}
