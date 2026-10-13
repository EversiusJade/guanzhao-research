'use strict';
const $ = id => document.getElementById(id);
const states = {draft:'尚未开始',preparing:'正在检查背景',clarifying:'等待补充条件',ready:'背景已就绪',running:'正在调研',completed:'已完成',partial:'资料收集未完成',stopped:'已停止',failed:'未能完成',interrupted:'已中断'};
let config, mode='demo', current=null, timer=null, epoch=0, autoStart=false, historySignature='', revisionId=null, onlineDraft={question:'',context:''};

function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function showError(message){$('error').textContent=message;$('error').hidden=false;}
function clearError(){$('error').hidden=true;}
async function api(path,body){
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok){throw new Error(typeof data.detail==='string'?data.detail:'输入未通过检查，请确认长度和必填内容。');}
  return data;
}
function selectMode(next){
  if(next==='online'&&!config.online_enabled&&!config.cloud_online){showError('公开展示版暂未开放在线调研，可以免费体验演示流程。');return;}
  if(mode==='online')onlineDraft={question:$('question').value,context:$('context').value};
  mode=next;
  for(const m of ['demo','online']){$('mode-'+m).classList.toggle('selected',m===mode);$('mode-'+m).setAttribute('aria-pressed',String(m===mode));}
  const demo=mode==='demo';
  $('question').readOnly=demo;$('context').readOnly=demo;
  $('question').value=demo?config.demo_question:onlineDraft.question;
  $('context').value=demo?config.demo_context:onlineDraft.context;
  $('question').placeholder='例如：我想做一个 AI 项目，应该先用平台搭建，还是借助 AI 编写代码？';
  $('context').placeholder='写下目标、已有基础、可投入时间或你不想放弃的条件。';
  $('mode-note').textContent=demo?'固定演示 · 预置官方资料，本次未联网检索。':config.cloud_online?'输入自己的问题，搜索真实网页，查看出处及参考价值。本轮试用每位访客限一次调研。':'在线模式通过 Tavily 搜索并读取网页，再由模型整理结果；实际调用会计费。';
  $('mode-note').classList.toggle('online',!demo);
  const limits=config.request_limits;
  $('run-hint').textContent=demo?'无需密钥，可以直接体验':config.configured?`本次最多模型 ${limits.model_requests} / 搜索 ${limits.search_requests} / 读取 ${limits.extract_requests} 次；含失败与重试，按服务用量计费。`:'在线配置尚未齐全，查看“如何使用”';
  $('start').textContent=demo?'体验完整流程':'开始联网调研（调用计费）';
  $('start').disabled=!demo&&(!config.configured||!config.online_enabled);
  if(config.cloud_online){
    $('start').textContent='开始联网调研';
    const end=new Date(config.online_until);const until=Number.isNaN(end.getTime())?'待开放':end.toLocaleString('zh-CN');
    $('run-hint').textContent=config.online_enabled&&config.configured?`本轮剩余 ${config.remaining_tasks} 个名额，开放至 ${until}。达到上限会保留已有资料。`:'本轮联网试用尚未开放或已结束，请联系项目维护者。';
    $('start').disabled=!config.configured||!config.online_enabled||config.remaining_tasks===0;
  }
}
function resetForm(revise=false){
  const previous=current;epoch++;clearTimeout(timer);autoStart=false;current=null;historySignature='';clearError();$('answers').value='';
  revisionId=revise&&previous&&['clarifying','ready'].includes(previous.state)?previous.id:null;
  if(revise&&previous){onlineDraft={question:previous.input.question,context:previous.input.context};mode='demo';selectMode('online');}
  else selectMode(mode);
  $('intro').hidden=false;$('composer').hidden=false;$('task-view').hidden=true;$('new-task').disabled=false;
  $('sources').replaceChildren(node('p','读取到的资料会出现在这里。','muted'));$('source-count').textContent='0';
  history.replaceState(null,'','/');window.scrollTo({top:0});loadHistory();
}
async function loadHistory(){
  try{
    const items=await api('/api/tasks');$('history').replaceChildren();
    if(!items.length){$('history').append(node('p','还没有调研记录','muted'));return;}
    for(const item of items){
      const b=node('button',undefined,'history-item'+(current?.id===item.id?' active':''));
      b.append(node('strong',item.question.length>28?item.question.slice(0,28)+'…':item.question),node('small',(item.mode==='demo'?'演示':'在线')+' / '+(states[item.state]||item.state)));
      b.addEventListener('click',()=>openTask(item.id));$('history').append(b);
    }
  }catch(error){showError(error.message);}
}
async function openTask(id){epoch++;clearTimeout(timer);autoStart=false;clearError();$('answers').value='';await poll(id,epoch);window.scrollTo({top:0});}
async function poll(id,version){
  try{
    const task=await api('/api/tasks/'+encodeURIComponent(id));if(version!==epoch)return;
    current=task;history.replaceState(null,'','/?task='+encodeURIComponent(id));renderTask(task);
    if(task.state==='ready'&&autoStart){autoStart=false;await api(`/api/tasks/${id}/start`,{});if(version===epoch)timer=setTimeout(()=>poll(id,version),150);return;}
    if(['preparing','running'].includes(task.state))timer=setTimeout(()=>poll(id,version),1000);
  }catch(error){if(version===epoch){showError(error.message);$('new-task').disabled=false;}}
}
function renderTask(task){
  $('intro').hidden=true;$('composer').hidden=true;$('task-view').hidden=false;
  $('task-question').textContent=task.input.question;
  $('task-mode').textContent=task.input.mode==='demo'?'演示数据，未联网检索':'在线调研';
  const active=['preparing','running'].includes(task.state);
  $('stop').hidden=!active;$('stop').disabled=false;$('new-task').disabled=active;
  $('state-label').textContent=states[task.state]||task.state;
  const u=task.usage;
  $('usage-summary').textContent=task.input.mode==='demo'?'演示不产生 API 请求':`模型 ${u.model_requests} 次 / 搜索 ${u.search_requests} 次 / 读取 ${u.extract_requests} 次`;
  $('events').replaceChildren();
  for(const event of task.events){const li=node('li');li.append(node('time',new Date(event.at).toLocaleTimeString('zh-CN',{hour12:false})),node('span',event.message));$('events').append(li);}
  $('events').scrollTop=$('events').scrollHeight;
  const needsInput=['clarifying','ready'].includes(task.state);
  $('clarification').hidden=!needsInput;
  if(needsInput){
    $('scope-message').textContent=task.scope_message||(!task.questions.length?'背景已就绪，可以开始调研。':'只补充影响选择的条件，也可以按明确假设继续。');
    $('questions').replaceChildren(...task.questions.map(q=>node('li',q)));
    const out=task.in_scope===false;
    $('answers').hidden=out||!task.questions.length;$('answers').previousElementSibling.hidden=out||!task.questions.length;
    $('answer-start').hidden=out;$('assume-start').hidden=out||!task.questions.length;$('rewrite').hidden=!out;
    $('answer-start').textContent=task.questions.length?'补充并开始':task.understanding?.needs_confirmation?'确认上述理解并开始':'开始调研';
    if(task.understanding?.needs_confirmation&&!task.questions.length)$('scope-message').textContent='请核对下面的目标和核心选择；若理解有误，先修改问题。';
    $('rewrite').hidden=!out&&!task.understanding?.needs_confirmation;
    if(task.assumptions.length){$('questions').append(node('li','若不补充，将采用假设：'+task.assumptions.join('；')));}
  }
  renderUnderstanding(task.understanding,task.answers);
  $('report').hidden=!task.report;
  if(task.report)renderReport(task);
  renderSources(task);
  $('result-next').hidden=active||needsInput||task.state==='draft';
  if(task.error)showError(task.error);
  const signature=task.id+task.state;if(historySignature!==signature){historySignature=signature;loadHistory();}
}
function appendSection(parent,title,items,cls){if(!items?.length)return;const section=node('section',undefined,cls);section.append(node('h3',title));const ul=node('ul');items.forEach(x=>ul.append(node('li',x)));section.append(ul);parent.append(section);}
function labeled(parent,label,text){if(text){const p=node('p');p.append(node('strong',label+'：'),document.createTextNode(text));parent.append(p);}}
function renderUnderstanding(plan,answers){
  const root=$('understanding');root.hidden=!plan;root.replaceChildren();if(!plan)return;
  root.append(node('h2','先明确这次选择'));labeled(root,'希望达成',plan.goal);labeled(root,'核心选择',plan.decision);
  labeled(root,'用户后续补充（优先于暂定理解）',answers);
  labeled(root,'硬约束',plan.constraints.join('；'));labeled(root,'已知情况',plan.observations.join('；'));labeled(root,'用户偏好',plan.preferences.join('；'));
  const detail=node('details',undefined,'assumption-check');detail.append(node('summary','双方预设与关键未知'));
  appendSection(detail,'用户提出、尚待核实的前提',plan.user_assumptions);appendSection(detail,'AI 的暂定理解',plan.ai_assumptions);appendSection(detail,'会影响选择的未知',plan.consequential_unknowns);root.append(detail);
  for(const perspective of plan.perspectives){const block=node('section',undefined,'perspective');block.append(node('h3',perspective.name));labeled(block,'需要查证',perspective.decision_question);labeled(block,'为什么看这个视角',perspective.why_it_matters);root.append(block);}
}
function sourceLink(source,task){
  if(!source||source.reference_kind==='illustration'||(task.input.mode==='demo'&&source.reference_kind!=='preset'))return null;
  try{const url=new URL(source.url);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)return null;
    const link=node('a',source.title+' ↗','external-source');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';return link;
  }catch{return null;}
}
function renderReport(task){
  const report=task.report, root=$('report-body');root.replaceChildren();
  if(task.input.mode==='demo')root.append(node('p','人工编写的固定示例，使用预置资料；本次未调用模型或 Tavily。','mode-note'));
  if(report.incomplete)root.append(node('p','资料收集未完成：'+(report.reason||'证据不足'),'mode-note'));
  root.append(node('p',report.summary,'summary'));
  appendSection(root,'本次采用的假设',report.assumptions);
  if(report.facts.length){root.append(node('h3','资料对这次选择有什么用'));for(const [index,fact] of report.facts.entries()){
    const block=node('div',undefined,'fact');block.id='fact-'+index;block.append(node('h4',`${index+1}. ${fact.text}`));
    labeled(block,'回答的问题',fact.decision_question);labeled(block,'参考价值',fact.relevance);labeled(block,'证据边界',fact.limitation);
    for(const citation of fact.citations){const source=task.sources.find(s=>s.id===citation.source_id);const external=sourceLink(source,task);if(external)block.append(external);
      const link=node('a',`“${citation.quote}” · 查看采集原文`,'quote');link.href='#source-'+citation.source_id;
      link.addEventListener('click',()=>{const detail=$('source-'+citation.source_id);if(detail)detail.open=true;});block.append(link);}
    root.append(block);
  }}
  if(report.options.length)root.append(node('h3','可以怎样选择'));
  for(const option of report.options){const block=node('section',undefined,'option');block.append(node('h4',option.name));
    labeled(block,'这个视角的意义',option.why_it_matters);
    for(const [label,key] of [['适用条件','conditions'],['可能收益','benefits'],['需要承担','costs']])if(option[key].length)block.append(node('p',label+'：'+option[key].join('；')));
    labeled(block,'决定性未知',option.pivotal_uncertainty);
    if(option.claim_ids.length){const support=node('p','依据：');for(const id of option.claim_ids){if(!report.facts[id])continue;const link=node('a',`资料 ${id+1} `);link.href='#fact-'+id;support.append(link);}block.append(support);}root.append(block);
  }
  appendSection(root,'基于资料的推断',report.inferences);
  appendSection(root,'什么条件下这样选',report.recommendation_conditions);
  appendSection(root,'什么情况会改变建议',report.reversal_conditions);
  appendSection(root,'仍然不知道的事',report.unknowns,'unknowns');
  const next=node('section',undefined,'next-step');next.append(node('h3','一个可执行的下一步'),node('p',report.next_step));root.append(next);
  if(report.continuations?.length){const section=node('section',undefined,'continuations');section.append(node('h3','由你选择是否继续'),node('p','也可以到此结束。选择方向只会填入草稿，开始前可修改条件。','muted'));
    for(const direction of report.continuations){const b=node('button',direction,'secondary-button');b.disabled=config.public_demo||config.cloud_online||task.input.mode==='demo';b.addEventListener('click',()=>{const question=task.input.question;const context=['本次只继续：'+direction,task.answers?'用户已补充：'+task.answers:'',task.understanding?'当前目标：'+task.understanding.goal+'\n已识别约束：'+task.understanding.constraints.join('；'):'','此前结论（需结合新证据修正）：'+report.summary,'原始背景：'+task.input.context].filter(Boolean).join('\n').slice(0,4000);resetForm();selectMode('online');$('question').value=question;$('context').value=context;$('context').focus();});section.append(b);}if(task.input.mode==='demo')section.append(node('p','演示只展示方向；在线模式可选择后继续。','muted'));if(config.cloud_online)section.append(node('p','本轮限一次调研，你可以按上述方向自行核查或留待下轮。','muted'));root.append(section);}
  $('download').href=`/api/tasks/${task.id}/report.md`;
}
function renderSources(task){
  const expanded=new Set([...$('sources').querySelectorAll('details[open]')].map(el=>el.id));
  $('source-count').textContent=String(task.sources.length);$('sources').replaceChildren();
  if(!task.sources.length){$('sources').append(node('p','尚未读取到正文。搜索摘要不会直接当作证据。','muted'));return;}
  for(const source of task.sources){const card=node('section',undefined,'source');const external=sourceLink(source,task);card.append(external||node('h3',source.title));
    labeled(card,'回答的问题',source.research_question);labeled(card,'为什么选它',source.why_selected);
    card.append(node('p',source.reference_kind==='preset'?'预置官方资料 · 本次未检索':task.input.mode==='demo'?'人工示例 · 没有真实网页':'Tavily 正文采集','source-meta'));
    const detail=node('details');detail.id='source-'+source.id;detail.open=expanded.has(detail.id);detail.append(node('summary','查看采集原文与时间'));
    const date=new Date(source.retrieved_at);const accessed=Number.isNaN(date.getTime())?source.retrieved_at:date.toLocaleString('zh-CN');
    detail.append(node('div',`访问：${accessed}\n发布：${source.published_at||'未知'}`,'source-meta'));
    detail.append(node('p',source.text));if(source.truncated)detail.append(node('span',source.reference_kind==='preset'?'预置节选，完整内容请打开原网页':'正文已截断','muted'));card.append(detail);$('sources').append(card);
  }
}
$('research-form').addEventListener('submit',async event=>{
  event.preventDefault();clearError();$('start').disabled=true;epoch++;const version=epoch;
  try{const input={question:$('question').value,context:$('context').value,mode};const task=revisionId?await api(`/api/tasks/${revisionId}/revise`,input):await api('/api/tasks',{request_id:crypto.randomUUID(),input});revisionId=null;
    current=task;autoStart=true;await api(`/api/tasks/${task.id}/prepare`,{});await poll(task.id,version);
  }catch(error){showError(error.message);}finally{if(!$('composer').hidden)$('start').disabled=mode==='online'&&(!config.configured||!config.online_enabled);}
});
async function startWith(assumptions){
  if(!current)return;clearError();$('answer-start').disabled=$('assume-start').disabled=true;
  try{await api(`/api/tasks/${current.id}/start`,{answers:$('answers').value,use_assumptions:assumptions});await poll(current.id,epoch);}
  catch(error){showError(error.message);}finally{$('answer-start').disabled=$('assume-start').disabled=false;}
}
$('answer-start').addEventListener('click',()=>startWith(false));$('assume-start').addEventListener('click',()=>startWith(true));
$('stop').addEventListener('click',async()=>{if(!current)return;$('stop').disabled=true;try{await api(`/api/tasks/${current.id}/stop`,{});clearTimeout(timer);await poll(current.id,epoch);}catch(error){showError(error.message);$('stop').disabled=false;}});
$('new-task').addEventListener('click',()=>resetForm());$('revise').addEventListener('click',()=>resetForm(true));$('rewrite').addEventListener('click',()=>resetForm(true));
$('mode-demo').addEventListener('click',()=>selectMode('demo'));$('mode-online').addEventListener('click',()=>selectMode('online'));
$('help-toggle').addEventListener('click',()=>{$('help').hidden=!$('help').hidden;});
(async()=>{try{config=await api('/api/config');config.request_limits??={model_requests:6,search_requests:2,extract_requests:3};selectMode(config.cloud_online||config.online_enabled?'online':'demo');
  if(config.cloud_online){
    $('workspace-kind').textContent='观照 · 联网调研';$('history-location').textContent='仅当前浏览器可见';$('mode-demo').hidden=true;
    $('help-intro').textContent='输入自己的问题和背景，按关键条件补充信息，再查看真实网页依据和报告。访客无需填写密钥。记录仅当前浏览器可见，清除浏览器Cookie或服务重建后可能无法找回，请及时下载报告。';
    $('result-next').querySelector('p').textContent='请核查引用是否支持结论，及时下载报告留存。本轮每位访客限一次调研。';
    $('revise').hidden=true;
  }
  if(config.public_demo){
    $('workspace-kind').textContent='固定示例 · 未联网';
    $('history-location').textContent='固定示例记录';
    $('mode-online').disabled=true;$('mode-online').textContent='在线调研 · 暂未开放';
    $('help-intro').textContent='本页面提供固定演示，不调用模型或搜索服务。在线调研将在使用额度和访问控制就绪后开放。演示记录由访客共用，服务重启后可能清空，请勿输入个人资料。';
    $('revise').hidden=true;
  }
  const names={model_url:'模型地址',model_key:'模型密钥',model_name:'模型名称',search_key:'搜索密钥'};
  $('config-detail').textContent=config.cloud_online?'服务调用由项目试用额度承担；证据不足或额度到达上限时，会保存已收集的资料。':config.public_demo?'公开展示不需要密钥。':'本机配置：'+Object.entries(config.fields).map(([k,v])=>`${names[k]}${v?'已填写':'未填写'}`).join('，')+'。配置齐全不代表连接已验证。';
  $('tool-limits').textContent=`${config.request_limits.search_requests} / ${config.request_limits.extract_requests} 次`;
  await loadHistory();const id=new URLSearchParams(location.search).get('task');if(id)await openTask(id);
}catch(error){showError('暂时无法连接观照服务，请稍后刷新。');}})();
