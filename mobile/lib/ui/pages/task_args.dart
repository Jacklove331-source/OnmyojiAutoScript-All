import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

/// 任务参数页：数据来自 GET /<script>/<task>/args，
/// 改一项就 PUT /…/<group>/<arg>/value 立刻写回 config。
///
/// 手机化要点：调度组（8 个电脑式字段）默认折叠成「高级」，
/// 英文键名按阴阳师术语汉化，乱码说明丢弃。
class TaskArgsPage extends StatefulWidget {
  const TaskArgsPage({
    super.key,
    required this.script,
    required this.task,
    required this.title,
  });

  final String script;
  final String task;
  final String title;

  @override
  State<TaskArgsPage> createState() => _TaskArgsPageState();
}

class _TaskArgsPageState extends State<TaskArgsPage> {
  List<OasArgGroup>? _groups;
  String? _error;
  bool _loading = true;
  bool _showAdvanced = false;
  String? _busyField;

  /// OAS 官方中文词表（英文键 → 中文）；没有译文的键按原键显示。
  Map<String, String> _zh = <String, String>{};

  String _t(String key, {bool allowHelp = true}) {
    if (key.isEmpty) return key;
    final String? hit = _zh[key];
    if (hit != null && hit.isNotEmpty) return hit;
    // OAS 的说明键常常是 `xxx_help`，而标题键是 `xxx`（或反过来），两边都试一次。
    // 但「标题」不能用 `_help` 的译文：那里面往往是「可选[左一, …]」这种整句说明。
    if (!allowHelp) return key;
    if (key.endsWith('_help')) {
      final String? alt = _zh[key.substring(0, key.length - 5)];
      if (alt != null && alt.isNotEmpty) return alt;
    } else {
      final String? alt = _zh['${key}_help'];
      if (alt != null && alt.isNotEmpty) return alt;
    }
    return key;
  }

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
      // 枚举下拉需要「存值 → 标签」表，先确保资源已加载
      await OasEnums.ensureLoaded();
      final List<OasArgGroup> g = await OasApi.i.taskArgs(widget.script, widget.task);
      // 用 OAS 官方词表把英文键换成中文（缺译文的键保留原样）
      final Set<String> keys = <String>{};
      for (final OasArgGroup group in g) {
        keys.add(group.title);
        for (final OasArgField f in group.fields) {
          keys.add(f.title);
          keys.add(f.description);
        }
      }
      final Map<String, String> zh = await OasApi.i.i18n(keys.toList());
      if (!mounted) return;
      setState(() {
        _zh = zh;
        _groups = g;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '读不到参数：$e';
        _loading = false;
      });
    }
  }

  Future<void> _save(OasArgGroup g, OasArgField f, String value) async {
    setState(() => _busyField = '${g.key}.${f.name}');
    try {
      final bool ok = await OasApi.i.setValue(
        script: widget.script,
        task: widget.task,
        group: g.key,
        argument: f.name,
        value: value,
        type: f.type,
      );
      if (!mounted) return;
      _toast(ok ? '已保存：${f.displayTitle}' : '保存失败，后端返回 false');
      if (ok) await _load();
    } catch (e) {
      if (!mounted) return;
      _toast('保存失败：$e');
    } finally {
      if (mounted) setState(() => _busyField = null);
    }
  }

  void _toast(String msg) {
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(msg, style: const TextStyle(fontSize: 12)),
          behavior: SnackBarBehavior.floating,
          duration: const Duration(seconds: 2),
        ),
      );
  }

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    final List<OasArgGroup> all = _groups ?? <OasArgGroup>[];
    final List<OasArgGroup> normal =
        all.where((OasArgGroup g) => !g.isAdvanced).toList(growable: false);
    final List<OasArgGroup> advanced =
        all.where((OasArgGroup g) => g.isAdvanced).toList(growable: false);

    return Scaffold(
      backgroundColor: p.bg,
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 6, 16, 28),
          physics: const BouncingScrollPhysics(),
          children: <Widget>[
            _topBar(context, p),
            const SizedBox(height: 10),
            if (_loading)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 60),
                child: Center(
                  child: CircularProgressIndicator(strokeWidth: 2, color: p.vermilion),
                ),
              )
            else if (_error != null)
              OasCard(
                padding: const EdgeInsets.all(12),
                child: Text(_error!, style: TextStyle(fontSize: 12, color: p.vermilion)),
              )
            else ...<Widget>[
              for (final OasArgGroup g in normal) ...<Widget>[
                _groupCard(context, p, g),
                const SizedBox(height: 12),
              ],
              if (advanced.isNotEmpty) ...<Widget>[
                OasCard(
                  padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
                  onTap: () => setState(() => _showAdvanced = !_showAdvanced),
                  child: Row(
                    children: <Widget>[
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text(
                              '高级 · 调度',
                              style: TextStyle(
                                fontSize: 13,
                                fontWeight: FontWeight.w600,
                                color: p.ink,
                              ),
                            ),
                            const SizedBox(height: 3),
                            Text(
                              '下次运行、优先级、间隔这些电脑式选项，收在这里',
                              style: TextStyle(fontSize: 10.5, color: p.inkDim),
                            ),
                          ],
                        ),
                      ),
                      Icon(
                        _showAdvanced ? Icons.expand_less : Icons.expand_more,
                        size: 18,
                        color: p.inkDim,
                      ),
                    ],
                  ),
                ),
                if (_showAdvanced) ...<Widget>[
                  const SizedBox(height: 12),
                  for (final OasArgGroup g in advanced) ...<Widget>[
                    _groupCard(context, p, g),
                    const SizedBox(height: 12),
                  ],
                ],
              ],
            ],
          ],
        ),
      ),
    );
  }

  Widget _topBar(BuildContext context, OasPalette p) {
    return Row(
      children: <Widget>[
        GestureDetector(
          onTap: () => Navigator.of(context).pop(),
          behavior: HitTestBehavior.opaque,
          child: Container(
            width: 36,
            height: 36,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: p.surfaceAlt,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: p.line),
            ),
            child: Icon(Icons.arrow_back_ios_new, size: 14, color: p.inkSoft),
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                widget.title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: OasFont.song(size: 16, color: p.ink, weight: FontWeight.w700),
              ),
              const SizedBox(height: 2),
              Text(
                widget.task,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 10.5, color: p.inkDim),
              ),
            ],
          ),
        ),
        GestureDetector(
          onTap: _load,
          behavior: HitTestBehavior.opaque,
          child: Container(
            width: 36,
            height: 36,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: p.surfaceAlt,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: p.line),
            ),
            child: Icon(Icons.refresh, size: 16, color: p.inkSoft),
          ),
        ),
      ],
    );
  }

  Widget _groupCard(BuildContext context, OasPalette p, OasArgGroup g) {
    return OasCard(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            g.title,
            style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: p.inkSoft),
          ),
          const SizedBox(height: 2),
          for (int i = 0; i < g.fields.length; i++) ...<Widget>[
            if (i > 0)
              Container(height: 1, color: p.line, margin: const EdgeInsets.symmetric(vertical: 1)),
            _fieldRow(context, p, g, g.fields[i]),
          ],
        ],
      ),
    );
  }

  Widget _fieldRow(BuildContext context, OasPalette p, OasArgGroup g, OasArgField f) {
    final String key = '${g.key}.${f.name}';
    final bool busy = _busyField == key;
    final String desc = f.cleanDescription;

    if (f.isBool) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 7),
        child: Row(
          children: <Widget>[
            Expanded(child: _label(p, f, desc)),
            if (busy)
              const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            else
              OasSwitch(
                value: f.value == true,
                onChanged: (bool v) => _save(g, f, v ? 'true' : 'false'),
              ),
          ],
        ),
      );
    }

    return InkWell(
      onTap: busy
          ? null
          : (f.hasPicker
              ? () => _pickEnum(context, p, g, f)
              : () => _editNumberOrText(context, p, g, f)),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 9),
        child: Row(
          children: <Widget>[
            Expanded(child: _label(p, f, desc)),
            const SizedBox(width: 8),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 150),
              child: Text(
                f.valueText.isEmpty ? '—' : f.valueText,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: OasFont.song(size: 12.5, color: p.ink, weight: FontWeight.w600),
              ),
            ),
            const SizedBox(width: 4),
            Icon(Icons.chevron_right, size: 15, color: p.inkDim),
          ],
        ),
      ),
    );
  }

  Widget _label(OasPalette p, OasArgField f, String desc) {
    final String? secondary = f.secondaryTitle;
    // 标题/说明都过一遍 OAS 官方词表；没译文就保留英文原样（不要显示空白）
    // 枚举字段的 displayTitle 是把「可选[左一, 左二…]」截断来的（带省略号、没有右括号），
    // 所以这里要能吃掉「可选[」到结尾的半截文本。
    String stripOptions(String text) => text
        .replaceAll(RegExp(r'可选\[[^\]]*\]?'), '')
        .replaceAll(RegExp(r'…+$'), '')
        .trim();
    String title = stripOptions(_t(f.displayTitle, allowHelp: false));
    if (title.isEmpty) title = _t(f.name, allowHelp: false);
    final String descT = _t(f.description).isNotEmpty &&
            _t(f.description) != f.description
        ? _t(f.description)
        : _t(desc);
    final String shownDesc = descT.isEmpty ? desc : descT;
    // 说明已经被拿去当主标题时，别在下面重复一遍；枚举的「可选[...]」交给下面的绿色候选行，
    // 这里再显示一次就是同一句出现两遍。
    final bool showDesc = shownDesc.isNotEmpty &&
        shownDesc != title &&
        !shownDesc.startsWith('可选') &&
        !shownDesc.startsWith(title.replaceAll('…', ''));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(
          title,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(fontSize: 12.5, color: p.ink),
        ),
        if (secondary != null && _t(secondary) != title) ...<Widget>[
          const SizedBox(height: 2),
          Text(
            _t(secondary),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 9.5, color: p.inkDim),
          ),
        ],
        if (showDesc) ...<Widget>[
          const SizedBox(height: 2),
          Text(
            shownDesc,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 10, color: p.inkDim),
          ),
        ],
        if (f.hasOptions) ...<Widget>[
          const SizedBox(height: 2),
          Text(
            '可选：${f.optionLabels.join(" / ")}',
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 10, color: p.jade),
          ),
        ],
      ],
    );
  }

  /// 枚举字段：后端已给出「存值 → 中文标签」时用真下拉选，选完直接写回存值。
  Future<void> _pickEnum(BuildContext context, OasPalette p, OasArgGroup g,
      OasArgField f) async {
    final String? picked = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: p.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      builder: (BuildContext ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
              child: Text(_t(f.displayTitle, allowHelp: false),
                  style: OasFont.song(
                      size: 15, color: p.ink, weight: FontWeight.w700)),
            ),
            Flexible(
              child: ListView(
                shrinkWrap: true,
                children: <Widget>[
                  for (final MapEntry<String, String> e in f.options.entries)
                    ListTile(
                      dense: true,
                      title: Text(e.value,
                          style: OasFont.song(size: 13, color: p.ink)),
                      subtitle: Text(e.key,
                          style: TextStyle(fontSize: 10, color: p.inkDim)),
                      trailing: f.valueText == e.key
                          ? Icon(Icons.check, size: 16, color: p.jade)
                          : null,
                      onTap: () => Navigator.of(ctx).pop(e.key),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
    if (picked != null && picked != f.valueText) {
      await _save(g, f, picked);
    }
  }

  Future<void> _editNumberOrText(
    BuildContext context,
    OasPalette p,
    OasArgGroup g,
    OasArgField f,
  ) async {
    final TextEditingController c = TextEditingController(text: f.valueText);
    final String? result = await showModalBottomSheet<String>(
      context: context,
      backgroundColor: p.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      builder: (BuildContext ctx) => Padding(
        padding: EdgeInsets.only(
          left: 16,
          right: 16,
          top: 16,
          bottom: MediaQuery.of(ctx).viewInsets.bottom + 18,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              f.displayTitle,
              style: OasFont.song(size: 15, color: p.ink, weight: FontWeight.w700),
            ),
            const SizedBox(height: 4),
            Text(
              '类型：${f.type} · 原值 ${f.valueText.isEmpty ? '—' : f.valueText}',
              style: TextStyle(fontSize: 10.5, color: p.inkDim),
            ),
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10),
              decoration: BoxDecoration(
                color: p.surfaceAlt,
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: p.line),
              ),
              child: TextField(
                controller: c,
                autofocus: true,
                style: TextStyle(fontSize: 14, color: p.ink),
                decoration: const InputDecoration(isDense: true, border: InputBorder.none),
              ),
            ),
            const SizedBox(height: 14),
            SizedBox(
              width: double.infinity,
              child: OasPrimaryButton(
                label: '保存',
                onTap: () => Navigator.of(ctx).pop(c.text.trim()),
              ),
            ),
          ],
        ),
      ),
    );
    c.dispose();
    if (result == null) return;
    if (result == f.valueText) return;
    await _save(g, f, result);
  }
}
