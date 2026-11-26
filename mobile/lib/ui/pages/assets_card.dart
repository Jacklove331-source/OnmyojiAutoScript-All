import 'package:flutter/material.dart';

import '../api.dart';
import '../widgets.dart';

/// Formats an asset value for compact display.
/// - null -> '—'
/// - num >= 100_000_000 -> 'X.XX亿'
/// - num >= 100_000 -> 'X万'
/// - else -> integer string
String formatAssetValue(Object? val, String? text) {
  if (val == null) return '—';
  if (val is num) {
    if (val >= 100000000) {
      return '${(val / 100000000).toStringAsFixed(2)}亿';
    }
    if (val >= 100000) {
      return '${(val / 10000).toStringAsFixed(0)}万';
    }
    return val.toInt().toString();
  }
  return val.toString();
}

/// Short datetime formatter: MM-DD HH:mm
String _shortDateTime(DateTime dt) {
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(dt.month)}-${two(dt.day)} ${two(dt.hour)}:${two(dt.minute)}';
}

String? _formatCapturedAt(dynamic value) {
  if (value == null) return null;
  final dt = DateTime.tryParse(value.toString());
  if (dt == null) return null;
  return _shortDateTime(dt);
}

Map<String, dynamic>? _itemMap(Map<String, dynamic> data, String key) {
  final Object? items = data['items'];
  if (items is Map && items[key] is Map) {
    return Map<String, dynamic>.from(items[key] as Map);
  }
  return null;
}

Object? _valueFor(Map<String, dynamic> data, String key) {
  final Map<String, dynamic>? item = _itemMap(data, key);
  if (item != null && item.containsKey('value')) return item['value'];
  return data[key];
}

String _precisionTextFor(Map<String, dynamic> data, String key) {
  final Map<String, dynamic>? item = _itemMap(data, key);
  final Object? raw = item?['precision'] ??
      (data['precision'] is Map ? (data['precision'] as Map)[key] : null);
  if (raw is String) {
    switch (raw.toLowerCase()) {
      case 'precise':
      case 'accurate':
      case 'exact':
        return '精确';
      case 'approx':
      case 'approximate':
      case 'rough':
        return '近似';
      default:
        return '未采集';
    }
  }
  return '未采集';
}

String _lastOkFor(Map<String, dynamic> data, String key) {
  final Map<String, dynamic>? item = _itemMap(data, key);
  final Object? v = item?['last_ok_at'] ??
      (data['last_ok'] is Map ? (data['last_ok'] as Map)[key] : null) ??
      data['captured_at'];
  if (v != null) {
    final DateTime? dt = DateTime.tryParse(v.toString());
    if (dt != null) return _shortDateTime(dt);
    return v.toString();
  }
  return '—';
}

num? _deltaFor(Map<String, dynamic> data, String key) {
  final Object? d = data['delta'];
  if (d is Map) {
    final Object? v = d[key];
    if (v is num && v != 0) return v;
  }
  return null;
}

const _assetItems = <({String label, String key})>[
  (label: '勾玉', key: 'jade'),
  (label: '金币', key: 'coin'),
  (label: '体力', key: 'ap'),
  (label: '神秘符咒', key: 'mystery_amulet'),
  (label: '皮肤券', key: 'skin_token'),
  (label: '魂玉', key: 's_jade'),
];

const _summaryKeys = <({String label, String key})>[
  (label: '勾玉', key: 'jade'),
  (label: '体力', key: 'ap'),
  (label: '金币', key: 'coin'),
  (label: '符咒', key: 'mystery_amulet'),
];

/// Full-screen detail dialog for asset data.
Future<void> showAssetsDetailDialog(
  BuildContext context,
  Map<String, dynamic>? data,
  VoidCallback onRefresh,
) async {
  final snapshot = data ?? const <String, dynamic>{};

  return showDialog<void>(
    context: context,
    builder: (context) {
      return AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.auto_awesome, size: 20),
            SizedBox(width: 8),
            Text('资产数据详情'),
          ],
        ),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              for (final item in _assetItems)
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  leading: _CurrencyIcon(assetKey: item.key, size: 28),
                  title: Text(item.label),
                  trailing: Column(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        formatAssetValue(_valueFor(snapshot, item.key), item.label),
                        style: const TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 2),
                      _PrecisionBadge(text: _precisionTextFor(snapshot, item.key)),
                      const SizedBox(height: 2),
                      Text(
                        'Δ ${_deltaText(_deltaFor(snapshot, item.key))}',
                        style: TextStyle(
                          fontSize: 11,
                          color: _deltaColor(_deltaFor(snapshot, item.key)),
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '最后采集: ${_lastOkFor(snapshot, item.key)}',
                        style: const TextStyle(fontSize: 10, color: Colors.grey),
                      ),
                    ],
                  ),
                ),
              const Divider(height: 24),
              const Text(
                '纯截图 OCR 只读，绝不点购买/确认；每天任务全部跑完自动更新 1 次',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 11, color: Colors.grey),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('关闭'),
          ),
          TextButton.icon(
            onPressed: () {
              Navigator.pop(context);
              onRefresh();
            },
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('立即巡检更新'),
          ),
        ],
      );
    },
  );
}

String _deltaText(num? delta) {
  if (delta == null) return '—';
  return delta > 0 ? '+${delta.toInt()}' : '${delta.toInt()}';
}

Color _deltaColor(num? delta) {
  if (delta == null) return Colors.grey;
  return delta > 0 ? Colors.green : Colors.redAccent;
}

class _PrecisionBadge extends StatelessWidget {
  const _PrecisionBadge({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    Color color;
    switch (text) {
      case '精确':
        color = Colors.green;
        break;
      case '近似':
        color = Colors.orange;
        break;
      default:
        color = Colors.grey;
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        color: color.withOpacity(0.12),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withOpacity(0.4), width: 0.5),
      ),
      child: Text(
        text,
        style: TextStyle(fontSize: 10, color: color, fontWeight: FontWeight.w500),
      ),
    );
  }
}

class OasAssetsCard extends StatefulWidget {
  const OasAssetsCard({super.key, this.api});

  final OasApi? api;

  @override
  State<OasAssetsCard> createState() => _OasAssetsCardState();
}

class _OasAssetsCardState extends State<OasAssetsCard> {
  Map<String, dynamic> _snapshot = const {};
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  OasApi get _api => widget.api ?? OasApi.i;

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await _api.assetsSnapshot();
      if (!mounted) return;
      setState(() {
        _snapshot = data;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _snapshot = const {};
        _loading = false;
        _error = null;
      });
    }
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      await _api.assetsRefresh(force: true);
      await Future<void>.delayed(const Duration(seconds: 3));
      if (!mounted) return;
      final data = await _api.assetsSnapshot();
      if (!mounted) return;
      setState(() {
        _snapshot = data;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = '更新失败';
      });
    }
  }

  void _openDetail() {
    showAssetsDetailDialog(context, _snapshot, _refresh);
  }

  @override
  Widget build(BuildContext context) {
    final subtitle = _buildSubtitle();
    return OasCard(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        '资产数据',
                        style: TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        subtitle,
                        style: const TextStyle(fontSize: 11, color: Colors.grey),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                  ),
                ),
                TextButton(
                  onPressed: _loading ? null : _openDetail,
                  style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    visualDensity: VisualDensity.compact,
                  ),
                  child: const Text('详情', style: TextStyle(fontSize: 12)),
                ),
                const SizedBox(width: 4),
                TextButton(
                  onPressed: _loading ? null : _refresh,
                  style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    visualDensity: VisualDensity.compact,
                  ),
                  child: _loading
                      ? const SizedBox(
                          width: 12,
                          height: 12,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('更新', style: TextStyle(fontSize: 12)),
                ),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                for (final item in _summaryKeys)
                  Expanded(
                    child: _SummaryItem(
                      assetKey: item.key,
                      label: item.label,
                      value: _valueFor(_snapshot, item.key),
                      delta: _deltaFor(_snapshot, item.key),
                    ),
                  ),
              ],
            ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  _error!,
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 10, color: Colors.redAccent),
                ),
              ),
          ],
        ),
      ),
    );
  }

  String _buildSubtitle() {
    final captured = _snapshot['captured_at'];
    final short = _formatCapturedAt(captured);
    if (short != null) {
      return '每日任务全完自动更新 · $short';
    }
    return '当日任务全完后自动更新一次';
  }
}

class _SummaryItem extends StatelessWidget {
  const _SummaryItem({
    required this.assetKey,
    required this.label,
    required this.value,
    required this.delta,
  });

  final String assetKey;
  final String label;
  final Object? value;
  final num? delta;

  @override
  Widget build(BuildContext context) {
    final deltaText = _deltaText(delta);
    final deltaColor = _deltaColor(delta);
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        _CurrencyIcon(assetKey: assetKey, size: 22),
        const SizedBox(height: 3),
        Text(
          label,
          style: const TextStyle(fontSize: 10.5, color: Colors.grey),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        const SizedBox(height: 2),
        FittedBox(
          fit: BoxFit.scaleDown,
          child: Text(
            formatAssetValue(value, label),
            style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
            maxLines: 1,
          ),
        ),
        const SizedBox(height: 2),
        SizedBox(
          height: 12,
          child: delta == null
              ? const SizedBox.shrink()
              : Container(
                  padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 0),
                  decoration: BoxDecoration(
                    color: deltaColor.withOpacity(0.12),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(
                    deltaText,
                    style: TextStyle(fontSize: 9, color: deltaColor),
                  ),
                ),
        ),
      ],
    );
  }
}

class _CurrencyIcon extends StatelessWidget {
  const _CurrencyIcon({required this.assetKey, this.size = 24});

  final String assetKey;
  final double size;

  @override
  Widget build(BuildContext context) {
    final (IconData icon, Color fg, Color bg) = switch (assetKey) {
      'jade' => (Icons.nights_stay_rounded, const Color(0xFFFFE082), const Color(0xFF92400E)),
      'ap' => (Icons.rice_bowl_rounded, const Color(0xFFFCA5A5), const Color(0xFF991B1B)),
      'coin' => (Icons.monetization_on_rounded, const Color(0xFFFDE047), const Color(0xFF854D0E)),
      'mystery_amulet' => (Icons.auto_awesome_rounded, const Color(0xFF93C5FD), const Color(0xFF1E3A8A)),
      'skin_token' => (Icons.confirmation_number_rounded, const Color(0xFFF9A8D4), const Color(0xFF831843)),
      's_jade' => (Icons.diamond_rounded, const Color(0xFFD8B4FE), const Color(0xFF581C87)),
      _ => (Icons.stars_rounded, const Color(0xFFE5E7EB), const Color(0xFF374151)),
    };
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: <Color>[bg, bg.withOpacity(0.7)],
        ),
        border: Border.all(color: fg.withOpacity(0.75), width: 1.1),
      ),
      alignment: Alignment.center,
      child: Icon(icon, size: size * 0.58, color: fg),
    );
  }
}
