# This Python file uses the following encoding: utf-8

"""百鬼棋局「连续多局」实机测试台（不改用户配置）。

为什么需要它：`run_count=3` 与 `limit_time=00:30:00` 是互相矛盾的——一局
百鬼棋局要跑 25~40 分钟，30 分钟预算会在第 1 局结束后就跳出循环，永远测不到
连续对局。本工具在执行期把 `run_count` / `limit_time` 覆盖成测试值（只改内存），
任务结束后在 `finally` 里恢复原值并保存，避免污染 `config/oas3.json`。

用法（必须脱离 shell，否则会话切换会杀掉子进程）：

```powershell
cmd /c start "" /b "<repo>\\toolkit\\python.exe" dev_tools\\chess_series_test.py oas3 3 04:00:00
```

参数：
    1. 配置名（默认 oas3）
    2. 局数（默认 3）
    3. 时间预算 HH:MM:SS（默认 04:00:00）

日志：与本进程 stdout 一致，脱离 shell 时请自行重定向到
`D:\\3D\\YYS2\\_recovery_*\\chess_series_<时间戳>.log`。
"""

import os
import sys
import time
from datetime import time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _redirect_log():
    """把 stdout/stderr 直接写进 UTF-8 日志文件。

    为什么不靠 shell 重定向：PowerShell/cmd 的 `>` 会产出 **UTF-16LE** 日志
    （带 BOM），后续任何 grep/解析都要先转码，很容易被当成「日志为空」。
    这里由 Python 自己写 UTF-8，日志工具可以直接读。
    """
    argv = list(sys.argv)
    log_path = None
    if '--log' in argv:
        index = argv.index('--log')
        if index + 1 < len(argv):
            log_path = argv[index + 1]
            del argv[index:index + 2]
    elif os.environ.get('CHESS_SERIES_LOG'):
        log_path = os.environ['CHESS_SERIES_LOG']
    sys.argv = argv
    if not log_path:
        return None
    path = Path(log_path)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a', encoding='utf-8', buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    print(f'[ChessSeries] log -> {path}', flush=True)
    return stream


STDOUT_STREAM = _redirect_log()

from script import Script  # noqa: E402


def parse_time(text):
    parts = [int(part) for part in str(text).split(':')]
    while len(parts) < 3:
        parts.append(0)
    hour, minute, second = parts[:3]
    return dtime(hour=hour, minute=minute, second=second)


def main() -> int:
    config_name = sys.argv[1] if len(sys.argv) > 1 else 'oas3'
    run_count = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    limit_time = parse_time(sys.argv[3]) if len(sys.argv) > 3 else parse_time('04:00:00')

    script = Script(config_name)
    chess_config = script.config.chess.chess_config
    original_count = chess_config.run_count
    original_limit = chess_config.limit_time
    print(
        f'[ChessSeries] config={config_name} override run_count={run_count} '
        f'limit_time={limit_time} (original run_count={original_count} '
        f'limit_time={original_limit})',
        flush=True,
    )
    chess_config.run_count = int(run_count)
    chess_config.limit_time = limit_time
    started = time.monotonic()
    try:
        result = script.run('Chess')
    finally:
        chess_config.run_count = original_count
        chess_config.limit_time = original_limit
        try:
            script.config.save()
            print(
                f'[ChessSeries] restored run_count={original_count} '
                f'limit_time={original_limit}',
                flush=True,
            )
        except Exception as error:  # noqa: BLE001 - 恢复失败只影响下次测试
            print(f'[ChessSeries] restore save failed: {error!r}', flush=True)
    elapsed = time.monotonic() - started
    print(
        f'[ChessSeries] finished result={result!r} elapsed={elapsed / 60:.1f}min',
        flush=True,
    )
    print(f'RESULT Chess -> {result!r}')
    return 0 if result else 1


if __name__ == '__main__':
    sys.exit(main())
