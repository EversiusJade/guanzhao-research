import asyncio
import json
import os
import hashlib
import hmac
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import Field
from app.config import ROOT, Settings
from app.schemas import Model, TaskInput
from app.store import Store
from app.providers import Providers
from app.agent import Agent
from app.reports import to_markdown, partial_report


class CreateTask(Model):
    request_id: str = Field(min_length=1, max_length=100)
    input: TaskInput


class StartTask(Model):
    answers: str = Field(default='', max_length=2000)
    use_assumptions: bool = False


def create_app(settings: Settings | None = None, db_path: Path | None = None) -> FastAPI:
    settings = settings or Settings.load()
    default_db = (Path(tempfile.gettempdir())/'guanzhao-online/research.db' if settings.cloud_online else
                  Path(tempfile.gettempdir())/'guanzhao/research.db' if settings.public_demo else ROOT/'data/research.db')
    os_data_dir=os.getenv('GUANZHAO_DATA_DIR')
    if settings.cloud_online and os_data_dir:
        default_db=Path(os_data_dir)/'research.db'
    store = Store(db_path or default_db)
    demo_path = Path(__file__).parent/'fixtures/demo.json'
    if not demo_path.exists():
        demo_path = ROOT/'fixtures/demo.json'
    demo = json.loads(demo_path.read_text(encoding='utf-8'))
    jobs = {}

    @asynccontextmanager
    async def lifespan(app):
        store.interrupt_pending()
        async with httpx.AsyncClient() as client:
            app.state.agent = Agent(store,Providers(settings,client))
            yield
            for job,cancel in list(jobs.values()):
                cancel.set(); job.cancel()
            if jobs:
                await asyncio.gather(*(job for job,_ in list(jobs.values())),return_exceptions=True)

    app=FastAPI(title='观照 · 多视角调研',lifespan=lifespan,docs_url=None,redoc_url=None)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['*'] if settings.public_demo or settings.cloud_online else ['127.0.0.1','localhost'])

    def session_signature(value):
        return hmac.new(settings.session_secret.encode(),value.encode(),hashlib.sha256).hexdigest()

    def owner(request):
        if settings.cloud_online and not request.state.owner:
            raise HTTPException(503,'云端访客配置尚未就绪。')
        return request.state.owner

    @app.middleware('http')
    async def local_origin(request: Request,call_next):
        request.state.owner='local'
        new_cookie=None
        if settings.cloud_online:
            request.state.owner=''
            if len(settings.session_secret)>=32:
                cookie=request.cookies.get('guanzhao_session','')
                value,_,signature=cookie.partition('.')
                if 16<=len(value)<=64 and len(signature)==64 and hmac.compare_digest(signature.encode(),session_signature(value).encode()):
                    request.state.owner=value
                else:
                    request.state.owner=secrets.token_urlsafe(24)
                    new_cookie=request.state.owner+'.'+session_signature(request.state.owner)
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin=request.headers.get('origin')
            expected=f'{request.url.scheme}://{request.headers.get("host","")}'
            same_origin=not origin or origin.rstrip('/')==expected
            if settings.cloud_online and origin:
                try:
                    parsed=urlsplit(origin)
                    same_origin=parsed.scheme in ('http','https') and parsed.netloc==request.headers.get('host','')
                except ValueError:
                    same_origin=False
            if not same_origin or request.headers.get('sec-fetch-site')=='cross-site':
                return JSONResponse({'detail':'请从观照应用页面操作。'},status_code=403)
        response=await call_next(request)
        if new_cookie:
            response.set_cookie('guanzhao_session',new_cookie,max_age=3600,httponly=True,secure=True,samesite='strict')
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    def get_task(id,request):
        try:
            return store.get(id,owner(request) if settings.cloud_online else None)
        except KeyError:
            raise HTTPException(404,'没有找到这次调研。') from None

    def check_available(task_input):
        if settings.cloud_online and not settings.cloud_ready():
            raise HTTPException(403,'本轮联网名额尚未开放或使用时间已结束。')
        if task_input.mode=='online' and settings.public_demo:
            raise HTTPException(403,'公开展示版暂未开放在线调研，可以免费体验演示流程。')
        if any(not job.done() for job,_ in jobs.values()):
            raise HTTPException(409,'已有任务正在执行，请等待或先停止它。')
        if task_input.mode=='online' and not settings.public()['configured']:
            raise HTTPException(409,'服务配置尚未齐全，请联系项目维护者完成模型和搜索服务配置。' if settings.cloud_online else '在线配置尚未齐全。请在本机 .env 填写百炼和搜索服务配置，重启后再试；现在可以体验演示模式。')

    def launch(id,coroutine,cancel):
        async def wrapped():
            try:
                await coroutine
            finally:
                jobs.pop(id,None)
        jobs[id]=(asyncio.create_task(wrapped()),cancel)

    @app.get('/api/config')
    def config():
        return settings.public() | {'application_id':'guanzhao-research','version':'0.3.1',
            'demo_question':demo['question'],'demo_context':demo['context'],
            'remaining_tasks':max(0,settings.cloud_task_limit-store.count()) if settings.cloud_online else None}

    @app.get('/health')
    def health():
        return {'status':'ok','application':'guanzhao-research'}

    @app.post('/api/tasks')
    def create(data: CreateTask,request: Request):
        if settings.cloud_online:
            if data.input.mode!='online':
                raise HTTPException(422,'云端联网版请使用自己的问题进行在线调研。')
            check_available(data.input)
        if settings.public_demo and data.input.mode!='demo':
            raise HTTPException(403,'公开展示版暂未开放在线调研，可以免费体验演示流程。')
        if data.input.mode=='demo' and data.input.question != demo['question']:
            raise HTTPException(422,'演示模式只使用固定示例。请切换在线模式研究自己的问题。')
        if data.input.mode=='demo':
            data.input.context=demo['context']
        try:
            return store.create(data.input,data.request_id,owner(request),settings.cloud_task_limit if settings.cloud_online else None)
        except ValueError as exc:
            raise HTTPException(429,str(exc)) from None

    @app.get('/api/tasks')
    def recent(request: Request):
        return [{'id':t.id,'question':t.input.question,'mode':t.input.mode,'state':t.state,'updated_at':t.updated_at} for t in store.recent(owner=owner(request) if settings.cloud_online else None)]

    @app.get('/api/tasks/{id}')
    def get(id: str,request: Request):
        return get_task(id,request)

    @app.post('/api/tasks/{id}/prepare')
    async def prepare(id: str,request: Request):
        task=get_task(id,request)
        if task.state!='draft':
            raise HTTPException(409,'该任务已经准备过。请查看当前状态或新建调研。')
        check_available(task.input)
        task.state='preparing';store.save(task)
        launch(id,app.state.agent.prepare(id),asyncio.Event())
        return {'id':id,'state':'preparing'}

    @app.post('/api/tasks/{id}/start')
    async def start(id: str,data: StartTask,request: Request):
        task=get_task(id,request)
        if task.state not in ('ready','clarifying') or task.in_scope is not True:
            raise HTTPException(409,'任务尚未就绪或已结束，请先确认问题范围。')
        if task.questions and not data.answers.strip() and not data.use_assumptions:
            raise HTTPException(422,'请补充信息，或选择按假设继续。')
        check_available(task.input)
        task.answers=data.answers.strip()
        if data.use_assumptions and task.questions and not task.assumptions:
            task.assumptions=['未提供澄清信息，本次仅作一般条件下的方案比较，不视为已确认个人条件。']
        if task.answers:
            task.assumptions=[]
        task.state='running';store.save(task)
        cancel=asyncio.Event()
        launch(id,app.state.agent.run(id,cancel),cancel)
        return {'id':id,'state':'running'}

    @app.post('/api/tasks/{id}/revise')
    def revise(id: str,data: TaskInput,request: Request):
        task=get_task(id,request)
        if task.state not in ('clarifying','ready') or data.mode!=task.input.mode:
            raise HTTPException(409,'仅能在正式调研前修改当前问题。')
        check_available(data)
        task.input=data
        task.state='draft'
        task.in_scope=task.scope_message=task.understanding=task.error=None
        task.questions=[];task.assumptions=[];task.answers=''
        store.save(task)
        return task

    @app.post('/api/tasks/{id}/stop')
    async def stop(id: str,request: Request):
        task=get_task(id,request)
        if id in jobs:
            job,cancel=jobs[id]
            cancel.set();job.cancel()
            await asyncio.gather(job,return_exceptions=True)
            task=get_task(id,request)
            if task.state in ('running','preparing'):
                task.state='stopped';task.report=partial_report(task,'用户已停止任务');store.save(task)
            jobs.pop(id,None)
        return get_task(id,request)

    @app.get('/api/tasks/{id}/report.md')
    def export(id: str,request: Request):
        return Response(to_markdown(get_task(id,request)),media_type='text/markdown; charset=utf-8',
                        headers={'Content-Disposition':f'attachment; filename="research-{id}.md"'})

    app.mount('/static',StaticFiles(directory=ROOT/'app/static'),name='static')

    @app.get('/')
    def index():
        return FileResponse(ROOT/'app/static/index.html')

    return app


app=create_app()
