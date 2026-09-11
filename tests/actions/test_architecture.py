import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]/'src/poise'


def test_plan_application_uses_ports_without_io_or_lifecycle_assignment():
    source=(ROOT/'application/actions.py').read_text();violations=[]
    for n in ast.walk(ast.parse(source)):
        if isinstance(n,ast.Import):modules=[x.name for x in n.names]
        elif isinstance(n,ast.ImportFrom):modules=[n.module or '']
        else:modules=[]
        violations.extend(m for m in modules if any(t in m for t in ('infrastructure','subprocess','sqlite3','pathlib')))
    assert not violations
    assert 'task.restart_action' in source and 'uow.tasks.save' in source


def test_external_executor_does_not_write_task_tables_or_use_goal_type_dispatch():
    source=(ROOT/'infrastructure/actions.py').read_text()
    assert 'goal_type' not in source
    assert '.execute(' not in source and 'UPDATE tasks' not in source
    assert 'self._save(' in source and 'self._obtain(' in source
