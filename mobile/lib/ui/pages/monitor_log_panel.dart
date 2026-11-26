import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../widgets.dart';

enum _LogFilter { all, warn, history }

class OasMonitorLogPanel extends StatefulWidget {
  const OasMonitorLogPanel({super.key, this.api});

  final OasApi? api;

  @override
  State<OasMonitorLogPanel> createState() => _OasMonitorLogPanelState();
}

class _OasMonitorLogPanelState extends State<OasMonitorLogPanel> {
  static const Color _crimson = Color(0xFFFCA5A5);
  static const Color _amber = Color(0xFFFCD34D);
  static const Color _emerald = Color(0xFF6EE7B7);
  static const Color _cream = Color(0xFFE7E0D4);
  static const Color _muted = Color(0xFF9C958C);

  Timer? _timer;
  final ScrollController _scrollController = ScrollController();

  _LogFilter _filter = _LogFilter.all;
  bool _autoRefresh = true;
  bool _loading = false;
  String? _error;
  List<String> _lines = [];
  List<dynamic> _files = [];
  String? _viewingPath;
  String? _viewingName;
  int _requestSeq = 0;

  @override
  void initState() {
    super.initState();
    if (_autoRefresh) {
      _startTimer();
    }
    _loadCurrent();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _scrollController.dispose();
    super.dispose();
  }

  void _startTimer() {
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _onTimerTick());
  }

  void _onTimerTick() {
    if (!mounted || !_autoRefresh) return;
    if (!TickerMode.of(context)) return;
    _loadCurrent();
  }

  void _toggleAutoRefresh() {
    setState(() {
      _autoRefresh = !_autoRefresh;
    });
    if (_autoRefresh) {
      _startTimer();
      _loadCurrent();
    } else {
      _timer?.cancel();
      _timer = null;
    }
  }

  void _setFilter(_LogFilter filter) {
    setState(() {
      _filter = filter;
      _viewingPath = null;
      _viewingName = null;
    });
    _loadCurrent();
  }

  void _clearViewingChip() {
    setState(() {
      _viewingPath = null;
      _viewingName = null;
      _filter = _LogFilter.all;
    });
    _loadCurrent();
  }

  OasApi get _api => widget.api ?? OasApi.i;

  Future<void> _loadCurrent() async {
    final OasApi api = _api;

    final int seq = ++_requestSeq;
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      if (_viewingPath != null) {
        final Map<String, dynamic> tail =
            await api.logTail(path: _viewingPath!, lines: 200);
        final List<String> lines = _asLines(tail);
        if (!mounted || seq != _requestSeq) return;
        setState(() {
          _lines = lines;
          _loading = false;
        });
        _scrollToBottom();
      } else if (_filter == _LogFilter.history) {
        final List<Map<String, dynamic>> files = await api.logFiles();
        if (!mounted || seq != _requestSeq) return;
        setState(() {
          _files = _normalizeFiles(files);
          _loading = false;
        });
      } else {
        final Map<String, dynamic> tail = await api.logTail(lines: 200);
        List<String> lines = _asLines(tail);
        if (_filter == _LogFilter.warn) {
          lines = lines.where(_isWarnLine).toList();
        }
        if (!mounted || seq != _requestSeq) return;
        setState(() {
          _lines = lines;
          _loading = false;
        });
        _scrollToBottom();
      }
    } catch (e) {
      if (!mounted || seq != _requestSeq) return;
      setState(() {
        _loading = false;
        _error = '加载失败: $e';
      });
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scrollController.hasClients) return;
      _scrollController.jumpTo(0);
    });
  }

  Future<void> _openFile(dynamic entry) async {
    final (path, name, _) = _extractFileInfo(entry);
    if (path.isEmpty) return;

    final OasApi api = _api;

    final int seq = ++_requestSeq;
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final Map<String, dynamic> tail =
          await api.logTail(path: path, lines: 200);
      final List<String> lines = _asLines(tail);
      if (!mounted || seq != _requestSeq) return;
      setState(() {
        _viewingPath = path;
        _viewingName = name;
        _filter = _LogFilter.all;
        _lines = lines;
        _loading = false;
      });
      _scrollToBottom();
    } catch (e) {
      if (!mounted || seq != _requestSeq) return;
      setState(() {
        _loading = false;
        _error = '加载文件失败: $e';
      });
    }
  }

  Future<void> _copyLogs() async {
    final String data = _lines.join('\n');
    await Clipboard.setData(ClipboardData(text: data));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('日志已复制'),
        duration: Duration(seconds: 1),
      ),
    );
  }

  List<String> _asLines(dynamic content) {
    if (content == null) return <String>[];
    if (content is Map) {
      final Object? raw = content['lines'];
      if (raw is List) {
        return raw.map((Object? e) => '$e').toList();
      }
      return <String>[];
    }
    if (content is String) return content.split('\n');
    if (content is List) return content.map((Object? e) => '$e').toList();
    return <String>['$content'];
  }

  List<dynamic> _normalizeFiles(dynamic files) {
    if (files is List) return files.take(8).toList();
    return [];
  }

  (String path, String name, int size) _extractFileInfo(dynamic entry) {
    String path = '';
    String name = '';
    int size = 0;

    if (entry is String) {
      path = entry;
      name = path.split('/').last;
      size = 0;
      return (path, name, size);
    }

    if (entry is Map) {
      path = entry['path']?.toString() ?? '';
      name = entry['name']?.toString() ?? '';
      size = _parseSize(entry['size']);
    } else {
      path = entry.path?.toString() ?? '';
      name = entry.name?.toString() ?? '';
      size = _parseSize(entry.size);
    }

    if (name.isEmpty && path.isNotEmpty) {
      name = path.split('/').last;
    }
    return (path, name, size);
  }

  int _parseSize(dynamic size) {
    if (size == null) return 0;
    if (size is int) return size;
    if (size is num) return size.toInt();
    return int.tryParse(size.toString()) ?? 0;
  }

  String _formatBytes(int bytes) {
    if (bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    int i = 0;
    double value = bytes.toDouble();
    while (value >= 1024 && i < units.length - 1) {
      value /= 1024;
      i++;
    }
    return '${value.toStringAsFixed(value >= 100 ? 0 : 1)} ${units[i]}';
  }

  bool _isWarnLine(String line) {
    final upper = line.toUpperCase();
    return upper.contains('WARN') ||
        upper.contains('ERROR') ||
        line.contains('异常') ||
        line.contains('失败') ||
        upper.contains('TRACEBACK') ||
        line.contains('退出');
  }

  Color _colorForLine(String line) {
    final upper = line.toUpperCase();
    if (upper.contains('ERROR') || line.contains('失败') || upper.contains('TRACEBACK')) {
      return _crimson;
    }
    if (upper.contains('WARN') || line.contains('退出') || line.contains('重试')) {
      return _amber;
    }
    if (upper.contains('OK') || line.contains('完成') || line.contains('成功') || line.contains('匹配')) {
      return _emerald;
    }
    return _cream;
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _buildHeader(),
        if (_viewingPath != null) _buildViewingChip(),
        const SizedBox(height: 6),
        SizedBox(
          height: 230,
          child: _viewingPath != null || _filter != _LogFilter.history
              ? _buildLogBox()
              : _buildFileList(),
        ),
      ],
    );
  }

  Widget _buildHeader() {
    final OasPalette p = context.oas;
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 8, 4, 4),
      child: Row(
        children: [
          Text(
            '实时运行日志',
            style: TextStyle(
              fontSize: 12.5,
              color: p.ink,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(width: 8),
          _FilterPill(
            label: '实时',
            selected: _filter == _LogFilter.all,
            onTap: () => _setFilter(_LogFilter.all),
          ),
          const SizedBox(width: 4),
          _FilterPill(
            label: '告警',
            selected: _filter == _LogFilter.warn,
            onTap: () => _setFilter(_LogFilter.warn),
          ),
          const SizedBox(width: 4),
          _FilterPill(
            label: '历史',
            selected: _filter == _LogFilter.history,
            onTap: () => _setFilter(_LogFilter.history),
          ),
          const Spacer(),
          _buildAutoRefreshButton(),
          _buildManualRefreshButton(),
          _buildCopyButton(),
        ],
      ),
    );
  }

  Widget _buildAutoRefreshButton() {
    return IconButton(
      onPressed: _toggleAutoRefresh,
      icon: Icon(
        _autoRefresh ? Icons.autorenew : Icons.autorenew_outlined,
        size: 16,
        color: _autoRefresh ? _emerald : _muted,
      ),
      tooltip: _autoRefresh ? '自动刷新：开' : '自动刷新：关',
      visualDensity: VisualDensity.compact,
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
    );
  }

  Widget _buildManualRefreshButton() {
    return IconButton(
      onPressed: _loadCurrent,
      icon: const Icon(Icons.refresh, size: 16, color: _muted),
      tooltip: '刷新',
      visualDensity: VisualDensity.compact,
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
    );
  }

  Widget _buildCopyButton() {
    return IconButton(
      onPressed: _copyLogs,
      icon: const Icon(Icons.copy, size: 16, color: _muted),
      tooltip: '复制日志',
      visualDensity: VisualDensity.compact,
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints(minWidth: 28, minHeight: 28),
    );
  }

  Widget _buildViewingChip() {
    final String name = _viewingName ?? (_viewingPath?.split('/').last ?? '');
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 4),
      child: Align(
        alignment: Alignment.centerLeft,
        child: GestureDetector(
          onTap: _clearViewingChip,
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
              color: const Color(0xFF2E2A27),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: _amber),
            ),
            child: Text(
              '查看中: $name · 点此切回最新',
              style: const TextStyle(
                fontFamily: 'monospace',
                fontSize: 10,
                color: _amber,
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildLogBox() {
    return Container(
      height: 230,
      decoration: BoxDecoration(
        color: const Color(0xFF151210),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFF2E2926)),
      ),
      child: _loading && _lines.isEmpty
          ? const Center(
              child: SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: _amber,
                ),
              ),
            )
          : _lines.isEmpty
              ? Center(
                  child: Text(
                    _error ?? '暂无日志',
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 12,
                      color: _muted,
                    ),
                  ),
                )
              : ListView.builder(
                  controller: _scrollController,
                  reverse: true,
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                  itemCount: _lines.length,
                  itemBuilder: (context, index) {
                    final line = _lines[_lines.length - 1 - index];
                    return Text(
                      line,
                      style: TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 11,
                        height: 1.4,
                        color: _colorForLine(line),
                      ),
                    );
                  },
                ),
    );
  }

  Widget _buildFileList() {
    return Container(
      height: 230,
      decoration: BoxDecoration(
        color: const Color(0xFF151210),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFF2E2926)),
      ),
      child: _loading && _files.isEmpty
          ? const Center(
              child: SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: _amber,
                ),
              ),
            )
          : _files.isEmpty
              ? Center(
                  child: Text(
                    _error ?? '暂无历史日志文件',
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 12,
                      color: _muted,
                    ),
                  ),
                )
              : ListView.builder(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  itemCount: _files.length,
                  itemBuilder: (context, index) {
                    final info = _extractFileInfo(_files[index]);
                    return ListTile(
                      dense: true,
                      visualDensity: VisualDensity.compact,
                      leading: const Icon(
                        Icons.description_outlined,
                        size: 16,
                        color: _muted,
                      ),
                      title: Text(
                        info.$2,
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 11,
                          color: _cream,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      subtitle: Text(
                        _formatBytes(info.$3),
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 10,
                          color: _muted,
                        ),
                      ),
                      onTap: () => _openFile(_files[index]),
                    );
                  },
                ),
    );
  }
}

class _FilterPill extends StatelessWidget {
  const _FilterPill({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: selected ? const Color(0xFF4A3F38) : const Color(0xFF221E1B),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(
            color: selected ? const Color(0xFFFCD34D) : const Color(0xFF3E352F),
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontFamily: 'monospace',
            fontSize: 11,
            color: selected ? const Color(0xFFFCD34D) : const Color(0xFF9C958C),
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    );
  }
}
