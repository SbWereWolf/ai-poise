import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]/'src/poise'


def test_new_applications_use_ports_not_io():
    for name in ('capabilities','hook_transport'):
        source=(ROOT/'application'/f'{name}.py').read_text()
        for node in ast.walk(ast.parse(source)):
            imports=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            assert not any(x.split('.')[0] in {'os','pathlib','sqlite3','subprocess','time'} or 'infrastructure' in x for x in imports)


def test_native_hook_transport_changes_work_only_through_worktools():
    source=(ROOT/'infrastructure/hook_transport.py').read_text()
    assert 'WorkTools(h).invoke(req)' in source
    assert 'UPDATE tasks' not in source and 'INSERT INTO tasks' not in source
    assert 'task.accept' not in source and 'task.complete' not in source
    assert 'dangerously-bypass' not in source
