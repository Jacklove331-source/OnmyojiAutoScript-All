# This Python file uses the following encoding: utf-8

"""AI 自愈层命令行工具（不自动操作游戏，除显式 live 命令）。

用法：
    .\\toolkit\\python.exe dev_tools\\ai_recovery_cli.py status
    .\\toolkit\\python.exe dev_tools\\ai_recovery_cli.py health [oas3]
    .\\toolkit\\python.exe dev_tools\\ai_recovery_cli.py repairs [oas3]
    .\\toolkit\\python.exe dev_tools\\ai_recovery_cli.py key --from-credentials
    .\\toolkit\\python.exe dev_tools\\ai_recovery_cli.py analyze <png> [--mode observe|auto]
    .\\toolkit\\python.exe dev_tools\\ai_recovery_cli.py budget
    .\\toolkit\\python.exe dev_tools\\ai_recovery_cli.py templates list
    .\\toolkit\\python.exe dev_tools\\ai_recovery_cli.py templates apply <task> <name> <source.png>
    .\\toolkit\\python.exe dev_tools\\ai_recovery_cli.py templates rollback <task> <name>
    .\\toolkit\\python.exe dev_tools\\ai_recovery_cli.py capture [--config oas3] [png]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.ai_recovery.budget import BudgetGuard
from module.ai_recovery.config import load_config, write_default_config, config_path
from module.ai_recovery.evidence import EvidenceStore
from module.ai_recovery.imageutil import dhash, load_image, save_png
from module.ai_recovery.kb import KnowledgeBase
from module.ai_recovery.templates import TemplateLibrary
from module.ai_recovery.types import Anomaly


def cmd_status(args: list[str]) -> int:
    cfg = load_config(config_name=args[0] if args else 'oas3')
    guard = BudgetGuard(cfg, config_name=cfg.config_name)
    kb = KnowledgeBase(cfg, config_name=cfg.config_name)
    templates = TemplateLibrary(cfg, config_name=cfg.config_name)
    print(json.dumps({
        'config': cfg.config_name,
        'config_file': cfg.source_path,
        'enabled': cfg.enabled,
        'mode': cfg.mode,
        'model': cfg.api.model,
        'api_key_present': bool(cfg.api_key or cfg.resolve_api_key()),
        'budget': guard.snapshot(),
        'knowledge': kb.stats(),
        'templates': templates.list_all(),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_key(args: list[str]) -> int:
    from_credentials = '--from-credentials' in args
    target = ROOT / 'config' / 'ai_recovery.key'
    if not from_credentials:
        print(json.dumps({'key_file': str(target), 'exists': target.exists(),
                          'env_present': bool(os.environ.get('DEEPSEEK_API_KEY'))}))
        return 0
    import yaml
    source = Path(os.path.expanduser('~/.dsh/.credentials.yaml'))
    if not source.exists():
        print('credentials file not found', file=sys.stderr)
        return 2
    data = yaml.safe_load(source.read_text(encoding='utf-8')) or {}
    key = str((data.get('refs') or {}).get('DEEPSEEK_API_KEY') or '').strip()
    if not key:
        print('DEEPSEEK_API_KEY missing in credentials', file=sys.stderr)
        return 2
    target.write_text(key + '\n', encoding='utf-8')
    print(json.dumps({'written': str(target), 'chars': len(key)}))
    return 0


def cmd_analyze(args: list[str]) -> int:
    if not args:
        print('usage: analyze <png> [--mode observe|auto]', file=sys.stderr)
        return 2
    image_path = Path(args[0])
    mode = 'observe'
    if '--mode' in args:
        mode = args[args.index('--mode') + 1]
    cfg = load_config(config_name='oas3', force=True)
    cfg.enabled = True
    cfg.mode = mode
    from module.ai_recovery.client import DeepSeekClient
    from module.ai_recovery.imageutil import encode_jpeg
    from module.ai_recovery.prompt import build_user_text
    image = load_image(image_path)
    context = {
        'task': 'CLI', 'config': cfg.config_name, 'mode': mode,
        'anomaly': 'manual_analysis', 'anomaly_detail': '人工指定的离线截图',
        'page': '', 'page_known_by_oas': False,
        'resolution': list(image.shape[1::-1]),
        'known_pages': [], 'recent_actions': [], 'frame_history': [],
        'attempted_recoveries': [], 'error': '',
    }
    jpeg = encode_jpeg(image, max_side=cfg.api.image_max_side, quality=cfg.api.image_quality,
                       redact_top_px=cfg.api.redact_top_px)
    client = DeepSeekClient(cfg)
    guard = BudgetGuard(cfg, config_name=cfg.config_name)
    guard.start_incident()
    client.request_guard = guard
    result = client.analyze(jpeg, context)
    guard.commit(result.usage)
    print(json.dumps({
        'diagnosis': result.diagnosis.to_dict(),
        'usage': result.usage.to_dict(),
        'latency_s': round(result.latency_s, 2),
        'context_preview': build_user_text(context)[:400],
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_budget(args: list[str]) -> int:
    cfg = load_config(config_name=args[0] if args else 'oas3')
    guard = BudgetGuard(cfg, config_name=cfg.config_name)
    print(json.dumps(guard.snapshot(), ensure_ascii=False, indent=2))
    return 0


def cmd_templates(args: list[str]) -> int:
    if not args:
        print('usage: templates list|apply|rollback ...', file=sys.stderr)
        return 2
    cfg = load_config(config_name='oas3')
    lib = TemplateLibrary(cfg, config_name=cfg.config_name)
    action = args[0]
    if action == 'list':
        print(json.dumps(lib.list_all(), ensure_ascii=False, indent=2))
        return 0
    if action == 'apply':
        task, name, source = args[1], args[2], args[3]
        record = lib.apply_to_source(task, name, source)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0
    if action == 'rollback':
        task, name = args[1], args[2]
        version = lib.rollback(task, name)
        print(json.dumps({'task': task, 'name': name, 'active_version': version}))
        return 0
    print('unknown templates action', file=sys.stderr)
    return 2


def cmd_capture(args: list[str]) -> int:
    config_name = 'oas3'
    if '--config' in args:
        config_name = args[args.index('--config') + 1]
    output = Path(args[0]) if args and not args[0].startswith('--') else ROOT / 'log' / 'ai_recovery' / 'capture.png'
    from module.config.config import Config
    from module.device.device import Device
    config = Config(config_name)
    device = Device(config)
    device.screenshot()
    save_png(output, device.image)
    print(json.dumps({'captured': str(output), 'size': list(device.image.shape[:2][::-1]),
                      'dhash': dhash(device.image)}))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    command, rest = argv[0], argv[1:]
    if command == 'code-repair':
        from module.ai_recovery.repair_worker import RepairEngine
        from module.ai_recovery.repair_context import redact
        action = rest[0] if rest else 'status'
        account = rest[1] if len(rest)>1 else 'oas1'
        cfg = load_config(config_name=account,force=True)
        engine = RepairEngine()
        if action == 'status':
            result = engine.store.status(account)
        elif action == 'run-once':
            result = engine.tick(account)
        elif action == 'rollback' and len(rest)>2:
            job = engine.store.get(rest[2])
            if not job or job['account']!=account:raise ValueError('repair job not found')
            engine.store.update(job['id'],'rollback_pending','用户请求回滚')
            result = {'rolled_back':engine.deployer.rollback(job)}
        elif action == 'doctor':
            from module.ai_recovery.client import DeepSeekClient
            import numpy as np
            from module.ai_recovery.imageutil import encode_jpeg
            client = DeepSeekClient(cfg)
            guard = BudgetGuard(cfg,config_name=account);guard.start_incident();client.request_guard=guard
            analyzed = client.analyze(encode_jpeg(np.zeros((360,640,3),dtype=np.uint8)),{
                'task':'API自检','anomaly':'manual_analysis','error':'这是一张自检纯黑图。请返回合法JSON，needs_human=true,actions=[]。'})
            guard.commit(analyzed.usage)
            result = {'api_responded':True,'structured_output_valid':True,'model':cfg.api.model,
                      'latency_s':round(analyzed.latency_s,2),'thinking':cfg.api.thinking,
                      'actions_executed':0,'usage':analyzed.usage.to_dict()}
        else:
            raise ValueError('usage: code-repair status|run-once|doctor [oas1] or rollback oas1 <job>')
        print(redact(json.dumps(result,ensure_ascii=False,indent=2),[cfg.api_key or cfg.resolve_api_key()]))
        return 0
    if command == 'status':
        return cmd_status(rest)
    if command in ('health', 'repairs'):
        from module.ai_recovery.reporting import health_report, pending_repairs
        cfg = load_config(config_name=rest[0] if rest else 'oas3')
        result = health_report(cfg) if command == 'health' else pending_repairs(cfg.config_name)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if command == 'learning':
        from module.ai_recovery.learning import LearningMemory
        from module.ai_recovery.reporting import log_root
        cfg = load_config(config_name=rest[0] if rest else 'oas3')
        print(json.dumps(LearningMemory(cfg,log_root()).stats(), ensure_ascii=False, indent=2))
        return 0
    if command == 'key':
        return cmd_key(rest)
    if command == 'analyze':
        return cmd_analyze(rest)
    if command == 'budget':
        return cmd_budget(rest)
    if command == 'templates':
        return cmd_templates(rest)
    if command == 'capture':
        return cmd_capture(rest)
    if command == 'init':
        path = write_default_config()
        print(json.dumps({'config': str(path)}))
        return 0
    print(f'unknown command: {command}', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
