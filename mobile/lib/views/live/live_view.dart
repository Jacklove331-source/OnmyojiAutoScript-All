import 'dart:async';

import 'package:flutter/material.dart';
import 'package:get/get.dart';

/// 实时画面页：轮询 OAS 服务端的 /{配置}/frame 接口显示游戏画面。
///
/// 用法：`Get.toNamed('/live', arguments: {'server': 'http://100.97.227.69:22267', 'config': 'oas1'})`
class LiveView extends StatefulWidget {
  const LiveView({super.key});

  @override
  State<LiveView> createState() => _LiveViewState();
}

class _LiveViewState extends State<LiveView> {
  late TextEditingController _serverCtrl;
  late TextEditingController _configCtrl;
  Timer? _timer;
  int _tick = 0;
  bool _playing = true;

  @override
  void initState() {
    super.initState();
    final args = (Get.arguments as Map?) ?? const {};
    _serverCtrl = TextEditingController(
      text: (args['server'] as String?) ?? 'http://127.0.0.1:22267',
    );
    _configCtrl = TextEditingController(
      text: (args['config'] as String?) ?? 'oas1',
    );
    _start();
  }

  void _start() {
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(milliseconds: 100), (_) {
      if (_playing && mounted) {
        setState(() => _tick++);
      }
    });
  }

  String get _frameUrl {
    final server = _serverCtrl.text.trim().replaceAll(RegExp(r'/+$'), '');
    final config = _configCtrl.text.trim();
    return '$server/$config/frame?t=$_tick';
  }

  @override
  void dispose() {
    _timer?.cancel();
    _serverCtrl.dispose();
    _configCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('实时画面'),
        actions: [
          IconButton(
            tooltip: _playing ? '暂停' : '继续',
            icon: Icon(_playing ? Icons.pause : Icons.play_arrow),
            onPressed: () => setState(() => _playing = !_playing),
          ),
          IconButton(
            tooltip: '立即刷新',
            icon: const Icon(Icons.refresh),
            onPressed: () => setState(() => _tick++),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(8),
            child: Row(
              children: [
                Expanded(
                  flex: 3,
                  child: TextField(
                    controller: _serverCtrl,
                    decoration: const InputDecoration(
                      labelText: '服务器地址',
                      hintText: 'http://100.x.y.z:22267',
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  flex: 2,
                  child: TextField(
                    controller: _configCtrl,
                    decoration: const InputDecoration(
                      labelText: '配置名',
                      hintText: 'oas1',
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                FilledButton(
                  onPressed: () => setState(() => _tick++),
                  child: const Text('连接'),
                ),
              ],
            ),
          ),
          const Divider(height: 1),
          Expanded(
            child: Center(
              child: Image.network(
                _frameUrl,
                fit: BoxFit.contain,
                gaplessPlayback: true,
                errorBuilder: (context, error, stack) => Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const Icon(Icons.videocam_off, size: 48),
                    const SizedBox(height: 12),
                    const Text('暂无画面'),
                    const SizedBox(height: 4),
                    Text(
                      '任务运行中才会有画面；\n请确认服务器地址与配置名。\n$_frameUrl',
                      textAlign: TextAlign.center,
                      style: const TextStyle(fontSize: 12),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
