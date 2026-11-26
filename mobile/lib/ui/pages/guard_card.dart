import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

class OasGuardCard extends StatefulWidget {
  const OasGuardCard({super.key, this.api});

  final OasApi? api;

  @override
  State<OasGuardCard> createState() => _OasGuardCardState();
}

class _OasGuardCardState extends State<OasGuardCard> {
  OasApi get _api => widget.api ?? OasApi.i;

  Map<String, dynamic> _status = const <String, dynamic>{};
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Map<String, dynamic> get _cfg {
    final Object? raw = _status['config'];
    if (raw is Map<String, dynamic>) return raw;
    if (raw is Map) return Map<String, dynamic>.from(raw);
    return const <String, dynamic>{};
  }

  bool _cfgBool(String key, [bool fallback = false]) {
    final Object? v = _cfg[key];
    return v is bool ? v : fallback;
  }

  int _cfgInt(String key, [int fallback = 0]) {
    final Object? v = _cfg[key];
    if (v is int) return v;
    if (v is num) return v.toInt();
    return fallback;
  }

  String _cfgStr(String key, [String fallback = '']) {
    final Object? v = _cfg[key];
    return v is String && v.isNotEmpty ? v : fallback;
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final Map<String, dynamic> status = await _api.guardStatus();
      if (!mounted) return;
      if (status['ok'] == false) {
        setState(() {
          _error = '${status['error'] ?? '读取失败'}';
          _loading = false;
        });
        return;
      }
      setState(() {
        _status = status;
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

  Future<void> _savePatch(Map<String, dynamic> patch) async {
    final Map<String, dynamic> nextCfg = <String, dynamic>{..._cfg, ...patch};
    setState(() {
      _status = <String, dynamic>{..._status, 'config': nextCfg};
    });
    try {
      final Map<String, dynamic> res = await _api.guardConfigSave(patch);
      if (!mounted) return;
      if (res['ok'] == true) {
        setState(() => _status = res);
      }
    } catch (_) {}
  }

  void _cycleThermal() {
    const List<int> values = <int>[40, 42, 44, 46];
    final int current = _cfgInt('max_temp_c', 42);
    final int index = values.indexOf(current);
    final int next = values[(index + 1) % values.length];
    _savePatch(<String, dynamic>{
      'max_temp_c': next.toDouble(),
      'cooldown_temp_c': (next - 4).toDouble(),
    });
  }

  void _cycleBattery() {
    const List<int> values = <int>[10, 15, 20, 30];
    final int current = _cfgInt('min_battery_pct', 15);
    final int index = values.indexOf(current);
    final int next = values[(index + 1) % values.length];
    _savePatch(<String, dynamic>{'min_battery_pct': next});
  }

  Future<void> _editSchedule() async {
    final Map<String, String>? result = await showDialog<Map<String, String>>(
      context: context,
      builder: (_) => _ScheduleDialog(
        initialStart: _cfgStr('start_time', '00:05'),
        initialStop: _cfgStr('stop_time', '02:30'),
      ),
    );
    if (result == null) return;
    final String start = result['start']?.trim() ?? '';
    final String stop = result['stop']?.trim() ?? '';
    if (start.isEmpty || stop.isEmpty) return;
    await _savePatch(<String, dynamic>{
      'start_time': start,
      'stop_time': stop,
    });
  }

  @override
  Widget build(BuildContext context) {
    final OasPalette p = context.oas;
    return OasCard(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  '手机护航（温控 / 电量 / 定时 / 静音）',
                  style: TextStyle(
                    fontSize: 13.5,
                    fontWeight: FontWeight.w700,
                    color: p.ink,
                  ),
                ),
              ),
              InkWell(
                onTap: _load,
                borderRadius: BorderRadius.circular(12),
                child: Padding(
                  padding: const EdgeInsets.all(4),
                  child: Icon(Icons.refresh, size: 16, color: p.inkDim),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          if (_loading)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Center(
                child: SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              ),
            )
          else if (_error != null)
            _buildError(context, p)
          else
            _buildContent(context, p),
        ],
      ),
    );
  }

  Widget _buildError(BuildContext context, OasPalette p) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Text('护航状态读取失败', style: TextStyle(fontSize: 12, color: p.inkDim)),
        const SizedBox(height: 4),
        Text(
          _error!,
          style: TextStyle(fontSize: 11, color: p.inkDim),
          textAlign: TextAlign.center,
        ),
        TextButton(onPressed: _load, child: const Text('重试')),
      ],
    );
  }

  Widget _buildContent(BuildContext context, OasPalette p) {
    final Map<String, dynamic> telemetry =
        (_status['telemetry'] is Map)
            ? Map<String, dynamic>.from(_status['telemetry'] as Map)
            : const <String, dynamic>{};
    final double temp =
        (telemetry['battery_temp_c'] as num?)?.toDouble() ?? -1.0;
    final int batteryPct = (telemetry['battery_pct'] as num?)?.toInt() ?? -1;
    final bool charging = telemetry['charging'] == true;
    final bool online = telemetry['network_online'] != false;
    final String networkType = '${telemetry['network_type'] ?? ''}';

    final int maxTemp = _cfgInt('max_temp_c', 42);
    final bool overheat = temp > 0 && temp >= maxTemp;
    final String tempText = temp > 0 ? '${temp.toStringAsFixed(1)}℃' : '—';
    final String batteryText =
        batteryPct >= 0 ? '$batteryPct%${charging ? " ⚡" : ""}' : '—';
    final String networkText =
        online ? (networkType == 'wifi' ? 'Wi-Fi' : '已联网') : '断网';

    final bool thermalEnabled = _cfgBool('thermal_enabled', true);
    final bool batteryEnabled = _cfgBool('battery_enabled', true);
    final bool autoMute = _cfgBool('auto_mute_game', true);
    final bool notifyBoth =
        _cfgBool('notify_progress', true) && _cfgBool('notify_alerts', true);
    final bool scheduleEnabled = _cfgBool('schedule_enabled', false);

    final String startTime = _cfgStr('start_time', '00:05');
    final String stopTime = _cfgStr('stop_time', '02:30');

    final bool autoPaused = _status['auto_paused'] == true;
    final String autoPauseReason = '${_status['auto_pause_reason'] ?? ''}';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        if (autoPaused)
          Container(
            margin: const EdgeInsets.only(bottom: 10),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(
              color: p.gold.withOpacity(0.15),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: p.gold.withOpacity(0.45)),
            ),
            child: Text(
              '护航已介入暂停：$autoPauseReason',
              style: TextStyle(color: p.gold, fontSize: 12),
            ),
          ),
        Wrap(
          spacing: 8,
          runSpacing: 6,
          children: <Widget>[
            _chip(
              p,
              '电池温度',
              tempText,
              color: overheat ? p.vermilion : p.ink,
            ),
            _chip(
              p,
              '电量',
              batteryText,
              color: (batteryPct >= 0 &&
                      batteryPct <= _cfgInt('min_battery_pct', 15) &&
                      !charging)
                  ? p.vermilion
                  : p.ink,
            ),
            _chip(
              p,
              '网络',
              networkText,
              color: online ? p.jade : p.vermilion,
            ),
          ],
        ),
        const SizedBox(height: 10),
        _switchRow(
          p,
          '过温保护',
          thermalEnabled,
          (bool v) => _savePatch(<String, dynamic>{'thermal_enabled': v}),
          trailing: _pill(p, '上限 $maxTemp℃', onTap: _cycleThermal),
        ),
        _switchRow(
          p,
          '低电量停挂',
          batteryEnabled,
          (bool v) => _savePatch(<String, dynamic>{'battery_enabled': v}),
          trailing: _pill(
            p,
            '低于 ${_cfgInt('min_battery_pct', 15)}%',
            onTap: _cycleBattery,
          ),
        ),
        _switchRow(
          p,
          '后台挂机自动静音',
          autoMute,
          (bool v) => _savePatch(<String, dynamic>{'auto_mute_game': v}),
        ),
        _switchRow(
          p,
          '通知栏实时进度与提醒',
          notifyBoth,
          (bool v) => _savePatch(<String, dynamic>{
            'notify_progress': v,
            'notify_alerts': v,
          }),
        ),
        _switchRow(
          p,
          '定时启停窗口',
          scheduleEnabled,
          (bool v) => _savePatch(<String, dynamic>{'schedule_enabled': v}),
          trailing: _pill(p, '$startTime ~ $stopTime', onTap: _editSchedule),
        ),
      ],
    );
  }

  Widget _switchRow(
    OasPalette p,
    String label,
    bool value,
    ValueChanged<bool> onChanged, {
    Widget? trailing,
  }) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(
              label,
              style: TextStyle(fontSize: 12.5, color: p.inkSoft),
            ),
          ),
          if (trailing != null) ...<Widget>[
            trailing,
            const SizedBox(width: 6),
          ],
          Switch.adaptive(
            value: value,
            onChanged: onChanged,
            activeColor: p.vermilion,
          ),
        ],
      ),
    );
  }

  Widget _pill(OasPalette p, String label, {VoidCallback? onTap}) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(20),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
        decoration: BoxDecoration(
          color: p.gold.withOpacity(0.12),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: p.gold.withOpacity(0.35)),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w600,
            color: p.gold,
          ),
        ),
      ),
    );
  }

  Widget _chip(OasPalette p, String label, String value, {Color? color}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: p.surfaceAlt.withOpacity(0.7),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: p.line),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(label, style: TextStyle(fontSize: 11, color: p.inkDim)),
          const SizedBox(width: 4),
          Text(
            value,
            style: TextStyle(
              fontSize: 11.5,
              fontWeight: FontWeight.w700,
              color: color ?? p.ink,
            ),
          ),
        ],
      ),
    );
  }
}

class _ScheduleDialog extends StatefulWidget {
  const _ScheduleDialog({
    required this.initialStart,
    required this.initialStop,
  });

  final String initialStart;
  final String initialStop;

  @override
  State<_ScheduleDialog> createState() => _ScheduleDialogState();
}

class _ScheduleDialogState extends State<_ScheduleDialog> {
  late final TextEditingController _start =
      TextEditingController(text: widget.initialStart);
  late final TextEditingController _stop =
      TextEditingController(text: widget.initialStop);

  @override
  void dispose() {
    _start.dispose();
    _stop.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('定时启停窗口'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          TextField(
            controller: _start,
            decoration: const InputDecoration(
              labelText: '开始时间 (HH:mm)',
              hintText: '例如 00:05',
            ),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: _stop,
            decoration: const InputDecoration(
              labelText: '结束时间 (HH:mm)',
              hintText: '例如 02:30',
            ),
          ),
        ],
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, <String, String>{
            'start': _start.text.trim(),
            'stop': _stop.text.trim(),
          }),
          child: const Text('保存'),
        ),
      ],
    );
  }
}
