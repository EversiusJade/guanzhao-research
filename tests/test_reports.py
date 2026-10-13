import pytest


@pytest.mark.parametrize('source_id,quote,index', [('missing','支持工具调用',0), ('s1','凭空编造',0), ('s1','支持工具调用',9)])
def test_bad_reference_rejected(source_id, quote, index):
    from app.reports import validate_report
    from app.schemas import Report, Claim, Citation, Source, Option
    report = Report(summary='对比', facts=[Claim(text='支持工具调用', citations=[Citation(source_id=source_id,quote=quote)])],
                    options=[Option(name='方案',claim_ids=[index])])
    assert validate_report(report, [Source(id='s1',title='文档',url='https://example.com',text='本服务支持工具调用。')])


def test_whitespace_quote_and_export_labels():
    from app.reports import validate_report, to_markdown, partial_report
    from app.schemas import Report, Claim, Citation, Source, TaskInput, TaskRecord
    task = TaskRecord(id='t', input=TaskInput(question='问题',mode='demo'), sources=[Source(id='s1',title='文档',url='https://example.com',text='支持 工具\n调用')])
    report = Report(summary='结论',facts=[Claim(text='支持',citations=[Citation(source_id='s1',quote='支持工具调用')])])
    assert validate_report(report,task.sources) == []
    task.report = partial_report(task,'测试停止')
    output = to_markdown(task)
    assert '演示数据，未联网检索' in output
    assert '资料收集未完成' in output
    assert 'https://example.com' not in output
    assert '真实网页' in output


def test_empty_evidence_cannot_be_completed():
    from app.reports import validate_report
    from app.schemas import Report
    assert validate_report(Report(summary='肯定适合你'),[])


def test_quote_candidates_preserve_contiguous_original_and_escapes():
    from app.reports import quote_candidates
    text='标题\n应用程序执行指定工具，获取输出结果。\n若无需工具，返回自然语言回复。\n通过 Runs.submit\\_tool\\_outputs 返回结果。\n'+('超长段落'*60)
    quotes=quote_candidates(text)
    assert '应用程序执行指定工具，获取输出结果。' in quotes
    assert '通过 Runs.submit\\_tool\\_outputs 返回结果。' in quotes
    assert all(q in text and 12 <= len(q) <= 200 for q in quotes)
    assert quote_candidates((text+'\n')*20)==quotes


def test_stitched_quote_is_rejected_even_if_each_sentence_exists():
    from app.reports import validate_report
    from app.schemas import Report,Claim,Citation,Source
    source=Source(id='s1',title='原文',url='https://example.com',text='应用程序发送请求。模型可选择无需工具。应用程序执行指定工具。')
    report=Report(summary='结果',facts=[Claim(text='执行职责',citations=[Citation(source_id='s1',quote='应用程序发送请求。应用程序执行指定工具。')])])
    assert validate_report(report,[source])==['事实 0 的引用原文不匹配']


def test_quote_id_resolves_only_to_read_source_original():
    from app.reports import resolve_citations,quote_options,validate_report
    from app.schemas import Source,Report
    source=Source(id='s1',title='文档',url='https://example.com',text='应用程序执行指定工具，获取输出结果。')
    option=quote_options(source)[0]
    data={'summary':'结果','facts':[{'text':'应用执行工具','citations':[{'source_id':'s1','quote_id':option['quote_id']}]}]}
    resolved=resolve_citations(data,[source])
    assert resolved['facts'][0]['citations'][0]=={'source_id':'s1','quote':source.text}
    assert validate_report(Report.model_validate(resolved),[source])==[]
    data['facts'][0]['citations'][0]['quote_id']='q999'
    with pytest.raises(ValueError):resolve_citations(data,[source])
    data['facts'][0]['citations'][0]={'source_id':'missing','quote_id':'q1'}
    with pytest.raises(ValueError):resolve_citations(data,[source])
