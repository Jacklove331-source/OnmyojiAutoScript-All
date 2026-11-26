import 'dart:async';

import 'package:flutter/material.dart';
import '../api.dart';
import '../system_ui.dart';
import '../widgets.dart';

class OasOtaCard extends StatefulWidget {
  const OasOtaCard({super.key, this.api});

  final OasApi? api;

  @override
  State<OasOtaCard> createState() => _OasOtaCardState();
}

class _OasOtaCardState extends State<OasOtaCard> {
  OasApi get _api => widget.api ?? OasApi.i;

  Map<String, dynamic> _status = {};
  Map<String, dynamic> _apkDownload = {};
  bool _loading = true;
  bool _applying = false;
  String? _error;
  Timer? _apkPoll;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _apkPoll?.cancel();
    super.dispose();
  }

  Map<String, dynamic> _asMap(dynamic raw) {
    if (raw == null) return {};
    if (raw is Map<String, dynamic>) return raw;
    if (raw is Map) return Map<String, dynamic>.from(raw);
    return {};
  }

  String _str(String key, [String fallback = '']) {
    final value = _status[key];
    return value?.toString() ?? fallback;
  }

  Map<String, dynamic> get _remoteManifest {
    final value = _status['remote_manifest'];
    if (value is Map) return Map<String, dynamic>.from(value);
    return {};
  }

  String get _repo => _str('repo', 'Jacklove331-source/OnmyojiAutoScript-All');
  String get _branch => _str('branch', 'main');
  String get _baseUrl => _str('base_url', 'https://github.com');
  bool get _isGitHub => _baseUrl.toLowerCase().contains('github.com');
  String get _installedVersion {
    if (_status.containsKey('installedVersion')) {
      return _str('installedVersion', '');
    }
    return _str('installed_version', '');
  }

  bool get _tokenConfigured =>
      _status['has_token'] == true ||
      _status['token_configured'] == true ||
      _status['tokenConfigured'] == true;

  bool get _canRollback =>
      _status['can_rollback'] == true || _status['canRollback'] == true;
  bool get _active => _status['active'] == true;

  bool _isNewer(String remoteVersion, String installedVersion) {
    List<int> parse(String source) {
      return source
          .split(RegExp(r'[.+-]'))
          .map((e) => int.tryParse(e) ?? 0)
          .toList();
    }

    final remote = parse(remoteVersion);
    final installed = parse(installedVersion);
    final length = remote.length > installed.length
        ? remote.length
        : installed.length;

    for (var i = 0; i < length; i++) {
      final a = i < remote.length ? remote[i] : 0;
      final b = i < installed.length ? installed[i] : 0;
      if (a != b) return a > b;
    }
    return false;
  }

  bool get _hasUpdate {
    final remote = _remoteManifest;
    if (remote.isEmpty) return false;

    final remoteVersion = remote['version']?.toString();
    if (remoteVersion == null || remoteVersion.isEmpty) return false;

    final installedVersion = _installedVersion;
    if (installedVersion.isEmpty) return true;

    return _isNewer(remoteVersion, installedVersion);
  }

  Map<String, dynamic> get _apkUpdate => _asMap(_status['apk_update']);

  bool get _hasApkUpdate =>
      _apkUpdate['has_update'] == true &&
      (_apkUpdate['download_url']?.toString().isNotEmpty ?? false);

  bool get _apkDownloading => _apkDownload['downloading'] == true;

  bool get _apkReady =>
      _apkDownload['ready'] == true &&
      (_apkDownload['apk_path']?.toString().isNotEmpty ?? false);

  void _startApkPolling() {
    _apkPoll?.cancel();
    _apkPoll = Timer.periodic(const Duration(seconds: 1), (_) async {
      final Map<String, dynamic> res = await _api.otaApkStatus();
      if (!mounted) return;
      final bool wasDownloading = _apkDownloading;
      setState(() => _apkDownload = res);
      if (res['downloading'] != true) {
        _apkPoll?.cancel();
        if (res['ready'] == true && wasDownloading) {
          await _triggerInstallApk('${res['apk_path'] ?? ''}');
        } else if ((res['error']?.toString().isNotEmpty ?? false)) {
          _toast('APK 下载失败：${res['error']}');
        }
      }
    });
  }

  Future<void> _downloadApk() async {
    final Map<String, dynamic> res = await _api.otaDownloadApk();
    if (!mounted) return;
    if (res['ok'] == false) {
      _toast('无法下载新版 APK：${res['error'] ?? '未知错误'}');
      return;
    }
    setState(() => _apkDownload = res);
    _toast('正在下载新版安装包…');
    _startApkPolling();
  }

  Future<void> _triggerInstallApk(String path) async {
    if (path.isEmpty) return;
    final bool ok = await OasSystem.installApk(path);
    if (!mounted) return;
    if (!ok) {
      _toast('请先在系统设置允许「安装未知应用」，返回后再点「安装新版 APK」');
    }
  }

  void _toast(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
    });

    try {
      final result = await _api.otaStatus();
      if (!mounted) return;

      setState(() {
        _status = _asMap(result);
        _apkDownload = _asMap(_status['apk_download']);
        _error = null;
        _loading = false;
      });
      if (_apkDownloading) _startApkPolling();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _status = {};
        _error = '$e';
        _loading = false;
      });
    }
  }

  Future<void> _editConfig() async {
    final baseUrlController = TextEditingController(text: _baseUrl);
    final repoController = TextEditingController(text: _repo);
    final branchController = TextEditingController(text: _branch);
    final tokenController = TextEditingController();

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('更新源设置'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: baseUrlController,
                decoration: const InputDecoration(
                  labelText: '更新平台地址',
                  hintText: 'https://github.com 或 https://gitcode.com',
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: repoController,
                decoration: const InputDecoration(
                  labelText: '仓库（owner/repo）',
                ),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: branchController,
                decoration: const InputDecoration(labelText: '分支'),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: tokenController,
                obscureText: true,
                decoration: const InputDecoration(
                  labelText: '访问令牌（公开库可留空）',
                  hintText: '留空保持原令牌不变；填新值覆盖',
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('保存'),
          ),
        ],
      ),
    );

    if (confirmed != true) {
      baseUrlController.dispose();
      repoController.dispose();
      branchController.dispose();
      tokenController.dispose();
      return;
    }

    final baseUrl = baseUrlController.text.trim().isEmpty
        ? _baseUrl
        : baseUrlController.text.trim();
    final repo = repoController.text.trim().isEmpty
        ? _repo
        : repoController.text.trim();
    final branch =
        branchController.text.trim().isEmpty ? 'main' : branchController.text.trim();
    final token = tokenController.text.trim();

    baseUrlController.dispose();
    repoController.dispose();
    branchController.dispose();
    tokenController.dispose();

    try {
      final Map<String, dynamic> patch = <String, dynamic>{
        'base_url': baseUrl,
        'repo': repo,
        'branch': branch,
      };
      if (token.isNotEmpty) patch['token'] = token;
      final Map<String, dynamic> res = await _api.otaConfigSave(patch);
      if (res['ok'] == false) {
        _toast('保存失败：${res['error'] ?? '未知错误'}');
      } else {
        _toast('配置已保存');
      }
      await _refresh();
    } catch (e) {
      _toast('保存失败：$e');
    }
  }

  Future<void> _check() async {
    try {
      final result = await _api.otaCheck();
      final map = _asMap(result);
      if (map['ok'] == false) {
        _toast('检查失败：${map['error'] ?? '网络错误'}');
        return;
      }

      if (map['has_update'] == true || map['hasUpdate'] == true) {
        final Map<String, dynamic> rem = _asMap(map['remote']);
        final version = rem['version']?.toString() ??
            map['version']?.toString() ??
            _remoteManifest['version']?.toString() ??
            '新版本';
        _toast('发现新数据包 $version');
      } else {
        _toast('数据包已是最新');
      }

      await _refresh();
    } catch (e) {
      _toast('检查失败：$e');
    }
  }

  Future<void> _apply() async {
    setState(() {
      _applying = true;
    });

    try {
      final result = await _api.otaApply();
      final map = _asMap(result);
      if (map['ok'] == false) {
        _toast('更新失败：${map['error'] ?? '下载或校验失败'}');
        return;
      }
      final version = map['installed_version']?.toString() ??
          map['version']?.toString() ??
          _remoteManifest['version']?.toString() ??
          '';

      _toast(
        version.isNotEmpty
            ? '热更新已生效：版本 $version，无需重装 APK'
            : '热更新已生效，无需重装 APK',
      );

      await _refresh();
    } catch (e) {
      _toast('更新失败：$e');
    } finally {
      if (mounted) {
        setState(() {
          _applying = false;
        });
      }
    }
  }

  Future<void> _rollback() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('确认回滚'),
        content: const Text('确定要回滚到上一个数据包吗？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogContext).pop(false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(dialogContext).pop(true),
            child: const Text('回滚'),
          ),
        ],
      ),
    );

    if (confirmed != true) return;

    try {
      await _api.otaRollback();
      _toast('已回滚');
      await _refresh();
    } catch (e) {
      _toast('回滚失败：$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    final remote = _remoteManifest;
    final hasUpdate = _hasUpdate;

    final subtitleText =
        '仓库：$_repo ($_branch) · 当前版本：${_installedVersion.isEmpty ? "内置版" : _installedVersion}';

    return OasCard(
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '在线更新（数据包热更 / APK 自更新）',
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: Text(
                  subtitleText,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
              const SizedBox(width: 8),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(
                  color: (_tokenConfigured || _isGitHub)
                      ? p.jade.withOpacity(0.12)
                      : p.inkDim.withOpacity(0.12),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Text(
                  _tokenConfigured
                      ? '令牌已配置'
                      : (_isGitHub ? 'GitHub 公开源' : '未配令牌'),
                  style: TextStyle(
                    color: (_tokenConfigured || _isGitHub) ? p.jade : p.inkDim,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ],
          ),
          if (hasUpdate) ...[
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              decoration: BoxDecoration(
                color: p.jade.withOpacity(0.08),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                '发现新数据包：${remote['version']?.toString() ?? ''} · ${remote['note']?.toString() ?? ''}',
                style: TextStyle(color: p.jade, fontSize: 13),
              ),
            ),
          ],
          if (_hasApkUpdate || _apkDownloading || _apkReady) ...[
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              decoration: BoxDecoration(
                color: p.gold.withOpacity(0.10),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: p.gold.withOpacity(0.35)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    '发现新版安装包：${_apkUpdate['version'] ?? _apkDownload['version'] ?? ''}'
                    '${(_apkUpdate['note']?.toString().isNotEmpty ?? false) ? ' · ${_apkUpdate['note']}' : ''}',
                    style: TextStyle(
                      color: p.gold,
                      fontSize: 12.5,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  if (_apkDownloading) ...[
                    const SizedBox(height: 6),
                    LinearProgressIndicator(
                      value: ((_apkDownload['percent'] as num?)?.toDouble() ??
                              0) /
                          100.0,
                      color: p.gold,
                      backgroundColor: p.line,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '正在下载 ${_apkDownload['percent'] ?? 0}% …',
                      style: TextStyle(fontSize: 11, color: p.inkDim),
                    ),
                  ],
                  const SizedBox(height: 6),
                  Row(
                    children: [
                      if (_apkReady)
                        FilledButton(
                          onPressed: () => _triggerInstallApk(
                              '${_apkDownload['apk_path'] ?? ''}'),
                          child: const Text('安装新版 APK'),
                        )
                      else
                        FilledButton(
                          onPressed: _apkDownloading ? null : _downloadApk,
                          child: Text(
                              _apkDownloading ? '下载中…' : '下载新版 APK'),
                        ),
                    ],
                  ),
                ],
              ),
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(
              '状态获取失败：$_error',
              style: TextStyle(
                color: Theme.of(context).colorScheme.error,
                fontSize: 12,
              ),
            ),
          ],
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton(
                onPressed: _loading ? null : _editConfig,
                child: const Text('更新源设置'),
              ),
              OutlinedButton(
                onPressed: _loading ? null : _check,
                child: const Text('检查更新'),
              ),
              FilledButton(
                onPressed: (_loading || _applying) ? null : _apply,
                child: _applying
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('下载热更新'),
              ),
              if (_canRollback || _active)
                TextButton(
                  onPressed: _loading ? null : _rollback,
                  child: const Text('回滚'),
                ),
            ],
          ),
        ],
      ),
    );
  }
}
