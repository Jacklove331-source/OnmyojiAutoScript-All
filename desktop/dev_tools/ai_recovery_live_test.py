# This Python file uses the following encoding: utf-8

"""AI 自愈层实机验收（oas3 + MuMu 实例 1）。

用法（每次只跑一个场景，避免同时抢占设备）：
    .\\toolkit\\python.exe dev_tools\\ai_recovery_live_test.py state
    .\\toolkit\\python.exe dev_tools\\ai_recovery_live_test.py popup
    .\\toolkit\\python.exe dev_tools\\ai_recovery_live_test.py adb
    .\\toolkit\\python.exe dev_tools\\ai_recovery_live_test.py screenshot
    .\\toolkit\\python.exe dev_tools\\ai_recovery_live_test.py offline
    .\\toolkit\\python.exe dev_tools\\ai_recovery_live_test.py template
    .\\toolkit\\python.exe dev_tools\\ai_recovery_live_test.py normal --task Restart

设计约束：
- 同一时间只允许一个 OAS 设备进程；
- 任何会点游戏的动作都走 AI 安全白名单流程（不是本脚本自带的裸点击）；
- 每个场景把截图/结果写到 log/ai_recovery/live/，便于人工复核。
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.ai_recovery.actions import ActionExecutor
from module.ai_recovery.config import load_config
from module.ai_recovery.evidence import EvidenceStore
from module.ai_recovery.imageutil import dhash, save_png
from module.ai_recovery.integration import TaskAdapter
from module.ai_recovery.client import _BREAKER
from module.ai_recovery.orchestrator import AiRecovery, _STATE
from module.ai_recovery.templates import TemplateLibrary
from module.ai_recovery.types import Action, Anomaly, Outcome

OUT = ROOT / 'log' / 'ai_recovery' / 'live'
OUT.mkdir(parents=True, exist_ok=True)
RESULTS: list[dict] = []


def record(name: str, ok: bool, detail: str, extra: dict | None = None) -> None:
    item = {'name': name, 'ok': bool(ok), 'detail': detail, 'time': time.strftime('%H:%M:%S')}
    if extra:
        item['extra'] = extra
    RESULTS.append(item)
    print(('PASS  ' if ok else 'FAIL  ') + f'{name}: {detail}')


def save_result() -> None:
    path = OUT / f'live_{time.strftime("%Y%m%d_%H%M%S")}.json'
    path.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'saved: {path}')
    ok = all(item['ok'] for item in RESULTS)
    print(f'LIVE RESULT: {"PASSED" if ok else "FAILED"} ({sum(1 for i in RESULTS if i["ok"])}/{len(RESULTS)})')


def make_live():
    from module.config.config import Config
    from module.device.device import Device
    from tasks.GameUi.navigator import GameUi
    config = Config('oas3')
    device = Device(config)
    ui = GameUi(config, device)
    return config, device, ui


def ensure_main(ui, timeout: float = 90) -> bool:
    from tasks.GameUi.default_pages import page_main
    try:
        if ui.get_current_page(skip_first_screenshot=False) == page_main:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(ui.goto_page(page_main, timeout=min(60, int(timeout))))
    except Exception as error:  # noqa: BLE001
        print(f'ensure_main failed: {type(error).__name__}: {error}')
        return False


# ---------------------------------------------------------------- 场景


def scenario_state() -> None:
    _, device, ui = make_live()
    device.screenshot()
    image = device.image
    save_png(OUT / 'state.png', image)
    page = ui.get_current_page(skip_first_screenshot=True)
    record('live state', image is not None,
           f'size={image.shape[1]}x{image.shape[0]} page={page} dhash={dhash(image)}',
           {'page': str(page)})


def scenario_popup() -> None:
    """真实异常：庭院按返回键弹出「退出游戏」确认框，OAS 模板识别不到 → 交给 AI 接管。"""
    config, device, ui = make_live()
    os.environ['OAS_AI_MODE'] = 'auto'
    if not ensure_main(ui):
        record('popup: goto main', False, '无法回到庭院，跳过（避免误操作）')
        return
    device.adb_shell(['input', 'keyevent', '4'])
    time.sleep(1.2)
    device.screenshot()
    save_png(OUT / 'popup_before.png', device.image)
    dialog_seen = bool(ui.appear(ui.O_EXIT_GAME_CONFIRM))
    page_before = ui.get_current_page(skip_first_screenshot=True)
    record('popup: dialog blocks main page', dialog_seen,
           f'exit_confirm_template={dialog_seen} page_cache={page_before}')

    _STATE.clear()
    start = time.time()
    result = ui.ai_recover(kind='unknown_page', detail='实机验收：退出游戏确认框遮挡，OAS 模板无法识别')
    elapsed = round(time.time() - start, 1)
    evidence = OUT / f'popup_result_{int(time.time())}.json'
    evidence.write_text(json.dumps(result.to_dict() if result else None, ensure_ascii=False, indent=2),
                        encoding='utf-8')
    if result is None:
        record('popup: ai recovery', False, 'ai_recover returned None')
        return
    device.screenshot()
    save_png(OUT / 'popup_after.png', device.image)
    page_after = ui.get_current_page(skip_first_screenshot=False)
    record('popup: ai recovery executed', result.outcome in (Outcome.RECOVERED, Outcome.NEEDS_HUMAN),
           f'outcome={result.outcome.value} page_after={page_after} elapsed={elapsed}s ai_called={result.ai_called}',
           result.to_dict())
    diag = result.diagnosis
    if result.ai_called and diag:
        record('popup: ai identified dialog safely',
               ('退出' in diag.page or '弹窗' in diag.page)
               and not any('确认' == (action.text or '') for action in diag.actions),
               f'page={diag.page} actions={[a.text for a in diag.actions]}')
    else:
        record('popup: knowledge-base replay without api', True,
               f'outcome={result.outcome.value} message={result.message}')
    record('popup: handed back to OAS', result.outcome == Outcome.RECOVERED and page_after is not None,
           f'page_after={page_after}')
    # 立即再触发一次同 key：应被冷却/次数限制拦住，防循环
    again = ui.ai_recover(kind='unknown_page', detail='重复触发测试')
    record('popup: no immediate re-takeover', again is not None and again.outcome == Outcome.SKIPPED,
           f'second={again.outcome.value if again else None}')

    if result.outcome != Outcome.RECOVERED:
        # 兜底：确保不把游戏留在退出确认框里
        device.adb_shell(['input', 'keyevent', '4'])
        time.sleep(0.8)


def scenario_adb() -> None:
    """真实 ADB 断开 → 本地恢复（不调用 AI）。"""
    config, device, ui = make_live()
    device.screenshot()
    serial = '127.0.0.1:16416'
    import subprocess
    adb = str(ROOT / 'toolkit' / 'Lib' / 'site-packages' / 'adbutils' / 'binaries' / 'adb.exe')
    subprocess.run([adb, 'disconnect', serial], capture_output=True, timeout=20)
    time.sleep(0.5)

    _STATE.clear()
    start = time.time()
    result = ui.ai_recover(kind='adb_offline', detail='实机验收：主动断开 ADB')
    elapsed = round(time.time() - start, 1)
    ok = bool(result and result.outcome == Outcome.RECOVERED and not result.ai_called)
    record('adb: local recovery', ok,
           f'outcome={result.outcome.value if result else None} ai_called={result.ai_called if result else None} '
           f'elapsed={elapsed}s', result.to_dict() if result else None)
    try:
        device.screenshot()
        record('adb: screenshot after recovery', True, f'{device.image.shape[1]}x{device.image.shape[0]}')
    except Exception as error:  # noqa: BLE001
        record('adb: screenshot after recovery', False, f'{type(error).__name__}: {error}')


def scenario_screenshot() -> None:
    """截图连续失败 → 本地降级到 ADB，不调用 AI。"""
    _, device, ui = make_live()
    adapter = TaskAdapter(ui)
    state = {'count': 0}
    original = adapter.screenshot

    def flaky():
        state['count'] += 1
        if state['count'] <= 2:
            raise TimeoutError('live fault injection: screenshot timeout')
        return original()

    adapter.screenshot = flaky
    _STATE.clear()
    recovery = AiRecovery(load_config(config_name='oas3', force=True), adapter)
    result = recovery.handle(Anomaly(type='screenshot_failed', task='GameUi', page='',
                                     detail='实机验收：前两次截图强制超时'))
    ok = result.outcome == Outcome.RECOVERED and not result.ai_called
    record('screenshot: local fallback', ok,
           f'outcome={result.outcome.value} ai_called={result.ai_called} msg={result.message}',
           result.to_dict())


# ---------------------------------------------------------------- 离线故障注入


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get('Content-Length') or 0)
        self.rfile.read(length)
        if '/slow' in self.path:
            time.sleep(4)
            body = b'{"ok":true}'
            self.send_response(200)
        elif '/badjson' in self.path:
            body = b'not a json at all'
            self.send_response(200)
        elif '/badschema' in self.path:
            body = json.dumps({'choices': [{'message': {'content': '{"page":"x"}'}}],
                               'usage': {}}).encode()
            self.send_response(200)
        else:
            body = json.dumps({'choices': [{'message': {'content': '{"page":"庭院","page_known":true,'
                                                                    '"confidence":0.9,"cause":"test",'
                                                                    '"risk":"low","actions":[]}'}}],
                               'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}).encode()
            self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D401
        return


def scenario_offline() -> None:
    """API 超时 / 非法 JSON / 非法结构：不阻塞、不误动作、可退回本地。"""
    server = ThreadingHTTPServer(('127.0.0.1', 8771), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    try:
        from module.ai_recovery.client import AiBadResponse, AiTimeout, DeepSeekClient
        from module.ai_recovery.imageutil import encode_jpeg
        cfg = load_config(config_name='oas3', force=True)
        cfg.api.base_url = 'http://127.0.0.1:8771/slow'
        cfg.api.timeout_s = 1.5
        cfg.api.max_retries = 0
        cfg.api_key = 'test'
        image = encode_jpeg(__import__('numpy').zeros((720, 1280, 3), dtype='uint8'))
        _BREAKER.clear()
        started = time.time()
        try:
            DeepSeekClient(cfg).analyze(image, {'task': 'offline'})
            record('offline: timeout', False, 'expected timeout')
        except AiTimeout:
            record('offline: timeout', True, f'raised after {round(time.time() - started, 1)}s')
        except Exception as error:  # noqa: BLE001
            record('offline: timeout', False, f'{type(error).__name__}: {error}')

        _BREAKER.clear()
        cfg.api.base_url = 'http://127.0.0.1:8771/badjson'
        try:
            DeepSeekClient(cfg).analyze(image, {'task': 'offline'})
            record('offline: bad json', False, 'expected bad response')
        except AiBadResponse:
            record('offline: bad json', True, 'rejected')
        except Exception as error:  # noqa: BLE001
            record('offline: bad json', False, f'{type(error).__name__}: {error}')

        _BREAKER.clear()
        cfg.api.base_url = 'http://127.0.0.1:8771/badschema'
        try:
            DeepSeekClient(cfg).analyze(image, {'task': 'offline'})
            record('offline: bad schema', False, 'expected schema rejection')
        except AiBadResponse:
            record('offline: bad schema', True, 'rejected')
        except Exception as error:  # noqa: BLE001
            record('offline: bad schema', False, f'{type(error).__name__}: {error}')

        # 预算硬上限：把上限设为 0，编排器不应发出请求
        from module.ai_recovery.budget import BudgetGuard
        cfg2 = load_config(config_name='oas3', force=True)
        cfg2.enabled = True
        cfg2.mode = 'auto'
        cfg2.budget.per_incident_calls = 0
        guard = BudgetGuard(cfg2, config_name='oas3')
        ok, reason = guard.reserve()
        record('offline: budget hard stop', not ok and 'per_incident' in reason, reason)
    finally:
        server.shutdown()


# ---------------------------------------------------------------- 模板


def scenario_template() -> None:
    """真实截图 → 已有 AI 模板 → 新帧回归 → 生成新版本 → 晋级/回滚 → 正式资源替换演练（副本）。"""
    config, device, ui = make_live()
    if not ensure_main(ui):
        record('template: goto main', False, '无法回到庭院')
        return

    lib = TemplateLibrary(load_config(config_name='oas3', force=True), config_name='oas3')
    name = 'dialog_exit_cancel'
    manifest = lib.manifest('GameUi', name)
    if not manifest.get('versions'):
        record('template: existing learned template', False, '没有 dialog_exit_cancel 模板（需先跑 popup）')
        return
    region = dict(manifest['versions'][0].get('region') or {})
    active = lib.active_path('GameUi', name)

    # 造一个真实异常界面：退出游戏确认框
    device.adb_shell(['input', 'keyevent', '4'])
    time.sleep(1.2)
    device.screenshot()
    image = device.image
    save_png(OUT / 'template_dialog.png', image)
    dialog = bool(ui.appear(ui.O_EXIT_GAME_CONFIRM))
    record('template: real dialog frame', dialog, f'exit_confirm={dialog}')
    if not dialog:
        device.adb_shell(['input', 'keyevent', '4'])
        return

    # 1) 已有模板在新帧上的回归
    from module.ai_recovery.imageutil import load_image
    from module.ai_recovery.templates import Candidate
    existing = load_image(active, as_rgb=True)
    score = lib._score(existing, image)
    record('template: learned v1 matches fresh frame', bool(score and score >= 0.88),
           f'score={score:.3f}')

    evidence = EvidenceStore('oas3')
    negatives = evidence.negative_pool(limit=12)
    candidate = Candidate(task='GameUi', name=name, path=Path(active), region=region,
                          resolution=(image.shape[1], image.shape[0]), source='live_refresh')
    report = lib.validate(candidate, [image], negatives)
    record('template: validation on fresh frame', report.passed,
           f'{report.reason} positive={report.positive_min:.3f} negative={report.negative_max:.3f} '
           f'skipped_same_page={report.detail.get("negatives_skipped_same_page")}', report.to_dict())

    # 2) 从新帧重新裁剪候选并晋级 v2，再回滚到 v1
    if report.passed:
        fresh = lib.create_candidate(image, region, name, 'GameUi', source='live_refresh')
        report2 = lib.validate(fresh, [image], negatives)
        if report2.passed:
            lib.promote(fresh, report2, game_version='live', note='实机回归后自动更新')
            v2 = lib.active_path('GameUi', name)
            record('template: promoted new version from live frame', v2.endswith('v002.png'), v2)
            rolled = lib.rollback('GameUi', name)
            record('template: rollback to v1', rolled == 1 and lib.active_path('GameUi', name) == active,
                   f'active_version={rolled}')

        # 3) 正式资源替换演练（只对副本，不碰真实素材）
        import shutil
        copy = OUT / 'assets_copy.png'
        source = ROOT / ui.I_CHECK_MAIN.file
        shutil.copyfile(source, copy)
        original = copy.read_bytes()
        applied = lib.apply_to_source('GameUi', name, copy)
        replaced = copy.read_bytes() != original
        lib.restore_source(applied['backup'], copy)
        restored = copy.read_bytes() == original
        record('template: apply/rollback on copy', replaced and restored,
               f'replaced={replaced} backup={Path(applied["backup"]).name} restored={restored}')
    else:
        record('template: conservative rejection recorded', True,
               f'负样本风险高，拒绝晋级（这是正确行为）：{report.reason}')

    # 收尾：关掉确认框
    ui.close_unknown_pages(skip_first_screenshot=False, allow_ai=False)
    time.sleep(0.5)


def scenario_synthetic() -> None:
    """无意义/黑屏画面：真实 API 下必须停手（低置信或验证失败），绝不盲点。"""
    import numpy as np
    from module.ai_recovery.config import load_config
    from module.ai_recovery.kb import KnowledgeBase
    from module.ai_recovery.selftest import FakeAdapter
    from module.ai_recovery.templates import TemplateLibrary
    from module.ai_recovery.types import Anomaly, Outcome

    cfg = load_config(config_name='oas3', force=True)
    cfg.enabled = True
    cfg.mode = 'auto'
    cfg.api_key = cfg.resolve_api_key()
    image = np.full((720, 1280, 3), 8, dtype=np.uint8)
    image[300:420, 400:880] = np.random.default_rng(7).integers(0, 255, (120, 480, 3), dtype=np.uint8)
    adapter = FakeAdapter(image=image, page='unknown', noop_click=True)
    recovery = AiRecovery(cfg, adapter,
                          kb=KnowledgeBase(cfg, config_name='synthetic',
                                           path=OUT / 'synthetic_kb.jsonl'),
                          templates=TemplateLibrary(cfg, config_name='oas3'),
                          evidence=EvidenceStore('synthetic', root=OUT / 'evidence'))
    _STATE.clear()
    result = recovery.handle(Anomaly(type='unknown_page', task='Synthetic', detail='无意义黑屏画面'))
    safe = result.outcome in (Outcome.NEEDS_HUMAN, Outcome.STOPPED, Outcome.OBSERVED, Outcome.UNAVAILABLE)
    record('synthetic: unsafe screen does not blind-click', safe and not adapter.clicks,
           f'outcome={result.outcome.value} clicks={adapter.clicks} msg={result.message}',
           result.to_dict())


def scenario_normal(task: str = 'Restart') -> None:
    """正常任务不应产生 AI 调用（开启观察模式）。"""
    before = _count_incidents()
    from script import Script
    started = time.time()
    result = Script('oas3').run(task)
    elapsed = round(time.time() - started, 1)
    after = _count_incidents()
    calls = _budget_calls()
    record('normal: task finished', bool(result), f'{task} -> {result!r} elapsed={elapsed}s')
    record('normal: zero AI takeover in normal run', after == before,
           f'incidents {before} -> {after}, api calls today={calls}', {'calls': calls})


def _count_incidents() -> int:
    path = ROOT / 'log' / 'ai_recovery' / 'incidents.jsonl'
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding='utf-8').splitlines() if line.strip())


def _budget_calls() -> int:
    path = ROOT / 'log' / 'ai_recovery' / 'budget_oas3.json'
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text(encoding='utf-8')).get('calls_today', 0))
    except (OSError, ValueError):
        return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    command = argv[0]
    try:
        if command == 'state':
            scenario_state()
        elif command == 'popup':
            scenario_popup()
        elif command == 'adb':
            scenario_adb()
        elif command == 'screenshot':
            scenario_screenshot()
        elif command == 'offline':
            scenario_offline()
        elif command == 'template':
            scenario_template()
        elif command == 'synthetic':
            scenario_synthetic()
        elif command == 'normal':
            task = 'Restart'
            if '--task' in argv:
                task = argv[argv.index('--task') + 1]
            elif len(argv) > 1:
                task = argv[1]
            scenario_normal(task)
        else:
            print(f'unknown scenario: {command}', file=sys.stderr)
            return 2
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        record(command, False, 'unhandled exception (see stdout)')
    save_result()
    return 0 if all(item['ok'] for item in RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

