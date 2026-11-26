/// 阴阳师玩法中文名映射：`tasks/` 目录名 → 界面显示名（全面对齐电脑端 OAS 官方译名）
const Map<String, String> kTaskDisplayNames = <String, String>{
  'AbyssShadows': '狭间暗域',
  'ActivityShikigami': '当期爬塔',
  'AreaBoss': '地域鬼王',
  'AutoCheckinBigGod': '大神签到',
  'BondlingFairyland': '契灵之境',
  'BudokaiTournament': '武道大会',
  'Chess': '百鬼棋局',
  'CollectiveMissions': '集体任务',
  'DailyTrifles': '每日琐事',
  'Delegation': '式神委派',
  'DemonEncounter': '逢魔之时',
  'DemonRetreat': '首领退治',
  'Dokan': '道馆',
  'DraftDuel': '协同对弈',
  'Duel': '斗技',
  'DyeTrials': '灵染试炼',
  'EternitySea': '永生之海',
  'EvoZone': '觉醒副本',
  'ExperienceYoukai': '经验妖怪',
  'Exploration': '探索',
  'FallenSun': '日轮之陨',
  'FindJade': '寻找协作任务',
  'FloatParade': '花车巡游',
  'FrogBoss': '对弈竞猜',
  'FrogChallenge': '青蛙瓷器挑战赛',
  'GoryouRealm': '御灵之境',
  'GoldYoukai': '金币妖怪',
  'GuguArtStudio': '呱呱画室',
  'GuildActivityMonitor': '寮活动监控',
  'GuildBanquet': '寮宴会',
  'HeroTest': '英杰试炼',
  'Hunt': '狩猎战',
  'Hyakkiyakou': '百鬼夜行',
  'IbukiArena': '狭间幻境',
  'KekkaiActivation': '结界挂卡',
  'KekkaiUtilize': '结界蹭卡',
  'KittyShop': '猫咪铺子',
  'LBS': 'LBS鬼王',
  'MartialArts': '武道大会',
  'MemoryScrolls': '绘卷',
  'MetaDemon': '超鬼王',
  'MysteryShop': '神秘商店',
  'Nian': '年兽',
  'Orochi': '八岐大蛇',
    'OtherWorldTwilight': '彼世逢魔',
  'Pets': '小猫咪',
  'Quiz': '智力竞赛',
  'RealmRaid': '个人突破',
  'Restart': '重启',
  'RichMan': '大富翁',
  'RyouToppa': '寮突破',
  'SixRealms': '六道之门',
  'Sougenbi': '业原火',
  'SoulsTidy': '御魂整理',
  'Tako': '石距',
  'TalismanPass': '花合战',
  'TrueOrochi': '真八岐大蛇',
  'WantedQuests': '悬赏封印',
  'WeeklyPurchase': '每周购买',
  'WeeklyTrifles': '每周琐事',
  'Secret': '秘闻副本',
  'Script': '脚本',
  'GlobalGame': '全局配置',
};

/// 后端 `/script_menu` 下发的动态译名表（支持 GitCode 数据包热更新，无需重装 APK）
final Map<String, String> _runtimeTaskTitles = <String, String>{};

void updateRuntimeTaskTitles(Map<String, String> titles) {
  _runtimeTaskTitles.addAll(titles);
}

/// 公共库目录：出现在 tasks/ 里但不是可跑任务
const List<String> kNonTaskDirs = <String>[
  'Utils',
  'Component',
  'GameUi',
  'General',
];

/// 目录名 → 中文名（优先用后端热更词表，再查内置表，未收录回退英文原名）
String taskDisplayName(String dir) =>
    _runtimeTaskTitles[dir] ?? kTaskDisplayNames[dir] ?? dir;

/// 每个玩法专属一对一独立图标集合（assets/onmyoji/tasks/<TaskDir>.png，互不重复）
const Set<String> kDedicatedTaskIcons = <String>{
  'AbyssShadows', 'ActivityShikigami', 'AreaBoss', 'AutoCheckinBigGod',
  'BondlingFairyland', 'BudokaiTournament', 'Chess', 'CollectiveMissions',
  'DailyTrifles', 'Delegation', 'DemonEncounter', 'DemonRetreat',
  'Dokan', 'DraftDuel', 'Duel', 'DyeTrials',
  'EternitySea', 'EvoZone', 'ExperienceYoukai', 'Exploration',
  'FallenSun', 'FindJade', 'FloatParade', 'FrogBoss',
  'FrogChallenge', 'GoldYoukai', 'GoryouRealm', 'GlobalGame',
  'GuguArtStudio', 'GuildActivityMonitor', 'GuildBanquet', 'HeroTest',
  'Hunt', 'Hyakkiyakou', 'IbukiArena', 'KekkaiActivation',
  'KekkaiUtilize', 'KittyShop', 'LBS', 'MartialArts',
  'MemoryScrolls', 'MetaDemon', 'MysteryShop', 'Nian',
  'Orochi', 'OtherWorldTwilight', 'Pets',
  'Quiz', 'RealmRaid', 'Restart', 'RichMan',
  'RyouToppa', 'Secret', 'SixRealms', 'Sougenbi',
  'SoulsTidy', 'Tako', 'TalismanPass', 'TrueOrochi',
  'WantedQuests', 'WeeklyPurchase', 'WeeklyTrifles',
};

/// 任务/分类 → 对应的专属玩法图标（优先 1 对 1 专属图标，未收录再按分类兜底）
String taskIconAsset(String taskDirOrTitle) {
  final String trimmed = taskDirOrTitle.trim();
  if (kDedicatedTaskIcons.contains(trimmed)) {
    return 'assets/onmyoji/tasks/$trimmed.png';
  }
  for (final MapEntry<String, String> entry in kTaskDisplayNames.entries) {
    if (entry.value == trimmed && kDedicatedTaskIcons.contains(entry.key)) {
      return 'assets/onmyoji/tasks/${entry.key}.png';
    }
  }
  final String key = trimmed.toLowerCase();
  if (key.contains('orochi') ||
      key.contains('sougenbi') ||
      key.contains('fallen') ||
      key.contains('eternity') ||
      key.contains('souls') ||
      key.contains('御魂') ||
      key.contains('大蛇') ||
      key.contains('业原火') ||
      key.contains('日轮') ||
      key.contains('永生')) {
    return 'assets/onmyoji/cat_orochi.png';
  }
  if (key.contains('evo') ||
      key.contains('goryou') ||
      key.contains('secret') ||
      key.contains('sixrealms') ||
      key.contains('herotest') ||
      key.contains('觉醒') ||
      key.contains('御灵') ||
      key.contains('秘闻') ||
      key.contains('六道')) {
    return 'assets/onmyoji/cat_oote.png';
  }
  if (key.contains('kekkai') ||
      key.contains('daily') ||
      key.contains('weekly') ||
      key.contains('talisman') ||
      key.contains('pets') ||
      key.contains('richman') ||
      key.contains('mystery') ||
      key.contains('kitty') ||
      key.contains('结界') ||
      key.contains('琐事') ||
      key.contains('花合战') ||
      key.contains('商店') ||
      key.contains('采购') ||
      key.contains('大富翁')) {
    return 'assets/onmyoji/cat_kaguya.png';
  }
  if (key.contains('realm') ||
      key.contains('ryou') ||
      key.contains('duel') ||
      key.contains('dokan') ||
      key.contains('budokai') ||
      key.contains('martial') ||
      key.contains('突破') ||
      key.contains('斗技') ||
      key.contains('道馆') ||
      key.contains('百鬼弈')) {
    return 'assets/onmyoji/cat_yoto.png';
  }
  if (key.contains('guild') ||
      key.contains('collective') ||
      key.contains('hunt') ||
      key.contains('demonretreat') ||
      key.contains('寮') ||
      key.contains('集体') ||
      key.contains('狩猎') ||
      key.contains('退治')) {
    return 'assets/onmyoji/cat_kujaku.png';
  }
  if (key.contains('wanted') ||
      key.contains('exploration') ||
      key.contains('delegation') ||
      key.contains('tako') ||
      key.contains('youkai') ||
      key.contains('findjade') ||
      key.contains('悬赏') ||
      key.contains('探索') ||
      key.contains('委派') ||
      key.contains('石距') ||
      key.contains('妖怪')) {
    return 'assets/onmyoji/cat_ubume.png';
  }
  if (key.contains('abyss') ||
      key.contains('demon') ||
      key.contains('activity') ||
      key.contains('area') ||
      key.contains('meta') ||
      key.contains('nian') ||
      key.contains('bondling') ||
      key.contains('scroll') ||
      key.contains('活动') ||
      key.contains('狭间') ||
      key.contains('逢魔') ||
      key.contains('年兽') ||
      key.contains('鬼王') ||
      key.contains('爬塔') ||
      key.contains('契灵')) {
    return 'assets/onmyoji/cat_amaterasu.png';
  }
  return 'assets/onmyoji/cat_shiranui.png';
}

/// 过滤掉公共库后的任务名列表（仅在拿不到 /script_menu 时兜底用）
List<String> get kFallbackTaskNames =>
    kTaskDisplayNames.keys.toList(growable: false);
