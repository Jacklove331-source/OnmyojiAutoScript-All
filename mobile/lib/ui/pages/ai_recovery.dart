import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

/// 「异常 · AI 修复」的明细页：读 `/ai/recovery`，按时间倒序列出每次自愈。
///
/// 每行：时间 + 结果徽记（已自愈/需人工/已停下）→ 任务 · 原因 → 动作数 / tokens / 费用。
/// 后端没有这条路由（老版本）时显示「这次运行还没有异常记录」，不报错。
class AiRecoveryPage extends StatefulWidget {
  const AiRecoveryPage({super.key, this.api});

  final OasApi? api;

  @override
  State<AiRecoveryPage> createState() => _AiRecoveryPageState();
}

class _AiRecoveryPageState extends State<AiRecoveryPage> {
  OasApi get _api => widget.api ?? OasApi.i;

  OasAiRecovery? _data;
  String? _error;
  bool _loading = true;

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
      final OasAiRecovery data = await _api.aiRecovery(limit: 80);
      if (!mounted) return;
      setState(() {
        _data = data;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '读不到自愈记录：$e';
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    final OasAiRecovery data = _data ?? OasAiRecovery.empty;
    return Scaffold(
      backgroundColor: p.bg,
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: _load,
          color: p.vermilion,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 6, 16, 28),
            physics: const AlwaysScrollableScrollPhysics(
              parent: BouncingScrollPhysics(),
            ),
            children: <Widget>[
              _topBar(context, p),
              const SizedBox(height: 10),
              if (_loading)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 60),
                  child: Center(
                    child: CircularProgressIndicator(
                        strokeWidth: 2, color: p.vermilion),
                  ),
                )
              else ...<Widget>[
                _summaryCard(p, data),
                const SizedBox(height: 12),
                if (data.incidents.isEmpty)
                  OasCard(
                    padding: const EdgeInsets.fromLTRB(12, 16, 12, 16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(
                          _error ?? '还没有异常记录',
                          style: TextStyle(fontSize: 12.5, color: p.inkSoft),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          'OAS 自己处理掉的问题不会打扰你；真卡住了才会记在这里',
                          style: TextStyle(fontSize: 10.5, color: p.inkDim),
                        ),
                      ],
                    ),
                  )
                else
                  for (final OasAiIncident item in data.incidents) ...<Widget>[
                    _incidentCard(context, p, item),
                    const SizedBox(height: 9),
                  ],
              ],
            ],
          ),
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
              Text('异常 · AI 修复记录',
                  style: OasFont.song(
                      size: 16, color: p.ink, weight: FontWeight.w700)),
              const SizedBox(height: 2),
              Text('来自 log/ai_recovery/incidents，按时间倒序',
                  style: TextStyle(fontSize: 10.5, color: p.inkDim)),
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

  Widget _summaryCard(OasPalette p, OasAiRecovery data) {
    return OasCard(
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 12),
      child: Row(
        children: <Widget>[
          _stat(p, '${data.total}', '异常次数'),
          _stat(p, '${data.recovered}', '已自愈', color: p.jade),
          _stat(p, '${data.needsHuman}', '需人工', color: p.vermilion),
          _stat(
            p,
            data.costCny > 0 ? '¥${data.costCny.toStringAsFixed(3)}' : '0',
            '${data.aiCalls} 次 AI',
          ),
        ],
      ),
    );
  }

  Widget _stat(OasPalette p, String value, String label, {Color? color}) {
    return Expanded(
      child: Column(
        children: <Widget>[
          Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w700,
              color: color ?? p.ink,
            ),
          ),
          const SizedBox(height: 2),
          Text(label,
              style: TextStyle(fontSize: 10.5, color: p.inkDim)),
        ],
      ),
    );
  }

  Widget _incidentCard(
      BuildContext context, OasPalette p, OasAiIncident item) {
    final Color tone = switch (item.outcome) {
      'recovered' => p.jade,
      'needs_human' => p.vermilion,
      'stopped' => p.gold,
      _ => p.inkDim,
    };
    final List<String> meta = <String>[
      if (item.actions > 0) '动作 ${item.actionsOk}/${item.actions}',
      if (item.aiCalled) 'AI 调用',
      if (item.tokens > 0) '${item.tokens} tokens',
      if (item.costCny > 0) '¥${item.costCny.toStringAsFixed(4)}',
    ];
    return OasCard(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                decoration: BoxDecoration(
                  color: tone.withOpacity(0.16),
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(color: tone.withOpacity(0.5)),
                ),
                child: Text(item.outcomeLabel,
                    style: TextStyle(
                        fontSize: 10, fontWeight: FontWeight.w700, color: tone)),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  item.at.isEmpty ? item.id : item.at,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 10.5, color: p.inkDim),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            item.headline,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 12.5, height: 1.3, color: p.ink),
          ),
          if (item.detail.isNotEmpty && item.detail != item.cause) ...<Widget>[
            const SizedBox(height: 3),
            Text(
              item.detail,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 10.5, color: p.inkDim),
            ),
          ],
          if (meta.isNotEmpty) ...<Widget>[
            const SizedBox(height: 5),
            Text(meta.join(' · '),
                style: TextStyle(fontSize: 10, color: p.inkSoft)),
          ],
        ],
      ),
    );
  }
}
