import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]/'src/poise'


def test_handoff_application_has_no_io_and_calls_task_owner():
    source=(ROOT/'application/handoff.py').read_text()
    assert 'release_task_in(uow, actor,' in source and 'task.resume_handoff(' in source
    assert 'uow.tasks.load_newborn(' in source and 'uow.tasks.acquire_newborn(' in source
    ownership=(ROOT/'application/ownership.py').read_text()
    assert 'task.handoff(' in ownership and 'uow.tasks.release_newborn(' in ownership
    for n in ast.walk(ast.parse(source)):
        modules=[x.name for x in n.names] if isinstance(n,ast.Import) else [n.module or ''] if isinstance(n,ast.ImportFrom) else []
        assert not any(x.split('.')[0] in {'os','pathlib','sqlite3','subprocess'} or 'infrastructure' in x for x in modules)


def test_result_worker_cannot_reexecute_project_command_or_mutate_task():
    source=(ROOT/'output_worker.py').read_text()
    assert 'run_command' not in source and 'WorkTools' not in source and 'Task' not in source
    parser=(ROOT/'infrastructure/result_views.py').read_text()
    assert 'UPDATE tasks' not in parser and 'INSERT INTO tasks' not in parser


def test_runtime_adapter_delegates_to_common_work_api_and_does_not_store_prompts():
    source=(ROOT/'infrastructure/runtime_adapter.py').read_text()
    assert 'WorkTools(h).invoke(work)' in source
    assert "'message':payload" not in source
