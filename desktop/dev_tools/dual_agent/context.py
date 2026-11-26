# This Python file uses the following encoding: utf-8

"""给 Agent 组装**精简上下文**，避免每轮把整个项目塞进模型。

原则：
- 只给当前任务相关的片段（文件头尾、diff、命中的日志行）；
- 全部有硬上限（字符数），超了截断并标注；
- 已验证结论放 digest，不重复贴原文。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

SKIP_DIRS = {'.git', 'log', '__pycache__', 'node_modules', '.venv'}


def git(project_root: Path, *args: str, timeout: int = 60) -> str:
    try:
        completed = subprocess.run(
            ['git', *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
        if completed.returncode != 0:
            return (completed.stdout or '') + (completed.stderr or '')
        return completed.stdout or ''
    except (OSError, subprocess.SubprocessError) as error:
        return f'<git failed: {error}>'


def read_slice(path: Path, max_chars: int = 6000) -> str:
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError as error:
        return f'<read failed {path}: {error}>'
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    return (
        text[:head]
        + f'\n... <truncated {len(text) - max_chars} chars> ...\n'
        + text[-tail:]
    )


DIFF_IGNORE_PATHS = (
    ':(exclude)dev_tools/dual_agent/state',
    ':(exclude)dev_tools/dual_agent/logs',
    ':(exclude)log',
)


def diff_text(project_root: Path, max_chars: int = 12000,
              staged: bool = False, since: str | None = None) -> str:
    """默认只看未提交改动；给了 since（任务检查点 sha）就看该提交以来的全部改动。

    为什么需要：外部自动提交（autosave）可能把 Agent B 的改动提前提交掉，
    只看 `git diff` 会误判成「B 什么都没改」。
    """
    args = ['diff', '--unified=2', '--', '.', *DIFF_IGNORE_PATHS]
    if staged:
        args.insert(1, '--cached')
    if since:
        args.append(str(since))
    text = git(project_root, *args)
    if len(text) > max_chars:
        return text[:max_chars] + f'\n... <diff truncated, {len(text)} chars total>'
    return text


def diff_stat(project_root: Path, since: str | None = None) -> str:
    args = ['diff', '--stat', '--', '.', *DIFF_IGNORE_PATHS]
    if since:
        args.append(str(since))
    return git(project_root, *args)[-2500:]


def changed_files(project_root: Path) -> list[str]:
    text = git(project_root, 'status', '--porcelain')
    files = []
    for line in text.splitlines():
        line = line.rstrip()
        if len(line) > 3:
            files.append(line[3:].strip())
    return files


def grep_repo(project_root: Path, pattern: str, include: str = '*.py',
              max_matches: int = 40, max_chars: int = 4000) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as error:
        return f'<bad pattern: {error}>'
    matches: list[str] = []
    total = 0
    for path in project_root.rglob(include):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        try:
            for number, line in enumerate(
                path.read_text(encoding='utf-8', errors='replace').splitlines()
            ):
                if regex.search(line):
                    rel = path.relative_to(project_root)
                    snippet = f'{rel}:{number + 1}: {line.strip()[:160]}'
                    matches.append(snippet)
                    total += len(snippet)
                    if len(matches) >= max_matches or total >= max_chars:
                        return '\n'.join(matches)
        except OSError:
            continue
    return '\n'.join(matches)


def log_extracts(log_path: Path, patterns: list[str], context: int = 0,
                 max_lines: int = 60) -> str:
    """从大日志里只挑命中的行（配合 patterns），避免整份日志进模型。"""
    if not log_path or not log_path.exists():
        return f'<no log: {log_path}>'
    try:
        raw = log_path.read_bytes()
    except OSError as error:
        return f'<log read failed: {error}>'
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        text = raw.decode('utf-16', errors='replace')
    else:
        text = raw.decode('utf-8', errors='replace')
    regexes = [re.compile(pattern) for pattern in patterns]
    lines = text.splitlines()
    picked: list[str] = []
    for index, line in enumerate(lines):
        if any(regex.search(line) for regex in regexes):
            low = max(0, index - context)
            high = min(len(lines), index + context + 1)
            for extra in lines[low:high]:
                picked.append(extra.strip()[:180])
            if len(picked) >= max_lines:
                break
    return '\n'.join(picked[-max_lines:])


def latest_chess_log(project_root: Path) -> Path | None:
    logs = sorted(
        Path('D:/3D/YYS2/_recovery_070208').glob('chess_*.log'),
        key=lambda path: path.stat().st_mtime,
    )
    return logs[-1] if logs else None


def project_digest(config, state, extra: str = '') -> str:
    """一段紧凑的项目现状摘要（不贴源码）。"""
    root = config.project_root
    branch = git(root, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    head = git(root, 'log', '--oneline', '-5').strip()
    changed = changed_files(root)
    totals = state.run_totals()
    counts = state.task_counts()
    acceptance = state.acceptance_rows()
    passed = [row['ac_id'] for row in acceptance if row['ok']]
    failed = [row['ac_id'] for row in acceptance if not row['ok']]
    failed_detail = [
        f"  - {row['ac_id']} {row['title']}: {str(row['detail'])[:220]}"
        for row in acceptance if not row['ok']
    ]
    knowledge_limit = 12
    try:
        knowledge_limit = int(config.limit('knowledge_lines_in_digest', 12))
    except (AttributeError, TypeError, ValueError):
        pass
    knowledge = state.knowledge(knowledge_limit) if hasattr(state, 'knowledge') else []
    log = latest_chess_log(root)
    log_tail = ''
    if log is not None:
        log_tail = log_extracts(
            log,
            [r'ChessOutcome', r'ChessMatch\] end', r'game-over rank'],
            max_lines=6,
        )
    parts = [
        f'## 项目现状',
        f'- 分支: {branch}',
        f'- 最近提交:\n{head}',
        f'- 未提交改动: {len(changed)} 个文件' + (
            f' ({", ".join(changed[:8])})' if changed else ''),
        f'- 任务: {json.dumps(counts, ensure_ascii=False)}',
        f'- 预算: calls={totals["calls"]} cost=¥{totals["cost_cny"]}',
        f'- 验收通过: {", ".join(passed) or "无"}',
        f'- 验收未过: {", ".join(failed) or "无"}',
    ]
    if failed_detail:
        parts.append('- 未过验收项明细:\n' + '\n'.join(failed_detail))
    if knowledge:
        parts.append('- 已验证结论（缓存，不要重复分析）:\n' + '\n'.join(
            f"  - [{item.get('verdict')}] {item.get('task_key')}: {item.get('note')}"
            for item in knowledge))
    if log is not None:
        parts.append(f'- 最近棋局日志: {log.name}\n{log_tail}')
    if extra:
        parts.append(extra)
    return '\n'.join(parts)
