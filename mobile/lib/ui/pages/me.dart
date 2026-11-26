import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../api.dart';
import '../avatar_store.dart';
import '../safety.dart';
import '../system_ui.dart';
import '../widgets.dart';
import 'guard_card.dart';
import 'ota_card.dart';

/// ⑤ 我的：狐面头像 + 权限与自愈状态集中管理。
///
/// 权限卡读真值：GET /health + GET /state（不再写死「已授权」）。
/// AI 自愈开关与月费用上限写 config；外观切换深浅主题（已接 oasThemeMode）。
class MePage extends StatefulWidget {
  const MePage({super.key});

  @override
  State<MePage> createState() => _MePageState();
}

class _MePageState extends State<MePage> {
  Map<String, dynamic>? _health;
  Map<String, dynamic>? _state;
  Map<String, dynamic>? _ai;
  Map<String, dynamic>? _sched;
  bool _loading = true;
  String? _error;
  OasAvatar _avatar = OasAvatar.all.first;

  @override
  void initState() {
    super.initState();
    _load();
    // 头像样式：内置几款字符头像，存在 app 私有目录，不碰相册权限
    OasAvatarStore.load().then((OasAvatar value) {
      if (mounted) setState(() => _avatar = value);
    });
    // 读一次「托管上限」，否则设置过的值要等点开对话框才显示
    OasSafety.i.load().then((_) {
      if (mounted) setState(() {});
    });
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final List<Map<String, dynamic>> both = await Future.wait(
        <Future<Map<String, dynamic>>>[
          OasApi.i.health(),
          OasApi.i.state(),
          OasApi.i.aiHealth(),
          OasApi.i.schedulerStatus(),
        ],
      );
      if (!mounted) return;
      setState(() {
        _health = both[0];
        _state = both[1];
        _ai = both[2];
        _sched = both[3];
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '读不到权限状态：$e';
        _loading = false;
      });
    }
  }

  /// 后端 /state.shizuku 形如 "binder=true permission=false backend=none"
  ({bool binder, bool permission, String backend}) get _shizuku {
    final String raw = '${_state?['shizuku'] ?? ''}';
    bool pick(String key) => RegExp('$key=(true|false)').firstMatch(raw)?.group(1) == 'true';
    final String backend =
        RegExp('backend=([a-z]+)').firstMatch(raw)?.group(1) ?? 'none';
    return (binder: pick('binder'), permission: pick('permission'), backend: backend);
  }

  /// 顶部一行真实战绩：轮次来自 /scheduler/status，没有就老实说没跑过。
  String get _profileLine {
    final Object? rawRound = _sched?['round'];
    final int rounds = (rawRound is num) ? rawRound.toInt() : 0;
    if (_sched == null) return '正在读取托管记录…';
    if (rounds <= 0) return '还没跑过 · 点「庭院」开始托管';
    final String running = _sched?['running'] == true ? '托管中' : '空闲';
    return '累计 $rounds 轮 · 当前$running';
  }

  bool get _accessibilityOk => _health?['accessibility_ready'] == true;

  bool get _backgroundOk => _health?['background_service'] == true;

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return SafeArea(
      bottom: false,
      child: RefreshIndicator(
        onRefresh: _load,
        color: p.vermilion,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 24),
          physics: const AlwaysScrollableScrollPhysics(
            parent: BouncingScrollPhysics(),
          ),
          children: <Widget>[
            OasPageHeader(
              badge: '晴',
              badgeAsset: 'assets/onmyoji/dock_me.png',
              title: '我的',
              subtitle: '平安京庭院 · 换肤与同步中心',
              badgeColor: p.purple,
            ),
            const SizedBox(height: 14),
            _profileCard(context, p),
            const SizedBox(height: 12),
            _courtyardSkinCard(context, p),
            const SizedBox(height: 12),
            const OasOtaCard(),
            const SizedBox(height: 12),
            const OasGuardCard(),
            const SizedBox(height: 12),
            _permissionCard(context, p),
            const SizedBox(height: 12),
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: _pickAiConfig,
              child: _aiCard(context, p),
            ),
            const SizedBox(height: 12),
            OasRowItem(
              badge: '日',
              badgeAsset: 'assets/onmyoji/cat_bukkuman.png',
              badgeColor: p.purple,
              title: '运行日志',
              subtitle: '今天的执行记录与 AI 修复过程',
              trailing: Icon(Icons.chevron_right, size: 16, color: p.inkDim),
              onTap: _openLogs,
          ),
          const SizedBox(height: 9),
          OasRowItem(
            badge: '号',
            badgeAsset: 'assets/onmyoji/cut_kagura.png',
            badgeColor: p.vermilion,
            title: '当前配置（账号）',
            subtitle: '${OasApi.i.activeConfig} · 点一下切换',
            trailing: Icon(Icons.chevron_right, size: 16, color: p.inkDim),
            onTap: _pickConfig,
          ),
          const SizedBox(height: 9),
          OasRowItem(
            badge: '限',
            badgeAsset: 'assets/onmyoji/cut_yaobiquni.png',
            badgeColor: p.gold,
            title: '托管上限',
            subtitle: OasSafety.i.maxRoundsPerDay > 0
                ? '每日最多 ${OasSafety.i.maxRoundsPerDay} 轮 · 到顶自动停'
                : '不限制 · 点一下设上限',
            trailing: Icon(Icons.chevron_right, size: 16, color: p.inkDim),
            onTap: _pickRoundLimit,
          ),
          const SizedBox(height: 9),
          OasRowItem(
            badge: '外',
            badgeAsset: 'assets/onmyoji/cat_shiranui.png',
            badgeColor: p.jade,
            title: '外观 · 深色 / 和纸',
            subtitle: Theme.of(context).brightness == Brightness.dark
                ? '当前：深色'
                : '当前：和纸浅色',
            trailing: Icon(Icons.chevron_right, size: 16, color: p.inkDim),
            onTap: () {
              final bool toDark = oasThemeMode.value != ThemeMode.dark;
              oasThemeMode.value =
                  toDark ? ThemeMode.dark : ThemeMode.light;
              OasToast.show(context, toDark ? '已切到深色' : '已切回和纸浅色');
            },
          ),
          ],
        ),
      ),
    );
  }

  Widget _profileCard(BuildContext context, OasPalette p) {
    return OasCard(
      padding: const EdgeInsets.fromLTRB(14, 14, 14, 14),
      gradient: LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: <Color>[p.surface, p.surfaceAlt],
      ),
      child: Row(
        children: <Widget>[
          Container(
            width: 52,
            height: 52,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: p.surfaceAlt.withOpacity(0.45),
              border: Border.all(color: p.gold.withOpacity(0.65), width: 1.5),
            ),
            child: Padding(
              padding: const EdgeInsets.all(2),
              child: Image.asset(
                'assets/onmyoji/dock_me.png',
                fit: BoxFit.contain,
                errorBuilder: (_, __, ___) => Text(
                  _avatar.char,
                  style: TextStyle(
                    fontFamily: OasFont.serif,
                    fontFamilyFallback: OasFont.serifFallback,
                    fontSize: 20,
                    height: 1,
                    fontWeight: FontWeight.w700,
                    color: p.goldHi,
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  '晴明大人 · ${OasApi.i.activeConfig}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: OasFont.song(size: 16, color: p.ink),
                ),
                const SizedBox(height: 4),
                Text(
                  _profileLine,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 11, color: p.inkDim),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          OasPill(
            text: '改头像',
            color: p.inkSoft,
            outlined: true,
            onTap: _pickAvatar,
          ),
        ],
      ),
    );
  }

  /// 5 款阴阳师庭院皮肤切换 + 随机换肤 + 从相册自定义庭院壁纸
  Widget _courtyardSkinCard(BuildContext context, OasPalette p) {
    return ValueListenableBuilder<OasSkinState>(
      valueListenable: OasSkinController.i.state,
      builder: (BuildContext context, OasSkinState skinState, Widget? _) {
        final OasCourtyardSkin active = skinState.skin;
        return OasCard(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Text(
                    '庭院皮肤',
                    style: TextStyle(
                      fontSize: 13.5,
                      fontWeight: FontWeight.w700,
                      color: p.ink,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      skinState.hasCustomWallpaper
                          ? '已使用相册自定义壁纸'
                          : active.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 11, color: p.gold),
                    ),
                  ),
                  OasPill(
                    text: '🎲 随机换肤',
                    color: p.vermilion,
                    onTap: () async {
                      final OasCourtyardSkin picked =
                          await OasSkinController.i.randomSkin();
                      if (!context.mounted) return;
                      OasToast.show(context, '已切换庭院：${picked.name}');
                    },
                  ),
                ],
              ),
              const SizedBox(height: 10),
              SizedBox(
                height: 92,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: OasCourtyardSkin.all.length,
                  separatorBuilder: (_, __) => const SizedBox(width: 8),
                  itemBuilder: (BuildContext ctx, int idx) {
                    final OasCourtyardSkin sk = OasCourtyardSkin.all[idx];
                    final bool selected = !skinState.hasCustomWallpaper &&
                        skinState.skinId == sk.id;
                    return GestureDetector(
                      onTap: () async {
                        await OasSkinController.i.selectSkin(sk.id);
                        if (!context.mounted) return;
                        OasToast.show(context, '已切换：${sk.name}');
                      },
                      child: SizedBox(
                        width: 74,
                        child: Column(
                          children: <Widget>[
                            Expanded(
                              child: Container(
                                decoration: BoxDecoration(
                                  borderRadius: BorderRadius.circular(10),
                                  border: Border.all(
                                    color: selected ? sk.gold : p.line,
                                    width: selected ? 2.2 : 1,
                                  ),
                                ),
                                clipBehavior: Clip.antiAlias,
                                child: Stack(
                                  fit: StackFit.expand,
                                  children: <Widget>[
                                    Image.asset(
                                      sk.assetPath,
                                      fit: BoxFit.cover,
                                    ),
                                    if (selected)
                                      Align(
                                        alignment: Alignment.topRight,
                                        child: Container(
                                          margin: const EdgeInsets.all(3),
                                          padding: const EdgeInsets.all(2),
                                          decoration: BoxDecoration(
                                            color: sk.accent,
                                            shape: BoxShape.circle,
                                          ),
                                          child: const Icon(
                                            Icons.check,
                                            size: 10,
                                            color: Colors.white,
                                          ),
                                        ),
                                      ),
                                  ],
                                ),
                              ),
                            ),
                            const SizedBox(height: 4),
                            Text(
                              sk.name,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                fontSize: 10,
                                fontWeight: selected
                                    ? FontWeight.w700
                                    : FontWeight.w400,
                                color: selected ? p.gold : p.inkDim,
                              ),
                            ),
                          ],
                        ),
                      ),
                    );
                  },
                ),
              ),
              const SizedBox(height: 10),
              Row(
                children: <Widget>[
                  Expanded(
                    child: OasSoftButton(
                      label: '从相册自定义壁纸',
                      color: p.gold,
                      onTap: () async {
                        try {
                          final FilePickerResult? res =
                              await FilePicker.platform.pickFiles(
                            type: FileType.image,
                          );
                          final String? path = res?.files.single.path;
                          if (path == null || path.isEmpty) return;
                          final bool ok = await OasSkinController.i
                              .setCustomWallpaperFromFile(path);
                          if (!context.mounted) return;
                          OasToast.show(
                            context,
                            ok ? '已应用相册壁纸' : '读取图片失败',
                          );
                        } catch (e) {
                          if (!context.mounted) return;
                          OasToast.show(context, '打开相册失败：$e');
                        }
                      },
                    ),
                  ),
                  if (skinState.hasCustomWallpaper) ...<Widget>[
                    const SizedBox(width: 8),
                    OasSoftButton(
                      label: '恢复内置',
                      color: p.inkSoft,
                      onTap: () async {
                        await OasSkinController.i.clearCustomWallpaper();
                        if (!context.mounted) return;
                        OasToast.show(context, '已恢复内置庭院皮肤');
                      },
                    ),
                  ],
                ],
              ),
              const SizedBox(height: 6),
              Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      '每次启动 App 随机换庭院皮肤',
                      style: TextStyle(fontSize: 12, color: p.inkSoft),
                    ),
                  ),
                  Switch.adaptive(
                    value: skinState.randomOnLaunch,
                    activeColor: p.vermilion,
                    onChanged: (bool v) =>
                        OasSkinController.i.setRandomOnLaunch(v),
                  ),
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _permissionCard(BuildContext context, OasPalette p) {
    final ({bool binder, bool permission, String backend}) shz = _shizuku;
    final bool shzOk = shz.binder && shz.permission;
    final bool a11yOk = _accessibilityOk;
    final bool bgOk = _backgroundOk;

    final List<({String title, String subtitle, String status, bool ok, bool required})> rows =
        <({String title, String subtitle, String status, bool ok, bool required})>[
      if (_loading)
        (title: '正在检查…', subtitle: '读取 /health 与 /state', status: '—', ok: false, required: true)
      else if (_error != null)
        (title: '读取失败', subtitle: '下拉刷新重试', status: '未知', ok: false, required: true)
      else ...<({String title, String subtitle, String status, bool ok, bool required})>[
        (
          title: 'Shizuku',
          subtitle: shz.binder
              ? (shz.permission ? '后台虚拟屏 + 直连点击（${shz.backend}）' : '服务在跑，但本 App 未授权')
              : '服务没启动，点开 Shizuku 点「启动」',
          status: shzOk ? '已授权' : (shz.binder ? '待授权' : '未启动'),
          ok: shzOk,
          required: true,
        ),
        (
          title: '后台保活',
          subtitle: bgOk ? '前台服务在跑' : '前台服务未起',
          status: bgOk ? '正常' : '去设置',
          ok: bgOk,
          required: true,
        ),
        (
          title: '无障碍服务（可选）',
          subtitle: a11yOk
              ? '已开启（备用）'
              : (shzOk ? '已由 Shizuku 接管，无需开启' : '仅无 Shizuku 时备用'),
          status: a11yOk ? '已开启' : '可选',
          ok: a11yOk || shzOk,
          required: false,
        ),
      ],
    ];

    final List<({String title, String subtitle, String status, bool ok, bool required})> requiredRows =
        rows.where((({String title, String subtitle, String status, bool ok, bool required}) r) => r.required).toList();
    final int okCount = requiredRows.where((({String title, String subtitle, String status, bool ok, bool required}) r) => r.ok).length;
    final bool allReady = !_loading && _error == null && requiredRows.isNotEmpty && okCount == requiredRows.length;
    final String summary = _loading
        ? '检查中'
        : (_error != null ? '未知' : (allReady ? '全部就绪' : '$okCount / ${requiredRows.length} 就绪'));

    return OasCard(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Text(
                '权限',
                style: TextStyle(
                  fontSize: 13,
                  height: 1.2,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.5,
                  color: p.ink,
                ),
              ),
              const Spacer(),
              Text(
                summary,
                style: TextStyle(
                  fontSize: 11,
                  color: allReady ? p.jade : p.vermilion,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          for (int i = 0; i < rows.length; i++) ...<Widget>[
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 7),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Text.rich(
                      TextSpan(
                        children: <InlineSpan>[
                          TextSpan(
                            text: rows[i].title,
                            style: TextStyle(
                              fontSize: 12.5,
                              color: p.ink,
                            ),
                          ),
                          TextSpan(
                            text: '   ${rows[i].subtitle}',
                            style: TextStyle(
                              fontSize: 10.5,
                              color: p.inkDim,
                            ),
                          ),
                        ],
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 8),
                  OasPill(
                    text: rows[i].status,
                    color: rows[i].ok ? p.jade : p.inkDim,
                    outlined: !rows[i].ok,
                    dot: rows[i].ok,
                    onTap: () async {
                      if (rows[i].ok) {
                        OasToast.show(context, '${rows[i].title}：已就绪');
                        return;
                      }
                      final String page = switch (rows[i].title) {
                        'Shizuku' => 'shizuku',
                        '无障碍服务（可选）' || '无障碍服务' => 'accessibility',
                        '后台保活' => 'notification',
                        _ => '',
                      };
                      if (page.isEmpty) return;
                      final bool opened = await OasSystem.open(page);
                      if (!context.mounted) return;
                      OasToast.show(
                        context,
                        opened
                            ? '已打开系统设置，开好后返回本页会自动刷新'
                            : '打不开系统设置页，请手动到 设置 → ${rows[i].title}',
                      );
                    },
                  ),
                ],
              ),
            ),
            if (i != rows.length - 1) const OasDivider(),
          ],
        ],
      ),
    );
  }

  /// 每日轮次硬上限：到顶就不再自动开新一轮（护栏在 App 侧，不写进 OAS 配置）。
  Future<void> _pickRoundLimit() async {
    await OasSafety.i.load();
    if (!mounted) return;
    final TextEditingController ctrl = TextEditingController(
      text: OasSafety.i.maxRoundsPerDay == 0
          ? ''
          : '${OasSafety.i.maxRoundsPerDay}',
    );
    final String? picked;
    try {
      picked = await showDialog<String>(
        context: context,
        builder: (BuildContext ctx) => AlertDialog(
          title: const Text('每日最多跑几轮'),
          content: TextField(
            controller: ctrl,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
              hintText: '例如 20；留空或 0 = 不限制',
            ),
          ),
          actions: <Widget>[
            TextButton(
              onPressed: () => Navigator.of(ctx).pop('0'),
              child: const Text('不限制'),
            ),
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(ctrl.text.trim()),
              child: const Text('保存'),
            ),
          ],
        ),
      );
    } finally {
      ctrl.dispose();
    }
    if (picked == null) return;
    final int value = int.tryParse(picked) ?? 0;
    final int previous = OasSafety.i.maxRoundsPerDay;
    OasSafety.i.maxRoundsPerDay = value < 0 ? 0 : value;
    final bool saved = await OasSafety.i.save();
    if (!saved) OasSafety.i.maxRoundsPerDay = previous;
    if (!mounted) return;
    setState(() {});
    if (!saved) {
      OasToast.show(context, '保存托管上限失败');
      return;
    }
    OasToast.show(context, '已设置：${OasSafety.i.label}');
  }

  Future<void> _pickConfig() async {
    final List<String> names = await OasApi.i.configList();
    if (!mounted) return;
    if (names.isEmpty) {
      OasToast.show(context, '读不到配置列表（/config_list）');
      return;
    }
    final String? picked = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: Colors.transparent,
      builder: (BuildContext c) {
        final OasPalette p = c.oas;
        return Container(
          margin: const EdgeInsets.all(12),
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 18),
          decoration: BoxDecoration(
            color: p.surface,
            borderRadius: BorderRadius.circular(kRadiusCard),
            border: Border.all(color: p.line),
          ),
          child: ListView(
            padding: EdgeInsets.zero,
            shrinkWrap: true,
            children: <Widget>[
              Text('选择配置（账号）',
                  style: OasFont.song(size: 15, color: p.ink, weight: FontWeight.w700)),
              const SizedBox(height: 10),
              for (final String name in names)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: OasSoftButton(
                    label: name == OasApi.i.activeConfig ? '$name（当前）' : name,
                    color: name == OasApi.i.activeConfig ? p.vermilion : p.inkSoft,
                    onTap: () => Navigator.of(c).pop(name),
                  ),
                ),
            ],
          ),
        );
      },
    );
    if (picked == null || !mounted) return;
    final bool saved = await OasApi.i.selectConfig(picked);
    if (!mounted) return;
    if (!saved) {
      OasToast.show(context, '保存配置选择失败');
      return;
    }
    setState(() {});
    OasToast.show(context, '已切换到 $picked，下次启动托管就用它');
  }

  Future<void> _pickAvatar() async {
    await showModalBottomSheet<void>(
      context: context,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      builder: (BuildContext c) {
        return Padding(
          padding: const EdgeInsets.fromLTRB(18, 16, 18, 22),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              const Text('选择头像',
                  style:
                      TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
              const SizedBox(height: 4),
              const Text('内置字符头像，不需要相册权限、不会弹授权框',
                  style: TextStyle(fontSize: 11)),
              const SizedBox(height: 14),
              Row(
                children: <Widget>[
                  for (final OasAvatar item in OasAvatar.all) ...<Widget>[
                    Expanded(
                      child: InkWell(
                        onTap: () async {
                          final bool ok = await OasAvatarStore.save(item);
                          if (!c.mounted) return;
                          Navigator.of(c).pop();
                          if (ok) setState(() => _avatar = item);
                          if (!mounted) return;
                          OasToast.show(context,
                              ok ? '头像已换成「${item.label}」' : '保存失败，请重试');
                        },
                        child: Column(
                          children: <Widget>[
                            Container(
                              width: 52,
                              height: 52,
                              alignment: Alignment.center,
                              decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                color: item.id == _avatar.id
                                    ? const Color(0xFF3A2A22)
                                    : const Color(0xFFEDE3D4),
                              ),
                              child: Text(
                                item.char,
                                style: TextStyle(
                                  fontSize: 20,
                                  fontWeight: FontWeight.w700,
                                  color: item.id == _avatar.id
                                      ? const Color(0xFFE8C87A)
                                      : const Color(0xFF6B5B4A),
                                ),
                              ),
                            ),
                            const SizedBox(height: 6),
                            Text(item.label,
                                style: const TextStyle(fontSize: 11)),
                          ],
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  Future<void> _openLogs() async {
    final Map<String, dynamic> data = await OasApi.i.logTail(lines: 200);
    if (!mounted) return;
    final List<String> lines = <String>[
      for (final Object? item in (data['lines'] as List<Object?>? ?? <Object?>[]))
        '$item',
    ];
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      builder: (BuildContext c) {
        return DraggableScrollableSheet(
          expand: false,
          initialChildSize: 0.7,
          maxChildSize: 0.95,
          minChildSize: 0.4,
          builder: (BuildContext c, ScrollController controller) {
            return Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      const Text('运行日志',
                          style: TextStyle(
                              fontSize: 16, fontWeight: FontWeight.w700)),
                      const Spacer(),
                      Text(
                        data['file'] == null
                            ? '还没有日志文件'
                            : '${data['file']}',
                        style: const TextStyle(fontSize: 11),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Expanded(
                    child: lines.isEmpty
                        ? const Center(
                            child: Text(
                              '还没有运行日志\n跑一次托管后，这里会出现执行记录与 AI 修复过程',
                              textAlign: TextAlign.center,
                              style: TextStyle(fontSize: 12),
                            ),
                          )
                        : ListView.builder(
                            controller: controller,
                            itemCount: lines.length,
                            itemBuilder: (BuildContext c, int i) => Padding(
                              padding: const EdgeInsets.symmetric(vertical: 2),
                              child: SelectableText(
                                lines[i],
                                style: const TextStyle(
                                    fontSize: 11, fontFamily: 'monospace'),
                              ),
                            ),
                          ),
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }

  Future<void> _pickAiConfig() async {
    final Map<String, dynamic> cur = await OasApi.i.aiConfig();
    if (!mounted) return;
    if (cur['ok'] != true) {
      OasToast.show(context, '读不到 AI 修复配置：${cur['error'] ?? '未知'}');
      return;
    }
    final TextEditingController keyCtrl = TextEditingController();
    final TextEditingController modelCtrl =
        TextEditingController(text: '${cur['model'] ?? 'deepseek-flash'}');
    String mode = '${cur['mode'] ?? 'observe'}';
    final bool hasKey = cur['has_key'] == true;

    try {
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
        ),
        builder: (BuildContext c) {
          return StatefulBuilder(
            builder: (BuildContext c, StateSetter setSheet) {
              return Padding(
                padding: EdgeInsets.fromLTRB(
                    18, 16, 18, 18 + MediaQuery.of(c).viewInsets.bottom),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: <Widget>[
                    const Text('AI 修复设置（DeepSeek）',
                        style: TextStyle(
                            fontSize: 16, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 6),
                    Text(
                      hasKey
                          ? 'Key 已配置（不会显示出来，填空即覆盖）'
                          : '还没 Key：填一个才会生效',
                      style: const TextStyle(fontSize: 12),
                    ),
                    const SizedBox(height: 12),
                    Row(
                      children: <Widget>[
                        const Text('工作模式'),
                        const Spacer(),
                        ChoiceChip(
                          label: const Text('观察'),
                          selected: mode == 'observe',
                          onSelected: (bool _) =>
                              setSheet(() => mode = 'observe'),
                        ),
                        const SizedBox(width: 8),
                        ChoiceChip(
                          label: const Text('自动'),
                          selected: mode == 'auto',
                          onSelected: (bool _) =>
                              setSheet(() => mode = 'auto'),
                        ),
                      ],
                    ),
                    TextField(
                      controller: modelCtrl,
                      decoration: const InputDecoration(labelText: '模型'),
                    ),
                    TextField(
                      controller: keyCtrl,
                      obscureText: true,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        labelText: 'API Key',
                        hintText: '留空 = 不改',
                      ),
                    ),
                    const SizedBox(height: 14),
                    Row(
                      children: <Widget>[
                        Expanded(
                          child: TextButton(
                            onPressed: () => Navigator.of(c).pop(),
                            child: const Text('取消'),
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: ElevatedButton(
                            onPressed: () async {
                              final Map<String, dynamic> r =
                                  await OasApi.i.aiConfigSave(
                                <String, dynamic>{
                                  'mode': mode,
                                  'model': modelCtrl.text.trim(),
                                  if (keyCtrl.text.trim().isNotEmpty)
                                    'api_key': keyCtrl.text.trim(),
                                },
                              );
                              if (!c.mounted) return;
                              Navigator.of(c).pop();
                              await _load();
                              if (!mounted) return;
                              OasToast.show(
                                context,
                                r['ok'] == true
                                    ? '已保存：${r['mode']} · ${r['model']}'
                                    : '保存失败：${r['error'] ?? '未知'}',
                              );
                            },
                            child: const Text('保存'),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              );
            },
          );
        },
      );
    } finally {
      keyCtrl.dispose();
      modelCtrl.dispose();
    }
  }

  Widget _aiCard(BuildContext context, OasPalette p) {
    final bool aiOk = _ai != null;
    final bool aiOn = _ai?['enabled'] == true;
    final bool hasKey = _ai?['has_key'] == true;
    final String mode = _aiModeText('${_ai?['mode'] ?? ''}');
    final String model = '${_ai?['model'] ?? ''}';

    return OasCard(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Text(
                'AI 修复',
                style: TextStyle(
                  fontSize: 13,
                  height: 1.2,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.5,
                  color: p.ink,
                ),
              ),
              const Spacer(),
              OasPill(
                text: !aiOk ? '未知' : (aiOn ? '已开启' : '已关闭'),
                color: aiOn ? p.vermilion : p.inkDim,
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      '工作模式',
                      style: TextStyle(fontSize: 10.5, color: p.inkDim),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      aiOk ? mode : '—',
                      style: OasFont.song(
                        size: 15,
                        color: aiOn ? p.ink : p.inkDim,
                        weight: FontWeight.w700,
                      ),
                    ),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: <Widget>[
                  Text(
                    'API Key',
                    style: TextStyle(fontSize: 10.5, color: p.inkDim),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    !aiOk ? '—' : (hasKey ? '已配置' : '未配置'),
                    style: OasFont.song(
                      size: 14,
                      color: hasKey ? p.jade : p.vermilion,
                      weight: FontWeight.w700,
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            !aiOk
                ? '读不到 AI 状态（/ai/health）'
                : (hasKey
                    ? '模型 $model · 卡住时按「$mode」处理'
                    : '模型 $model · 缺少 Key，AI 修复不会生效（在桌面端或设置里填 Key）'),
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 10.5, color: p.inkDim),
          ),
        ],
      ),
    );
  }

  String _aiModeText(String mode) {
    switch (mode) {
      case 'observe':
        return '观察（只判断不操作）';
      case 'auto':
        return '自动（自己点）';
      case 'assist':
        return '辅助（给建议）';
      case 'off':
        return '关闭';
      default:
        return mode.isEmpty ? '未知' : mode;
    }
  }
}
