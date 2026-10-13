import time
import pytest


def make_client(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Settings
    return TestClient(create_app(Settings(),tmp_path/'api.db'),base_url='http://127.0.0.1')


def create_demo(client):
    config=client.get('/api/config').json()
    response=client.post('/api/tasks',json={'request_id':'demo-1','input':{'question':config['demo_question'],'mode':'demo'}})
    assert response.status_code == 200
    return response.json()['id']


def wait_end(client,id):
    for _ in range(60):
        task=client.get('/api/tasks/'+id).json()
        if task['state'] not in ('preparing','running'):
            return task
        time.sleep(.05)
    pytest.fail('task did not finish')


def test_demo_run_refresh_history_export(tmp_path):
    with make_client(tmp_path) as client:
        id=create_demo(client)
        assert client.post(f'/api/tasks/{id}/prepare').status_code == 200
        wait_end(client,id)
        assert client.post(f'/api/tasks/{id}/start',json={}).status_code == 200
        assert client.post(f'/api/tasks/{id}/start',json={}).status_code == 409
        result=wait_end(client,id)
        assert result['state']=='completed'
        assert len(result['sources'])==2
        assert result['usage']['model_requests']==0
        assert client.get('/api/tasks').json()[0]['id']==id
        export=client.get(f'/api/tasks/{id}/report.md')
        assert '演示数据，未联网检索' in export.text
        assert client.get(f'/api/tasks/{id}').json()['report']==result['report']


def test_stop_idempotent_and_keeps_partial(tmp_path):
    with make_client(tmp_path) as client:
        id=create_demo(client)
        client.post(f'/api/tasks/{id}/prepare'); wait_end(client,id)
        client.post(f'/api/tasks/{id}/start',json={})
        assert client.post(f'/api/tasks/{id}/stop').status_code==200
        assert client.post(f'/api/tasks/{id}/stop').status_code==200
        result=wait_end(client,id)
        assert result['state']=='stopped'
        assert result['report']['incomplete']


def test_online_missing_keys_and_input_boundaries(tmp_path):
    with make_client(tmp_path) as client:
        response=client.post('/api/tasks',json={'request_id':'x','input':{'question':'工具比较','mode':'online'}})
        id=response.json()['id']
        assert client.post(f'/api/tasks/{id}/prepare').status_code==409
        assert client.get(f'/api/tasks/{id}').json()['usage']['model_requests']==0
        assert client.post('/api/tasks',json={'request_id':'y','input':{'question':'x'*2001}}).status_code==422
        assert client.post('/api/tasks',json={'request_id':'z','input':{'question':'任意问题','mode':'demo'}}).status_code==422
        assert client.get('/api/tasks/missing').status_code==404


def test_cross_origin_mutation_rejected(tmp_path):
    with make_client(tmp_path) as client:
        response=client.post('/api/tasks',headers={'Origin':'https://evil.example'},json={'request_id':'x','input':{'question':'hi'}})
        assert response.status_code==403
        assert client.get('/api/config',headers={'Host':'evil.example'}).status_code==400


def test_two_different_tasks_cannot_run_together(tmp_path):
    with make_client(tmp_path) as client:
        first=create_demo(client)
        question=client.get('/api/config').json()['demo_question']
        second=client.post('/api/tasks',json={'request_id':'second','input':{'question':question,'mode':'demo'}}).json()['id']
        for id in (first,second):
            client.post(f'/api/tasks/{id}/prepare');wait_end(client,id)
        client.post(f'/api/tasks/{first}/start',json={})
        assert client.post(f'/api/tasks/{second}/start',json={}).status_code==409
        client.post(f'/api/tasks/{first}/stop')
