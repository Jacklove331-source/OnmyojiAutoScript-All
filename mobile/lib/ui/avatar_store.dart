import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart'
    show ValueNotifier, visibleForTesting;
import 'package:path_provider/path_provider.dart';

/// 头像：内置几款游戏内真实素材（式神头像/立绘），不申请相册权限、不弹任何系统框。
///
/// [char] 只是图片加载失败时的字形兜底，[imageAsset] 才是真正画出来的头像。
class OasAvatar {
  const OasAvatar(this.id, this.char, this.label, this.imageAsset);

  final String id;
  final String char;
  final String label;
  final String imageAsset;

  static const List<OasAvatar> all = <OasAvatar>[
    OasAvatar('seimei', '晴', '晴明', 'assets/onmyoji/dock_me.png'),
    OasAvatar('kagura', '乐', '神乐', 'assets/onmyoji/cut_kagura.png'),
    OasAvatar('yaobikuni', '八', '八百比丘尼', 'assets/onmyoji/cut_yaobiquni.png'),
    OasAvatar('shiranui', '离', '不知火', 'assets/onmyoji/cat_shiranui.png'),
    OasAvatar('ciqiu', '茨', '茨球', 'assets/onmyoji/av_ciqiu.png'),
    OasAvatar('orochi', '蛇', '八岐大蛇', 'assets/onmyoji/cat_orochi.png'),
    OasAvatar('kaguya', '辉', '辉夜姬', 'assets/onmyoji/cat_kaguya.png'),
  ];

  static OasAvatar byId(String id) {
    for (final OasAvatar item in all) {
      if (item.id == id) return item;
    }
    return all.first;
  }
}

/// 一次选择的结果，调用方据此决定要不要提示「失败」。
enum OasAvatarSaveResult {
  /// 已落盘，内存与磁盘一致。
  saved,

  /// 落盘失败且本次选择仍是最新的：头像已回滚到最后一次成功落盘的那一款。
  failedRolledBack,

  /// 期间用户已经选了更新的头像（不论本次写盘成败），由那次选择负责落盘与提示。
  superseded,
}

/// 头像状态的唯一来源：庭院账户卡与「我的」页的头像、称呼都读 [state]，
/// 任意一处改头像，另一处立刻跟着变；选择结果落盘到 app 私有 avatar.json。
class OasAvatarStore {
  OasAvatarStore._();

  static final OasAvatarStore i = OasAvatarStore._();

  final ValueNotifier<OasAvatar> state = ValueNotifier<OasAvatar>(
    OasAvatar.all.first,
  );

  /// 自定义称呼（空串 = 没设过，用头像自带的「晴明大人」这种默认称呼）。
  final ValueNotifier<String> nickname = ValueNotifier<String>('');

  /// 「我的」页/账户卡上显示的名字：设过昵称就用昵称，否则「<头像名>大人」。
  static String displayName(OasAvatar avatar, String nick) {
    final String trimmed = nick.trim();
    return trimmed.isEmpty ? '${avatar.label}大人' : trimmed;
  }

  /// 昵称清洗：去首尾空白、折叠内部空白、最多 12 个字。
  static String sanitizeNickname(String raw) {
    final String cleaned = raw.replaceAll(RegExp(r'\s+'), ' ').trim();
    return cleaned.length <= 12 ? cleaned : cleaned.substring(0, 12);
  }

  /// 写盘串行链：同一时刻只允许一个 avatar.json 写入，
  /// 连点两次也不会互相截断。
  Future<void> _writeChain = Future<void>.value();

  int _selectSeq = 0;

  /// 进程内已知的「磁盘上确实存在」的头像（null = 磁盘上没有文件）。
  /// 读盘不可用时用它兜底，别把用户上一次的选择直接丢掉。
  OasAvatar? _persisted;

  OasAvatar get current => state.value;

  Future<File> _file() async {
    final Directory dir = await getApplicationSupportDirectory();
    return File('${dir.path}/avatar.json');
  }

  /// 读盘的唯一入口。返回 `known=false` 表示「读不到/解析失败」（未知），
  /// `known=true` 且 `avatar=null` 表示「磁盘上本来就没有文件」。
  Future<({bool known, OasAvatar? avatar, String nickname})> _readFromDisk() async {
    try {
      final File file = await _file();
      if (!await file.exists()) {
        return (known: true, avatar: null, nickname: '');
      }
      final Object? decoded = jsonDecode(await file.readAsString());
      if (decoded is Map) {
        final String nick =
            decoded['nickname'] is String ? decoded['nickname'] as String : '';
        final OasAvatar? avatar =
            decoded['id'] is String ? OasAvatar.byId(decoded['id'] as String) : null;
        return (known: true, avatar: avatar, nickname: nick);
      }
      return (known: true, avatar: null, nickname: '');
    } catch (_) {
      return (known: false, avatar: null, nickname: '');
    }
  }

  /// 启动时读一次；读不到就用第一款（晴明）。
  ///
  /// 内部已兜底所有异常（含 getApplicationSupportDirectory 失败、json 损坏），
  /// 不会向上抛，调用方不必担心挡住启动。
  Future<void> load() async {
    final ({bool known, OasAvatar? avatar, String nickname}) disk =
        await _readFromDisk();
    if (disk.known) _persisted = disk.avatar;
    state.value = disk.avatar ?? _persisted ?? OasAvatar.all.first;
    nickname.value = disk.known ? disk.nickname : nickname.value;
  }

  /// 选中即生效：先改内存状态（两处头像同一帧内同步），再串行落盘。
  /// 落盘失败且本次仍是最新选择时，按磁盘真值回滚（读不到就用内存里已知的真值）。
  Future<OasAvatarSaveResult> select(OasAvatar avatar) async {
    final int seq = ++_selectSeq;
    state.value = avatar;

    final bool saved = await _persist(avatar, nickname.value);
    if (saved) {
      _persisted = avatar;
      // 落盘期间状态可能被更新的一次选择（或 load()）改掉，那就别抢界面
      if (seq != _selectSeq || state.value.id != avatar.id) {
        return OasAvatarSaveResult.superseded;
      }
      return OasAvatarSaveResult.saved;
    }

    // 写盘失败：以磁盘真值回滚。读盘本身要 await，期间可能又有新的选择，
    // 所以读完要重新校验一次序号，别把更新的选择盖掉。
    final ({bool known, OasAvatar? avatar, String nickname}) disk =
        await _readFromDisk();
    if (seq != _selectSeq || state.value.id != avatar.id) {
      return OasAvatarSaveResult.superseded;
    }
    if (disk.known) _persisted = disk.avatar;
    state.value = disk.avatar ?? _persisted ?? OasAvatar.all.first;
    return OasAvatarSaveResult.failedRolledBack;
  }

  /// 改称呼：先即时生效，再串行落盘；失败就回到旧称呼。
  Future<bool> setNickname(String raw) async {
    final String next = sanitizeNickname(raw);
    final String previous = nickname.value;
    if (next == previous) return true;
    nickname.value = next;
    final bool ok = await _persist(state.value, next);
    if (!ok) nickname.value = previous;
    return ok;
  }

  /// 仅供测试：等旧的写盘链结束，再把单例恢复到「刚启动、还没 load()」的状态。
  @visibleForTesting
  Future<void> resetForTest() async {
    try {
      await _writeChain;
    } catch (_) {}
    _writeChain = Future<void>.value();
    _selectSeq = 0;
    _persisted = null;
    state.value = OasAvatar.all.first;
    nickname.value = '';
  }

  Future<bool> _persist(OasAvatar avatar, String nick) {
    final Completer<bool> done = Completer<bool>();
    _writeChain = _writeChain.then((_) async {
      bool ok = false;
      try {
        final String payload = jsonEncode(<String, String>{
          'id': avatar.id,
          'nickname': nick,
        });
        final File file = await _file();
        final File tmp = File('${file.path}.tmp');
        await tmp.writeAsString(payload, flush: true);
        try {
          // 临时文件 + rename：任何时刻都不会留下半截 JSON
          await tmp.rename(file.path);
        } on FileSystemException {
          await file.writeAsString(payload, flush: true);
          if (await tmp.exists()) await tmp.delete();
        }
        ok = true;
      } catch (_) {
        ok = false;
      } finally {
        // 无论成败都要收尾，否则链上的调用方会永久等待
        if (!done.isCompleted) done.complete(ok);
      }
    }).catchError((Object _) {});
    return done.future;
  }
}
