from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TaskInput(Model):
    question: str = Field(min_length=1, max_length=2000)
    context: str = Field(default='', max_length=4000)
    mode: Literal['demo', 'online'] = 'online'

    @field_validator('question')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('请输入一个具体问题')
        return value.strip()


TaskState = Literal['draft', 'preparing', 'clarifying', 'ready', 'running',
                    'completed', 'partial', 'stopped', 'failed', 'interrupted']


class SearchHit(Model):
    id: str
    title: str
    url: str
    snippet: str = ''
    published_at: str | None = None


class Source(Model):
    id: str
    title: str
    url: str
    text: str
    retrieved_at: str = Field(default_factory=now)
    published_at: str | None = None
    truncated: bool = False
    research_question: str = Field(default='', max_length=500)
    why_selected: str = Field(default='', max_length=500)
    reference_kind: Literal['live', 'preset', 'illustration'] = 'live'


class Citation(Model):
    source_id: str
    quote: str = Field(min_length=1, max_length=200)

    @field_validator('quote')
    @classmethod
    def nonblank_quote(cls, value):
        if not value.strip():
            raise ValueError('引用原文不能为空白')
        return value


class Claim(Model):
    text: str = Field(min_length=1, max_length=1000)
    citations: list[Citation] = Field(min_length=1, max_length=3)
    decision_question: str = Field(default='', max_length=500)
    relevance: str = Field(default='', max_length=500)
    limitation: str = Field(default='', max_length=500)


class Option(Model):
    name: str
    conditions: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    costs: list[str] = Field(default_factory=list)
    claim_ids: list[int] = Field(default_factory=list)
    why_it_matters: str = Field(default='', max_length=500)
    pivotal_uncertainty: str = Field(default='', max_length=500)


class Perspective(Model):
    name: str = Field(min_length=1, max_length=100)
    decision_question: str = Field(min_length=1, max_length=300)
    why_it_matters: str = Field(min_length=1, max_length=300)


class Understanding(Model):
    goal: str = Field(min_length=1, max_length=500)
    decision: str = Field(min_length=1, max_length=500)
    constraints: list[str] = Field(default_factory=list, max_length=5)
    observations: list[str] = Field(default_factory=list, max_length=5)
    preferences: list[str] = Field(default_factory=list, max_length=5)
    user_assumptions: list[str] = Field(default_factory=list, max_length=3)
    ai_assumptions: list[str] = Field(default_factory=list, max_length=3)
    consequential_unknowns: list[str] = Field(default_factory=list, max_length=3)
    perspectives: list[Perspective] = Field(default_factory=list, max_length=3)
    needs_confirmation: bool = False


class Report(Model):
    summary: str
    assumptions: list[str] = Field(default_factory=list)
    facts: list[Claim] = Field(default_factory=list, max_length=12)
    options: list[Option] = Field(default_factory=list, max_length=3)
    inferences: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    next_step: str = ''
    incomplete: bool = False
    reason: str | None = None
    recommendation_conditions: list[str] = Field(default_factory=list, max_length=3)
    reversal_conditions: list[str] = Field(default_factory=list, max_length=3)
    continuations: list[str] = Field(default_factory=list, max_length=3)


class Event(Model):
    at: str = Field(default_factory=now)
    kind: str
    message: str


class Usage(Model):
    model_requests: int = 0
    search_requests: int = 0
    extract_requests: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


class TaskRecord(Model):
    id: str
    input: TaskInput
    state: TaskState = 'draft'
    in_scope: bool | None = None
    scope_message: str | None = None
    questions: list[str] = Field(default_factory=list)
    answers: str = ''
    assumptions: list[str] = Field(default_factory=list)
    understanding: Understanding | None = None
    events: list[Event] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    report: Report | None = None
    usage: Usage = Field(default_factory=Usage)
    error: str | None = None
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class ModelReply(Model):
    content: str | None = None
    tool_calls: list[dict] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None


class Preparation(Model):
    in_scope: bool
    scope_message: str | None = None
    questions: list[str] = Field(default_factory=list, max_length=3)
    assumptions: list[str] = Field(default_factory=list, max_length=5)
    understanding: Understanding | None = None
