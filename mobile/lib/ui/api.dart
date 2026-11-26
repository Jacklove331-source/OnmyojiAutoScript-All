import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart' show ValueListenable, ValueNotifier;
import 'package:flutter/services.dart' show rootBundle;
import 'package:path_provider/path_provider.dart';

import 'task_names.dart';

/// 枚举取值表：`value -> 该枚举的全部取值`。
///
/// 来源是 `dev_tools/gen_enum_values.py` 在构建前扫 OAS 源码导出的
/// `assets/enum_values.json`（Chaquopy 打完包只剩 .imy，Python 侧读不到源码，
/// 所以这张表必须随 Dart 资源一起打进 APK）。
class OasEnums {
  OasEnums._();

  static Map<String, List<String>> _table = <String, List<String>>{};
  static bool _loaded = false;

  static Future<void> ensureLoaded() async {
    if (_loaded) return;
    _loaded = true;
    try {
      final String raw = await rootBundle.loadString('assets/enum_values.json');
      final Object? data = jsonDecode(raw);
      if (data is Map) {
        final Map<String, List<String>> table = <String, List<String>>{};
        data.forEach((Object? k, Object? v) {
          if (v is List) {
            table['$k'] = v.map((Object? e) => '$e').toList();
          }
        });
        _table = table;
      }
    } catch (_) {
      _table = <String, List<String>>{};
    }
  }

  /// 把「中文标签表」和「同枚举的存值表」按顺序配成 `存值 -> 标签`。
  /// 长度不一致就返回空（宁可不给下拉，也不要错配一个值）。
  static Map<String, String> pairs(List<String> labels, String currentValue) {
    if (labels.isEmpty) return const <String, String>{};
    final List<String>? values = _table[currentValue];
    if (values == null || values.length != labels.length) {
      return const <String, String>{};
    }
    return <String, String>{
      for (int i = 0; i < values.length; i++) values[i]: labels[i],
    };
  }
}

/// OASX 内嵌桥接客户端：App 自己就在 22267 上跑 Python 服务，
/// 所以这里直接打 127.0.0.1，不需要用户填地址。
class OasApi {
  OasApi._()
      : this.withClient(Dio(BaseOptions(
          baseUrl: 'http://127.0.0.1:22267',
          connectTimeout: const Duration(seconds: 5),
          receiveTimeout: const Duration(seconds: 20),
          responseType: ResponseType.plain,
        )));

  OasApi.withClient(this._dio);

  static final OasApi i = OasApi._();

  final Dio _dio;

  /// 内嵌 Python 服务要 10–20 秒才起得来（Chaquopy 初始化 + 导入 OAS 内核），
  /// App 冷启动后立刻请求会吃 connection refused，所以这里自带重试。
  Future<Response<String>> _getWithRetry(String path) async {
    DioException? last;
    for (int attempt = 0; attempt < 6; attempt++) {
      try {
        return await _dio.get<String>(path);
      } on DioException catch (e) {
        last = e;
        final bool transient = e.type == DioExceptionType.connectionError ||
            e.type == DioExceptionType.connectionTimeout ||
            e.type == DioExceptionType.receiveTimeout;
        if (!transient) rethrow;
        await Future<void>.delayed(Duration(milliseconds: 700 + attempt * 600));
      }
    }
    throw last ?? Exception('bridge unreachable');
  }

  Future<Map<String, dynamic>> _getJson(String path) async {
    final Response<String> r = await _getWithRetry(path);
    final Object? decoded = jsonDecode(r.data ?? '{}');
    return decoded is Map<String, dynamic> ? decoded : <String, dynamic>{};
  }

  /// 桥接体检。bridge_available 为 false 时说明 Shizuku 没授权。
  Future<Map<String, dynamic>> health() => _getJson('/health');

  /// AI 自愈状态：enabled / mode / model / has_key（不要把 Key 读出来）。
  Future<Map<String, dynamic>> aiHealth() => _getJson('/ai/health');

  /// AI 自愈历史：`/ai/recovery?limit=N`。老后端没有这条路由，失败就返回空页，
  /// 不让首页跟着报错。
  Future<OasAiRecovery> aiRecovery({int limit = 50}) async {
    try {
      final Map<String, dynamic> j =
          await _getJson('/ai/recovery?limit=$limit&config=$activeConfig');
      if (j['ok'] == false) return OasAiRecovery.empty;
      return OasAiRecovery.fromJson(j);
    } catch (_) {
      return OasAiRecovery.empty;
    }
  }

  /// AI 自愈配置（含 Key 是否已配，不回显 Key）。
  Future<Map<String, dynamic>> aiConfig() => _getJson('/ai/config');

  /// 运行日志尾部：{ok, file, lines: [...]}；没有日志时 lines 为空数组。

  /// 写 AI 配置：只传要改的字段；api_key 只写不读。
  Future<Map<String, dynamic>> aiConfigSave(Map<String, dynamic> patch) async {
    try {
      final Response<String> r =
          await _dio.post<String>('/ai/config', data: patch);
      final Object? decoded = jsonDecode(r.data ?? '{}');
      return decoded is Map<String, dynamic>
          ? decoded
          : <String, dynamic>{'ok': false, 'error': 'bad response'};
    } catch (error) {
      return <String, dynamic>{'ok': false, 'error': '$error'};
    }
  }

  /// 手机端账号资产快照（纯截图 OCR 只读，每天任务全完自动更新一次）。
  Future<Map<String, dynamic>> assetsSnapshot({String? config}) async {
    final String cfg = Uri.encodeQueryComponent(config ?? activeConfig);
    try {
      return await _getJson('/assets/snapshot?config=$cfg');
    } catch (_) {
      return <String, dynamic>{};
    }
  }

  /// 在线数据包热更新状态（不回显 token 明文）。
  Future<Map<String, dynamic>> otaStatus() async {
    try {
      return await _getJson('/ota/status');
    } catch (error) {
      return <String, dynamic>{'ok': false, 'error': '$error'};
    }
  }

  Future<Map<String, dynamic>> _postJson(String path,
      {Map<String, dynamic>? body, Duration? receiveTimeout}) async {
    try {
      final Response<String> r = await _dio.post<String>(
        path,
        data: body,
        options: receiveTimeout != null
            ? Options(receiveTimeout: receiveTimeout)
            : null,
      );
      final Object? decoded = jsonDecode(r.data ?? '{}');
      return decoded is Map<String, dynamic>
          ? decoded
          : <String, dynamic>{'ok': false, 'error': 'bad response'};
    } catch (error) {
      return <String, dynamic>{'ok': false, 'error': '$error'};
    }
  }

  Future<Map<String, dynamic>> otaConfigSave(Map<String, dynamic> patch) =>
      _postJson('/ota/config', body: patch);

  Future<Map<String, dynamic>> otaCheck() =>
      _postJson('/ota/check', receiveTimeout: const Duration(seconds: 30));

  Future<Map<String, dynamic>> otaApply() =>
      _postJson('/ota/apply', receiveTimeout: const Duration(seconds: 90));

  Future<Map<String, dynamic>> otaRollback() => _postJson('/ota/rollback');

  /// 私库 Release APK 下载进度与就绪路径。
  Future<Map<String, dynamic>> otaApkStatus() async {
    try {
      return await _getJson('/ota/apk_status');
    } catch (error) {
      return <String, dynamic>{'ok': false, 'error': '$error'};
    }
  }

  /// 启动后台下载私库新版 APK。
  Future<Map<String, dynamic>> otaDownloadApk() =>
      _postJson('/ota/download_apk', receiveTimeout: const Duration(seconds: 30));

  /// 手机护航状态（温度/电量/网络 + 护航策略 + 自动暂停状态）。
  Future<Map<String, dynamic>> guardStatus() async {
    try {
      return await _getJson('/guard/status');
    } catch (error) {
      return <String, dynamic>{'ok': false, 'error': '$error'};
    }
  }

  /// 更新手机护航配置。
  Future<Map<String, dynamic>> guardConfigSave(Map<String, dynamic> patch) =>
      _postJson('/guard/config', body: patch);

  /// OAS 自带中文词表：`{英文键: 中文}`。没有译文的键不会出现在返回里，前端按原键兜底。
  Future<Map<String, String>> i18n(List<String> keys) async {
    if (keys.isEmpty) return <String, String>{};
    try {
      final Response<String> r = await _getWithRetry(
        '/i18n?keys=${Uri.encodeQueryComponent(keys.join(','))}',
      );
      final Object? data = jsonDecode(r.data ?? '{}');
      if (data is Map && data['map'] is Map) {
        final Map<String, String> out = <String, String>{};
        (data['map'] as Map).forEach((Object? k, Object? v) {
          final String value = '$v';
          if (value.isNotEmpty) out['$k'] = value;
        });
        return out;
      }
    } catch (_) {}
    return <String, String>{};
  }

  /// log/ 下的日志文件清单（新→旧），用于「最近记录」。
  Future<List<Map<String, dynamic>>> logFiles() async {
    try {
      final Response<String> r = await _getWithRetry('/log/files');
      final Object? data = jsonDecode(r.data ?? '{}');
      if (data is Map && data['files'] is List) {
        return (data['files'] as List)
            .whereType<Map>()
            .map((Map e) => Map<String, dynamic>.from(e))
            .toList();
      }
    } catch (_) {}
    return <Map<String, dynamic>>[];
  }

  /// 读 OAS 自己写的日志：不带 path 就取 log/ 下最新的那份。
  Future<Map<String, dynamic>> logTail(
      {String path = '', int lines = 200}) async {
    try {
      final String q =
          path.isEmpty ? '' : '&path=${Uri.encodeQueryComponent(path)}';
      final Response<String> r =
          await _getWithRetry('/log/tail?lines=$lines$q');
      final Object? data = jsonDecode(r.data ?? '{}');
      if (data is Map<String, dynamic>) return data;
      if (data is Map) return Map<String, dynamic>.from(data);
    } catch (_) {}
    return <String, dynamic>{'ok': false, 'lines': <String>[]};
  }

  /// 配置（账号）列表：config/*.json 的文件名。
  Future<List<String>> configList() async {
    try {
      final Response<String> r = await _getWithRetry('/config_list');
      final Object? data = jsonDecode(r.data ?? '[]');
      if (data is List) {
        return data.map((Object? e) => '$e').toList();
      }
    } catch (_) {}
    return <String>[];
  }

  /// 当前选中的配置名（启动托管/跑任务时带给后端）。
  final ValueNotifier<String> _activeConfig = ValueNotifier<String>('oas1');
  bool _savingActiveConfig = false;
  String get activeConfig => _activeConfig.value;
  ValueListenable<String> get activeConfigChanges => _activeConfig;

  static final RegExp _invalidConfigCharacter = RegExp(r'[\\/:*?"<>|\x00-\x1f]');

  static bool _validConfigName(String name) =>
      name.isNotEmpty &&
      name.runes.length <= 80 &&
      name != '.' &&
      name != '..' &&
      !_invalidConfigCharacter.hasMatch(name);

  Future<File> _activeConfigFile() async {
    final Directory directory = await getApplicationSupportDirectory();
    return File('${directory.path}/active_config');
  }

  Future<void> loadActiveConfig() async {
    try {
      final File file = await _activeConfigFile();
      if (!await file.exists()) return;
      final String saved = await file.readAsString();
      if (_validConfigName(saved)) _activeConfig.value = saved;
    } catch (_) {}
  }

  Future<bool> selectConfig(String name) async {
    if (_savingActiveConfig || !_validConfigName(name)) return false;
    if (name == activeConfig) return true;
    _savingActiveConfig = true;
    try {
      final File file = await _activeConfigFile();
      final File pending = File('${file.path}.tmp');
      await pending.writeAsString(name, flush: true);
      await pending.rename(file.path);
      _activeConfig.value = name;
      return true;
    } catch (_) {
      return false;
    } finally {
      _savingActiveConfig = false;
    }
  }

  /// 监控页用的「最近一帧」：后台常驻抓帧，App 在前台也能看到刚才的游戏画面。
  Future<Uint8List?> frameLatest() async => (await monitorFrame())?.bytes;

  Future<MobileMonitorFrame?> monitorFrame({CancelToken? cancelToken}) async {
    try {
      final Response<List<int>> response = await _dio.get<List<int>>(
        '/monitor/frame',
        cancelToken: cancelToken,
        options: Options(
            responseType: ResponseType.bytes,
            receiveTimeout: const Duration(seconds: 3)),
      );
      final List<int>? data = response.data;
      if (data == null || data.length < 8) return null;
      final bool jpeg = data[0] == 0xff &&
          data[1] == 0xd8 &&
          data[data.length - 2] == 0xff &&
          data.last == 0xd9;
      final bool png = data[0] == 0x89 &&
          data[1] == 0x50 &&
          data[2] == 0x4e &&
          data[3] == 0x47;
      if (!jpeg && !png) return null;
      int number(String header, int fallback) =>
          int.tryParse(response.headers.value(header) ?? '') ?? fallback;
      final int captured = number('x-oas-captured-at', 0);
      final int width = number('x-oas-width', 0);
      final int height = number('x-oas-height', 0);
      return MobileMonitorFrame(
        bytes: Uint8List.fromList(data),
        frameId: number('x-oas-frame-id', 0),
        displayId: number('x-oas-display-id', -1),
        width: width > 0 ? width : 1280,
        height: height > 0 ? height : 720,
        isJpeg: jpeg,
        capturedAt:
            captured > 0 ? DateTime.fromMillisecondsSinceEpoch(captured) : null,
      );
    } on DioException catch (error) {
      if (CancelToken.isCancel(error)) rethrow;
      return null;
    }
  }

  Future<Map<String, dynamic>> monitorStatus() => _getJson('/monitor/status');
  Future<bool> startBackground() =>
      _post('/background/start', receiveTimeout: const Duration(seconds: 55));
  Future<bool> bringGameToForeground() => _post('/background/foreground');

  Future<bool> vdTap(int x, int y, {int? displayId}) {
    final String disp =
        displayId != null && displayId >= 0 ? '&display=$displayId' : '';
    return _post('/vd/tap?x=$x&y=$y$disp',
        receiveTimeout: const Duration(seconds: 4));
  }

  Future<bool> vdSwipe(int x1, int y1, int x2, int y2,
      {int durationMs = 260, int? displayId}) {
    final String disp =
        displayId != null && displayId >= 0 ? '&display=$displayId' : '';
    return _post(
        '/vd/swipe?x1=$x1&y1=$y1&x2=$x2&y2=$y2&duration=$durationMs$disp',
        receiveTimeout: const Duration(seconds: 4));
  }

  Future<bool> vdKeyEvent(int code, {int? displayId}) {
    final String disp =
        displayId != null && displayId >= 0 ? '&display=$displayId' : '';
    return _post('/vd/keyevent?code=$code$disp',
        receiveTimeout: const Duration(seconds: 4));
  }

  Future<Map<String, bool>> taskEnabled(String config) async {
    final Map<String, dynamic> data =
        await _getJson('/config_tasks?config=${Uri.encodeComponent(config)}');
    if (data['ok'] != true || data['tasks'] is! List) {
      throw StateError('${data['error'] ?? '无法读取任务开关'}');
    }
    return <String, bool>{
      for (final Object? row in data['tasks'] as List)
        if (row is Map && row['name'] is String)
          row['name'] as String: row['enabled'] == true
    };
  }

  Future<Map<String, dynamic>> state() => _getJson('/state');

  Future<Map<String, dynamic>> schedulerStatus() =>
      _getJson('/scheduler/status');

  /// 首页「任务排序」真实执行队列（运行中 → 待执行 → 等待中）；老后端自动回退 /config_tasks 排序。
  Future<Map<String, dynamic>> schedulerQueue({String? config}) async {
    final String cfg = config ?? activeConfig;
    try {
      final Map<String, dynamic> data = await _getJson(
        '/scheduler/queue?config=${Uri.encodeComponent(cfg)}',
      );
      if (data['ok'] == true && data['queue'] is List) return data;
    } catch (_) {}
    // Fallback for older backends or minimal test adapters
    try {
      final Map<String, dynamic> raw = await _getJson(
        '/config_tasks?config=${Uri.encodeComponent(cfg)}',
      );
      if (raw['ok'] == true && raw['tasks'] is List) {
        final DateTime now = DateTime.now();
        final List<Map<String, dynamic>> pending = <Map<String, dynamic>>[];
        final List<Map<String, dynamic>> waiting = <Map<String, dynamic>>[];
        for (final Object? row in raw['tasks'] as List) {
          if (row is! Map || row['enabled'] != true) continue;
          final String name = '${row['name'] ?? ''}';
          if (name.isEmpty) continue;
          final String nextRun = '${row['next_run'] ?? ''}';
          final int prio = (row['priority'] is num)
              ? (row['priority'] as num).toInt()
              : 5;
          final DateTime? parsed = DateTime.tryParse(nextRun);
          final bool isPending = parsed == null || !parsed.isAfter(now);
          final Map<String, dynamic> item = <String, dynamic>{
            'name': name,
            'title': taskDisplayName(name),
            'priority': prio,
            'next_run': nextRun,
            'status': isPending ? 'pending' : 'waiting',
            'enabled': true,
          };
          (isPending ? pending : waiting).add(item);
        }
        int cmpNext(Map<String, dynamic> a, Map<String, dynamic> b) =>
            '${a['next_run']}'.compareTo('${b['next_run']}');
        pending.sort((Map<String, dynamic> a, Map<String, dynamic> b) {
          final int p = (a['priority'] as int).compareTo(b['priority'] as int);
          return p != 0 ? p : cmpNext(a, b);
        });
        waiting.sort(cmpNext);
        return <String, dynamic>{
          'ok': true,
          'config': cfg,
          'running': '',
          'pending_count': pending.length,
          'waiting_count': waiting.length,
          'queue': <Map<String, dynamic>>[...pending, ...waiting],
        };
      }
    } catch (_) {}
    return <String, dynamic>{
      'ok': false,
      'queue': <Map<String, dynamic>>[],
      'pending_count': 0,
      'waiting_count': 0,
    };
  }

  /// 一键把任务置顶到下一位优先执行。
  Future<Map<String, dynamic>> pinTask(String task, {String? config}) {
    final String cfg = config ?? activeConfig;
    return _postJson(
      '/scheduler/pin?config=${Uri.encodeComponent(cfg)}&task=${Uri.encodeComponent(task)}',
    );
  }

  Future<Map<String, dynamic>> taskStatus() => _getJson('/task/status');

  Future<Map<String, dynamic>> startSchedulerDetail({bool force = false}) {
    final String forceQ = force ? '&force=1' : '';
    return _postJson(
      '/scheduler/start?config=${Uri.encodeComponent(activeConfig)}$forceQ',
    );
  }

  Future<bool> startScheduler({bool force = false}) async {
    final Map<String, dynamic> res = await startSchedulerDetail(force: force);
    return res['ok'] == true;
  }

  Future<bool> stopScheduler() async {
    final bool scheduler = await _post('/scheduler/stop');
    final bool task = await _post('/task/stop');
    return scheduler && task;
  }

  Future<bool> pause() => _post('/pause');

  Future<bool> resume() => _post('/resume');

  Future<bool> _post(String path, {Duration? receiveTimeout}) async {
    final Response<String> r = await _dio.post<String>(path,
        options: Options(receiveTimeout: receiveTimeout));
    final String body = (r.data ?? '').trim();
    if (body == 'true') return true;
    try {
      final Object? value = jsonDecode(body);
      return value is Map && value['ok'] == true;
    } catch (_) {
      return false;
    }
  }

  /// 任务总表：`{"Tasks": ["Orochi", ...], "titles": {"IbukiArena": "狭间幻境"}}`，来自后端 tasks 目录，不写死。
  Future<List<OasTask>> tasks() async {
    final Map<String, dynamic> j = await _getJson('/script_menu');
    final Object? rawTitles = j['titles'];
    if (rawTitles is Map) {
      final Map<String, String> parsed = <String, String>{};
      rawTitles.forEach((Object? k, Object? v) {
        final String ks = '$k';
        final String vs = '$v';
        if (ks.isNotEmpty && vs.isNotEmpty) parsed[ks] = vs;
      });
      updateRuntimeTaskTitles(parsed);
    }
    final List<OasTask> out = <OasTask>[];
    for (final Object? v in j.values) {
      if (v is! List) continue;
      for (final Object? name in v) {
        if (name is! String) continue;
        if (kNonTaskDirs.contains(name)) continue;
        out.add(OasTask(dir: name, title: taskDisplayName(name)));
      }
    }
    return out;
  }

  /// 单个任务的参数表：`{分组: [字段...]}`，字段来自 OAS 自己的 Config 模型。
  Future<List<OasArgGroup>> taskArgs(String script, String task) async {
    final Map<String, dynamic> j = await _getJson('/$script/$task/args');
    if (j['ok'] == false) {
      throw StateError('${j['error'] ?? '任务参数读取失败'}');
    }
    final List<OasArgGroup> groups = <OasArgGroup>[];
    j.forEach((String key, Object? value) {
      if (value is! List) return;
      final List<OasArgField> fields = <OasArgField>[];
      for (final Object? raw in value) {
        if (raw is! Map) continue;
        final Map<dynamic, dynamic> m = raw;
        fields.add(
          OasArgField(
            name: '${m['name'] ?? ''}',
            title: '${m['title'] ?? m['name'] ?? ''}',
            description: '${m['description'] ?? ''}',
            value: m['value'],
            defaultValue: m['default'],
            type: '${m['type'] ?? 'string'}',
            optionLabels: (m['option_labels'] is List)
                ? (m['option_labels'] as List).map((Object? e) => '$e').toList()
                : const <String>[],
            options: (m['options'] is List)
                ? <String, String>{
                    for (final Object? item in (m['options'] as List))
                      if (item is Map && item['value'] != null)
                        '${item['value']}': '${item['label'] ?? item['value']}',
                  }
                : OasEnums.pairs(
                    (m['option_labels'] is List)
                        ? (m['option_labels'] as List)
                            .map((Object? e) => '$e')
                            .toList()
                        : const <String>[],
                    '${m['value'] ?? ''}',
                  ),
          ),
        );
      }
      groups.add(
          OasArgGroup(key: key, title: oasGroupTitle(key), fields: fields));
    });
    return groups;
  }

  /// 写单个参数：PUT /<script>/<task>/<group>/<arg>/value?value=..&types=..
  Future<bool> setValue({
    required String script,
    required String task,
    required String group,
    required String argument,
    required String value,
    required String type,
  }) async {
    final Response<String> r = await _dio.put<String>(
      '/$script/$task/$group/$argument/value',
      queryParameters: <String, dynamic>{'value': value, 'types': type},
    );
    return (r.data ?? '').trim().toLowerCase().startsWith('true');
  }
}

class OasTask {
  const OasTask({required this.dir, required this.title});

  final String dir;
  final String title;
}

/// AI 自愈的一次记录（后端读 log/ai_recovery/incidents/<ts>_<id>/{anomaly,result}.json）。
class OasAiIncident {
  const OasAiIncident({
    required this.id,
    required this.at,
    required this.task,
    required this.type,
    required this.outcome,
    required this.message,
    required this.cause,
    required this.detail,
    required this.aiCalled,
    required this.verified,
    required this.tokens,
    required this.costCny,
    required this.actions,
    required this.actionsOk,
  });

  final String id;
  final String at;
  final String task;
  final String type;
  final String outcome;
  final String message;
  final String cause;
  final String detail;
  final bool aiCalled;
  final bool verified;
  final int tokens;
  final double costCny;
  final int actions;
  final int actionsOk;

  /// 后端字段可能缺失或类型漂移（历史文件是外部写的），这里全部兜底。
  factory OasAiIncident.fromJson(Map<String, dynamic> j) {
    int asInt(Object? v) {
      if (v is num) return v.toInt();
      return int.tryParse('${v ?? ''}') ?? 0;
    }

    double asDouble(Object? v) {
      if (v is num) return v.toDouble();
      return double.tryParse('${v ?? ''}') ?? 0.0;
    }

    String asStr(Object? v) => v == null ? '' : '$v';

    return OasAiIncident(
      id: asStr(j['id']),
      at: asStr(j['at']),
      task: asStr(j['task']),
      type: asStr(j['type']),
      outcome: asStr(j['outcome']).isEmpty ? 'unknown' : asStr(j['outcome']),
      message: asStr(j['message']),
      cause: asStr(j['cause']),
      detail: asStr(j['detail']),
      aiCalled: j['ai_called'] == true,
      verified: j['verified'] == true,
      tokens: asInt(j['tokens']),
      costCny: asDouble(j['cost_cny']),
      actions: asInt(j['actions']),
      actionsOk: asInt(j['actions_ok']),
    );
  }

  String get outcomeLabel => switch (outcome) {
        'recovered' => '已自愈',
        'needs_human' => '需人工',
        'stopped' => '已停下',
        _ => '未记录结果',
      };

  String get headline {
    final String who = task.isEmpty ? type : task;
    if (cause.isNotEmpty) return '$who · $cause';
    if (message.isNotEmpty) return '$who · $message';
    return who.isEmpty ? '异常' : who;
  }
}

/// `/ai/recovery` 的整页结果：summary 统计**全量**记录，incidents 只带前 N 条。
class OasAiRecovery {
  const OasAiRecovery({
    required this.total,
    required this.recovered,
    required this.needsHuman,
    required this.stopped,
    required this.aiCalls,
    required this.costCny,
    required this.incidents,
  });

  final int total;
  final int recovered;
  final int needsHuman;
  final int stopped;
  final int aiCalls;
  final double costCny;
  final List<OasAiIncident> incidents;

  static const OasAiRecovery empty = OasAiRecovery(
    total: 0,
    recovered: 0,
    needsHuman: 0,
    stopped: 0,
    aiCalls: 0,
    costCny: 0,
    incidents: <OasAiIncident>[],
  );

  factory OasAiRecovery.fromJson(Map<String, dynamic> j) {
    int asInt(Object? v) {
      if (v is num) return v.toInt();
      return int.tryParse('${v ?? ''}') ?? 0;
    }

    final Map<String, dynamic> summary =
        j['summary'] is Map ? Map<String, dynamic>.from(j['summary'] as Map) : <String, dynamic>{};
    final List<OasAiIncident> list = <OasAiIncident>[
      if (j['entries'] is List)
        for (final Object? item in j['entries'] as List)
          if (item is Map) OasAiIncident.fromJson(Map<String, dynamic>.from(item)),
    ];
    return OasAiRecovery(
      total: asInt(summary['total']).clamp(0, 1 << 30),
      recovered: asInt(summary['recovered']),
      needsHuman: asInt(summary['needs_human']),
      stopped: asInt(summary['stopped']),
      aiCalls: asInt(summary['ai_calls']),
      costCny: (summary['cost_cny'] is num)
          ? (summary['cost_cny'] as num).toDouble()
          : double.tryParse('${summary['cost_cny'] ?? ''}') ?? 0.0,
      incidents: list,
    );
  }

  bool get isEmpty => total == 0 && incidents.isEmpty;
}


class OasArgGroup {
  OasArgGroup({required this.key, required this.title, required this.fields});

  final String key;
  final String title;
  final List<OasArgField> fields;

  /// 手机端收进「高级」的组：只剩调度器。
  static const Set<String> kAdvancedArgGroups = <String>{'scheduler'};

  /// 电脑专属、手机端**完全不显示**的组（用户要求去掉）：
  /// device 模拟器 / desktop 桌面版 / auto_lineup 自动阵容 / roster_scan 式神池扫描 / workbench 工作台。
  static const Set<String> kHiddenArgGroups = <String>{
    'device',
    'desktop',
    'auto_lineup',
    'roster_scan',
    'workbench',
  };

  /// 这些组默认折叠进「高级」。
  bool get isAdvanced => kAdvancedArgGroups.contains(key);

  /// 电脑专属组：手机参数页直接不渲染。
  bool get isHidden => kHiddenArgGroups.contains(key);
}

class OasArgField {
  const OasArgField({
    required this.name,
    required this.title,
    required this.description,
    required this.value,
    required this.defaultValue,
    required this.type,
    this.optionLabels = const <String>[],
    this.options = const <String, String>{},
  });

  final String name;
  final String title;
  final String description;
  final Object? value;
  final Object? defaultValue;
  final String type;

  /// 枚举字段的候选值中文标签（后端从 OAS 的 `<name>_help` 文案里解析出来的）。
  /// 注意：这是**标签**，不是能直接写回配置的值。
  final List<String> optionLabels;

  bool get isEnum => type == 'enum';

  /// 「存值 → 中文标签」；非空才允许做真下拉（否则只能手填，别猜映射）。
  final Map<String, String> options;

  bool get hasOptions => options.isNotEmpty || optionLabels.isNotEmpty;

  bool get hasPicker => options.isNotEmpty;

  bool get isBool => type == 'boolean';

  String get valueText => value?.toString() ?? '';

  /// 给用户看的当前值文本：优先把枚举内部存值（如 costume_main）映射为中文标签（如 初语谧景）。
  String get displayValueText {
    final String raw = valueText;
    if (raw.isEmpty) return '';
    final String? mapped = options[raw];
    if (mapped != null && mapped.isNotEmpty) return mapped;
    return raw;
  }

  /// 后端有一部分 description 是 UTF-8 被当 latin-1 的乱码（如 åå¤ç±»å），
  /// 这种直接丢弃，别把乱码显示给用户。
  String get cleanDescription {
    if (description.isEmpty) return '';
    if (description.endsWith('_help')) return '';
    if (RegExp(r'[\u00C0-\u00FF]').hasMatch(description)) return '';
    return description;
  }

  /// 字段名汉化优先级：
  /// 1) 后端已返回干净中文 title（来自电脑端 OAS 双词库，且支持热更新）→ 直接采用；
  /// 2) 命中本地常用词表 → 用中文；
  /// 3) 后端 title 是英文、但 description 是干净中文 → 用中文说明当主标题（英文降为副标题）；
  /// 4) 都不满足 → 保留英文，不编造含义。
  String get displayTitle {
    if (_isChinese(title)) return title;
    final String? zh = _kFieldTitles[name];
    if (zh != null) return zh;
    if (_isAscii(title) && _isChinese(cleanDescription)) {
      final String d = cleanDescription.replaceAll(RegExp(r'[。.]$'), '');
      return d.length > 18 ? '${d.substring(0, 18)}…' : d;
    }
    return title;
  }

  /// 副标题：把被顶掉的英文原名留一行小字，方便对照桌面版。
  String? get secondaryTitle {
    if (_isChinese(title)) return null;
    if (_kFieldTitles.containsKey(name)) {
      return _isAscii(title) ? title : null;
    }
    if (_isAscii(title) && _isChinese(cleanDescription)) return title;
    return null;
  }
}

bool _isAscii(String s) =>
    s.isNotEmpty && !RegExp(r'[\u4E00-\u9FFF]').hasMatch(s);

bool _isChinese(String s) => RegExp(r'[\u4E00-\u9FFF]').hasMatch(s);

/// 常用字段的中文名（不够时原样显示英文，但不编造含义）。
const Map<String, String> _kFieldTitles = <String, String>{
  // —— 战斗/预设/绿标（参数页最常见的英文键，按阴阳师术语汉化）——
  'custom_run_time_friday': '周五运行时间',
  'custom_run_time_saturday': '周六运行时间',
  'custom_run_time_sunday': '周日运行时间',
  'boss_combat_time': '首领战斗时间',
  'general_combat_time': '副将战斗时间',
  'elite_combat_time': '精英战斗时间',
  'lock_team_enable': '锁定阵容',
  'preset_enable': '启用切换预设队伍',
  'preset_group': '第 X 个预设组',
  'preset_team': '第 X 个预设队伍',
  'green_enable': '启用我方绿标',
  'green_mark': '绿标位置',
  'green_mark_type': '绿标类型',
  'green_mark_name': '绿标名称',
  'random_click_swipt_enable': '战斗时随机点击或滑动',
  'switch_group_team': '御魂装配分组设置',
  'enable_switch_by_name': '通过名称切换御魂',
  'group_name': '御魂分组名',
  'team_name': '队伍名',
  'enable': '启用',
  'next_run': '下次运行',
  'priority': '优先级',
  'success_interval': '成功间隔',
  'failure_interval': '失败间隔',
  'server_update': '刷新时间',
  'delay_date': '顺延天数',
  'float_time': '浮动时间',
  'limit_count': '次数上限',
  'one_summon': '单抽',
  'summon_type': '召唤类型',
  'draw_mystery_pattern': '绘制神秘图案',
  'guild_wish': '寮祈愿',
  'friend_love': '好友点赞',
  'luck_msg': '吉言',
  'store_sign': '商店签到',
  'buy_sushi_count': '采购寿司数',
  'user_status': '组队身份',
  'switch_soul': '御魂切换',
  'layer': '层数',
  'soul': '御魂',
  'invite': '组队邀请',
};

/// 分组名汉化：先查固定表，再用「词元翻译」兜底，
/// 这样 abyss_shadows_combat_time 也能变成「狭间暗域 战斗 时间」而不是露英文 key。
String oasGroupTitle(String key) {
  const Map<String, String> exact = <String, String>{
    'scheduler': '调度（高级）',
    'costume_config': '皮肤配置',
    'team_flow': '团队流程',
    'emergency': '紧急停止',
    'optimization': '脚本优化',
    'error': '异常处理',
    'script': '脚本配置',
    'global_game': '全局配置',
    'invite_config': '组队设置',
    'general_battle_config': '战斗通用',
    'switch_soul': '独立御魂切换（未启用队伍预设时使用）',
    'switch_soul_config': '独立御魂切换（未启用队伍预设时使用）',
    'trifles_config': '日常设置',
    'stagger': '错峰执行',
    'anti_ban': '防风控作息',
    'auto_lineup': '自动阵容',
    'roster_scan': '式神池自动扫描',
    'desktop': '桌面版设置',
    'checkin_config': '签到配置',
    'daily_training': '配置',
    'chess_config': '百鬼棋局配置',
    'lbs_config': 'LBS鬼王配置',
    'other_world_twilight_config': '彼世逢魔配置',
    'kirin_battle_config': '麒麟战斗配置',
    'netherworld_battle_config': '阴界之门战斗配置',
    'demon_battle_config': '逢魔战斗内切换阵容',
    'best_demon_battle_config': '极逢魔战斗内切换阵容',
    'guild_activity_monitor_combat_time': '监控时间设置',
    'frog_challenge': '青蛙瓷器挑战赛',
  };
  final String? zh = exact[key];
  if (zh != null) return zh;

  const Map<String, String> tokens = <String, String>{
    'abyss': '狭间',
    'shadows': '暗域',
    'orochi': '八岐大蛇',
    'true': '真',
    'evo': '觉醒',
    'zone': '区域',
    'hunt': '狩猎',
    'kirin': '麒麟',
    'netherworld': '冥界',
    'combat': '战斗',
    'time': '时间',
    'config': '设置',
    'invite': '组队',
    'general': '通用',
    'battle': '战斗',
    'soul': '御魂',
    'switch': '切换',
    'duel': '斗技',
    'kekkai': '结界',
    'exploration': '探索',
    'trifles': '日常',
    'daily': '日常',
    'souls': '御魂',
    'tidy': '整理',
    'activity': '活动',
    'shikigami': '式神',
    'guild': '阴阳寮',
    'banquet': '宴会',
    'memory': '追忆',
    'scrolls': '绘卷',
    'pets': '小动物',
    'quiz': '答题',
  };
  final List<String> parts =
      key.split('_').where((String s) => s.isNotEmpty).toList();
  final List<String> zhParts = <String>[];
  for (final String p in parts) {
    final String? t = tokens[p];
    if (t != null) {
      zhParts.add(t);
    } else if (p.endsWith('config')) {
      zhParts.add('设置');
    } else {
      return key; // 有词不认识就老实露英文，别编
    }
  }
  return zhParts.join(' ');
}

class MobileMonitorFrame {
  const MobileMonitorFrame(
      {required this.bytes,
      required this.frameId,
      required this.displayId,
      required this.isJpeg,
      this.width = 1280,
      this.height = 720,
      this.capturedAt});
  final Uint8List bytes;
  final int frameId;
  final int displayId;
  final int width;
  final int height;
  final bool isJpeg;
  final DateTime? capturedAt;
  bool get stale =>
      capturedAt != null &&
      DateTime.now().difference(capturedAt!) > const Duration(seconds: 5);
}
