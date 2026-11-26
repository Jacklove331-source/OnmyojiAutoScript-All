import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';
import '../widgets_live.dart';
import 'monitor_log_panel.dart';

class MonitorPage extends StatefulWidget {
  const MonitorPage({super.key});

  @override
  State<MonitorPage> createState() => _MonitorPageState();
}

class _MonitorPageState extends State<MonitorPage> {
  final GlobalKey<LiveFrameState> _live = GlobalKey<LiveFrameState>();
  final List<MobileMonitorFrame> _recent = <MobileMonitorFrame>[];
  DateTime? _lastThumbnail;
  bool _paused = false;

  void _received(MobileMonitorFrame frame) {
    final DateTime now = DateTime.now();
    if (_lastThumbnail != null &&
        now.difference(_lastThumbnail!) < const Duration(seconds: 3)) return;
    _lastThumbnail = now;
    setState(() {
      _recent.insert(0, frame);
      if (_recent.length > 5) _recent.removeLast();
    });
  }

  Future<void> _save(MobileMonitorFrame frame) async {
    try {
      final String stamp =
          DateTime.now().toIso8601String().replaceAll(':', '-');
      final String? path = await FilePicker.platform.saveFile(
        dialogTitle: '保存 OAS 游戏截图',
        fileName: 'OAS-$stamp.${frame.isJpeg ? 'jpg' : 'png'}',
        bytes: frame.bytes,
      );
      if (mounted && path != null) OasToast.show(context, '图片已保存');
    } catch (error) {
      if (mounted) OasToast.show(context, '保存失败：$error');
    }
  }

  Future<void> _preview(MobileMonitorFrame frame, {bool canSave = false}) =>
      showDialog<void>(
        context: context,
        barrierColor: Colors.black87,
        builder: (BuildContext dialog) => Dialog(
          backgroundColor: const Color(0xff17131b),
          insetPadding: const EdgeInsets.all(12),
          child: Column(mainAxisSize: MainAxisSize.min, children: <Widget>[
            AspectRatio(
                aspectRatio: 16 / 9,
                child: InteractiveViewer(
                    maxScale: 5,
                    child: Image.memory(frame.bytes, fit: BoxFit.contain))),
            Padding(
                padding: const EdgeInsets.all(10),
                child: Row(children: <Widget>[
                  Expanded(
                      child: Text(
                          frame.capturedAt == null
                              ? '保存的画面'
                              : '采集于 ${_time(frame.capturedAt!)}',
                          style: const TextStyle(
                              color: Colors.white70, fontSize: 12))),
                  if (canSave)
                    TextButton(
                        onPressed: () => _save(frame),
                        child: const Text('保存图片')),
                  TextButton(
                      onPressed: () => Navigator.of(dialog).pop(),
                      child: const Text('关闭')),
                ])),
          ]),
        ),
      );

  Future<void> _fullscreen() async {
    _live.currentState?.setPaused(true);
    await showDialog<void>(
        context: context,
        barrierColor: Colors.black,
        useSafeArea: false,
        builder: (BuildContext dialog) => const _TakeoverScreen());
    if (mounted) _live.currentState?.setPaused(_paused);
  }

  static String _time(DateTime value) =>
      '${value.hour.toString().padLeft(2, '0')}:${value.minute.toString().padLeft(2, '0')}:${value.second.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return SafeArea(
        bottom: false,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 24),
          children: <Widget>[
            OasPageHeader(
                badge: '监',
                badgeAsset: 'assets/onmyoji/dock_monitor.png',
                title: '监控 · 日志',
                subtitle: '上方点画面直接接管 · 下方实时滚动运行日志',
                trailing:
                    OasPill(text: '可触控虚拟屏', color: p.jade, outlined: true)),
            const SizedBox(height: 12),
            Container(
                clipBehavior: Clip.antiAlias,
                decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(kRadiusCard),
                    border: Border.all(color: p.line)),
                child: AspectRatio(
                    aspectRatio: 16 / 9,
                    child: LiveFrame(
                        key: _live,
                        onFrame: _received,
                        interactive: true,
                        showStatusOverlay: false))),
            const SizedBox(height: 10),
            Row(children: <Widget>[
              Expanded(
                  child: OasSoftButton(
                      label: _paused ? '继续画面' : '暂停画面',
                      color: p.inkSoft,
                      onTap: () {
                        setState(() => _paused = !_paused);
                        _live.currentState?.setPaused(_paused);
                      })),
              const SizedBox(width: 8),
              Expanded(
                  child: OasSoftButton(
                      label: '刷新画面',
                      color: p.jade,
                      onTap: () => _live.currentState?.refresh())),
              const SizedBox(width: 8),
              Expanded(
                  child: OasSoftButton(
                      label: '全屏接管', color: p.purple, onTap: _fullscreen)),
            ]),
            const SizedBox(height: 14),
            const OasMonitorLogPanel(),
            const SizedBox(height: 14),
            const _AiStatusCard(),
            const SizedBox(height: 16),
            const OasSectionHeader(
                title: '最近画面', trailing: '每 3 秒留一帧 · 最近 5 张'),
            const SizedBox(height: 8),
            if (_recent.isEmpty)
              OasCard(
                  child: Text('收到游戏画面后显示记录',
                      style: TextStyle(color: p.inkDim, fontSize: 12)))
            else
              SizedBox(
                  height: 94,
                  child: ListView.separated(
                      scrollDirection: Axis.horizontal,
                      itemCount: _recent.length,
                      separatorBuilder: (_, __) => const SizedBox(width: 8),
                      itemBuilder: (_, int index) {
                        final MobileMonitorFrame frame = _recent[index];
                        return GestureDetector(
                            onTap: () => _preview(frame, canSave: true),
                            child: SizedBox(
                                width: 128,
                                child: Column(children: <Widget>[
                                  ClipRRect(
                                      borderRadius: BorderRadius.circular(6),
                                      child: AspectRatio(
                                          aspectRatio: 16 / 9,
                                          child: Image.memory(frame.bytes,
                                              fit: BoxFit.contain))),
                                  const SizedBox(height: 4),
                                  Text(
                                      frame.capturedAt == null
                                          ? '已接收'
                                          : _time(frame.capturedAt!),
                                      style: TextStyle(
                                          color: p.inkDim, fontSize: 10)),
                                ])));
                      })),
          ],
        ));
  }
}

/// 自愈状态卡：进页面取一次、点一下重取。
/// 不轮询——桥接（Chaquopy）本身要 10–20 秒才起得来，每秒打一次只会堆请求。
class _AiStatusCard extends StatefulWidget {
  const _AiStatusCard();

  @override
  State<_AiStatusCard> createState() => _AiStatusCardState();
}

class _AiStatusCardState extends State<_AiStatusCard> {
  Map<String, dynamic>? _data;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    // initState 里不调 setState；后续点击重取时才需要「读取中」的过渡态。
    if (_data != null || _error != null) {
      setState(() => _loading = true);
    }
    try {
      final Map<String, dynamic> d = await OasApi.i.aiHealth();
      if (!mounted) return;
      setState(() {
        _data = d;
        _error = null;
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

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: _load,
      child: OasAiStatusStrip(data: _data, error: _error, loading: _loading),
    );
  }
}

/// 全屏手动接管页：支持直接点击与滑动虚拟屏、发送虚拟屏返回键、以及一键暂停/恢复脚本防止抢手。
class _TakeoverScreen extends StatefulWidget {
  const _TakeoverScreen();

  @override
  State<_TakeoverScreen> createState() => _TakeoverScreenState();
}

class _TakeoverScreenState extends State<_TakeoverScreen> {
  final GlobalKey<LiveFrameState> _frameKey = GlobalKey<LiveFrameState>();
  bool _scriptPaused = false;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _syncPauseState();
  }

  Future<void> _syncPauseState() async {
    try {
      final Map<String, dynamic> st = await OasApi.i.state();
      if (mounted) {
        setState(() => _scriptPaused = st['paused'] == true);
      }
    } catch (_) {}
  }

  Future<void> _togglePause() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final bool next = !_scriptPaused;
      final bool ok = next ? await OasApi.i.pause() : await OasApi.i.resume();
      if (mounted && ok) {
        setState(() => _scriptPaused = next);
      }
    } catch (_) {} finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _sendBack() async {
    final int? displayId = _frameKey.currentState?.lastFrame?.displayId;
    try {
      await OasApi.i.vdKeyEvent(4, displayId: displayId);
      await Future<void>.delayed(const Duration(milliseconds: 120));
      _frameKey.currentState?.refresh();
    } catch (_) {}
  }

  Widget _chip(IconData icon, String label, VoidCallback onTap,
      {Color color = Colors.white}) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(18),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
            color: Colors.black.withOpacity(.72),
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: Colors.white24)),
        child: Row(mainAxisSize: MainAxisSize.min, children: <Widget>[
          Icon(icon, size: 15, color: color),
          const SizedBox(width: 4),
          Text(label, style: TextStyle(color: color, fontSize: 12)),
        ]),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: SafeArea(
        child: Stack(children: <Widget>[
          Positioned.fill(
            child: LiveFrame(
              key: _frameKey,
              interval: const Duration(milliseconds: 140),
              interactive: true,
              allowSwipe: true,
              showStatusOverlay: false,
            ),
          ),
          Positioned(
            left: 10,
            right: 10,
            top: 8,
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(children: <Widget>[
                _chip(
                  _scriptPaused ? Icons.play_arrow : Icons.pause,
                  _scriptPaused ? '恢复脚本' : '暂停脚本',
                  _togglePause,
                  color: _scriptPaused
                      ? const Color(0xff6ee7b7)
                      : const Color(0xfffcd34d),
                ),
                const SizedBox(width: 8),
                _chip(Icons.arrow_back, '虚拟屏返回', _sendBack),
                const SizedBox(width: 8),
                _chip(Icons.close, '退出接管', () => Navigator.of(context).pop(),
                    color: const Color(0xfffca5a5)),
              ]),
            ),
          ),
        ]),
      ),
    );
  }
}
