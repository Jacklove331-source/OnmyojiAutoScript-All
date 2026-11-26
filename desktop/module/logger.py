# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import sys

import logging
import os
import shutil
from datetime import datetime, timedelta, date
from io import TextIOBase
from pathlib import Path
from rich.console import Console, ConsoleOptions, ConsoleRenderable, NewLine, RenderResult
from rich.highlighter import NullHighlighter
from rich.logging import RichHandler
from rich.rule import Rule
from typing import Callable, List


def cleanup_logs(log_dir: str = "./log", keep_days: int = 14):
    """Remove expired log files and error snapshots during process startup."""
    log_path = Path(log_dir)
    cutoff = (datetime.now() - timedelta(days=keep_days)).timestamp()

    def remove_if_expired(path: Path, *, snapshot: bool = False):
        try:
            if path.is_symlink() or path.stat().st_mtime >= cutoff:
                return
            if snapshot:
                if path.is_dir() and path.resolve().parent == path.parent.resolve():
                    shutil.rmtree(path)
            elif path.is_file():
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            # Another account's process may have removed the same expired entry.
            pass
        except PermissionError:
            logger.warning('Skipping expired log without delete access: %s', path)
        except OSError as error:
            logger.warning('Could not clean up expired log %s: %s', path, error)

    try:
        for path in log_path.iterdir():
            if (path.name == 'error' and path.is_dir() and not path.is_symlink()
                    and path.resolve().parent == log_path.resolve()):
                for snapshot in path.iterdir():
                    remove_if_expired(snapshot, snapshot=True)
            else:
                remove_if_expired(path)
    except FileNotFoundError:
        pass
    except OSError as error:
        logger.warning('Could not enumerate log directory %s: %s', log_path, error)


def empty_function(*args, **kwargs):
    pass


# Ensure running in Alas root folder
os.chdir(os.path.join(os.path.dirname(__file__), '../'))
# cnocr will set root logger in cnocr.utils
# Delete logging.basicConfig to avoid logging the same message twice.
logging.basicConfig = empty_function
logging.raiseExceptions = True  # Set True if wanna see encode errors on console

# Remove HTTP keywords (GET, POST etc.)
# RichHandler.KEYWORDS = []


# def show_handlers(handlers):
#     # 获取并打印日志记录器中处理器的信息
#     for handler in logger.handlers:
#         # 获取处理器的类名
#         handler_class = handler.__class__.__name__
#         print(f"Handler class: {handler_class}")
#
#         # 获取处理器的级别
#         handler_level = logging.getLevelName(handler.level)
#         print(f"Handler level: {handler_level}")
#
#         # 获取处理器的格式化器
#         formatter = handler.formatter
#         if formatter is not None:
#             formatter_class = formatter.__class__.__name__
#             print(f"Formatter class: {formatter_class}")
#
#         # 其他处理器的属性和方法，根据需要进行获取和打印
#         print()  # 打印空行，用于分隔处理器的信息


# Logger init
logger_debug = False
logger = logging.getLogger('oas')
logger.setLevel(logging.DEBUG if logger_debug else logging.INFO)
file_formatter = logging.Formatter(
    fmt='%(asctime)s.%(msecs)03d | %(filename)20s:%(lineno)04d | %(levelname)8s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
console_formatter = logging.Formatter(
    fmt='%(asctime)s.%(msecs)03d │ %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
flutter_formatter = logging.Formatter(
    fmt='| %(asctime)s.%(msecs)03d | %(message)08s', datefmt='%H:%M:%S')


# ======================================================================================================================
#            Stream safety: GBK 控制台 / 已关闭的管道
# ======================================================================================================================
_console_replace_warned = False


def _reencode(text: str, encoding: str) -> str:
    """用替换符把 text 转成 encoding 能表示的文本（不会抛 UnicodeEncodeError）。"""
    try:
        return text.encode(encoding, 'replace').decode(encoding, 'replace')
    except (LookupError, UnicodeError):
        return text.encode('ascii', 'replace').decode('ascii')


class SafeTextStream:
    """包住终端流：窄编码（中文 Windows 控制台是 GBK）写不下的字符用替换符代替。

    - 只影响终端：文件日志是 UTF-8，原文完整保留；
    - 第一次发生替换时额外打一行提示，避免"日志静默丢字"；
    - 不做空 except：换成 ascii 都失败（理论上不会）才继续抛给上层。
    """

    def __init__(self, stream):
        self._stream = stream

    @property
    def encoding(self):
        return getattr(self._stream, 'encoding', None) or 'utf-8'

    def write(self, text):
        try:
            return self._stream.write(text)
        except UnicodeEncodeError:
            global _console_replace_warned
            encoding = self.encoding
            if not _console_replace_warned:
                _console_replace_warned = True
                note = f'[logger] 控制台编码 {encoding} 无法表示的字符已替换为占位符；文件日志保留原文\n'
                self._stream.write(_reencode(note, encoding))
            return self._stream.write(_reencode(text, encoding))

    def flush(self):
        flush = getattr(self._stream, 'flush', None)
        if callable(flush):
            flush()

    def isatty(self) -> bool:
        isatty = getattr(self._stream, 'isatty', None)
        return bool(isatty()) if callable(isatty) else False

    def __getattr__(self, item):
        # encoding / fileno / closed 等属性全部转发给真实流
        return getattr(self._stream, item)


# ======================================================================================================================
#            Set console logger
# ======================================================================================================================
console_hdlr = RichHandler(
    console=Console(
        width=120,
        file=SafeTextStream(sys.stdout),
    ),
    show_path=False,
    show_time=False,
    rich_tracebacks=True,
    tracebacks_show_locals=True,
    tracebacks_extra_lines=3,
    tracebacks_width=160
)
console_hdlr.setFormatter(console_formatter)
logger.addHandler(console_hdlr)


# ======================================================================================================================
#            Set file
# ======================================================================================================================
class RichFileHandler(RichHandler):
    # Rename
    pass


# Add file logger
pyw_name = os.path.splitext(os.path.basename(sys.argv[0]))[0]


def set_file_logger(name=pyw_name, *, do_cleanup=False):
    if '_' in name:
        name = name.split('_', 1)[0]
    log_file = f'./log/{date.today()}_{name}.txt'
    try:
        file = open(log_file, mode='a', encoding='utf-8')
    except FileNotFoundError:
        os.mkdir('./log')
        file = open(log_file, mode='a', encoding='utf-8')

    file_console = Console(
        file=file,
        no_color=True,
        highlight=False,
        width=160,
    ) 

    hdlr = RichFileHandler(
        console=file_console,
        show_path=False,
        show_time=False,
        show_level=False,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
        tracebacks_extra_lines=3,
        tracebacks_width=160,
        highlighter=NullHighlighter(),
    )
    hdlr.setFormatter(file_formatter)

    logger.handlers = [h for h in logger.handlers if not isinstance(
        h, (logging.FileHandler, RichFileHandler))]
    logger.addHandler(hdlr)
    logger.log_file = log_file

    # ---------- 可选：清理旧文件 ----------
    if do_cleanup:
        cleanup_logs()
        logger.info("Log cleanup finished")


# ======================================================================================================================
#            Set flutter
# ======================================================================================================================
class FlutterHandler(RichHandler):
    # Rename
    pass


class FlutterConsole(Console):
    """
    Force full feature console
    but not working lol :(
    """

    @property
    def options(self) -> ConsoleOptions:
        return ConsoleOptions(
            max_height=self.size.height,
            size=self.size,
            legacy_windows=False,
            min_width=1,
            max_width=self.width,
            encoding='utf-8',
            is_terminal=False,
        )


class FlutterLogStream(TextIOBase):
    """把日志推给外部接收方（GUI 服务进程的 multiprocessing 管道）。

    对端关闭后 `send()` 会抛 BrokenPipeError（Windows 上是 WinError 232），
    未加保护的写会把异常穿过 logger 调用栈直接打断正在跑的任务，所以这里：
    - 写之前先问 `is_open()`：管道已经 close 就不再尝试；
    - 写入异常（BrokenPipeError/EOFError/OSError/ValueError）只报一次，
      之后 `broken=True` 全部改走 `fallback`（文件日志），不重试、不吞错。
    """

    def __init__(self, *args, func: Callable[[ConsoleRenderable], None] = None,
                 is_open: Callable[[], bool] = None,
                 fallback: Callable[[str], None] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._func = func
        self._is_open = is_open
        self._fallback = fallback
        self.broken = False
        self._degraded_reported = False

    def write(self, msg: str) -> int:
        if isinstance(msg, bytes):
            msg = msg.decode("utf-8")
        if self._func is None:
            return len(msg)
        opened = not self.broken
        if opened and self._is_open is not None:
            try:
                opened = bool(self._is_open())
            except (OSError, ValueError) as error:
                opened = False
                self._report_degraded(
                    f'日志管道状态检查失败（{type(error).__name__}: {error}），降级为仅写文件日志')
        if not opened:
            self.broken = True
            self._degrade(msg)
            return len(msg)
        try:
            self._func(msg)
        except (BrokenPipeError, EOFError, OSError, ValueError) as error:
            self.broken = True
            self._report_degraded(
                f'日志管道不可写（{type(error).__name__}: {error}），降级为仅写文件日志')
            self._degrade(msg)
        return len(msg)

    def _report_degraded(self, reason: str) -> None:
        """降级原因只说明一次，直接落文件日志（不回调 logger，避免在这里递归）。"""
        if self._degraded_reported or self._fallback is None:
            return
        self._degraded_reported = True
        self._fallback(f'[logger] {reason}')

    def _degrade(self, msg: str) -> None:
        if self._fallback is not None:
            self._fallback(msg)


def _file_log_fallback(msg: str) -> None:
    """管道挂了之后的落点：直接写文件日志的控制台（UTF-8，不经过 logger 递归）。"""
    for hdlr in list(logger.handlers):
        if isinstance(hdlr, RichFileHandler):
            try:
                hdlr.console.print(msg, markup=False, highlight=False, soft_wrap=True)
                return
            except (OSError, ValueError) as error:
                sys.stderr.write(f'[logger] 文件日志降级失败：{type(error).__name__}: {error}\n')
                return


def set_func_logger(func, *, is_open: Callable[[], bool] = None):
    stream = FlutterLogStream(func=func, is_open=is_open, fallback=_file_log_fallback)
    stream_console = Console(
        file=stream,
        force_terminal=False,
        force_interactive=False,
        no_color=True,
        highlight=False,
        width=80,
    )
    hdlr = FlutterHandler(
        console=stream_console,
        show_path=False,
        show_time=False,
        show_level=True,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
        tracebacks_extra_lines=3,
        highlighter=NullHighlighter(),
    )
    hdlr.setFormatter(flutter_formatter)
    logger.addHandler(hdlr)

# ======================================================================================================================
#            Set print format
# ======================================================================================================================


def _get_renderables(
        self: Console, *objects, sep=" ", end="\n", justify=None, emoji=None, markup=None, highlight=None,
) -> List[ConsoleRenderable]:
    """
    Refer to rich.console.Console.print()
    """
    if not objects:
        objects = (NewLine(),)

    render_hooks = self._render_hooks[:]
    with self:
        renderables = self._collect_renderables(
            objects,
            sep,
            end,
            justify=justify,
            emoji=emoji,
            markup=markup,
            highlight=highlight,
        )
        for hook in render_hooks:
            renderables = hook.process_renderables(renderables)
    return renderables


def print(*objects: ConsoleRenderable, **kwargs):
    for hdlr in list(logger.handlers):
        try:
            if isinstance(hdlr, FlutterHandler):
                for renderable in _get_renderables(hdlr.console, *objects, **kwargs):
                    # 走 FlutterLogStream.write：管道已关闭时自动降级，不再抛 BrokenPipeError
                    hdlr.console.file.write(str(renderable))
            elif isinstance(hdlr, RichHandler):
                hdlr.console.print(*objects)
        except (BrokenPipeError, EOFError, OSError, ValueError, UnicodeError) as error:
            # 输出端不可用不能打断正在跑的任务：降级到文件日志，并把原因写明
            _file_log_fallback(f'[logger] 控制台输出失败（{type(error).__name__}: {error}）：'
                               f'{str(objects)[:200]}')


class GuiRule(Rule):
    def __rich_console__(
            self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        options.max_width = 80
        return super().__rich_console__(console, options)

    def __str__(self):
        total_width = 80
        cell_len = len(self.title) + 2
        aside_len = (total_width - cell_len) // 2
        left = self.characters * aside_len
        right = self.characters * (total_width - cell_len - aside_len)
        if self.title:
            space = ' '
        else:
            space = self.characters
        return f"{left}{space}{self.title}{space}{right}\n"

    def __repr__(self):
        return self.__str__()


def rule(title="", *, characters="─", style="rule.line", end="\n", align="center"):
    rule = GuiRule(title=title, characters=characters,
                   style=style, end=end)
    print(rule)


def hr(title, level=3):
    title = str(title).upper()
    if level == 1:
        logger.rule(title, characters='═')
        logger.info(title)
    if level == 2:
        logger.rule(title, characters='─')
        logger.info(title)
    if level == 3:
        logger.info(f"[bold]<<< {title} >>>[/bold]", extra={"markup": True})
    if level == 0:
        logger.rule(characters='═')
        logger.rule(title, characters='─')
        logger.rule(characters='═')


def attr(name, text):
    logger.info('[%s] %s' % (str(name), str(text)))


def attr_align(name, text, front='', align=22):
    name = str(name).rjust(align)
    if front:
        name = front + name[len(front):]
    logger.info('%s: %s' % (name, str(text)))


def show():
    logger.info('INFO')
    logger.warning('WARNING')
    logger.debug('DEBUG')
    logger.error('ERROR')
    logger.critical('CRITICAL')
    logger.hr('hr0', 0)
    logger.hr('hr1', 1)
    logger.hr('hr2', 2)
    logger.hr('hr3', 3)
    logger.info(r'Brace { [ ( ) ] }')
    logger.info(r'True, False, None')
    logger.info(r'E:/path\\to/alas/alas.exe, /root/alas/, ./relative/path/log.txt')
    logger.info('Tests very long strings. Tests very long strings. Tests very long strings. Tests very long strings. Tests very long strings.')
    local_var1 = 'This is local variable'
    # Line before exception
    raise Exception("Exception")
    # Line below exception


def error_convert(func):
    def error_wrapper(msg, *args, **kwargs):
        if isinstance(msg, Exception):
            msg = f'{type(msg).__name__}: {msg}'
        return func(msg, *args, **kwargs)

    return error_wrapper


logger.error = error_convert(logger.error)
logger.hr = hr
logger.attr = attr
logger.attr_align = attr_align
logger.set_file_logger = set_file_logger
logger.set_func_logger = set_func_logger
logger.rule = rule
logger.print = print
logger.log_file: str

logger.set_file_logger()
logger.hr('Start', level=0)
