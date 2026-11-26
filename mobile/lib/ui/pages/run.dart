import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

/// ③ 运行：日志与自愈过程看得见，控制键拇指够得着。
///
/// 数据来源：GET /state（暂停态）、/scheduler/status（running/round/log）、
/// /task/status（当前任务/log_tail）。控制键接 /pause、/resume、/scheduler/stop。
/// 没有日志时老实显示"暂无日志"，不摆示例。
class RunPage extends StatefulWidget {
  const RunPage({super.key});

  @override
  State<RunPage> createState() => _RunPageState();
}

class _RunPageState extends State<RunPage> {
  Map<String, dynamic>? _state;
  Map<String, dynamic>? _sched;
  Map<String, dynamic>? _task;
  Timer? _timer;
  bool _loading = true;
  bool _busy = false;
  String? _error;

  /// OAS 自己写在 log/ 里的日志（调度器接口没给日志时用来兜底）
  List<String> _fileLog = <String>[];

  @override
  void initState() {
    super.initState();
    _load();
    // 3 秒轮询一次，页面不可见时不停（IndexedStack 保活，用户切回来就是最新）
    _timer =
        Timer.periodic(const Duration(seconds: 3), (_) => _load(silent: true));
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load({bool silent = false}) async {
    if (!silent) setState(() => _loading = true);
    try {
      final List<Map<String, dynamic>> r = await Future.wait(
        <Future<Map<String, dynamic>>>[
          OasApi.i.state(),
          OasApi.i.schedulerStatus(),
          OasApi.i.taskStatus(),
        ],
      );
      if (!mounted) return;
      setState(() {
        _state = r[0];
        _sched = r[1];
        _task = r[2];
        _loading = false;
        _error = null;
      });
      // 调度器/任务两个接口都没给日志时，回落去读 OAS 自己写在 log/ 里的日志文件
      final bool apiHasLog =
          (_sched?['log'] is List && (_sched?['log'] as List).isNotEmpty) ||
              (_task?['log_tail'] is List &&
                  (_task?['log_tail'] as List).isNotEmpty);
      if (!apiHasLog) {
        final Map<String, dynamic> tail = await OasApi.i.logTail(lines: 60);
        if (!mounted) return;
        final Object? raw = tail['lines'];
        if (raw is List) {
          setState(() {
            _fileLog = raw.map((Object? e) => '$e').toList();
          });
        }
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  bool get _running => _sched?['running'] == true || _task?['running'] == true;
  bool get _paused => _state?['paused'] == true;

  /// 合并调度日志与任务日志尾部，去重后取最后 40 行（旧→新）。
  /// 两个接口都没日志时，用 OAS 自己写在 log/ 里的文件内容兜底。
  List<String> get _logs {
    final List<String> out = <String>[];
    for (final Object? v in <Object?>[_sched?['log'], _task?['log_tail']]) {
      if (v is List) {
        for (final Object? line in v) {
          if (line is String && line.trim().isNotEmpty && !out.contains(line)) {
            out.add(line);
          }
        }
      }
    }
    if (out.isEmpty) {
      for (final String line in _fileLog) {
        if (line.trim().isNotEmpty && !out.contains(line)) out.add(line);
      }
    }
    if (out.length > 40) return out.sublist(out.length - 40);
    return out;
  }

  int _levelOf(String line) {
    final String s = line.toLowerCase();
    if (s.contains('ai') || s.contains('自愈') || s.contains('recovery')) {
      return 2;
    }
    if (s.contains('✓') || s.contains('成功') || s.contains(' ok')) return 3;
    if (s.contains('识别') || s.contains('开始') || s.contains('进入')) return 1;
    return 0;
  }

  Future<void> _act(Future<bool> Function() op, String okMsg) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final bool ok = await op();
      if (!mounted) return;
      OasToast.show(context, ok ? okMsg : '后端没有接受这次操作');
      await _load(silent: true);
    } catch (e) {
      if (!mounted) return;
      OasToast.show(context, '操作失败：$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    final String scheduled = '${_sched?['current'] ?? ''}';
    final String taskName = scheduled.isNotEmpty
        ? scheduled
        : (_task?['running'] == true ? '${_task?['task'] ?? ''}' : '');
    final int round = (_sched?['round'] as num?)?.toInt() ?? 0;
    final int taskCount =
        _sched?['tasks'] is List ? (_sched!['tasks'] as List).length : 0;

    return SafeArea(
      bottom: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            OasPageHeader(
              badge: '行',
              title: '运行',
              subtitle: _loading
                  ? '正在读取运行状态…'
                  : (taskName.isEmpty
                      ? (_running ? '托管进行中' : '当前没有任务在跑')
                      : '${taskName.isEmpty ? '' : taskName}   第 $round / $taskCount 项'),
              trailing: OasPill(
                text: _loading
                    ? '…'
                    : (_running ? (_paused ? '已暂停' : '运行中') : '空闲'),
                color: _running ? p.jade : p.inkDim,
                dot: true,
              ),
            ),
            const SizedBox(height: 12),
            _progressCard(p),
            const SizedBox(height: 10),
            Expanded(child: _logCard(context, p)),
            const SizedBox(height: 10),
            Row(
              children: <Widget>[
                Expanded(
                  child: OasSoftButton(
                    label: _paused ? '继续' : '暂停',
                    color: p.inkSoft,
                    onTap: _busy || _loading
                        ? () {}
                        : () => _act(
                              _paused ? OasApi.i.resume : OasApi.i.pause,
                              _paused ? '已继续' : '已暂停',
                            ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OasSoftButton(
                    label: (_sched?['stopping'] == true ||
                            _task?['phase'] == 'stopping')
                        ? '停止中…'
                        : '停止',
                    color: p.vermilion,
                    onTap: _busy || _loading
                        ? () {}
                        : () => _act(OasApi.i.stopScheduler, '已请求停止任务'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OasSoftButton(
                    label: '刷新',
                    color: p.purple,
                    onTap: () => _load(),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _progressCard(OasPalette p) {
    return OasCard(
      padding: const EdgeInsets.fromLTRB(12, 11, 12, 11),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Text('托管状态', style: TextStyle(fontSize: 11.5, color: p.inkDim)),
              const Spacer(),
              Text(
                _error != null
                    ? '读取失败'
                    : (_running ? (_paused ? '已暂停' : '运行中') : '空闲'),
                style: OasFont.song(
                  size: 14,
                  color: _running ? p.jade : p.inkDim,
                  weight: FontWeight.w700,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          if (_running)
            LinearProgressIndicator(
                color: p.vermilion,
                backgroundColor: p.surfaceAlt,
                minHeight: 4),
          const SizedBox(height: 6),
          Text(
            _error != null
                ? '连不上内嵌服务：$_error'
                : '第 ${(_sched?['round'] ?? 0)} 轮 · 下次 ${_nextAtText()}',
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 10, color: p.inkDim),
          ),
        ],
      ),
    );
  }

  String _nextAtText() {
    final num? at = (_sched?['next_at'] as num?);
    if (at == null || at <= 0) return '未排期';
    final DateTime t = DateTime.fromMillisecondsSinceEpoch(
      at > 1000000000000 ? at.toInt() : at.toInt() * 1000,
    );
    return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
  }

  Widget _logCard(BuildContext context, OasPalette p) {
    final List<String> lines = _logs;
    return OasCard(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Text(
                '实时日志',
                style: TextStyle(
                  fontSize: 11.5,
                  fontWeight: FontWeight.w600,
                  color: p.inkSoft,
                ),
              ),
              const Spacer(),
              Text(
                lines.isEmpty ? '0 行' : '${lines.length} 行',
                style: TextStyle(fontSize: 10, color: p.inkDim),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Expanded(
            child: lines.isEmpty
                ? Center(
                    child: Text(
                      _loading ? '读取中…' : '暂无日志（还没开始托管）',
                      style: TextStyle(fontSize: 11.5, color: p.inkDim),
                    ),
                  )
                : ListView.builder(
                    reverse: true,
                    padding: EdgeInsets.zero,
                    physics: const BouncingScrollPhysics(),
                    itemCount: lines.length,
                    itemBuilder: (BuildContext c, int i) {
                      final String line = lines[lines.length - 1 - i];
                      final int lv = _levelOf(line);
                      final Color color = lv == 2
                          ? p.vermilion
                          : (lv == 3
                              ? p.jade
                              : (lv == 1 ? p.inkSoft : p.inkDim));
                      return Padding(
                        padding: const EdgeInsets.symmetric(vertical: 3),
                        child: Text(
                          line,
                          style: TextStyle(
                            fontSize: 10.5,
                            height: 1.35,
                            color: color,
                            fontFamily: lv == 2 ? null : 'monospace',
                          ),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }
}
