import os
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
REQUEST_LIMITS = {'model_requests':6,'search_requests':2,'extract_requests':3}
RESEARCH_ROUNDS = 5


@dataclass(frozen=True)
class Settings:
    base_url: str = ''
    api_key: str = ''
    model: str = ''
    tavily_key: str = ''
    public_demo: bool = False
    cloud_online: bool = False
    session_secret: str = ''
    online_until: str = ''
    cloud_task_limit: int = 3

    def cloud_seconds_left(self) -> float:
        try:
            value = datetime.fromisoformat(self.online_until.replace('Z','+00:00'))
            if value.tzinfo is None:
                return 0
            return max(0,value.timestamp()-datetime.now().timestamp())
        except (ValueError,TypeError):
            return 0

    def cloud_ready(self) -> bool:
        return len(self.session_secret)>=32 and self.cloud_seconds_left()>0 and 1<=self.cloud_task_limit<=10

    @classmethod
    def load(cls):
        deployment = os.getenv('GUANZHAO_DEPLOYMENT', 'local')
        public_demo,cloud_online = deployment=='cloud-demo',deployment=='cloud-online'
        if deployment == 'local':
            load_dotenv(ROOT / '.env')
        try:
            task_limit=int(os.getenv('GUANZHAO_TASK_LIMIT','3'))
        except ValueError:
            task_limit=0
        return cls(os.getenv('LLM_BASE_URL', '').rstrip('/'), os.getenv('LLM_API_KEY', '') or os.getenv('DASHSCOPE_API_KEY',''),
                   os.getenv('LLM_MODEL', ''), os.getenv('TAVILY_API_KEY', ''), public_demo,cloud_online,
                   os.getenv('GUANZHAO_SESSION_SECRET',''),os.getenv('GUANZHAO_ONLINE_UNTIL',''),task_limit)

    def public(self):
        fields = {'model_url': bool(self.base_url), 'model_key': bool(self.api_key),
                  'model_name': bool(self.model), 'search_key': bool(self.tavily_key)}
        return {'fields': fields, 'configured': all(fields.values()),
                'online_enabled': not self.public_demo and (not self.cloud_online or self.cloud_ready()),
                'public_demo': self.public_demo,'cloud_online':self.cloud_online,
                'online_until':self.online_until if self.cloud_online else None,
                'demo_available':not self.cloud_online,
                'request_limits':REQUEST_LIMITS}
