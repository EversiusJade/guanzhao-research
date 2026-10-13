import pytest


def test_duplicate_create_survives_reopen(tmp_path):
    from app.schemas import TaskInput
    from app.store import Store
    db = tmp_path / 'research.db'
    first = Store(db).create(TaskInput(question='比较两种 AI 项目实现方式'), 'request-1')
    again = Store(db).create(TaskInput(question='不同输入'), 'request-1')
    assert again.id == first.id
    assert again.input.question == '比较两种 AI 项目实现方式'


def test_restart_preserves_waiting_and_interrupts_running(tmp_path):
    from app.schemas import TaskInput, Source
    from app.store import Store
    store = Store(tmp_path / 'research.db')
    running = store.create(TaskInput(question='比较平台'), 'a')
    running.state = 'running'
    running.sources = [Source(id='s1', title='官方', url='https://example.com', text='支持工具调用')]
    store.save(running)
    waiting = store.create(TaskInput(question='选择工具'), 'b')
    waiting.state = 'clarifying'
    store.save(waiting)
    assert store.interrupt_pending() == 1
    assert store.get(running.id).state == 'interrupted'
    assert store.get(running.id).sources[0].text == '支持工具调用'
    assert store.get(waiting.id).state == 'clarifying'


@pytest.mark.parametrize('question', ['', '   ', 'x' * 2001])
def test_invalid_question_rejected(question):
    from app.schemas import TaskInput
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TaskInput(question=question)


def test_recent_limits_and_missing_task(tmp_path):
    from app.schemas import TaskInput
    from app.store import Store
    store = Store(tmp_path / 'research.db')
    for n in range(23):
        store.create(TaskInput(question=f'问题{n}'), str(n))
    assert len(store.recent()) == 20
    with pytest.raises(KeyError):
        store.get('missing')
