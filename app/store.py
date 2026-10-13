import sqlite3
import uuid
from pathlib import Path
from app.schemas import TaskInput, TaskRecord, Event, now


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, updated TEXT NOT NULL, payload TEXT NOT NULL)')
            if 'owner' not in {row[1] for row in db.execute('PRAGMA table_info(tasks)')}:
                db.execute("ALTER TABLE tasks ADD COLUMN owner TEXT NOT NULL DEFAULT 'local'")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def create(self, input: TaskInput, request_id: str, owner: str = 'local', task_limit: int | None = None) -> TaskRecord:
        task = TaskRecord(id=uuid.uuid4().hex, input=input)
        key = request_id if owner=='local' else owner+':'+request_id
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM tasks WHERE request_id=? AND owner=?',(key,owner)).fetchone()
            if row:
                return TaskRecord.model_validate_json(row[0])
            if task_limit is not None:
                count = db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
                own_count = db.execute('SELECT COUNT(*) FROM tasks WHERE owner=?',(owner,)).fetchone()[0]
                if count>=task_limit:
                    raise ValueError('本轮全站调研名额已用完，暂无新的付费调用。')
                if own_count>=1:
                    raise ValueError('本轮每位访客限一次调研，请查看已有记录。')
            db.execute('INSERT INTO tasks (id,request_id,updated,payload,owner) VALUES (?,?,?,?,?)',
                       (task.id, key, task.updated_at, task.model_dump_json(),owner))
            row = db.execute('SELECT payload FROM tasks WHERE request_id=? AND owner=?', (key,owner)).fetchone()
        return TaskRecord.model_validate_json(row[0])

    def get(self, id: str, owner: str | None = None) -> TaskRecord:
        with self.connect() as db:
            row = db.execute('SELECT payload FROM tasks WHERE id=?'+(' AND owner=?' if owner is not None else ''),
                             (id,owner) if owner is not None else (id,)).fetchone()
        if not row:
            raise KeyError(id)
        return TaskRecord.model_validate_json(row[0])

    def save(self, task: TaskRecord) -> None:
        task.updated_at = now()
        with self.connect() as db:
            db.execute('UPDATE tasks SET payload=?, updated=? WHERE id=?',
                       (task.model_dump_json(), task.updated_at, task.id))

    def recent(self, limit: int = 20, owner: str | None = None) -> list[TaskRecord]:
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM tasks'+(' WHERE owner=?' if owner is not None else '')+' ORDER BY updated DESC LIMIT ?',
                              (owner,limit) if owner is not None else (limit,)).fetchall()
        return [TaskRecord.model_validate_json(r[0]) for r in rows]

    def count(self) -> int:
        with self.connect() as db:
            return db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]

    def interrupt_pending(self) -> int:
        count = 0
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM tasks').fetchall()
            for row in rows:
                task = TaskRecord.model_validate_json(row[0])
                if task.state in ('preparing', 'running'):
                    task.state = 'interrupted'
                    task.error = '服务已重启，任务已中断。已有资料仍保留；请新建任务重新调研。'
                    task.events.append(Event(kind='interrupted', message=task.error))
                    task.updated_at = now()
                    db.execute('UPDATE tasks SET payload=?, updated=? WHERE id=?',
                               (task.model_dump_json(), task.updated_at, task.id))
                    count += 1
        return count
