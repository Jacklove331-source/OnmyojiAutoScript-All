import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

/// 个人专享云端诊断回传卡片（公开版 configured=false 时完全隐藏）。
class OasDiagCard extends StatefulWidget {
  const OasDiagCard({
    super.key,
    this.api,
    this.compact = false,
  });

  final OasApi? api;
  final bool compact;

  @override
  State<OasDiagCard> createState() => _OasDiagCardState();
}

class _OasDiagCardState extends State<OasDiagCard> {
  OasApi get _api => widget.api ?? OasApi.i;

  Map<String, dynamic>? _status;
  bool _uploading = false;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final Map<String, dynamic> res = await _api.diagStatus();
    if (!mounted) return;
    setState(() {
      _status = res;
    });
  }

  bool get _configured => _status?['configured'] == true;
  bool get _autoUpload => _status?['auto_upload'] == true;

  String _formatSize(Object? raw) {
    final int bytes = (raw is num) ? raw.toInt() : 0;
    if (bytes <= 0) return '';
    if (bytes < 1024) return '${bytes}B';
    return '${(bytes / 1024).toStringAsFixed(0)}KB';
  }

  Future<void> _toggleAuto(bool value) async {
    if (_saving) return;
    setState(() => _saving = true);
    try {
      final Map<String, dynamic> res = await _api.diagConfigSave(<String, dynamic>{
        'enabled': true,
        'auto_upload': value,
      });
      if (!mounted) return;
      if (res['ok'] == true) {
        setState(() => _status = res);
        OasToast.show(context, value ? '已开启异常自动回传' : '已关闭自动回传（仍可手动上报）');
      } else {
        OasToast.show(context, '保存失败：${res['error'] ?? '未知错误'}');
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _promptAndUpload() async {
    if (_uploading) return;
    final TextEditingController noteCtrl = TextEditingController();
    bool includeShot = true;
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext dialogContext) {
        final OasPalette p = dialogContext.oas;
        return StatefulBuilder(
          builder: (BuildContext ctx, void Function(void Function()) setLocal) {
            return AlertDialog(
              backgroundColor: p.surface,
              title: Text(
                '上报当前现场',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: p.ink),
              ),
              content: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(
                    '将打包日志、脱敏配置、调度状态与后台游戏截图并上传至你的私有仓库，方便远程检修。',
                    style: TextStyle(fontSize: 12, color: p.inkDim, height: 1.4),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: noteCtrl,
                    maxLines: 2,
                    decoration: InputDecoration(
                      hintText: '可选备注（如：等任务没关游戏 / 探索卡住）',
                      hintStyle: TextStyle(fontSize: 12, color: p.inkDim),
                      border: const OutlineInputBorder(),
                      isDense: true,
                    ),
                  ),
                  const SizedBox(height: 8),
                  CheckboxListTile(
                    value: includeShot,
                    onChanged: (bool? v) => setLocal(() => includeShot = v ?? true),
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    controlAffinity: ListTileControlAffinity.leading,
                    title: Text(
                      '附带当前后台虚拟屏截图',
                      style: TextStyle(fontSize: 12.5, color: p.ink),
                    ),
                  ),
                ],
              ),
              actions: <Widget>[
                TextButton(
                  onPressed: () => Navigator.of(dialogContext).pop(false),
                  child: const Text('取消'),
                ),
                FilledButton(
                  onPressed: () => Navigator.of(dialogContext).pop(true),
                  child: const Text('立即上传'),
                ),
              ],
            );
          },
        );
      },
    );

    final String note = noteCtrl.text.trim();
    noteCtrl.dispose();
    if (confirmed != true || !mounted) return;

    setState(() => _uploading = true);
    try {
      final Map<String, dynamic> res = await _api.diagUpload(
        note: note,
        includeScreenshot: includeShot,
      );
      if (!mounted) return;
      if (res['ok'] == true) {
        final String sz = _formatSize(res['size_bytes']);
        final String shotTip = res['has_screenshot'] == true ? '含截图' : '无截图';
        OasToast.show(context, '现场已上传（$sz · $shotTip），可直接让我检修');
        await _load();
      } else {
        OasToast.show(context, '上传失败：${res['error'] ?? '请检查网络'}');
      }
    } catch (e) {
      if (!mounted) return;
      OasToast.show(context, '上传失败：$e');
    } finally {
      if (mounted) setState(() => _uploading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!_configured) return const SizedBox.shrink();
    final OasPalette p = context.oas;
    final Map<String, dynamic>? last = _status?['last_upload'] is Map
        ? Map<String, dynamic>.from(_status!['last_upload'] as Map)
        : null;
    final String lastTime = '${last?['uploaded_at'] ?? ''}'.trim();
    final String shortTime =
        lastTime.length >= 16 ? lastTime.substring(5, 16) : lastTime;

    if (widget.compact) {
      return Padding(
        padding: const EdgeInsets.only(top: 10),
        child: OasCard(
          padding: const EdgeInsets.fromLTRB(13, 10, 13, 10),
          child: Row(
            children: <Widget>[
              Icon(Icons.cloud_upload_outlined, size: 18, color: p.jade),
              const SizedBox(width: 9),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      '个人检修 · 诊断回传',
                      style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w700,
                        color: p.ink,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      shortTime.isNotEmpty
                          ? '${_autoUpload ? '异常自动上传' : '仅手动'} · 最近 $shortTime'
                          : (_autoUpload ? '报错/掉线将自动传至私有库' : '随时可一键打包上传现场'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontSize: 11, color: p.inkDim),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              OasPill(
                text: _uploading ? '上传中…' : '上报现场',
                color: p.jade,
                onTap: _uploading ? null : _promptAndUpload,
              ),
            ],
          ),
        ),
      );
    }

    final String repo = '${_status?['repo'] ?? ''}'.trim();
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: OasCard(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(Icons.cloud_upload_outlined, size: 18, color: p.jade),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      '个人专享 · 云端诊断回传',
                      style: TextStyle(
                        fontSize: 13.5,
                        fontWeight: FontWeight.w700,
                        color: p.ink,
                      ),
                    ),
                    if (repo.isNotEmpty)
                      Text(
                        '私有库 $repo（已自动脱敏）',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontSize: 11, color: p.inkDim),
                      ),
                  ],
                ),
              ),
              OasPill(
                text: _autoUpload ? '自动已开' : '仅手动',
                color: _autoUpload ? p.jade : p.inkDim,
                dot: true,
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  '任务报错 / 调度异常 / Shizuku 掉线时自动传',
                  style: TextStyle(fontSize: 12, color: p.inkSoft),
                ),
              ),
              Switch.adaptive(
                value: _autoUpload,
                onChanged: _saving ? null : _toggleAuto,
                activeColor: p.jade,
              ),
            ],
          ),
          if (last != null) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              '最近上传：$shortTime · ${last['trigger'] ?? ''} · ${_formatSize(last['size_bytes'])}${last['has_screenshot'] == true ? ' · 含截图' : ''}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 11, color: p.inkDim),
            ),
          ],
          const SizedBox(height: 10),
          FilledButton.icon(
            onPressed: _uploading ? null : _promptAndUpload,
            icon: _uploading
                ? const SizedBox(
                    width: 14,
                    height: 14,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.bug_report_outlined, size: 16),
            label: Text(_uploading ? '正在打包并上传现场…' : '一键上报当前现场'),
          ),
        ],
      ),
      ),
    );
  }
}
