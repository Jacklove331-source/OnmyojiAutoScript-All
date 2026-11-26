# This Python file uses the following encoding: utf-8

"""扫描 OAS 日志，聚合错误：今天 oas1/oas3/server + log\\error\\* 快照。

用法：.\\toolkit\\python.exe dev_tools\\check_oas_errors.py [--hours 12]
"""

import argparse
import collections
import datetime
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / 'log'

PATTERN = re.compile(r'(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+ \| (?P<level>ERROR|CRITICAL|WARNING) \| (?P<body>.*)')
EXC = re.compile(r'(?P<exc>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Timeout|Failed)): (?P<msg>.*)')


def scan(path: pathlib.Path, since: datetime.datetime):
    errors = collections.Counter()
    warnings = collections.Counter()
    exceptions = collections.Counter()
    last_error = None
    total = 0
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError as error:
        return None, error
    for line in text.splitlines():
        total += 1
        match = PATTERN.search(line)
        if not match:
            exc = EXC.search(line)
            if exc and 'Traceback' not in line:
                exceptions[f"{exc.group('exc')}: {exc.group('msg')[:90]}"] += 1
            continue
        try:
            stamp = datetime.datetime.strptime(match.group('ts'), '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        if stamp < since:
            continue
        body = match.group('body').strip()
        key = body[:110]
        if match.group('level') == 'ERROR':
            errors[key] += 1
            last_error = f"{match.group('ts')} {key}"
        else:
            warnings[key] += 1
    return {'total': total, 'errors': errors, 'warnings': warnings,
            'exceptions': exceptions, 'last_error': last_error}, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--hours', type=float, default=12.0)
    args = parser.parse_args()

    since = datetime.datetime.now() - datetime.timedelta(hours=args.hours)
    print(f'统计窗口：最近 {args.hours:g} 小时（since {since:%Y-%m-%d %H:%M}）\n')

    targets = sorted(LOG_DIR.glob('*.txt'), key=lambda p: p.stat().st_mtime, reverse=True)[:4]
    error_dirs = sorted(LOG_DIR.glob('error/*/log.txt'),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:3]

    for path in targets:
        stats, error = scan(path, since)
        if error is not None:
            print(f'{path.name}: 读取失败 {error}')
            continue
        errors = stats['errors']
        warnings = stats['warnings']
        exceptions = stats['exceptions']
        print(f'== {path.name} ==  行数 {stats["total"]}, 错误 {sum(errors.values())}, 警告 {sum(warnings.values())}')
        for message, count in errors.most_common(6):
            print(f'   [ERROR x{count}] {message}')
        for message, count in exceptions.most_common(4):
            print(f'   [异常 x{count}] {message}')
        for message, count in warnings.most_common(3):
            print(f'   [WARN x{count}] {message}')
        if stats['last_error']:
            print(f'   最后一条错误：{stats["last_error"][:140]}')
        print()

    if error_dirs:
        print('== 崩溃快照（log\\error）==')
        for path in error_dirs:
            stats, error = scan(path, since - datetime.timedelta(days=3))
            if error is not None:
                continue
            exceptions = stats['exceptions']
            errors = stats['errors']
            print(f'{path.parent.name}  错误 {sum(errors.values())} 异常 {sum(exceptions.values())}  ({datetime.datetime.fromtimestamp(path.stat().st_mtime):%m-%d %H:%M})')
            for message, count in (exceptions or errors).most_common(3):
                print(f'   {message}')
            try:
                lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                continue
            for line in lines[-6:]:
                if line.strip():
                    print(f'   尾部: {line.strip()[:120]}')
            print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
