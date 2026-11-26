# This Python file uses the following encoding: utf-8

"""DeepSeek 视觉分析客户端。

- OpenAI 兼容的 /chat/completions，图像走 image_url(data URL)；
- 结构化输出：response_format=json_object + 本地 JSON Schema 校验；
- 超时、有限重试、失败熔断；失败只影响自愈层，不影响 OAS 主流程；
- 不把 API Key 写进任何日志/异常文本。
"""

from __future__ import annotations

import json
import time
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from typing import Any

import requests

from module.ai_recovery.budget import Usage, estimate_cost
from module.ai_recovery.prompt import SYSTEM_PROMPT, build_user_text
from module.ai_recovery.schema import SchemaError, parse_diagnosis
from module.ai_recovery.types import Diagnosis

# 连续失败达到阈值后熔断一段时间，避免网络故障时每个异常都等一次超时
_BREAKER: dict[str, dict] = {}


class AiError(RuntimeError):
    pass


class AiUnavailable(AiError):
    """API 不可用（网络/鉴权/熔断），调用方应退回本地恢复。"""


class AiTimeout(AiUnavailable):
    pass


class AiBadResponse(AiError):
    """返回 200 但内容非法。"""


@dataclass
class AnalyzeResult:
    diagnosis: Diagnosis
    usage: Usage
    text: str = ''
    latency_s: float = 0.0
    payload: dict = field(default_factory=dict)


class DeepSeekClient:
    def __init__(self, cfg, *, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self.base_url = str(cfg.api.base_url).rstrip('/')
        self.model = cfg.api.model
        self._breaker_key = f'{self.base_url}|{self.model}'
        self.request_guard = None

    # ---------- 对外 ----------

    def analyze(self, image_jpeg: bytes, context: dict, *, effort: str | None = None) -> AnalyzeResult:
        api_key = self.cfg.api_key or self.cfg.resolve_api_key()
        if not api_key:
            raise AiUnavailable('missing API key (env DEEPSEEK_API_KEY or config/ai_recovery.key)')
        self._check_breaker()

        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': build_user_text(context)},
                        {
                            'type': 'image_url',
                            'image_url': {'url': 'data:image/jpeg;base64,'
                                                 + _b64(image_jpeg)},
                        },
                    ],
                },
            ],
            'response_format': {'type': 'json_object'},
            'max_tokens': int(self.cfg.api.max_tokens),
            'temperature': float(self.cfg.api.temperature),
            'reasoning_effort': effort or self.cfg.api.reasoning_effort,
        }
        if (urlsplit(self.base_url).hostname or '').lower() == 'api.deepseek.com':
            payload['thinking'] = {'type': self.cfg.api.thinking}
        started = time.time()
        last_error: Exception | None = None
        corrected = False
        attempt = 0
        while attempt <= int(self.cfg.api.max_retries):
            if self.request_guard is not None:
                allowed, reason = self.request_guard.reserve(model=self.model)
                if not allowed:
                    raise AiUnavailable(f'request budget exhausted: {reason}')
            try:
                data = self._post(payload, api_key)
                text = _extract_content(data)
                parsed = _loads_json(text)
                try:
                    diagnosis = parse_diagnosis(parsed, max_actions=self.cfg.safety.max_actions)
                except SchemaError as error:
                    raise AiBadResponse(f'schema: {error}') from None
                usage = estimate_cost(data.get('usage') or {}, self.cfg)
                usage.model = str(data.get('model') or self.model)
                self._record_success()
                return AnalyzeResult(diagnosis=diagnosis, usage=usage, text=text,
                                     latency_s=time.time() - started, payload=data)
            except AiBadResponse as error:
                self._record_failure()
                if not corrected and str(error).startswith(('empty content', 'truncated content')):
                    corrected = True
                    # JSON mode can mask a malformed result as whitespace. Retry
                    # once without that transport constraint; local schema checks
                    # remain mandatory, and the shared request budget still applies.
                    payload.pop('response_format', None)
                    if (urlsplit(self.base_url).hostname or '').lower() == 'api.deepseek.com':
                        payload['thinking'] = {'type': 'disabled'}
                    payload['messages'].append({'role':'user','content':
                        '上次没有得到完整正文。请直接输出最短的完整 JSON；保留全部必填字段，actions 可为空，禁止解释。'})
                    continue
                raise
            except (requests.Timeout, requests.ConnectionError, AiTimeout) as error:
                last_error = error
                self._record_failure()
            except requests.HTTPError as error:
                status = getattr(error.response, 'status_code', 0)
                if status in (400, 401, 403, 404):
                    # 参数/鉴权问题重试没有意义
                    self._record_failure()
                    raise AiUnavailable(f'http {status}: {_safe_error(error)}') from None
                last_error = error
                self._record_failure()
            if attempt < int(self.cfg.api.max_retries):
                time.sleep(min(4.0, 1.0 * (attempt + 1)))
            attempt += 1
        if isinstance(last_error, (requests.Timeout, AiTimeout)):
            raise AiTimeout(str(last_error) or 'timeout')
        raise AiUnavailable(f'request failed after retries: {last_error}')

    def healthy(self) -> bool:
        return not self._breaker_open()

    # ---------- 内部 ----------

    def _post(self, payload: dict, api_key: str) -> dict:
        url = f'{self.base_url}/chat/completions'
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
        timeout = (min(20.0, float(self.cfg.api.timeout_s)),
                   float(self.cfg.api.timeout_s))
        try:
            response = self.session.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.Timeout as error:
            raise AiTimeout(f'timeout after {self.cfg.api.timeout_s}s') from error
        except requests.RequestException as error:
            raise AiUnavailable(_safe_error(error)) from None
        if response.status_code >= 400:
            # 错误体可能含提示但不含密钥；仍做长度截断
            body = (response.text or '')[:300]
            error = requests.HTTPError(f'{response.status_code} {body}', response=response)
            raise error
        try:
            data = response.json()
        except ValueError as error:
            raise AiBadResponse('response is not json') from error
        if not isinstance(data, dict):
            raise AiBadResponse('response root is not object')
        return data

    def _check_breaker(self) -> None:
        if self._breaker_open():
            remaining = int(_BREAKER[self._breaker_key]['until'] - time.time())
            raise AiUnavailable(f'circuit open, retry in {max(0, remaining)}s')

    def _breaker_open(self) -> bool:
        state = _BREAKER.get(self._breaker_key)
        return bool(state and state.get('failures', 0) >= 3 and state.get('until', 0) > time.time())

    def _record_failure(self) -> None:
        state = _BREAKER.setdefault(self._breaker_key, {'failures': 0, 'until': 0})
        state['failures'] = int(state.get('failures', 0)) + 1
        if state['failures'] >= 3:
            state['until'] = time.time() + 600

    def _record_success(self) -> None:
        _BREAKER.pop(self._breaker_key, None)


def _extract_content(data: dict) -> str:
    choices = data.get('choices') or []
    if not choices:
        raise AiBadResponse('empty choices')
    message = choices[0].get('message') or {}
    content = message.get('content')
    if isinstance(content, list):
        # 某些兼容层返回分段内容
        content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        finish = str(choices[0].get('finish_reason') or 'unknown')[:24]
        thinking = bool(message.get('reasoning_content'))
        raise AiBadResponse(f'empty content (finish={finish}, reasoning_present={thinking})')
    if choices[0].get('finish_reason') == 'length':
        raise AiBadResponse('truncated content (finish=length)')
    return content.strip()


def _loads_json(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith('```'):
        cleaned = cleaned.split('\n', 1)[-1]
        if cleaned.rstrip().endswith('```'):
            cleaned = cleaned.rstrip()[:-3]
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        return json.loads(cleaned)
    except ValueError as error:
        raise AiBadResponse(f'invalid json: {error}') from error


def _b64(raw: bytes) -> str:
    import base64
    return base64.b64encode(raw).decode('ascii')


def _safe_error(error: Exception) -> str:
    """异常文本可能含 URL/状态；去掉换行并截断，绝不包含请求头。"""
    text = str(error).replace('\n', ' ').strip()
    return text[:240]
