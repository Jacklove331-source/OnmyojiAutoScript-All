import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';

import 'api.dart';

/// One sequential request at a time. Old responses cannot revive a paused feed.
class LiveFrame extends StatefulWidget {
  const LiveFrame(
      {super.key,
      this.interval = const Duration(milliseconds: 200),
      this.api,
      this.onFrame,
      this.interactive = false,
      this.allowSwipe = false,
      this.showStatusOverlay = false});
  final Duration interval;
  final OasApi? api;
  final ValueChanged<MobileMonitorFrame>? onFrame;
  final bool interactive;
  final bool allowSwipe;
  final bool showStatusOverlay;

  @override
  State<LiveFrame> createState() => LiveFrameState();
}

class LiveFrameState extends State<LiveFrame> with WidgetsBindingObserver {
  MobileMonitorFrame? _frame;
  Timer? _next;
  Timer? _clock;
  Timer? _markerTimer;
  Timer? _postInputRefresh;
  CancelToken? _cancel;
  int _generation = 0;
  bool _paused = false;
  bool _visible = true;
  bool _foreground = true;
  bool _failed = false;
  final List<DateTime> _arrivals = <DateTime>[];
  Offset? _touchMarker;
  Offset? _dragStartLocal;
  DateTime? _dragStartAt;

  MobileMonitorFrame? get lastFrame => _frame;
  bool get paused => _paused;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _foreground = WidgetsBinding.instance.lifecycleState == null ||
        WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed;
    _clock = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && _visible && _foreground && _frame != null) setState(() {});
    });
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _visible = TickerMode.of(context);
    _restart();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _foreground = state == AppLifecycleState.resumed;
    _restart();
  }

  void setPaused(bool value) {
    if (_paused == value) return;
    setState(() => _paused = value);
    _restart();
  }

  void refresh() => _restart();

  void _restart() {
    _generation++;
    _next?.cancel();
    _cancel?.cancel('feed visibility changed');
    if (!_paused && _visible && _foreground) {
      final int generation = _generation;
      _next = Timer(Duration.zero, () => _tick(generation));
    }
  }

  Future<void> _tick(int generation) async {
    if (!mounted || generation != _generation) return;
    final CancelToken cancel = CancelToken();
    _cancel = cancel;
    try {
      final MobileMonitorFrame? frame =
          await (widget.api ?? OasApi.i).monitorFrame(cancelToken: cancel);
      if (!mounted || generation != _generation) return;
      final bool changed = frame != null &&
          (frame.frameId <= 0 ||
              frame.frameId != _frame?.frameId ||
              frame.displayId != _frame?.displayId);
      setState(() {
        _failed = frame == null;
        if (changed) {
          _frame = frame;
          _arrivals.add(DateTime.now());
          if (_arrivals.length > 20) _arrivals.removeAt(0);
        }
      });
      if (changed) widget.onFrame?.call(frame);
    } on DioException catch (error) {
      if (!CancelToken.isCancel(error) &&
          mounted &&
          generation == _generation) {
        setState(() => _failed = true);
      }
    } finally {
      if (mounted &&
          generation == _generation &&
          !_paused &&
          _visible &&
          _foreground) {
        _next = Timer(widget.interval, () => _tick(generation));
      }
    }
  }

  @override
  void dispose() {
    _generation++;
    _next?.cancel();
    _clock?.cancel();
    _markerTimer?.cancel();
    _postInputRefresh?.cancel();
    _cancel?.cancel('monitor disposed');
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  /// 将控件内的触摸坐标精确映射到虚拟屏实际像素（按 BoxFit.contain 扣掉黑边）。
  (int, int)? _mapToVirtualPixels(
      Offset local, Size box, MobileMonitorFrame frame) {
    final int fw = frame.width > 0 ? frame.width : 1280;
    final int fh = frame.height > 0 ? frame.height : 720;
    if (box.width <= 0 || box.height <= 0) return null;
    final double boxRatio = box.width / box.height;
    final double frameRatio = fw / fh;
    late final double drawW;
    late final double drawH;
    late final double offsetX;
    late final double offsetY;
    if (boxRatio > frameRatio) {
      drawH = box.height;
      drawW = drawH * frameRatio;
      offsetX = (box.width - drawW) / 2.0;
      offsetY = 0.0;
    } else {
      drawW = box.width;
      drawH = drawW / frameRatio;
      offsetX = 0.0;
      offsetY = (box.height - drawH) / 2.0;
    }
    if (local.dx < offsetX ||
        local.dx > offsetX + drawW ||
        local.dy < offsetY ||
        local.dy > offsetY + drawH) {
      return null;
    }
    final double u = (local.dx - offsetX) / drawW;
    final double v = (local.dy - offsetY) / drawH;
    final int x = (u * fw).round().clamp(0, fw - 1);
    final int y = (v * fh).round().clamp(0, fh - 1);
    return (x, y);
  }

  void _showMarker(Offset local) {
    _markerTimer?.cancel();
    setState(() => _touchMarker = local);
    _markerTimer = Timer(const Duration(milliseconds: 360), () {
      if (mounted) setState(() => _touchMarker = null);
    });
  }

  void _schedulePostInputRefresh() {
    _postInputRefresh?.cancel();
    if (_paused) return;
    _postInputRefresh = Timer(const Duration(milliseconds: 110), () {
      if (mounted && !_paused && _visible && _foreground) {
        _restart();
      }
    });
  }

  Future<void> _sendTap(Offset local, Size box) async {
    final MobileMonitorFrame? frame = _frame;
    if (frame == null) return;
    final (int, int)? mapped = _mapToVirtualPixels(local, box, frame);
    if (mapped == null) return;
    _showMarker(local);
    try {
      await (widget.api ?? OasApi.i)
          .vdTap(mapped.$1, mapped.$2, displayId: frame.displayId);
      _schedulePostInputRefresh();
    } catch (_) {}
  }

  Future<void> _sendSwipe(
      Offset startLocal, Offset endLocal, Size box, int durationMs) async {
    final MobileMonitorFrame? frame = _frame;
    if (frame == null) return;
    final (int, int)? p1 = _mapToVirtualPixels(startLocal, box, frame);
    final (int, int)? p2 = _mapToVirtualPixels(endLocal, box, frame);
    if (p1 == null || p2 == null) return;
    _showMarker(endLocal);
    try {
      await (widget.api ?? OasApi.i).vdSwipe(
        p1.$1,
        p1.$2,
        p2.$1,
        p2.$2,
        durationMs: durationMs.clamp(120, 900),
        displayId: frame.displayId,
      );
      _schedulePostInputRefresh();
    } catch (_) {}
  }

  String get _status {
    if (_paused) return '画面已暂停';
    if (_frame == null) return _failed ? '尚无后台画面' : '正在连接画面…';
    if (_failed) return '连接中断 · 保留上一帧';
    if (_frame!.stale) return '等待新帧 · 保留上一帧';
    return _frame!.capturedAt == null ? '已收到画面' : '画面持续更新';
  }

  @override
  Widget build(BuildContext context) {
    final MobileMonitorFrame? frame = _frame;
    String details = '';
    if (frame != null) {
      details = '虚拟屏 ${frame.displayId} (${frame.width}×${frame.height})';
      if (frame.capturedAt != null) {
        final int age =
            DateTime.now().difference(frame.capturedAt!).inMilliseconds;
        details += ' · ${(age.clamp(0, 9999999) / 1000).toStringAsFixed(1)} 秒前';
      }
      if (_arrivals.length > 1 && !_paused && !_failed && !frame.stale) {
        final int elapsed =
            _arrivals.last.difference(_arrivals.first).inMilliseconds;
        if (elapsed > 0) {
          details +=
              ' · ${((_arrivals.length - 1) * 1000 / elapsed).toStringAsFixed(1)} fps';
        }
      }
    }
    return ColoredBox(
      color: const Color(0xff17131b),
      child: LayoutBuilder(
        builder: (BuildContext context, BoxConstraints constraints) {
          final Size box = constraints.biggest;
          Widget content = Stack(fit: StackFit.expand, children: <Widget>[
            if (frame != null)
              Image.memory(frame.bytes,
                  fit: BoxFit.contain,
                  gaplessPlayback: true,
                  filterQuality: FilterQuality.low,
                  errorBuilder: (_, __, ___) => const SizedBox.shrink())
            else if (widget.showStatusOverlay)
              const Center(
                  child: Padding(
                      padding: EdgeInsets.all(24),
                      child: Text('启动后台游戏后显示画面\n任务在独立虚拟屏运行',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                              color: Colors.white54,
                              fontSize: 12,
                              height: 1.8)))),
            if (_touchMarker != null)
              Positioned(
                  left: _touchMarker!.dx - 14,
                  top: _touchMarker!.dy - 14,
                  child: IgnorePointer(
                      child: Container(
                          width: 28,
                          height: 28,
                          decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: const Color(0x55ff5a36),
                              border: Border.all(
                                  color: const Color(0xffff8a65), width: 2))))),
            if (widget.showStatusOverlay)
              Positioned(
                  left: 8,
                  right: 8,
                  bottom: 8,
                  child: IgnorePointer(
                      child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 9, vertical: 6),
                          decoration: BoxDecoration(
                              color: Colors.black.withOpacity(.65),
                              borderRadius: BorderRadius.circular(6)),
                          child: Text(
                              '$_status${widget.interactive ? ' · 点击画面可操控' : ''}${details.isEmpty ? '' : '\n$details'}',
                              style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 10,
                                  height: 1.5))))),
          ]);
          if (widget.interactive && frame != null) {
            if (widget.allowSwipe) {
              content = Listener(
                behavior: HitTestBehavior.opaque,
                onPointerDown: (PointerDownEvent event) {
                  _dragStartLocal = event.localPosition;
                  _dragStartAt = DateTime.now();
                },
                onPointerUp: (PointerUpEvent event) {
                  final Offset? start = _dragStartLocal;
                  final DateTime? startedAt = _dragStartAt;
                  _dragStartLocal = null;
                  _dragStartAt = null;
                  if (start == null) return;
                  final double dist = (event.localPosition - start).distance;
                  if (dist < 14.0) {
                    _sendTap(event.localPosition, box);
                  } else {
                    final int ms = startedAt == null
                        ? 260
                        : DateTime.now().difference(startedAt).inMilliseconds;
                    _sendSwipe(start, event.localPosition, box, ms);
                  }
                },
                onPointerCancel: (_) {
                  _dragStartLocal = null;
                  _dragStartAt = null;
                },
                child: content,
              );
            } else {
              content = GestureDetector(
                behavior: HitTestBehavior.opaque,
                onTapUp: (TapUpDetails details) =>
                    _sendTap(details.localPosition, box),
                child: content,
              );
            }
          }
          return content;
        },
      ),
    );
  }
}
