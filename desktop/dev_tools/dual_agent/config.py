# This Python file uses the following encoding: utf-8

"""双 Agent 自动开发系统的配置加载。

要点：
- 配置来自 `config.json`（与本文件同目录）；
- Claude Code CLI 需要 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL，
  它们可能只存在于**用户级环境变量**（setx 写入），当前进程没有继承。
  这里用 winreg 读用户级变量补进子进程环境，**绝不打印明文**；
- 预算表是保守估计（宁可提前停，不可超支）。
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / 'config.json'
STATE_DIR = BASE_DIR / 'state'

ANTHROPIC_KEYS = (
    'ANTHROPIC_AUTH_TOKEN',
    'ANTHROPIC_BASE_URL',
    'ANTHROPIC_MODEL',
    'ANTHROPIC_DEFAULT_HAIKU_MODEL',
    'ANTHROPIC_DEFAULT_SONNET_MODEL',
    'ANTHROPIC_DEFAULT_OPUS_MODEL',
    'ANTHROPIC_API_KEY',
)


@dataclass
class Config:
    raw: dict
    project_root: Path
    config_path: Path
    state_dir: Path
    log_dir: Path
    prompt_dir: Path = field(default=BASE_DIR / 'roles')

    @property
    def budget(self) -> dict:
        return self.raw.get('budget', {})

    @property
    def limits(self) -> dict:
        return self.raw.get('limits', {})

    @property
    def safety(self) -> dict:
        return self.raw.get('safety', {})

    @property
    def python(self) -> Path:
        return self.project_root / self.raw['toolkit_python']

    def model_for(self, role: str) -> str:
        return str(self.raw['models'][role])

    def tools_for(self, role: str) -> str:
        return str(self.raw[f'{role}_tools'])

    def limit(self, key: str, fallback):
        value = self.limits.get(key, fallback)
        return value if value is not None else fallback

    def price_per_mtok(self) -> dict:
        return self.budget.get('price_cny_per_mtok', {
            'input': 4.0, 'cache_read': 1.0, 'output': 16.0,
        })

    # ------------------------------------------------------------ v2 agents
    # v2：两个角色可以走**不同的 CLI / 供应商**（A=真 Claude，B=DeepSeek）。
    # 没有 `agents` 段时退回 v1 行为（同一个 claude CLI + models/agent_x_tools）。
    def agent_profile(self, role: str) -> dict:
        return dict((self.raw.get('agents') or {}).get(role) or {})

    def provider_for(self, role: str) -> str:
        return str(self.agent_profile(role).get('provider') or 'deepseek')

    def cli_for(self, role: str) -> str:
        configured = str(self.agent_profile(role).get('cli') or '')
        if configured:
            return os.path.expandvars(configured)
        return find_claude_cli(str(self.raw.get('claude_cli') or ''))

    def model_for_purpose(self, role: str, purpose: str) -> str:
        models = self.agent_profile(role).get('models') or {}
        if purpose in models:
            return str(models[purpose])
        if models:
            return str(next(iter(models.values())))
        return self.model_for(role)

    def tools_for_purpose(self, role: str, purpose: str) -> str:
        tools = self.agent_profile(role).get('tools') or {}
        if purpose in tools:
            return str(tools[purpose])
        return self.tools_for(role)

    def turns_for_purpose(self, role: str, purpose: str) -> int:
        turns = self.agent_profile(role).get('max_turns') or {}
        if purpose in turns:
            return int(turns[purpose])
        return int(self.limit(f'{role}_max_turns', 12))

    @property
    def pricing(self) -> dict:
        return self.raw.get('pricing') or {}

    def usd_to_cny(self) -> float:
        return float(self.pricing.get('usd_to_cny', 7.25))

    def provider_caps(self, provider: str) -> dict:
        return dict((self.budget.get('providers') or {}).get(provider) or {})


def _read_user_env(name: str) -> str:
    """读用户级环境变量（setx 写入的那份），失败返回空串。"""
    value = os.environ.get(name)
    if value:
        return value
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value or '')
    except (OSError, ImportError):
        return ''


def agent_env() -> dict:
    """给 Claude Code 子进程准备环境；缺失的 anthropic 变量从用户级补。"""
    env = dict(os.environ)
    for name in ANTHROPIC_KEYS:
        if not env.get(name):
            value = _read_user_env(name)
            if value:
                env[name] = value
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    return env


def masked_env_report() -> dict:
    """只报告变量是否存在/长度，绝不输出明文。"""
    report = {}
    for name in ANTHROPIC_KEYS:
        value = os.environ.get(name) or _read_user_env(name)
        report[name] = {'present': bool(value), 'length': len(value or '')}
    return report


def find_claude_cli(configured: str = '') -> str:
    if configured:
        return configured
    candidates = [
        Path(os.environ.get('LOCALAPPDATA', '')) / 'pnpm' / 'bin' / 'claude.cmd',
        Path(os.environ.get('APPDATA', '')) / 'npm' / 'claude.cmd',
    ]
    which = shutil.which('claude')
    if which:
        candidates.insert(0, Path(which))
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return ''


def load_config(path: Path | None = None) -> Config:
    config_path = Path(path or CONFIG_PATH)
    raw = json.loads(config_path.read_text(encoding='utf-8'))
    project_root = Path(raw['project_root'])
    if not project_root.exists():
        raise SystemExit(f'project_root 不存在: {project_root}')
    config = Config(
        raw=raw,
        project_root=project_root,
        config_path=config_path,
        state_dir=STATE_DIR,
        log_dir=BASE_DIR / 'logs',
    )
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    return config
