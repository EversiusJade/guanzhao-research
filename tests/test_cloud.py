"""Cloud pilot checks. All HTTP providers are mocked; no paid service requests."""
import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers import BudgetStop, Providers, RunControl
from app.schemas import TaskInput, Usage
from app.store import Store


def cloud_settings():
    return Settings(base_url='https://model.invalid/v1', api_key='fake-model-key',
        model='fake-model', tavily_key='fake-search-key', cloud_online=True,
        session_secret='test-only-session-secret-32-characters',
        online_until=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat())


def new_task(client, request_id='same-request'):
    return client.post('/api/tasks', json={'request_id':request_id,
        'input':{'question':'比较两种软件开发方案','mode':'online'}})


@pytest.mark.parametrize('change', [
    {'online_until':''}, {'online_until':'not-a-date'},
    {'online_until':'2000-01-01T00:00:00+00:00'},
    {'online_until':'2099-01-01T00:00:00'},
    {'session_secret':'short'}, {'cloud_task_limit':0},
])
def test_unready_cloud_rejects_paid_work(tmp_path, change):
    settings=replace(cloud_settings(),**change)
    with TestClient(create_app(settings,tmp_path/'db'),base_url='https://cloud.example') as client:
        assert not client.get('/api/config').json()['online_enabled']
        assert new_task(client).status_code==403
        assert Store(tmp_path/'db').count()==0


def test_visitors_history_actions_and_idempotency_are_isolated(tmp_path):
    app=create_app(cloud_settings(),tmp_path/'db')
    with TestClient(app,base_url='https://cloud.example') as first:
        second=TestClient(app,base_url='https://cloud.example')
        a=new_task(first).json()['id']
        assert new_task(first).json()['id']==a
        b=new_task(second).json()['id']
        assert a!=b
        assert [t['id'] for t in first.get('/api/tasks').json()]==[a]
        assert [t['id'] for t in second.get('/api/tasks').json()]==[b]
        for route, method, data in [('', 'get',None),('/report.md','get',None),
                ('/prepare','post',None),('/start','post',{}),('/stop','post',None),
                ('/revise','post',{'question':'其他问题','mode':'online'})]:
            response=getattr(second,method)('/api/tasks/'+a+route,**({'json':data} if data is not None else {}))
            assert response.status_code==404
        assert new_task(first,'another').status_code==429
        assert first.get('/api/tasks/'+a).json()['usage']['model_requests']==0
        cookie=first.get('/health').request.headers.get('cookie')
        assert cookie and 'guanzhao_session=' in cookie


def test_quota_is_atomic_and_survives_same_database_reopen(tmp_path):
    store=Store(tmp_path/'db')
    def create_one(index):
        try:
            return store.create(TaskInput(question='task'),str(index),'owner-'+str(index),3).id
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(create_one,range(8)))
    assert len([r for r in results if r])==3
    reopened=Store(tmp_path/'db')
    assert reopened.count()==3
    with pytest.raises(ValueError):
        reopened.create(TaskInput(question='task'),'extra','another-owner',3)


def test_tampered_cookie_does_not_reveal_existing_history(tmp_path):
    app=create_app(cloud_settings(),tmp_path/'db')
    with TestClient(app,base_url='https://cloud.example') as client:
        task_id=new_task(client).json()['id']
        original=client.cookies.get('guanzhao_session')
        client.cookies.clear()
        client.cookies.set('guanzhao_session',original[:-1]+('a' if original[-1]!='a' else 'b'))
        response=client.get('/api/tasks')
        assert response.status_code==200 and response.json()==[]
        assert client.get('/api/tasks/'+task_id).status_code==404
        assert 'HttpOnly' in response.headers['set-cookie']
        assert 'Secure' in response.headers['set-cookie']
        assert 'SameSite=strict' in response.headers['set-cookie']


def test_revision_preserves_usage_owner_and_quota(tmp_path):
    app=create_app(cloud_settings(),tmp_path/'db')
    with TestClient(app,base_url='https://cloud.example') as client:
        id=new_task(client).json()['id']
        store=Store(tmp_path/'db'); task=store.get(id)
        task.state='ready'; task.usage.model_requests=1; store.save(task)
        result=client.post(f'/api/tasks/{id}/revise',json={'question':'调整问题','mode':'online'})
        assert result.status_code==200
        assert result.json()['state']=='draft'
        assert result.json()['usage']['model_requests']==1
        assert store.count()==1
        assert client.get('/api/tasks').json()[0]['id']==id


def test_same_origin_behind_https_proxy_and_cross_origin(tmp_path):
    app=create_app(cloud_settings(),tmp_path/'db')
    with TestClient(app,base_url='http://cloud.example') as client:
        assert new_task(client).status_code==200
        response=client.post('/api/tasks',headers={'Origin':'https://cloud.example'},
            json={'request_id':'proxy','input':{'question':'proxy'}})
        assert response.status_code==200
        response=client.post('/api/tasks',headers={'Origin':'https://evil.example'},
            json={'request_id':'evil','input':{'question':'evil'}})
        assert response.status_code==403


def test_expiry_prevents_outbound_requests_and_usage_increment():
    calls=[]
    def fail_if_called(request):
        calls.append(request)
        raise AssertionError('Expired cloud must never request a service')
    async def run():
        settings=replace(cloud_settings(),online_until='2000-01-01T00:00:00Z')
        control=RunControl(time.monotonic()+60,asyncio.Event(),Usage())
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail_if_called)) as client:
            with pytest.raises(BudgetStop):
                await Providers(settings,client)._post('https://model.invalid','fake',{},'model',control)
        assert control.usage.model_requests==0
    asyncio.run(run())
    assert not calls


def test_legacy_database_migrates_without_losing_tasks(tmp_path):
    store=Store(tmp_path/'db')
    task=store.create(TaskInput(question='legacy'),'one')
    with sqlite3.connect(tmp_path/'old') as db:
        db.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,updated TEXT NOT NULL,payload TEXT NOT NULL)')
        db.execute('INSERT INTO tasks VALUES (?,?,?,?)',(task.id,'one',task.updated_at,task.model_dump_json()))
    old=Store(tmp_path/'old')
    assert old.get(task.id,'local').input.question=='legacy'
    assert old.create(TaskInput(question='legacy'),'one').id==task.id
