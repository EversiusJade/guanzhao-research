import html
import re
from urllib.parse import urlsplit, quote
from app.schemas import Report, Source, TaskRecord


def quote_candidates(text: str) -> list[str]:
    quotes = []
    for line in text[:5000].splitlines():
        for part in re.split(r'(?<=[。！？])', line):
            quote = part.strip()
            if 12 <= len(quote) <= 200 and quote not in quotes:
                quotes.append(quote)
                if len(quotes) == 8:
                    return quotes
    return quotes or ([text[:200].strip()] if text[:200].strip() else [])


def quote_options(source: Source) -> list[dict]:
    return [{'quote_id':f'q{i+1}', 'quote':quote} for i,quote in enumerate(quote_candidates(source.text))]


def resolve_citations(data: dict, sources: list[Source]) -> dict:
    source_map={s.id:s for s in sources}
    resolved=dict(data)
    resolved['facts']=[]
    for fact in data.get('facts',[]):
        updated=dict(fact)
        updated['citations']=[]
        for citation in fact.get('citations',[]):
            if set(citation)=={'source_id','quote_id'}:
                source=source_map.get(citation['source_id'])
                options={o['quote_id']:o['quote'] for o in quote_options(source)} if source else {}
                if citation['quote_id'] not in options:
                    raise ValueError('引用编号不存在或来源尚未读取')
                citation={'source_id':source.id,'quote':options[citation['quote_id']]}
            updated['citations'].append(citation)
        resolved['facts'].append(updated)
    return resolved


def validate_report(report: Report, sources: list[Source], require_relevance: bool = False) -> list[str]:
    errors = []
    source_map = {s.id: s for s in sources}
    if not report.incomplete and (not sources or not report.facts):
        errors.append('完整报告需要已读取正文与有引用的事实')
    for index, claim in enumerate(report.facts):
        if require_relevance and any(not getattr(claim,k).strip() for k in ('decision_question','relevance','limitation')):
            errors.append(f'事实 {index} 缺少子问题、参考价值或证据边界')
        for citation in claim.citations:
            source = source_map.get(citation.source_id)
            quote = re.sub(r'\s+', '', citation.quote)
            if not quote:
                errors.append(f'事实 {index} 的引用原文为空白')
            elif source is None:
                errors.append(f'事实 {index} 引用了未读取的来源')
            elif quote not in re.sub(r'\s+', '', source.text):
                errors.append(f'事实 {index} 的引用原文不匹配')
    for option in report.options:
        if require_relevance and (not option.why_it_matters.strip() or not option.pivotal_uncertainty.strip() or not option.claim_ids):
            errors.append('方案必须说明视角价值、决定性未知并关联事实依据')
        if any(i < 0 or i >= len(report.facts) for i in option.claim_ids):
            errors.append('方案引用了不存在的事实编号')
    if require_relevance and not report.incomplete and (not report.recommendation_conditions or not report.reversal_conditions or not report.next_step.strip()):
        errors.append('完整报告需要条件判断、反转条件及最小验证下一步')
    return errors


def partial_report(task: TaskRecord, reason: str) -> Report:
    return Report(summary='资料收集未完成。已保留取得的证据，暂不据此给出确定建议。',
                  assumptions=task.assumptions, unknowns=['尚未完成证据核查和方案比较。'],
                  next_step='查看已有证据；补充条件或解决连接问题后，可以新建任务重试。',
                  incomplete=True, reason=reason)


def _text(value: str) -> str:
    return html.escape(value, quote=False).replace('[', r'\[').replace(']', r'\]')


def _source_link(source: Source, demo: bool) -> str:
    title = _text(source.title)
    try:
        parsed = urlsplit(source.url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
            return title
        if source.reference_kind == 'illustration' or (demo and source.reference_kind != 'preset'):
            return title + '（人工示例，无真实网页）'
        url = quote(source.url, safe=':/?#@!$&*+,;=%~_-')
        return f'[{title}]({url})'
    except ValueError:
        return title


def to_markdown(task: TaskRecord) -> str:
    report = task.report or partial_report(task, task.error or '尚未生成完整报告')
    lines = ['# 调研简报', '', _text(task.input.question), '',
             '**演示数据，未联网检索**' if task.input.mode == 'demo' else '**在线调研记录**', '',
             f'状态：{task.state}；记录时间：{task.updated_at}', '', _text(report.summary), '']
    if report.incomplete:
        lines.extend(['> 资料收集未完成：' + _text(report.reason or '证据不足'), ''])
    if task.understanding:
        plan = task.understanding
        lines.extend(['## 需求理解', '', '目标：'+_text(plan.goal), '', '核心选择：'+_text(plan.decision)])
        for label,key in [('硬约束','constraints'),('已知情况','observations'),('用户偏好','preferences'),
                          ('用户提出的待核实前提','user_assumptions'),('AI 暂定理解','ai_assumptions'),
                          ('关键未知','consequential_unknowns')]:
            if getattr(plan,key):
                lines.extend(['',label+'：'+_text('；'.join(getattr(plan,key)))])
        lines.extend(['','## 本次比较的视角',''])
        for perspective in plan.perspectives:
            lines.extend(['- '+_text(perspective.name), '  - 待查证：'+_text(perspective.decision_question),
                          '  - 为什么值得比较：'+_text(perspective.why_it_matters)])
    sections = [('本次假设', report.assumptions), ('基于资料的推断', report.inferences),
                ('什么条件下这样选',report.recommendation_conditions),
                ('什么情况会改变建议',report.reversal_conditions),('仍不确定', report.unknowns)]
    source_map = {source.id:source for source in task.sources}
    lines.extend(['','## 资料及参考价值', ''])
    for index,fact in enumerate(report.facts):
        lines.extend([f'### 资料 {index+1}', '', _text(fact.text), ''])
        for label,key in [('回答的问题','decision_question'),('参考价值','relevance'),('证据边界','limitation')]:
            if getattr(fact,key):
                lines.append('- '+label+'：'+_text(getattr(fact,key)))
        for c in fact.citations:
            source=source_map.get(c.source_id)
            title=_source_link(source,task.input.mode=='demo') if source else _text(c.source_id)
            lines.extend(['- 出处：'+title, '  - 原文：'+_text(c.quote)])
    for option in report.options:
        lines.extend(['', '## ' + _text(option.name), '',
                      '适用条件：' + _text('；'.join(option.conditions)), '',
                      '收益：' + _text('；'.join(option.benefits)), '',
                      '代价：' + _text('；'.join(option.costs)), '',
                      '视角意义：'+_text(option.why_it_matters), '',
                      '决定性未知：'+_text(option.pivotal_uncertainty), '',
                      '依据：'+ '、'.join(f'资料 {i+1}' for i in option.claim_ids if 0<=i<len(report.facts))])
    for title, items in sections:
        if items:
            lines.extend(['', '## ' + title, ''] + ['- ' + _text(x) for x in items])
    lines.extend(['', '## 下一步', '', _text(report.next_step), '', '## 来源记录', ''])
    for source in task.sources:
        lines.extend([f'- {_text(source.id)}：'+_source_link(source,task.input.mode=='demo'),
                      f'  - 采集记录：{_text(source.retrieved_at)}；发布日期：{_text(source.published_at or "未知")}'])
        if source.research_question:
            lines.append('  - 回答的问题：'+_text(source.research_question))
        if source.why_selected:
            lines.append('  - 为什么选它：'+_text(source.why_selected))
        if source.reference_kind=='preset':
            lines.append('  - 人工预置资料；本次没有联网检索。')
        if report.incomplete:
            lines.extend(['', '### 已保留正文（尚未完成核查）', '', _text(source.text), ''])
            if source.truncated:
                lines.append('> 正文已按采集长度上限截断。')
    if report.continuations:
        lines.extend(['','## 可选深入方向','', '由用户选择，也可以到此结束；不会自动调用服务。','']+
                     ['- '+_text(x) for x in report.continuations])
    lines.extend(['', '引用可回看不代表结论已获独立核实。', ''])
    return '\n'.join(lines)
