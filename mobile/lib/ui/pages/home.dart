import 'package:flutter/material.dart';

import '../api.dart';
import '../shell.dart';
import '../task_names.dart';
import '../widgets.dart';
import 'assets_card.dart';
import 'task_args.dart';

/// ① 庭院：一眼看清状态，主按钮只有一个；下方实时展示与电脑端 OAS 一致的「任务排序」。
///
/// 数据来自 GET /state、/health、/scheduler/status、/scheduler/queue；主按钮接 POST /scheduler/start|stop。
class HomePage extends StatefulWidget {
  const HomePage({super.key, this.api});

  final OasApi? api;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  OasApi get _api => widget.api ?? OasApi.i;
  Map<String, dynamic>? _health;
  Map<String, dynamic>? _state;
  Map<String, dynamic>? _sched;
  Map<String, dynamic>? _task;
  List<String> _enabledPlan = <String>[];
  List<Map<String, dynamic>> _queue = <Map<String, dynamic>>[];
  int _pendingCount = 0;
  int _waitingCount = 0;
  bool _loading = true;
  bool _expandedQueue = false;
  String? _pinningTask;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final List<Map<String, dynamic>> r = await Future.wait(
        <Future<Map<String, dynamic>>>[
          _api.health(),
          _api.state(),
          _api.schedulerStatus(),
          _api.taskStatus(),
          _api.schedulerQueue(config: _api.activeConfig),
        ],
      );
      final Map<String, bool> enabled =
          await _api.taskEnabled(_api.activeConfig);
      final List<OasTask> tasks = await _api.tasks();
      if (!mounted) return;
      final Map<String, dynamic> qData = r[4];
      final List<Map<String, dynamic>> parsedQueue = <Map<String, dynamic>>[];
      if (qData['queue'] is List) {
        for (final Object? item in qData['queue'] as List) {
          if (item is Map) parsedQueue.add(Map<String, dynamic>.from(item));
        }
      }
      setState(() {
        _enabledPlan = tasks
            .where((OasTask item) => enabled[item.dir] == true)
            .map((OasTask item) => item.title)
            .toList();
        _health = r[0];
        _state = r[1];
        _sched = r[2];
        _task = r[3];
        _queue = parsedQueue;
        _pendingCount = (qData['pending_count'] is num)
            ? (qData['pending_count'] as num).toInt()
            : parsedQueue.where((Map<String, dynamic> e) => e['status'] == 'pending').length;
        _waitingCount = (qData['waiting_count'] is num)
            ? (qData['waiting_count'] as num).toInt()
            : parsedQueue.where((Map<String, dynamic> e) => e['status'] == 'waiting').length;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  Future<void> _pinTask(String taskName, String title) async {
    if (_pinningTask != null) return;
    setState(() => _pinningTask = taskName);
    try {
      final Map<String, dynamic> res =
          await _api.pinTask(taskName, config: _api.activeConfig);
      if (!mounted) return;
      if (res['ok'] == true) {
        if (res['queue'] is List) {
          final List<Map<String, dynamic>> updated = <Map<String, dynamic>>[
            for (final Object? item in res['queue'] as List)
              if (item is Map) Map<String, dynamic>.from(item),
          ];
          setState(() {
            _queue = updated;
            _pendingCount = (res['pending_count'] is num)
                ? (res['pending_count'] as num).toInt()
                : _pendingCount;
            _waitingCount = (res['waiting_count'] is num)
                ? (res['waiting_count'] as num).toInt()
                : _waitingCount;
          });
        } else {
          await _load();
        }
        if (!mounted) return;
        OasToast.show(context, '已将「$title」置顶到下一位');
      } else {
        OasToast.show(context, '置顶失败：${res['error'] ?? '后端未接受'}');
      }
    } catch (e) {
      if (!mounted) return;
      OasToast.show(context, '置顶失败：$e');
    } finally {
      if (mounted) setState(() => _pinningTask = null);
    }
  }

  Future<void> _openTaskArgs(String taskDir, String title) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => TaskArgsPage(
          script: _api.activeConfig,
          task: taskDir,
          title: title,
        ),
      ),
    );
    if (mounted) _load();
  }

  bool get _running => _sched?['running'] == true || _task?['running'] == true;

  bool get _paused => _state?['paused'] == true;

  String get _planLine {
    final Object? raw = _sched?['tasks'];
    if (_running &&
        _sched?['config'] == _api.activeConfig &&
        raw is List &&
        raw.isNotEmpty) {
      final List<String> names = <String>[];
      for (final Object? item in raw) {
        if (item is Map) {
          final Object? n = item['name'] ?? item['task'] ?? item['title'];
          if (n != null) names.add(taskDisplayName('$n'));
        } else if (item != null) {
          names.add(taskDisplayName('$item'));
        }
      }
      if (names.isNotEmpty) return names.join(' + ');
    }
    if (_enabledPlan.isNotEmpty) {
      final String names = _enabledPlan.take(3).join('、');
      return '已启用 ${_enabledPlan.length} 项 · $names${_enabledPlan.length > 3 ? '…' : ''}';
    }
    return '还没排方案 · 点右边「改」加任务';
  }

  String get _accountSubline {
    if (_sched == null) return '正在读取…';
    final String cfg = _api.activeConfig;
    if (_queue.isNotEmpty) {
      final Map<String, dynamic> first = _queue.first;
      if (first['status'] == 'running') {
        final String t = taskDisplayName('${first['name'] ?? ''}');
        return '$cfg · 正在运行「$t」· 余 $_pendingCount 项待执行';
      }
      return '$cfg · 待执行 $_pendingCount 项 · 等待中 $_waitingCount 项';
    }
    return '$cfg · 暂未启用任务';
  }

  bool get _online => _health?['ok'] == true;

  Future<void> _toggle() async {
    if (_busy) return;
    final bool stopping = _running;
    setState(() => _busy = true);
    try {
      final bool ok =
          stopping ? await _api.stopScheduler() : await _api.startScheduler();
      if (!mounted) return;
      await _load();
      if (!mounted) return;
      final String message;
      if (ok) {
        message = stopping ? '已停止托管' : '已开始托管';
      } else if (!stopping && _sched?['limit_reached'] == true) {
        message = '已到今日托管上限';
      } else {
        message = '后端没有接受这次操作';
      }
      OasToast.show(context, message);
    } catch (e) {
      if (!mounted) return;
      OasToast.show(context, '操作失败：$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 格式化 `next_run` 为手机易读文案（返回：短胶囊文案 + 详细副标题）
  ({String badge, String detail}) _formatQueueTime(
      String status, String nextRun) {
    if (status == 'running') {
      return (badge: '运行中', detail: '正在执行中 · 实时进度同步至通知栏');
    }
    final DateTime? dt = DateTime.tryParse(nextRun);
    if (status == 'pending' || dt == null) {
      final String hhmm = (dt != null)
          ? '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}'
          : '';
      return (
        badge: '待执行',
        detail: hhmm.isNotEmpty ? '已到点 · $hhmm' : '已到点 · 等待轮到执行',
      );
    }
    final DateTime now = DateTime.now();
    final Duration diff = dt.difference(now);
    final String hhmm =
        '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
    if (diff.inSeconds <= 0) {
      return (badge: '待执行', detail: '已到点 · $hhmm');
    }
    if (diff.inMinutes < 60) {
      final int mins = diff.inMinutes.clamp(1, 59);
      return (badge: '$mins分后', detail: '$mins 分钟后 · $hhmm');
    }
    if (diff.inHours < 24 && dt.day == now.day) {
      return (badge: '今日 $hhmm', detail: '${diff.inHours} 小时后 · 今日 $hhmm');
    }
    final DateTime tomorrow = now.add(const Duration(days: 1));
    if (dt.year == tomorrow.year &&
        dt.month == tomorrow.month &&
        dt.day == tomorrow.day) {
      return (badge: '明日 $hhmm', detail: '明日 $hhmm 执行');
    }
    return (
      badge: '${dt.month}/${dt.day} $hhmm',
      detail: '${dt.month}月${dt.day}日 $hhmm',
    );
  }

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return SafeArea(
      bottom: false,
      child: RefreshIndicator(
        onRefresh: _load,
        color: p.vermilion,
        child: ListView(
          cacheExtent: 1000,
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 24),
          physics: const AlwaysScrollableScrollPhysics(
            parent: BouncingScrollPhysics(),
          ),
          children: <Widget>[
            OasPageHeader(
              badge: '庭',
              badgeAsset: 'assets/onmyoji/dock_home.png',
              title: '庭院',
              subtitle: '阴阳师 · ${OasSkinController.i.current.name}',
              trailing: Row(
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  OasPill(
                    text: '换庭院',
                    color: p.gold,
                    outlined: true,
                    onTap: () async {
                      final OasCourtyardSkin picked =
                          await OasSkinController.i.randomSkin();
                      if (!context.mounted) return;
                      setState(() {});
                      OasToast.show(context, '已换庭院：${picked.name}');
                    },
                  ),
                  const SizedBox(width: 6),
                  OasPill(
                    text: _loading ? '连接中' : (_online ? '已连接' : '未连接'),
                    color: _online ? p.jade : p.vermilion,
                    dot: true,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
            _accountCard(context, p),
            const SizedBox(height: 14),
            Row(
              children: <Widget>[
                Expanded(
                  child: OasStatCell(
                    value: _loading
                        ? '…'
                        : (_running ? (_paused ? '已暂停' : '运行中') : '空闲'),
                    label: '状态',
                    valueColor: _running ? p.jade : p.inkDim,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OasStatCell(
                    value: '${_sched?['round'] ?? 0}',
                    unit: '/ ${_enabledPlan.length} 项',
                    label: '托管轮次',
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OasStatCell(
                    value: _error == null ? '0' : '—',
                    label: '异常 · AI 修复',
                  ),
                ),
              ],
            ),
            const SizedBox(height: 18),
            const OasSectionHeader(title: '据点', trailing: '长按可编辑'),
            const SizedBox(height: 8),
            _baseGrid(context, p),
            const SizedBox(height: 18),
            _queueSectionHeader(p),
            const SizedBox(height: 8),
            _queueSectionBody(context, p),
            const SizedBox(height: 16),
            OasAssetsCard(api: _api),
          ],
        ),
      ),
    );
  }

  Widget _queueSectionHeader(OasPalette p) {
    return Row(
      children: <Widget>[
        Text(
          '任务排序',
          style: TextStyle(
            fontSize: 13.5,
            fontWeight: FontWeight.w700,
            color: p.ink,
          ),
        ),
        const SizedBox(width: 6),
        Text(
          '${_queue.length} 项排队',
          style: TextStyle(fontSize: 11, color: p.inkDim),
        ),
        const Spacer(),
        Text(
          '待执行 $_pendingCount · 等待 $_waitingCount',
          style: TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w600,
            color: p.gold,
          ),
        ),
      ],
    );
  }

  Widget _queueSectionBody(BuildContext context, OasPalette p) {
    if (_queue.isEmpty) {
      return OasRowItem(
        badge: '空',
        badgeAsset: 'assets/onmyoji/cat_bukkuman.png',
        badgeColor: p.inkDim,
        title: '还没有排队的任务',
        subtitle: '去「绘卷」启用任务后，这里会按 OAS 调度顺序实时排队',
        trailing: OasPill(
          text: '去启用',
          color: p.gold,
          onTap: () => OasShell.goTab?.call(1),
        ),
        onTap: () => OasShell.goTab?.call(1),
      );
    }

    final bool hasRunning =
        _queue.isNotEmpty && _queue.first['status'] == 'running';
    final List<Map<String, dynamic>> visible =
        _expandedQueue ? _queue : _queue.take(5).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        for (int i = 0; i < visible.length; i++) ...<Widget>[
          if (i > 0) const SizedBox(height: 8),
          _queueRow(context, p, visible[i], i, hasRunning),
        ],
        if (_queue.length > 5) ...<Widget>[
          const SizedBox(height: 9),
          InkWell(
            onTap: () => setState(() => _expandedQueue = !_expandedQueue),
            borderRadius: BorderRadius.circular(12),
            child: Container(
              padding: const EdgeInsets.symmetric(vertical: 9),
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: p.surface.withOpacity(0.85),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: p.gold.withOpacity(0.38)),
              ),
              child: Text(
                _expandedQueue
                    ? '收起排队列表 ▴'
                    : '展开全部 ${_queue.length} 项排队顺序 ▾',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                  color: p.gold,
                ),
              ),
            ),
          ),
        ],
      ],
    );
  }

  Widget _queueRow(
    BuildContext context,
    OasPalette p,
    Map<String, dynamic> item,
    int index,
    bool hasRunning,
  ) {
    final String dir = '${item['name'] ?? ''}';
    final String rawTitle = '${item['title'] ?? ''}';
    final String title = rawTitle.isNotEmpty && rawTitle != dir
        ? rawTitle
        : taskDisplayName(dir);
    final String status = '${item['status'] ?? 'pending'}';
    final int prio =
        (item['priority'] is num) ? (item['priority'] as num).toInt() : 5;
    final String nextRun = '${item['next_run'] ?? ''}';
    final bool isRunning = status == 'running';
    final bool isPending = status == 'pending';
    final int rankNum = hasRunning ? index : index + 1;
    final ({String badge, String detail}) timeInfo =
        _formatQueueTime(status, nextRun);
    final bool canPin =
        !isRunning && (hasRunning ? index > 1 : index > 0);
    final bool pinning = _pinningTask == dir;

    final Color statusColor =
        isRunning ? p.jade : (isPending ? p.vermilion : p.inkDim);

    return OasCard(
      padding: const EdgeInsets.fromLTRB(9, 8, 9, 8),
      onTap: () => _openTaskArgs(dir, title),
      child: Row(
        children: <Widget>[
          SizedBox(
            width: 24,
            child: isRunning
                ? Container(
                    width: 22,
                    height: 22,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: p.jade.withOpacity(0.16),
                      shape: BoxShape.circle,
                    ),
                    child: Icon(Icons.play_arrow_rounded,
                        size: 14, color: p.jade),
                  )
                : Text(
                    '#$rankNum',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      fontSize: rankNum == 1 ? 12.5 : 11.5,
                      fontWeight: FontWeight.w800,
                      color: rankNum == 1
                          ? p.vermilion
                          : (rankNum == 2 ? p.gold : p.inkDim),
                    ),
                  ),
          ),
          const SizedBox(width: 6),
          OasAssetBadge(asset: taskIconAsset(dir), size: 36, radius: 9),
          const SizedBox(width: 9),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Flexible(
                      child: Text(
                        title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          fontSize: 13.5,
                          fontWeight: FontWeight.w700,
                          color: p.ink,
                        ),
                      ),
                    ),
                    const SizedBox(width: 5),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 5, vertical: 1),
                      decoration: BoxDecoration(
                        color: p.gold.withOpacity(0.14),
                        borderRadius: BorderRadius.circular(5),
                      ),
                      child: Text(
                        'P$prio',
                        style: TextStyle(
                          fontSize: 9.5,
                          fontWeight: FontWeight.w700,
                          color: p.gold,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 2.5),
                Text(
                  timeInfo.detail,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 10.5, color: p.inkDim),
                ),
              ],
            ),
          ),
          const SizedBox(width: 6),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3.5),
            decoration: BoxDecoration(
              color: statusColor.withOpacity(0.12),
              borderRadius: BorderRadius.circular(999),
            ),
            child: Text(
              timeInfo.badge,
              style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w700,
                color: statusColor,
              ),
            ),
          ),
          if (canPin) ...<Widget>[
            const SizedBox(width: 5),
            InkWell(
              onTap: pinning ? null : () => _pinTask(dir, title),
              borderRadius: BorderRadius.circular(999),
              child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 3.5),
                decoration: BoxDecoration(
                  color: p.gold.withOpacity(0.12),
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(color: p.gold.withOpacity(0.45)),
                ),
                child: Text(
                  pinning ? '…' : '置顶',
                  style: TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.w700,
                    color: p.gold,
                  ),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  /// 账号卡 + 唯一主按钮 + 当前方案
  Widget _accountCard(BuildContext context, OasPalette p) {
    return OasCard(
      padding: EdgeInsets.zero,
      gradient: LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: <Color>[p.surface, p.surfaceAlt],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 14, 14, 0),
            child: Row(
              children: <Widget>[
                _FoxAvatar(p: p, size: 44),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Text(
                        '阴阳师 · 官服',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: OasFont.song(size: 17, color: p.ink),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        _accountSubline,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontSize: 11, color: p.inkDim),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                OasPill(
                    text: (_state?['virtual_display'] is num &&
                            (_state!['virtual_display'] as num) >= 0)
                        ? '后台'
                        : '主屏',
                    color: p.inkSoft,
                    outlined: true),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(10, 12, 10, 0),
            child: OasPrimaryButton(
              label: _busy ? '处理中…' : (_running ? '停止托管' : '开始托管'),
              suffix: _loading ? '…' : (_running ? '运行中' : '点一下开始'),
              height: 42,
              onTap: _busy || _loading ? null : _toggle,
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 9, 14, 12),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: Text.rich(
                    TextSpan(
                      children: <InlineSpan>[
                        TextSpan(
                          text: '当前方案 · ',
                          style: TextStyle(color: p.inkDim, fontSize: 11),
                        ),
                        TextSpan(
                          text: _planLine,
                          style: TextStyle(
                            color: p.inkSoft,
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: 8),
                GestureDetector(
                  onTap: () {
                    OasShell.goTab?.call(1);
                    OasToast.show(context, '已切到绘卷页，点任务右侧开关就能排方案');
                  },
                  child: Text(
                    '改',
                    style: TextStyle(
                      fontSize: 11.5,
                      fontWeight: FontWeight.w600,
                      color: p.gold,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// 据点四宫格：使用阴阳师官方式神高清头像
  Widget _baseGrid(BuildContext context, OasPalette p) {
    final List<({String label, String asset, String searchHint})> items =
        <({String label, String asset, String searchHint})>[
      (
        label: '庭院日常',
        asset: 'assets/onmyoji/cat_shiranui.png',
        searchHint: '每日'
      ),
      (
        label: '御魂副本',
        asset: 'assets/onmyoji/cat_orochi.png',
        searchHint: '大蛇'
      ),
      (
        label: '觉醒材料',
        asset: 'assets/onmyoji/cat_oote.png',
        searchHint: '觉醒'
      ),
      (
        label: '限时活动',
        asset: 'assets/onmyoji/cat_amaterasu.png',
        searchHint: '活动'
      ),
    ];
    return OasCard(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 11),
      child: Row(
        children: <Widget>[
          for (int i = 0; i < items.length; i++) ...<Widget>[
            if (i > 0) const SizedBox(width: 6),
            Expanded(
              child: GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTap: () {
                  OasShell.goTab?.call(1);
                  OasToast.show(
                    context,
                    '已切到绘卷页，搜「${items[i].searchHint}」挑一个任务配置',
                  );
                },
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    OasAssetBadge(
                      asset: items[i].asset,
                      size: 42,
                      radius: 11,
                    ),
                    const SizedBox(height: 6),
                    Text(
                      items[i].label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        color: p.ink,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// 庭院看板式神头像：使用官方纯人物透明立绘
class _FoxAvatar extends StatelessWidget {
  const _FoxAvatar({required this.p, required this.size});

  final OasPalette p;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: p.surfaceAlt.withOpacity(0.5),
        border: Border.all(color: p.gold.withOpacity(0.55), width: 1.4),
      ),
      child: Padding(
        padding: const EdgeInsets.all(2),
        child: Image.asset(
          'assets/onmyoji/dock_home.png',
          fit: BoxFit.contain,
          errorBuilder: (_, __, ___) => Text(
            '庭',
            style: TextStyle(
              fontFamily: OasFont.serif,
              fontFamilyFallback: OasFont.serifFallback,
              fontSize: size * 0.42,
              height: 1,
              fontWeight: FontWeight.w700,
              color: p.goldHi,
            ),
          ),
        ),
      ),
    );
  }
}
