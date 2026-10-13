import asyncio
import hashlib
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
import httpx
from app.config import Settings, REQUEST_LIMITS
from app.schemas import Usage, SearchHit, Source, ModelReply


class BudgetStop(Exception):
    pass


class ProviderError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


@dataclass
class RunControl:
    deadline: float
    cancel: asyncio.Event
    usage: Usage

    def check(self) -> float:
        if self.cancel.is_set():
            raise BudgetStop('用户已停止任务')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BudgetStop('已达到运行时间上限')
        return remaining

    def before_request(self, kind: str) -> float:
        remaining = self.check()
        field = {'model':'model_requests','search':'search_requests','extract':'extract_requests'}[kind]
        limit = REQUEST_LIMITS[field]
        if getattr(self.usage, field) >= limit:
            raise BudgetStop('已达到服务调用次数上限')
        setattr(self.usage, field, getattr(self.usage, field) + 1)
        return remaining


async def validate_public_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        if parsed.port not in (None, 80, 443):
            raise ValueError()
        host = parsed.hostname.rstrip('.').lower()
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            raise ValueError()
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            records = await asyncio.to_thread(socket.getaddrinfo, host, parsed.port or 443, 0, socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(r[4][0]) for r in records]
        if not addresses or any(not addr.is_global for addr in addresses):
            raise ValueError()
    except (ValueError, OSError):
        raise ProviderError('unsafe_url', '该网页地址不是可读取的公开地址') from None


class Providers:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client

    async def _post(self, url: str, key: str, payload: dict, kind: str, control: RunControl) -> dict:
        for attempt in range(2):
            if self.settings.cloud_online and not self.settings.cloud_ready():
                raise BudgetStop('本轮联网使用时间已结束或配置不可用，停止后续付费调用。')
            remaining = control.before_request(kind)
            if self.settings.cloud_online:
                remaining = min(remaining,self.settings.cloud_seconds_left())
            timeout = min(45 if kind == 'model' else 20, remaining)
            try:
                async with asyncio.timeout(timeout):
                    async with self.client.stream('POST', url, headers={'Authorization': f'Bearer {key}'},
                                                  json=payload, timeout=timeout, follow_redirects=False) as response:
                        if response.status_code >= 400 or 300 <= response.status_code < 400:
                            code = response.status_code
                            if code == 400 and kind == 'model':
                                error_body = bytearray()
                                async for chunk in response.aiter_bytes():
                                    error_body.extend(chunk)
                                    if len(error_body) > 16384:
                                        break
                                try:
                                    data = json.loads(error_body[:16384])
                                    detail = data.get('error', {}) if isinstance(data, dict) else {}
                                except (ValueError, UnicodeError):
                                    detail = {}
                                if isinstance(detail, dict) and detail.get('code') == 'Arrearage':
                                    raise ProviderError('billing', '百炼账户计费不可用（Arrearage）。请在阿里云费用控制台检查欠费、余额及额度；处理后再重试。')
                            retryable = code in (429, 500, 502, 503, 504)
                            message = '服务鉴权失败，请检查密钥、模型与地域配置' if code in (401, 403) else f'服务请求失败（HTTP {code}），请稍后重试或检查服务额度'
                            raise ProviderError('authentication' if code in (401, 403) else 'http_error', message, retryable)
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            control.check()
                            size += len(chunk)
                            if size > 2 * 1024 * 1024:
                                raise ProviderError('response_too_large', '服务返回内容超过大小上限')
                            chunks.append(chunk)
                        result = json.loads(b''.join(chunks))
                        if not isinstance(result, dict):
                            raise ValueError()
                        control.check()
                        return result
            except (httpx.RequestError, TimeoutError):
                error = ProviderError('network', '服务连接超时或网络不可达，请检查网络和服务地址', True)
            except (ValueError, UnicodeError):
                error = ProviderError('invalid_response', '服务返回的数据格式无法读取')
            except ProviderError as exc:
                error = exc
            if kind == 'model':
                control.usage.input_tokens = control.usage.output_tokens = None
            if attempt == 1 or not error.retryable:
                raise error
            control.check()
            await asyncio.sleep(min(0.4, control.check()))
        raise AssertionError('unreachable')

    async def complete(self, messages: list[dict], tools: list[dict], control: RunControl) -> ModelReply:
        if len(json.dumps(messages, ensure_ascii=False)) > 30000:
            raise BudgetStop('模型上下文已达到大小上限')
        if not self.settings.base_url.startswith('https://'):
            raise ProviderError('configuration', '模型服务地址必须为 HTTPS 地址')
        payload = {'model': self.settings.model, 'messages': messages, 'max_tokens': 2000}
        if self.settings.model == 'qwen-plus' and urlsplit(self.settings.base_url).hostname == 'dashscope.aliyuncs.com':
            payload['enable_thinking'] = False
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = 'auto'
        result = await self._post(self.settings.base_url + '/chat/completions', self.settings.api_key, payload, 'model', control)
        try:
            message = result['choices'][0]['message']
            usage = result.get('usage') or {}
            if not isinstance(message, dict) or not isinstance(usage, dict):
                raise ValueError('invalid response containers')
            reply = ModelReply(content=message.get('content'), tool_calls=message.get('tool_calls') or [],
                               input_tokens=usage.get('prompt_tokens'), output_tokens=usage.get('completion_tokens'))
            for field in ('input_tokens', 'output_tokens'):
                value, current = getattr(reply, field), getattr(control.usage, field)
                if value is None or (current is None and control.usage.model_requests > 1):
                    setattr(control.usage, field, None)
                else:
                    setattr(control.usage, field, (current or 0) + value)
            return reply
        except (KeyError, IndexError, TypeError, ValueError):
            control.usage.input_tokens = control.usage.output_tokens = None
            raise ProviderError('invalid_response', '模型返回的消息格式不完整') from None

    async def search(self, query: str, control: RunControl) -> list[SearchHit]:
        if not query.strip() or len(query) > 500:
            raise ProviderError('invalid_query', '搜索词为空或过长')
        result = await self._post('https://api.tavily.com/search', self.settings.tavily_key,
                                  {'query': query, 'search_depth': 'basic', 'max_results': 5,
                                   'include_answer': False, 'include_raw_content': False}, 'search', control)
        try:
            return [SearchHit(id='s' + hashlib.sha256(r['url'].encode()).hexdigest()[:10],
                              title=str(r.get('title', '未命名来源'))[:300], url=r['url'],
                              snippet=str(r.get('content', ''))[:600], published_at=r.get('published_date'))
                    for r in result['results'][:5]]
        except (KeyError, TypeError, ValueError):
            raise ProviderError('invalid_response', '搜索服务返回的结果格式不完整') from None

    async def extract(self, hit: SearchHit, control: RunControl) -> Source:
        try:
            await asyncio.wait_for(validate_public_url(hit.url), timeout=min(20, control.check()))
        except TimeoutError:
            raise ProviderError('network', '网页地址解析超时') from None
        result = await self._post('https://api.tavily.com/extract', self.settings.tavily_key,
                                  {'urls': [hit.url], 'extract_depth': 'basic', 'format': 'text'}, 'extract', control)
        rows = result.get('results') or []
        if not rows or not isinstance(rows[0], dict) or not isinstance(rows[0].get('raw_content'), str) or not rows[0]['raw_content'].strip():
            raise ProviderError('unreadable', '未能读取网页正文，搜索摘要不作为已核实证据')
        body = rows[0]['raw_content']
        return Source(id=hit.id, title=hit.title, url=hit.url, published_at=hit.published_at,
                      text=body[:20000], truncated=len(body) > 20000)
