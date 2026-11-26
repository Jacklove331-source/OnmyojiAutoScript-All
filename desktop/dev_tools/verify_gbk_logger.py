# This Python file uses the following encoding: utf-8
"""离线复现：GBK 控制台下 logger 写 emoji 不再抛 UnicodeEncodeError。

背景：log/error/1795139664374/log.txt:117 里 UnicodeEncodeError('gbk', ...\\U0001f4cb)
从 script.py:885 task.run() 抛出，直接把 KekkaiUtilize 打断。

用法（必须用 GBK 作为标准输出编码，模拟中文 Windows 控制台）：

    $env:PYTHONIOENCODING='gbk'
    .\\toolkit\\python.exe dev_tools\\verify_gbk_logger.py

判定：
1. 对照组——直接往 sys.stdout 写 emoji，必须抛 UnicodeEncodeError（证明这条流确实是窄编码）；
2. 实验组——走 module.logger 的 logger.info / logger.warning / logger.print / logger.hr，
   任何一条抛 UnicodeEncodeError 即 FAIL。

本脚本只写 stdout，不碰 device / adb / 游戏实例。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EMOJI = '\U0001f4cb'      # 📋 (日志里崩过的字符)
EMOJI2 = '\U0001f3c6'     # 🏆
WARN = '\u26a0\ufe0f'     # ⚠️ 带变体选择符
CHECK = '\u2705'          # ✅


def out(msg: str) -> None:
    """结果行一律写成 ASCII，避免自身被窄编码影响判读。"""
    sys.stdout.write(msg + '\n')
    sys.stdout.flush()


def main() -> int:
    failures = []

    encoding = (getattr(sys.stdout, 'encoding', '') or '').lower()
    out(f'[info] PYTHONIOENCODING={__import__("os").environ.get("PYTHONIOENCODING")!r} '
        f'sys.stdout.encoding={encoding!r}')
    if 'gbk' not in encoding.replace('-', ''):
        failures.append(f'sys.stdout.encoding is {encoding!r}, expected gbk; '
                        f'run with PYTHONIOENCODING=gbk')
        out('[FAIL] 控制台不是 GBK，本次复现无效')

    # 1) 对照组：裸流严格编码必须抛
    try:
        sys.stdout.write(EMOJI)
        sys.stdout.flush()
        out('[FAIL] control: raw sys.stdout accepted emoji without error (test is meaningless)')
        failures.append('control group did not raise; stdout is not strict gbk')
    except UnicodeEncodeError as error:
        out(f'[ok] control: raw sys.stdout raises {type(error).__name__} '
            f'(encoding={error.encoding}) -> 窄编码确认')
        try:
            sys.stdout.write('\n')
            sys.stdout.flush()
        except UnicodeEncodeError:
            pass

    # 2) 实验组：logger 全路径
    import module.logger as logmod
    logger = logmod.logger

    cases = [
        ('logger.info(emoji)', lambda: logger.info(f'{EMOJI} 悬赏封印 {CHECK}')),
        ('logger.warning(emoji)', lambda: logger.warning(f'{WARN} 式神不足 5 位 {EMOJI2}')),
        ('logger.print(emoji)', lambda: logmod.print(f'{EMOJI} logger.print 路径')),
        ('logger.hr(emoji)', lambda: logmod.hr(f'{EMOJI} logger.hr 路径', level=1)),
        ('logger.error(emoji)', lambda: logger.error(f'\u274c 失败 {EMOJI}')),
    ]
    for name, fn in cases:
        try:
            fn()
            out(f'[PASS] {name}')
        except UnicodeEncodeError as error:
            out(f'[FAIL] {name} -> UnicodeEncodeError({error.encoding!r}): {error.object!r}')
            failures.append(f'{name} raised UnicodeEncodeError')
        except Exception as error:  # noqa: BLE001 —— 其它异常同样算失败，但要看清类型
            out(f'[FAIL] {name} -> {type(error).__name__}: {error}')
            failures.append(f'{name} raised {type(error).__name__}')

    # 3) 文件日志仍保留原文（UTF-8），确认没被替换符污染
    #    logger.set_file_logger 把目标路径记在 logger.log_file（log/<date>_<name>.txt）。
    paths = []
    log_file = getattr(logger, 'log_file', None)
    if log_file:
        paths.append(str(ROOT / log_file) if not Path(log_file).is_absolute() else log_file)
    for handler in logger.handlers:
        name = getattr(handler, 'baseFilename', None)
        if name:
            paths.append(name)
    if not paths:
        out('[warn] logger.log_file unavailable, skip file-log check')
    for path in sorted(set(paths)):
        try:
            text = open(path, encoding='utf-8', errors='replace').read()
        except OSError as error:
            out(f'[warn] cannot read {path}: {error}')
            continue
        kept = EMOJI in text
        out(f'[{"PASS" if kept else "FAIL"}] file log keeps original emoji: {path}')
        if not kept:
            failures.append(f'{path} does not contain the original emoji')

    out('')
    if failures:
        out('GBK LOGGER VERIFY: FAILED')
        for item in failures:
            out(f'  - {item}')
        return 1
    out('GBK LOGGER VERIFY: PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
