# This Python file uses the following encoding: utf-8

"""OAS 日常体检：一条命令回答「现在这套脚本健康吗、卡在哪」。

检查项（全部只读，不点游戏、不改配置）：
  1. 仓库     分支 / AutoUpdate 守卫 / 未提交改动 / 最近提交
  2. 模板     模板 png 引用是否缺失、是否有 0 字节坏图
  3. 实例     每个 config/oas*.json 对应的实时帧与日志是否还在刷新
  4. 模拟器   MuMuManager info 的实例、android 是否启动、adb 端口是否与 adb devices 对齐
  5. 画面污染 是否挂着 OASX「叠加视图」浮层、mCurrentFocus 是否被抢成 null
  6. 错误     最近 N 小时日志的 ERROR 聚合（按正文指纹计数）
  7. 识图     用最新 live 帧跑页面判定模板，报告识别到的页面与最高分

退出码：0 = 全部 PASS/WARN，1 = 存在 FAIL（需要处理）。

用法：
    .\\toolkit\\python.exe dev_tools\\oas_maintain.py
    .\\toolkit\\python.exe dev_tools\\oas_maintain.py --hours 6 --json-out log/maintain.json
    .\\toolkit\\python.exe dev_tools\\oas_maintain.py --skip-screen    # 不跑识图（省 3 秒）
"""

import argparse
import collections
import datetime
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dev_tools'))

ADB = ROOT / 'toolkit' / 'Lib' / 'site-packages' / 'adbutils' / 'binaries' / 'adb.exe'
MUMU_MANAGER = pathlib.Path(r'E:\MuMuPlayer\nx_main\MuMuManager.exe')
RESULT_ORDER = {'PASS': 0, 'WARN': 1, 'FAIL': 2}


class Report:
    def __init__(self):
        self.items = []

    def add(self, section, name, status, detail):
        self.items.append({'section': section, 'name': name,
                           'status': status, 'detail': str(detail)})
        return status

    def worst(self):
        return max((RESULT_ORDER[i['status']] for i in self.items), default=0)

    def count(self, status):
        return sum(1 for i in self.items if i['status'] == status)

    def render(self):
        lines = []
        last_section = None
        for item in self.items:
            if item['section'] != last_section:
                lines.append(f'\n== {item["section"]} ==')
                last_section = item['section']
            lines.append(f'  [{item["status"]:<4}] {item["name"]:<26} {item["detail"]}')
        return '\n'.join(lines)


def run(cmd, timeout=20):
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=str(ROOT))
    except (OSError, subprocess.TimeoutExpired) as error:
        return -1, str(error)
    text = b''
    for enc in ('utf-8', 'gb18030'):
        try:
            text = (done.stdout + done.stderr).decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = (done.stdout + done.stderr).decode('utf-8', errors='replace')
    return done.returncode, text


def git(*args):
    return run(['git', *args], timeout=30)


def check_repo(report):
    code, out = git('rev-parse', '--abbrev-ref', 'HEAD')
    branch = out.strip() if code == 0 else '?'
    report.add('仓库', '分支', 'PASS' if branch not in ('?', 'HEAD') else 'WARN', branch)

    code, out = git('log', '-1', '--format=%h %ad %s', '--date=format:%Y-%m-%d %H:%M')
    report.add('仓库', '最新提交', 'PASS' if code == 0 else 'FAIL', out.strip().splitlines()[0] if out.strip() else '-')

    code, out = git('status', '--porcelain')
    changed = [line for line in out.splitlines() if line.strip()]
    core = [line for line in changed if not line.startswith(('?? log', ' M log'))]
    status = 'PASS' if len(core) <= 10 else 'WARN'
    report.add('仓库', '未提交改动', status, f'{len(core)} 个（核心文件）')

    deploy = ROOT / 'config' / 'deploy.yaml'
    auto = None
    if deploy.exists():
        for line in deploy.read_text(encoding='utf-8', errors='replace').splitlines():
            if 'AutoUpdate:' in line:
                auto = line.split(':', 1)[1].strip()
                break
    if auto is None:
        report.add('仓库', 'AutoUpdate', 'WARN', '未读到 AutoUpdate 配置')
    else:
        report.add('仓库', 'AutoUpdate', 'PASS' if auto.lower() == 'false' else 'FAIL',
                   f'{auto}（必须 false，否则启动会 reset --hard 抹掉本地改动）')


def check_templates(report):
    """模板文件完整性：引用缺失（复用 asset_file_check 的判定口径）+ 0 字节坏图。"""
    sys.path.insert(0, str(ROOT / 'dev_tools'))
    try:
        from asset_file_check import collect_refs, find_missing_detail
        refs = collect_refs()
        fatal, warn = find_missing_detail(refs)
        ref_count = len(refs)
    except ImportError as error:
        report.add('模板', '引用检查', 'WARN', f'无法 import asset_file_check: {error}')
        ref_count, fatal, warn = 0, [], []
    total_png = 0
    empty = []
    for path in ROOT.glob('tasks/**/*.png'):
        total_png += 1
        try:
            if path.stat().st_size == 0:
                empty.append(str(path.relative_to(ROOT)))
        except OSError:
            continue
    report.add('模板', '模板总数', 'PASS', f'{total_png} 张 png')
    report.add('模板', '引用缺失（会加载）', 'PASS' if not fatal else 'FAIL',
               f'{len(fatal)}/{ref_count} 个' + ('，例：' + fatal[0] if fatal else ''))
    if warn:
        report.add('模板', '引用缺失（仅坐标）', 'WARN',
                   f'{len(warn)} 个，例：{warn[0]}（click 传参，不加载图，暂不致命）')
    report.add('模板', '0 字节坏图', 'PASS' if not empty else 'FAIL',
               f'{len(empty)} 个' + ('，例：' + empty[0] if empty else ''))


def discover_configs():
    return sorted(p.stem for p in (ROOT / 'config').glob('oas*.json'))


def check_instances(report, configs, now):
    live_dir = ROOT / 'log' / 'live'
    log_dir = ROOT / 'log'
    for config in configs:
        frame = live_dir / f'{config}.jpg'
        if frame.exists():
            age = now - datetime.datetime.fromtimestamp(frame.stat().st_mtime)
            status = 'PASS' if age.total_seconds() < 90 else 'WARN'
            report.add('实例', f'{config} 实时帧', status, f'{age.total_seconds():.0f}s 前刷新')
        else:
            report.add('实例', f'{config} 实时帧', 'WARN', 'log/live 下没有该实例的帧')

        logs = sorted(log_dir.glob(f'*_{config}.txt'), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            age = now - datetime.datetime.fromtimestamp(logs[0].stat().st_mtime)
            status = 'PASS' if age.total_seconds() < 180 else 'WARN'
            report.add('实例', f'{config} 日志', status,
                       f'{logs[0].name}，{age.total_seconds():.0f}s 前刷新')

        # 最后一个任务与最近一次 RESULT
        if logs:
            tail = []
            with logs[0].open('r', encoding='utf-8', errors='replace') as handle:
                for line in handle:
                    tail.append(line)
                    if len(tail) > 400:
                        tail.pop(0)
            task = '-'
            for line in reversed(tail):
                match = re.search(r'Scheduler: Start task `([^`]+)`', line)
                if match:
                    task = match.group(1)
                    break
            result = '-'
            for line in reversed(tail):
                match = re.search(r'RESULT ([A-Za-z0-9_]+) -> (True|False)', line)
                if match:
                    result = f'{match.group(1)}={match.group(2)}'
                    break
            report.add('实例', f'{config} 当前任务', 'PASS', f'{task}；最近 RESULT {result}')


def check_emulator(report):
    if not MUMU_MANAGER.exists():
        report.add('模拟器', 'MuMuManager', 'WARN', f'未找到 {MUMU_MANAGER}')
        return
    code, out = run([str(MUMU_MANAGER), 'info', '-v', 'all'], timeout=30)
    if code != 0:
        report.add('模拟器', 'MuMuManager', 'FAIL', f'调用失败 code={code}')
        return
    try:
        data = json.loads(out[out.find('{'):out.rfind('}') + 1])
    except ValueError:
        report.add('模拟器', 'MuMuManager', 'WARN', '输出不是 json，跳过实例解析')
        data = {}
    adb_ports = {}
    for index, info in data.items():
        name = info.get('name') or f'instance {index}'
        started = bool(info.get('is_android_started'))
        port = info.get('adb_port')
        adb_ports[index] = port
        report.add('模拟器', f'{name}', 'PASS' if started else 'WARN',
                   f"android={'启动' if started else '未启动'} adb_port={port} "
                   f"state={info.get('player_state')}")

    code, out = run([str(ADB), 'devices'], timeout=20)
    online = set()
    offline = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith('List of'):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        (online if state == 'device' else offline).add(serial)
    report.add('模拟器', 'adb 在线设备', 'PASS' if online else 'FAIL',
               f'{len(online)} 台：{", ".join(sorted(online)) or "-"}')
    if offline:
        report.add('模拟器', 'adb 离线残留', 'WARN',
                   f'{len(offline)} 台：{", ".join(sorted(offline))}（需 adb disconnect 或重连）')
    for index, port in adb_ports.items():
        if port and f'127.0.0.1:{port}' not in online:
            report.add('模拟器', f'实例 {index} 端口', 'WARN',
                       f'127.0.0.1:{port} 不在 adb 在线列表')
    return online


def check_screen(report, serial, fix_overlay=False):
    """画面污染 + 焦点 + 页面识别。"""
    if serial is None:
        report.add('画面', 'adb 探针', 'WARN', '无在线设备，跳过')
        return
    code, out = run([str(ADB), '-s', serial, 'shell',
                     'dumpsys display | grep -m 3 "叠加视图"'], timeout=20)
    overlay_lines = [line.strip() for line in out.splitlines() if '叠加视图' in line]
    if overlay_lines and fix_overlay:
        run([str(ADB), '-s', serial, 'shell', 'am force-stop com.oas.oasx'], timeout=20)
        report.add('画面', 'OASX 叠加浮层', 'WARN',
                   '--fix-overlay 已 force-stop com.oas.oasx，请复跑确认已消失')
    else:
        report.add('画面', 'OASX 叠加浮层', 'PASS' if not overlay_lines else 'FAIL',
                   '无' if not overlay_lines else
                   f'{len(overlay_lines)} 处（抢焦点并污染截图；--fix-overlay 可停机）')

    code, out = run([str(ADB), '-s', serial, 'shell',
                     'dumpsys window | grep mCurrentFocus'], timeout=20)
    # dumpsys 可能同时给出 system_server 的 null 行与真实焦点行，以最后一行真实值为准
    focus_lines = [line.strip() for line in out.splitlines() if 'mCurrentFocus' in line]
    focus = focus_lines[-1] if focus_lines else ''
    focus_null = focus.replace(' ', '').endswith('mCurrentFocus=null')
    report.add('画面', '输入焦点', 'FAIL' if focus_null else 'PASS',
               (focus or '未读到')[:110])


def check_errors(report, hours):
    since = datetime.datetime.now() - datetime.timedelta(hours=hours)
    try:
        from check_oas_errors import scan  # 复用已有的日志扫描
    except ImportError:
        report.add('错误', '日志扫描', 'WARN', '无法 import check_oas_errors')
        return
    log_dir = ROOT / 'log'
    targets = sorted(log_dir.glob('*.txt'), key=lambda p: p.stat().st_mtime, reverse=True)[:4]
    total_errors = 0
    summary = collections.Counter()
    for path in targets:
        stats, error = scan(path, since)
        if error is not None or stats is None:
            continue
        total_errors += sum(stats['errors'].values())
        for message, count in stats['errors'].items():
            summary[message] += count
    status = 'PASS' if total_errors == 0 else ('WARN' if total_errors < 5 else 'FAIL')
    report.add('错误', f'最近 {hours:g}h ERROR', status, f'{total_errors} 条')
    for message, count in summary.most_common(5):
        report.add('错误', '  ↳ 指纹', 'WARN', f'x{count} {message[:100]}')


def check_vision(report, configs, live_dir):
    try:
        from asset_health_scan import load_rules, score_rule, imread, DEFAULT_SCOPES
    except ImportError as error:
        report.add('识图', '模板审计', 'WARN', f'无法 import asset_health_scan: {error}')
        return
    frame = None
    used = None
    for config in configs:
        candidate = live_dir / f'{config}.jpg'
        if candidate.exists():
            frame, used = imread(candidate), candidate.name
            break
    if frame is None:
        report.add('识图', '模板审计', 'WARN', '没有可用的 live 帧')
        return
    rules = load_rules(DEFAULT_SCOPES['page_checks'])
    best = {}
    hits = 0
    for rule in rules:
        score, _, _ = score_rule(rule, frame)
        if score is None:
            continue
        best[rule['group']] = max(best.get(rule['group'], 0.0), score)
        if score >= rule['threshold']:
            hits += 1
    report.add('识图', '帧来源', 'PASS', used)
    if hits == 0:
        report.add('识图', '页面命中', 'WARN',
                   f'0/{len(rules)} 项命中（可能在加载/登录页，或 UI 改版）')
    else:
        report.add('识图', '页面命中', 'PASS', f'{hits}/{len(rules)} 项命中')
    for group, score in sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:4]:
        report.add('识图', f'  ↳ {group}', 'PASS' if score >= 0.8 else 'WARN', f'{score:.3f}')


def check_login_loop(report, configs):
    """登录重试循环指纹：一天里点「进入游戏」多少次、等角色页超时多少次。

    正常一次登录 1~2 次点击；2026-11-09 oas1 实测 46 次点击 + 45 次超时，
    根因是加载需要 33s 而代码只等 5s（已由 _wait_login_loaded 修掉）。
    """
    today = datetime.date.today().strftime('%Y-%m-%d')
    for config in configs:
        path = ROOT / 'log' / f'{today}_{config}.txt'
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        clicks = len(re.findall(r'Click .* @ LOGIN_ENTER_GAME_ORIGIN', text))
        timeouts = text.count('Wait until appear LOGIN_LOGIN_SPECIFIC_SERVE timeout')
        if clicks == 0 and timeouts == 0:
            continue
        if clicks <= 3:
            status = 'PASS'
        elif clicks <= 10:
            status = 'WARN'
        else:
            status = 'FAIL'
        report.add('登录', f'{config} 今日重试', status,
                   f'点「进入游戏」{clicks} 次 / 等角色页超时 {timeouts} 次'
                   + ('（>10 次说明加载被打断，跑 dev_tools/login_loaded_wait_check.py 复核）'
                      if status == 'FAIL' else ''))


def main() -> int:
    parser = argparse.ArgumentParser(description='OAS 日常体检')
    parser.add_argument('--hours', type=float, default=12.0, help='错误统计窗口，默认 12 小时')
    parser.add_argument('--skip-screen', action='store_true', help='跳过识图审计')
    parser.add_argument('--fix-overlay', action='store_true',
                        help='发现 OASX 叠加浮层时执行 adb am force-stop com.oas.oasx（会关掉 OASX）')
    parser.add_argument('--json-out', help='结果写入 json')
    args = parser.parse_args()

    now = datetime.datetime.now()
    report = Report()
    configs = discover_configs()

    print(f'OAS 日常体检  {now:%Y-%m-%d %H:%M:%S}')
    print(f'仓库 {ROOT}')
    check_repo(report)
    check_templates(report)
    check_instances(report, configs, now)
    online = check_emulator(report)
    serial = sorted(online)[0] if online else None
    check_screen(report, serial, fix_overlay=args.fix_overlay)
    check_login_loop(report, configs)
    check_errors(report, args.hours)
    if not args.skip_screen:
        check_vision(report, configs, ROOT / 'log' / 'live')

    print(report.render())
    worst = report.worst()
    label = ['PASS', 'WARN', 'FAIL'][worst]
    print(f'\n共 {len(report.items)} 项：PASS {report.count("PASS")} / '
          f'WARN {report.count("WARN")} / FAIL {report.count("FAIL")}  → 结论 {label}')

    if args.json_out:
        out = pathlib.Path(args.json_out)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({'time': now.isoformat(), 'result': label,
                                   'items': report.items}, ensure_ascii=False, indent=2),
                       encoding='utf-8')
        print(f'结果写入 {out}')
    return 0 if worst < 2 else 1


if __name__ == '__main__':
    sys.exit(main())
