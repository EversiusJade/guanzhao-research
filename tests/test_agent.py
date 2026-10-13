import asyncio
import json
import pytest


def call(name, **args):
    return {'id': 'call-'+name, 'type':'function', 'function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}


def report_dict():
    return {'summary':'可以先用平台验证需求，再按目标扩展。', 'facts':[{'text':'支持工具调用','citations':[{'source_id':'s1','quote':'支持工具调用'}]}],
            'options':[], 'inferences':['选型仍取决于你的目标。'], 'unknowns':[], 'next_step':'先试一个任务'}


class FakeProviders:
    def __init__(self, replies, hook=None):
        self.replies, self.hook = list(replies), hook
        self.searches = []
        self.tools_seen = []

    async def complete(self, messages, tools, control):
        from app.schemas import ModelReply
        control.before_request('model')
        self.tools_seen.append([t['function']['name'] for t in tools])
        if self.hook:
            self.hook(control)
        data = self.replies.pop(0)
        return ModelReply(**data)

    async def search(self, query, control):
        from app.schemas import SearchHit
        control.before_request('search')
        self.searches.append(query)
        return [SearchHit(id='s1',title='官方文档',url='https://example.com/doc',snippet='摘要')]

    async def extract(self, hit, control):
        from app.schemas import Source
        control.before_request('extract')
        return Source(id=hit.id,title=hit.title,url=hit.url,text='本服务支持工具调用。需自行编写工具实现。')


def setup(tmp_path, replies, hook=None):
    from app.store import Store
    from app.schemas import TaskInput
    from app.agent import Agent
    store = Store(tmp_path/'db.sqlite')
    task = store.create(TaskInput(question='比较低代码和代码实现',context='不会代码，用于实习作品'), 'one')
    provider = FakeProviders(replies,hook)
    return Agent(store,provider), store, task, provider


def test_dynamic_research_persists_evidence(tmp_path):
    replies = [{'tool_calls':[call('search_web',query='平台功能')]}, {'tool_calls':[call('read_page',source_id='s1')]},
               {'tool_calls':[call('search_web',query='平台限制')]}, {'tool_calls':[call('submit_report',report=report_dict())]}]
    agent, store, task, provider = setup(tmp_path,replies)
    asyncio.run(agent.run(task.id,asyncio.Event()))
    result=store.get(task.id)
    assert result.state == 'completed'
    assert result.report.facts[0].citations[0].source_id == 's1'
    assert result.sources[0].text.startswith('本服务')
    assert provider.searches == ['平台功能','平台限制']
    assert result.usage.model_requests == 4


def test_cancel_before_run_makes_no_requests(tmp_path):
    agent, store, task, provider=setup(tmp_path,[])
    cancel=asyncio.Event(); cancel.set()
    asyncio.run(agent.run(task.id,cancel))
    result=store.get(task.id)
    assert result.state == 'stopped'
    assert result.usage.model_requests == 0
    assert result.report.incomplete


def test_excess_search_in_one_reply_preserves_results_and_continues(tmp_path):
    replies=[{'tool_calls':[call('search_web',query='路线一'),call('search_web',query='路线二'),
                           call('search_web',query='额外请求')]},
             {'tool_calls':[call('read_page',source_id='s1')]},
             {'tool_calls':[call('submit_report',report=report_dict())]}]
    agent,store,task,provider=setup(tmp_path,replies)
    asyncio.run(agent.run(task.id,asyncio.Event()))
    result=store.get(task.id)
    assert result.state=='completed'
    assert result.usage.search_requests==2
    assert provider.searches==['路线一','路线二']
    assert result.usage.extract_requests==1
    assert any(e.kind=='tool_limit' for e in result.events)
    assert 'search_web' not in provider.tools_seen[1]
    assert 'read_page' in provider.tools_seen[1]


def test_last_remaining_model_call_is_reserved_for_report(tmp_path):
    replies=[{'tool_calls':[call('search_web',query='功能')]},
             {'tool_calls':[call('read_page',source_id='s1')]},
             {'tool_calls':[call('submit_report',report=report_dict())]}]
    agent,store,task,provider=setup(tmp_path,replies)
    task.usage.model_requests=3;store.save(task)
    asyncio.run(agent.run(task.id,asyncio.Event()))
    assert store.get(task.id).state=='completed'
    assert store.get(task.id).usage.model_requests==6
    assert provider.tools_seen[-1]==['submit_report']


def test_repeated_search_cached_and_round_limit(tmp_path):
    from app.config import RESEARCH_ROUNDS
    replies=[{'tool_calls':[call('search_web',query='重复')]} for _ in range(RESEARCH_ROUNDS)]
    agent,store,task,provider=setup(tmp_path,replies)
    asyncio.run(agent.run(task.id,asyncio.Event()))
    result=store.get(task.id)
    assert result.state == 'partial'
    assert provider.searches == ['重复']
    assert result.usage.model_requests == RESEARCH_ROUNDS
    assert provider.tools_seen[-1] == ['submit_report']


def test_bad_report_only_one_repair(tmp_path):
    bad={'tool_calls':[call('submit_report',report=report_dict())]}
    agent,store,task,provider=setup(tmp_path,[bad,bad,bad])
    asyncio.run(agent.run(task.id,asyncio.Event()))
    result=store.get(task.id)
    assert result.state == 'partial'
    assert result.usage.model_requests == 2


def test_prepare_reuses_background(tmp_path):
    payload={'in_scope':True,'questions':[],'assumptions':[], 'understanding':{
        'goal':'形成可演示的实习作品','decision':'选择平台或代码路线',
        'perspectives':[{'name':'流程验证','decision_question':'平台是否覆盖任务？','why_it_matters':'决定是否能完成流程'},
                        {'name':'界面定制','decision_question':'是否需要自定义接口？','why_it_matters':'决定代码路线的必要性'}]}}
    agent,store,task,_=setup(tmp_path,[{'content':json.dumps(payload)}])
    asyncio.run(agent.prepare(task.id))
    assert store.get(task.id).state == 'ready'


def test_prepare_scope_and_question_limit(tmp_path):
    payload={'in_scope':False,'scope_message':'请改为 AI 项目问题','questions':[],'assumptions':[]}
    agent,store,task,_=setup(tmp_path,[{'content':json.dumps(payload)}])
    asyncio.run(agent.prepare(task.id))
    assert store.get(task.id).state == 'clarifying'
    assert store.get(task.id).in_scope is False


def test_deadline_does_not_call_final_summary(tmp_path):
    agent,store,task,provider=setup(tmp_path,[{'tool_calls':[call('search_web',query='test')]}],lambda control: setattr(control,'deadline',0))
    asyncio.run(agent.run(task.id,asyncio.Event()))
    result=store.get(task.id)
    assert result.state == 'partial'
    assert provider.searches == []
    assert result.usage.model_requests == 1


def test_unknown_tool_not_executed(tmp_path):
    agent,store,task,provider=setup(tmp_path,[{'tool_calls':[call('send_secret',url='https://evil.example')]}]*12)
    asyncio.run(agent.run(task.id,asyncio.Event()))
    result=store.get(task.id)
    assert result.state == 'partial'
    assert result.usage.search_requests == 0
    assert any(e.kind=='tool_error' for e in result.events)


@pytest.mark.parametrize('quote,expected_field,expected_reason', [
    ('x'*201, 'facts.0.citations.0.quote', '200'),
    ('不存在的原文', None, '引用原文不匹配'),
])
def test_report_repair_receives_specific_validation_feedback(tmp_path, quote, expected_field, expected_reason):
    bad=report_dict()
    bad['facts'][0]['citations'][0]['quote']=quote
    replies=[{'tool_calls':[call('search_web',query='功能')]},
             {'tool_calls':[call('read_page',source_id='s1')]},
             {'tool_calls':[call('submit_report',report=bad)]},
             {'tool_calls':[call('submit_report',report=report_dict())]}]
    agent,store,task,provider=setup(tmp_path,replies)
    feedback=[]
    original=provider.complete
    async def capture(messages,tools,control):
        if messages[-1]['role']=='tool':
            feedback.append(json.loads(messages[-1]['content']))
        return await original(messages,tools,control)
    provider.complete=capture
    asyncio.run(agent.run(task.id,asyncio.Event()))
    error=next(item for item in feedback if 'error' in item)
    serialized=json.dumps(error,ensure_ascii=False)
    assert expected_reason in serialized
    if expected_field:
        assert expected_field in serialized
        assert error['details'][0]['actual_chars']==201
        assert error['details'][0]['quote_prefix']==quote[:80]
        assert quote not in serialized
    assert store.get(task.id).state=='completed'


def test_agent_binds_quote_id_to_original_and_tool_schema_requests_ids(tmp_path):
    from app.agent import tools_for_round
    report=report_dict()
    report['facts'][0]['citations']=[{'source_id':'s1','quote_id':'q1'}]
    agent,store,task,_=setup(tmp_path,[{'tool_calls':[call('search_web',query='功能')]},
        {'tool_calls':[call('read_page',source_id='s1')]},
        {'tool_calls':[call('submit_report',report=report)]}])
    asyncio.run(agent.run(task.id,asyncio.Event()))
    result=store.get(task.id)
    assert result.state=='completed'
    assert result.report.facts[0].citations[0].quote in result.sources[0].text
    schema=tools_for_round(True)[0]['function']['parameters']['properties']['report']
    citation=schema['properties']['facts']['items']['properties']['citations']['items']
    assert set(citation['required'])=={'source_id','quote_id'}
    assert 'quote' not in citation['properties']
