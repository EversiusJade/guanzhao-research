import asyncio
import json
import time
import httpx
import pytest


def setup_provider(handler):
    from app.config import Settings
    from app.providers import Providers, RunControl
    from app.schemas import Usage
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Providers(Settings('https://model.example/v1', 'private-key', 'test-model', 'search-key'), client), RunControl(time.monotonic()+300, asyncio.Event(), Usage())


def test_auth_error_not_retried_or_exposed():
    from app.providers import ProviderError
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={'error': 'private-key'})
    provider, control = setup_provider(handler)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.search('test', control))
    assert len(calls) == 1
    assert 'private-key' not in str(exc.value)


def test_billing_error_is_actionable_and_not_retried():
    from app.providers import ProviderError
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(400,json={'error':{'code':'Arrearage','message':'private-key'}})
    provider,control=setup_provider(handler)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.complete([{'role':'user','content':'check'}],[],control))
    assert exc.value.code=='billing'
    assert 'Arrearage' in exc.value.message
    assert '控制台' in exc.value.message
    assert 'private-key' not in exc.value.message
    assert len(calls)==1


def test_retry_counted_and_missing_usage_unknown():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'hello'}}]})
    provider, control = setup_provider(handler)
    reply = asyncio.run(provider.complete([{'role':'user','content':'hi'}], [], control))
    assert reply.content == 'hello'
    assert control.usage.model_requests == 2
    assert control.usage.input_tokens is None
    assert json.loads(calls[1].content)['max_tokens'] == 2000


def test_budget_prevents_next_request():
    from app.providers import BudgetStop
    calls = []
    provider, control = setup_provider(lambda req: calls.append(req))
    control.usage.search_requests = 6
    with pytest.raises(BudgetStop):
        asyncio.run(provider.search('test', control))
    assert calls == []


@pytest.mark.parametrize('url', ['http://127.0.0.1/', 'http://10.0.0.1/', 'http://user:pass@example.com/', 'file:///etc/passwd', 'http://[::1]/', 'http://169.254.169.254/'])
def test_private_or_unsafe_urls_rejected(url):
    from app.providers import ProviderError
    from app.schemas import SearchHit
    provider, control = setup_provider(lambda req: pytest.fail('unsafe request sent'))
    with pytest.raises(ProviderError):
        asyncio.run(provider.extract(SearchHit(id='s1', title='test', url=url), control))


def test_search_snippets_not_sources_and_extract_truncated(monkeypatch):
    from app.schemas import SearchHit
    import app.providers as module
    monkeypatch.setattr(module.socket, 'getaddrinfo', lambda *args: [(2, 1, 6, '', ('93.184.216.34',443))])
    def handler(request):
        if request.url.path == '/search':
            return httpx.Response(200, json={'results':[{'title':'Doc', 'url':'https://example.com/doc','content':'snippet'}]})
        return httpx.Response(200, json={'results':[{'url':'https://example.com/doc','raw_content':'x'*25000}], 'failed_results':[]})
    provider, control = setup_provider(handler)
    hits = asyncio.run(provider.search('test', control))
    assert isinstance(hits[0], SearchHit)
    assert not hasattr(hits[0], 'text')
    source = asyncio.run(provider.extract(hits[0], control))
    assert len(source.text) == 20000
    assert source.truncated


def test_large_response_rejected():
    from app.providers import ProviderError
    provider, control = setup_provider(lambda req: httpx.Response(200, content=b'x'*(2*1024*1024+1)))
    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider.search('test', control))
    assert exc.value.code == 'response_too_large'


def test_cancel_and_deadline_prevent_request():
    from app.providers import BudgetStop
    provider, control = setup_provider(lambda req: pytest.fail('request after stop'))
    control.cancel.set()
    with pytest.raises(BudgetStop):
        asyncio.run(provider.search('test', control))
    control.cancel.clear()
    control.deadline = time.monotonic()-1
    with pytest.raises(BudgetStop):
        asyncio.run(provider.search('test', control))
