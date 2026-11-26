import 'package:flutter/material.dart';

import '../api.dart';
import '../task_categories.dart';
import '../task_names.dart';
import '../widgets.dart';
import 'task_args.dart';

/// ② 脚本：任务列表来自后端 GET /script_menu（动态枚举，不写死）。
/// 列表按电脑端 OAS 的七大类归档（`task_categories.dart`），点卡片进参数页，
/// 参数来自 /<script>/<task>/args，写回走 PUT .../value。
class ScriptsPage extends StatefulWidget {
  const ScriptsPage({super.key});

  @override
  State<ScriptsPage> createState() => _ScriptsPageState();
}

class _ScriptsPageState extends State<ScriptsPage> {
  final TextEditingController _search = TextEditingController();

  List<OasTask>? _tasks;
  String? _error;
  bool _loading = true;

  /// 卡片开关的本地状态（后端没有批量读 enable 的接口，先本地记住）。
  final Map<String, bool> _enabled = <String, bool>{};

  /// 折叠起来的大分类 id（默认全展开，点了标题才收）。
  final Set<String> _collapsed = <String>{};

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final List<OasTask> list = await OasApi.i.tasks();
      final Map<String, bool> enabled =
          await OasApi.i.taskEnabled(OasApi.i.activeConfig);
      if (!mounted) return;
      setState(() {
        _enabled
          ..clear()
          ..addAll(enabled);
        _tasks = list;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '读不到任务列表：$e';
        _loading = false;
      });
    }
  }

  List<OasTask> get _filtered {
    final List<OasTask> all = _tasks ?? <OasTask>[];
    final String q = _search.text.trim().toLowerCase();
    if (q.isEmpty) return all;
    return all
        .where((OasTask t) =>
            t.title.toLowerCase().contains(q) ||
            t.dir.toLowerCase().contains(q))
        .toList(growable: false);
  }

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    final List<OasTask> items = _filtered;
    final int total = _tasks?.length ?? 0;
    final List<({OasTaskGroup group, List<OasTask> tasks})> groups =
        groupTasks(items);

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
              badge: '符',
              badgeAsset: 'assets/onmyoji/dock_tasks.png',
              title: '绘卷 · 任务',
              subtitle: _loading
                  ? '正在读取任务表…'
                  : (_error != null
                      ? '读取失败，下拉重试'
                      : '共 $total 项 · ${groups.length} 个大类'),
              trailing: OasPill(
                text: _loading ? '…' : '$total 项',
                color: p.inkDim,
                outlined: true,
              ),
            ),
            const SizedBox(height: 12),
            _searchBox(p),
            const SizedBox(height: 12),
            if (_error != null) ...<Widget>[
              OasCard(
                padding: const EdgeInsets.fromLTRB(12, 12, 12, 12),
                child: Text(
                  _error!,
                  style: TextStyle(fontSize: 12, color: p.vermilion),
                ),
              ),
              const SizedBox(height: 12),
            ],
            if (_loading)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 40),
                child: Center(
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: p.vermilion,
                  ),
                ),
              )
            else
              for (final ({OasTaskGroup group, List<OasTask> tasks}) entry
                  in groups) ...<Widget>[
                _groupHeader(context, p, entry.group, entry.tasks),
                if (!_collapsed.contains(entry.group.id))
                  for (int i = 0; i < entry.tasks.length; i++) ...<Widget>[
                    const SizedBox(height: 8),
                    _taskCard(context, p, entry.tasks[i]),
                  ],
                const SizedBox(height: 14),
              ],
            if (!_loading && items.isEmpty && _error == null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 40),
                child: Center(
                  child: Text(
                    '没找到匹配的任务',
                    style: TextStyle(fontSize: 12, color: p.inkDim),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  /// 大分类标题：左侧竖金条 + 分类名 + 一句话说明，右侧「N 项 · M 启用」+ 折叠箭头。
  Widget _groupHeader(
    BuildContext context,
    OasPalette p,
    OasTaskGroup group,
    List<OasTask> tasks,
  ) {
    final int on =
        tasks.where((OasTask t) => _enabled[t.dir] == true).length;
    final bool collapsed = _collapsed.contains(group.id);
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () => setState(() {
        if (collapsed) {
          _collapsed.remove(group.id);
        } else {
          _collapsed.add(group.id);
        }
      }),
      child: Row(
        children: <Widget>[
          Container(
            width: 3,
            height: 16,
            decoration: BoxDecoration(
              color: p.gold,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(width: 8),
          Text(
            group.label,
            style: TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.w700,
              color: p.ink,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              group.hint,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 10.5, color: p.inkDim),
            ),
          ),
          const SizedBox(width: 6),
          Text(
            on > 0 ? '${tasks.length} 项 · $on 启用' : '${tasks.length} 项',
            style: TextStyle(
              fontSize: 10.5,
              fontWeight: FontWeight.w600,
              color: on > 0 ? p.jade : p.inkDim,
            ),
          ),
          Icon(
            collapsed ? Icons.expand_more : Icons.expand_less,
            size: 18,
            color: p.inkDim,
          ),
        ],
      ),
    );
  }

  Widget _searchBox(OasPalette p) {
    return Container(
      height: 38,
      padding: const EdgeInsets.symmetric(horizontal: 10),
      decoration: BoxDecoration(
        color: p.surfaceAlt,
        borderRadius: BorderRadius.circular(11),
        border: Border.all(color: p.line),
      ),
      child: Row(
        children: <Widget>[
          Icon(Icons.search, size: 16, color: p.inkDim),
          const SizedBox(width: 6),
          Expanded(
            child: TextField(
              controller: _search,
              style: TextStyle(fontSize: 12.5, color: p.ink),
              cursorColor: p.vermilion,
              decoration: InputDecoration(
                isDense: true,
                border: InputBorder.none,
                hintText: '搜任务名（中文或英文都行）',
                hintStyle: TextStyle(fontSize: 12.5, color: p.inkDim),
              ),
              onChanged: (_) => setState(() {}),
            ),
          ),
          if (_search.text.isNotEmpty)
            GestureDetector(
              onTap: () {
                _search.clear();
                setState(() {});
              },
              child: Icon(Icons.close, size: 15, color: p.inkDim),
            ),
        ],
      ),
    );
  }

  /// 卡片上的启用开关：写回该任务的 `scheduler.enable`。
  Future<void> _toggleTask(OasTask t, bool value) async {
    setState(() => _enabled[t.dir] = value);
    try {
      final bool ok = await OasApi.i.setValue(
        script: OasApi.i.activeConfig,
        task: t.dir,
        group: 'scheduler',
        argument: 'enable',
        value: value ? 'true' : 'false',
        type: 'boolean',
      );
      if (!mounted) return;
      if (!ok) {
        setState(() => _enabled[t.dir] = !value);
        OasToast.show(context, '后端没有接受这次修改');
      } else {
        OasToast.show(context, value ? '已启用 ${t.title}' : '已停用 ${t.title}');
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _enabled[t.dir] = !value);
      OasToast.show(context, '保存失败：$e');
    }
  }

  /// 仅参数配置面板（没有 scheduler/执行体），不渲染任务启用开关。
  static const Set<String> _kSettingsOnlyTasks = <String>{
    'Script',
    'GlobalGame',
  };

  Widget _taskCard(BuildContext context, OasPalette p, OasTask t) {
    final bool isSettingsOnly = _kSettingsOnlyTasks.contains(t.dir);
    return OasCard(
      padding: const EdgeInsets.fromLTRB(10, 9, 8, 9),
      onTap: () => Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => TaskArgsPage(
              script: OasApi.i.activeConfig, task: t.dir, title: t.title),
        ),
      ),
      child: Row(
        children: <Widget>[
          OasAssetBadge(asset: taskIconAsset(t.dir), size: 36, radius: 9),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  t.title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 13.5,
                    height: 1.2,
                    fontWeight: FontWeight.w600,
                    color: p.ink,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  t.dir,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style:
                      TextStyle(fontSize: 10.5, height: 1.15, color: p.inkDim),
                ),
              ],
            ),
          ),
          if (isSettingsOnly)
            Padding(
              padding: const EdgeInsets.only(right: 4),
              child: OasPill(
                text: '配置',
                color: p.inkDim,
                outlined: true,
              ),
            )
          else
            OasSwitch(
              value: _enabled[t.dir] ?? false,
              onChanged: (bool v) => _toggleTask(t, v),
            ),
          Icon(Icons.chevron_right, size: 18, color: p.inkDim),
        ],
      ),
    );
  }
}
