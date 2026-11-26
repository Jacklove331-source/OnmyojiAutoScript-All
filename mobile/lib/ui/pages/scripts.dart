import 'package:flutter/material.dart';

import '../api.dart';
import '../task_names.dart';
import '../widgets.dart';
import 'task_args.dart';

/// ② 脚本：任务列表来自后端 GET /script_menu（动态枚举，不写死）。
/// 点卡片进参数页，参数来自 /<script>/<task>/args，写回走 PUT .../value。
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
                  : (_error != null ? '读取失败，下拉重试' : '共 $total 项 · 全部来自游戏脚本库'),
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
              for (int i = 0; i < items.length; i++) ...<Widget>[
                if (i > 0) const SizedBox(height: 9),
                _taskCard(context, p, items[i]),
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

  Widget _taskCard(BuildContext context, OasPalette p, OasTask t) {
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
