import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]/'src/harness'


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


def test_bound_source_route_is_pure_and_preparation_stays_in_application_boundary():
    domain=(ROOT/'modules/hook_transport/domain.py').read_text()
    tree=ast.parse(domain)
    imports=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):imports.extend(item.name for item in node.names)
        elif isinstance(node,ast.ImportFrom):imports.append(node.module or '')
    assert 'BoundSourceRoute' in domain
    assert not any(name.split('.')[0] in {'os','pathlib','sqlite3','subprocess'} for name in imports)

    application=(ROOT/'application/work.py').read_text()
    transport=(ROOT/'infrastructure/hook_transport.py').read_text()
    assert 'prepare_bound_source' in application
    assert 'prepare_bound_source' in transport
    assert 'UPDATE tasks' not in transport and 'INSERT INTO tasks' not in transport
    assert "'selected_source'" not in transport and '"selected_source"' not in transport
