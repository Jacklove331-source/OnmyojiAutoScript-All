# This Python file uses the following encoding: utf-8

"""安全边界：命令白/黑名单、路径保护、Git 检查点、单实机进程约束。

对应「我睡觉期间的安全要求」：
1. 只有 Agent B 能改业务源码 → 由调用方给 B 用 acceptEdits、给 A 用 plan；
2. 同一时间最多一个 Chess 实机测试进程 → `chess_running()` + `claim_chess_slot()`；
3. 高风险修改前保留 Git 恢复点 → `git_checkpoint()`；
4/5. 禁止删除重要文件、覆盖用户配置 → 路径黑名单 + shell 黑名单；
6. 系统级高风险操作 → 黑名单（shutdown/reg delete/format…）。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from config import Config


class SafetyError(RuntimeError):
    pass


def _matches(text: str, patterns: list[str]) -> str | None:
    lowered = text.lower()
    for pattern in patterns:
        if pattern.lower() in lowered:
            return pattern
    return None


def check_git_argv(config: Config, argv: list[str]) -> None:
    joined = ' '.join(argv)
    hit = _matches(joined, list(config.safety.get('forbidden_git', [])))
    if hit:
        raise SafetyError(f'禁止的 git 操作: {hit}')


def check_shell(command: str, config: Config) -> None:
    hit = _matches(command, list(config.safety.get('forbidden_shell', [])))
    if hit:
        raise SafetyError(f'禁止的 shell 片段: {hit}')


def check_write_path(config: Config, path: Path) -> None:
    text = str(path).replace('\\', '/').lower()
    for forbidden in config.safety.get('forbidden_paths', []):
        if forbidden.lower().replace('\\', '/') in text:
            raise SafetyError(f'禁止写入受保护路径: {forbidden}')


def run_guarded(config: Config, args: list[str], timeout: int = 600,
                cwd: Path | None = None) -> tuple[int, str]:
    """执行调度器自己的命令（测试/校验），带黑白名单与超时。"""
    if not args:
        raise SafetyError('空命令')
    command = ' '.join(args)
    try:
        if args[0] == 'git':
            check_git_argv(config, args[1:])
        check_shell(command, config)
    except SafetyError as error:
        return 126, f'<blocked: {error}>'
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd or config.project_root),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
        return completed.returncode, (completed.stdout or '') + (
            completed.stderr or '')
    except subprocess.TimeoutExpired:
        return 124, f'<timeout after {timeout}s>'
    except OSError as error:
        return 127, f'<exec failed: {error}>'


def git_checkpoint(config: Config, state, message: str,
                   tag: str | None = None) -> dict:
    """提交当前改动（如果有），可打 tag；返回 {committed, sha, tag}。"""
    info = {'committed': False, 'sha': '', 'tag': ''}
    status = subprocess.run(
        ['git', 'status', '--porcelain'], cwd=str(config.project_root),
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    ).stdout.strip()
    if status and config.safety.get('allow_git_commit', True):
        run_guarded(config, ['git', 'add', '-A'], timeout=120)
        code, out = run_guarded(
            config, ['git', 'commit', '-m', message], timeout=180
        )
        info['committed'] = code == 0
        if code != 0:
            state.event('git_commit_failed', detail=out[-800:])
    sha = run_guarded(config, ['git', 'rev-parse', '--short', 'HEAD'])[1].strip()
    info['sha'] = sha[:12]
    if tag:
        code, out = run_guarded(
            config, ['git', 'tag', '-f', tag], timeout=60
        )
        info['tag'] = tag if code == 0 else ''
        if code != 0:
            state.event('git_tag_failed', detail=out[-400:])
    state.event('git_checkpoint', **info)
    state.set_meta('last_checkpoint', {'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
                                       **info})
    return info


def _list_python_processes() -> list[dict]:
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=60,
        )
        text = (completed.stdout or '').strip()
        if not text:
            return []
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        return [
            {'pid': int(item.get('ProcessId') or 0),
             'cmd': str(item.get('CommandLine') or '')}
            for item in data
        ]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


CHESS_MARKERS = ('chess_series_test', 'chess_series_run', 'Chess')


def chess_running() -> list[dict]:
    """返回正在跑的棋局相关进程（用于「同时只允许一个实机进程」）。"""
    hits = []
    for item in _list_python_processes():
        cmd = item['cmd']
        if 'dev_tools' in cmd and any(
            marker.lower() in cmd.lower() for marker in CHESS_MARKERS
        ):
            hits.append(item)
    return hits


def claim_chess_slot(config: Config, state) -> bool:
    """申请实机测试名额：已有进程或超次数则拒绝。"""
    running = chess_running()
    if running:
        state.event('chess_slot_busy', pids=[item['pid'] for item in running])
        return False
    used = int(state.get_meta('chess_runs_started', 0) or 0)
    limit = int(config.safety.get('max_chess_runs_per_session', 2))
    if used >= limit:
        state.event('chess_slot_exhausted', used=used, limit=limit)
        return False
    state.bump_meta('chess_runs_started', 1)
    state.event('chess_slot_claimed', used=used + 1, limit=limit)
    return True
