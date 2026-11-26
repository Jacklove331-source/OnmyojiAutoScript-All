# This Python file uses the following encoding: utf-8

"""把命令行 Agent（Claude Code headless）当成「带工具的 worker」来调用。

v2（2026-10-28）：两个角色走**不同供应商**——
- agent_a = 真 Claude（`claude-anthropic.cmd` → api.anthropic.com），规划/审核/疑难；
- agent_b = DeepSeek（`claude.CMD`，用户级 ANTHROPIC_BASE_URL 指向 api.deepseek.com/anthropic），执行。

注意：v1 里两个角色都走 `claude.CMD`，所以所谓「Claude 审核」其实是 DeepSeek。

安全与稳健：
- 提示词走 stdin，避免 Windows 32KB 命令行长度限制；
- 超时用 taskkill /T 杀进程树；
- 结果按 JSON 解析，取 result 文本、usage、permission_denials、total_cost_usd；
- 传输类错误重试，凭证/权限类直接失败；网络/代理类错误标记 error_kind='env'
  （调度器据此进入「环境故障暂停」，而不是把任务判失败）；
- 任何日志都不打印凭证明文。
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field

from config import Config, agent_env


class AgentError(RuntimeError):
    pass


@dataclass
class AgentResult:
    ok: bool
    text: str = ''
    data: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    cost_cny: float = 0.0
    cost_usd: float = 0.0
    error: str = ''
    error_kind: str = ''
    duration_s: float = 0.0
    denials: list = field(default_factory=list)
    terminal_reason: str = ''
    raw_stdout: str = ''
    raw_stderr: str = ''
    provider: str = ''
    model: str = ''
    purpose: str = ''


RETRYABLE_MARKERS = (
    'api_error',
    'overloaded',
    'rate limit',
    'timeout',
    'econnreset',
    'etimedout',
    'network',
    '529',
    '503',
    '502',
    'connection error',
    'fetch failed',
)

# 环境类故障（代理/VPN/网络）：重试后仍失败 → 调度器进入 paused_env，稍后自动恢复
ENV_MARKERS = (
    'econnrefused',
    'enotfound',
    'eai_again',
    'request not allowed',
    'connection error',
    'fetch failed',
    'unable to connect',
    'socket hang up',
    'proxy',
    'network',
    'etimedout',
    'econnreset',
)

FATAL_MARKERS = (
    'not logged in',
    'invalid api key',
    'invalid x-api-key',
    'authentication',
    'permission denied',
    'credit balance',
)


def estimate_cost(usage: dict, prices: dict) -> float:
    """按 token 单价估算（DeepSeek 用 ¥/百万 token）。cache 写入按 input 计。"""
    tokens_input = float(usage.get('input_tokens', 0) or 0)
    tokens_write = float(usage.get('cache_creation_input_tokens', 0) or 0)
    tokens_cached = float(usage.get('cache_read_input_tokens', 0) or 0)
    tokens_output = float(usage.get('output_tokens', 0) or 0)
    cost = (
        (tokens_input + tokens_write) / 1_000_000 * float(prices.get('input', 0))
        + tokens_cached / 1_000_000 * float(prices.get('cache_read', 0))
        + tokens_output / 1_000_000 * float(prices.get('output', 0))
    )
    return round(cost, 6)


def estimate_usd(usage: dict, prices: dict) -> float:
    """Anthropic 兜底估算（只有 CLI 没给 total_cost_usd 时才用）。"""
    cost = (
        float(usage.get('input_tokens', 0) or 0) * float(prices.get('input', 0))
        + float(usage.get('cache_creation_input_tokens', 0) or 0)
        * float(prices.get('cache_write', 0))
        + float(usage.get('cache_read_input_tokens', 0) or 0)
        * float(prices.get('cache_read', 0))
        + float(usage.get('output_tokens', 0) or 0) * float(prices.get('output', 0))
    ) / 1_000_000
    return round(cost, 6)


def classify_error(text: str) -> str:
    lowered = (text or '').lower()
    if any(marker in lowered for marker in FATAL_MARKERS):
        return 'fatal'
    if 'max_turns' in lowered or 'max turns' in lowered:
        return 'max_turns'
    if any(marker in lowered for marker in ENV_MARKERS):
        return 'env'
    if any(marker in lowered for marker in RETRYABLE_MARKERS):
        return 'retryable'
    return 'other'


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ['taskkill', '/PID', str(pid), '/T', '/F'],
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def normalize_usage(payload: dict) -> dict:
    usage = dict(payload.get('usage') or {})
    if not usage.get('input_tokens') and not usage.get('output_tokens'):
        model_usage = payload.get('modelUsage') or {}
        if isinstance(model_usage, dict) and model_usage:
            items = [item for item in model_usage.values() if isinstance(item, dict)]
            usage = {
                'input_tokens': sum(int(item.get('inputTokens') or 0) for item in items),
                'output_tokens': sum(int(item.get('outputTokens') or 0) for item in items),
                'cache_read_input_tokens': sum(
                    int(item.get('cacheReadInputTokens') or 0) for item in items),
                'cache_creation_input_tokens': sum(
                    int(item.get('cacheCreationInputTokens') or 0) for item in items),
            }
    for key in ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
                'cache_creation_input_tokens'):
        try:
            usage[key] = int(usage.get(key) or 0)
        except (TypeError, ValueError):
            usage[key] = 0
    return {key: usage[key] for key in (
        'input_tokens', 'output_tokens', 'cache_read_input_tokens',
        'cache_creation_input_tokens')}


class CliAgent:
    def __init__(self, config: Config):
        self.config = config
        self.env = agent_env()
        self.prices = config.price_per_mtok()
        self.clis = {}
        for role in ('agent_a', 'agent_b'):
            cli = config.cli_for(role)
            if not cli:
                raise AgentError(f'找不到 {role} 的 CLI（请检查 config.json agents.{role}.cli）')
            self.clis[role] = cli
        # 兼容 v1 字段（status/日志里会打印）
        self.cli = self.clis['agent_b']

    def cost_of(self, role: str, payload: dict, usage: dict) -> tuple[float, float]:
        """返回 (cost_cny, cost_usd)。Anthropic 优先用 CLI 报告的真实账单价。"""
        provider = self.config.provider_for(role)
        fx = self.config.usd_to_cny()
        if provider == 'anthropic':
            pricing = self.config.pricing.get('anthropic') or {}
            reported = payload.get('total_cost_usd')
            if pricing.get('use_reported_cost', True) and isinstance(reported, (int, float)):
                usd = float(reported)
            else:
                usd = estimate_usd(usage, pricing.get('usd_per_mtok_fallback') or {
                    'input': 5.0, 'cache_write': 6.25, 'cache_read': 0.5, 'output': 25.0})
            return round(usd * fx, 6), round(usd, 6)
        prices = ((self.config.pricing.get('deepseek') or {}).get('cny_per_mtok')
                  or self.prices)
        cny = estimate_cost(usage, prices)
        return cny, round(cny / fx, 6) if fx else 0.0

    def _command(self, role: str, model: str, max_turns: int, tools: str,
                 mode: str) -> list[str]:
        cmd = [
            self.clis[role],
            '-p',
            '--output-format', 'json',
            '--model', model,
            '--max-turns', str(max_turns),
            '--permission-mode', mode,
        ]
        if tools:
            cmd.extend(['--allowedTools', tools])
        return cmd

    def run(self, prompt: str, role: str, task_key: str = '',
            max_turns: int | None = None, mode: str = 'default',
            retries: int = 2, tools_override: str | None = None,
            prompt_for_retry: str | None = None, purpose: str = '',
            model_override: str = '') -> AgentResult:
        purpose = purpose or ('execute' if role == 'agent_b' else 'review')
        model = model_override or self.config.model_for_purpose(role, purpose)
        turns = int(max_turns or self.config.turns_for_purpose(role, purpose))
        tools = tools_override if tools_override is not None else (
            self.config.tools_for_purpose(role, purpose))
        timeout = int(self.config.limit('call_timeout_sec', 1500))
        provider = self.config.provider_for(role)
        # 按难度给思考预算（Claude Code 支持的 MAX_THINKING_TOKENS 环境变量）：
        # 实测一次常规审核思考输出 23k token（$0.38），审核/规划不需要那么长的推演
        env = self.env
        thinking = (self.config.agent_profile(role).get('thinking_tokens') or {}).get(purpose)
        if thinking:
            env = dict(self.env)
            env['MAX_THINKING_TOKENS'] = str(int(thinking))

        started = time.time()
        last_error = ''
        last_kind = ''
        attempts = retries + 2
        for attempt in range(1, attempts + 1):
            active_prompt = prompt
            forced_json = False
            if attempt > 1 and last_kind == 'max_turns':
                if prompt_for_retry:
                    active_prompt = prompt_for_retry
                turns += int(self.config.limit('max_turns_retry_bonus', 8))
                tools = 'Read'
                forced_json = True
            cmd = self._command(role, model, turns, tools, mode)
            result = self._run_once(role, cmd, active_prompt, timeout, started, env)
            result.provider = provider
            result.model = model
            result.purpose = purpose
            if result.ok:
                return result
            last_error = result.error or 'unknown'
            last_kind = classify_error(last_error + ' ' + result.raw_stderr)
            result.error_kind = last_kind
            if forced_json:
                return result
            if last_kind == 'max_turns':
                continue
            if last_kind in ('fatal', 'other'):
                return result
            if attempt >= attempts:
                return result
            time.sleep(min(60, 8 * attempt))
        return AgentResult(
            ok=False, error=last_error, error_kind=last_kind,
            duration_s=round(time.time() - started, 1),
            provider=provider, model=model, purpose=purpose,
        )

    def _run_once(self, role: str, cmd: list[str], prompt: str, timeout: int,
                  started: float, env: dict | None = None) -> AgentResult:
        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(self.config.project_root),
                env=env or self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
            )
        except OSError as error:
            return AgentResult(ok=False, error=f'spawn failed: {error}',
                               duration_s=round(time.time() - started, 1))
        try:
            stdout, stderr = process.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process.pid)
            try:
                stdout, stderr = process.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                stdout, stderr = '', ''
            return AgentResult(
                ok=False,
                error=f'timeout after {timeout}s',
                duration_s=round(time.time() - started, 1),
                raw_stdout=(stdout or '')[:4000],
                raw_stderr=(stderr or '')[:2000],
            )

        duration = round(time.time() - started, 1)
        payload = self._parse_json(stdout)
        if payload is None:
            return AgentResult(
                ok=False,
                error='unparsable output: ' + ((stderr or stdout or '')[-300:]),
                duration_s=duration,
                raw_stdout=(stdout or '')[:4000],
                raw_stderr=(stderr or '')[:2000],
            )

        usage = normalize_usage(payload)
        is_error = bool(payload.get('is_error'))
        text = str(payload.get('result') or '')
        denials = payload.get('permission_denials') or []
        terminal = str(payload.get('terminal_reason') or payload.get('subtype') or '')
        cost_cny, cost_usd = self.cost_of(role, payload, usage)
        common = dict(
            text=text, data=payload, usage=usage, cost_cny=cost_cny,
            cost_usd=cost_usd, duration_s=duration, denials=denials,
            terminal_reason=terminal, raw_stdout=(stdout or '')[:4000],
            raw_stderr=(stderr or '')[:2000],
        )
        if is_error:
            error = text or terminal or str(payload.get('api_error_status') or '') \
                or 'agent reported error'
            return AgentResult(ok=False, error=error, **common)
        return AgentResult(ok=True, **common)

    @staticmethod
    def _parse_json(stdout: str) -> dict | None:
        text = (stdout or '').strip()
        if not text:
            return None
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith('{'):
                continue
            try:
                return json.loads(line)
            except ValueError:
                continue
        try:
            return json.loads(text)
        except ValueError:
            return None


def extract_json_block(text: str) -> dict | None:
    """从 Agent 的最终回答里抓第一个完整 JSON 对象（允许前后有说明文字）。"""
    if not text:
        return None
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == '{':
            if depth == 0:
                start = index
            depth += 1
        elif char == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = text[start:index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except ValueError:
                        start = -1
                        continue
                    if isinstance(parsed, dict):
                        return parsed
                    start = -1
    return None
