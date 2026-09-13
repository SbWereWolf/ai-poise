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


def test_task_work_never_selects_or_dispatches_a_task_owned_harness():
    domain=(ROOT/'modules/hook_transport/domain.py').read_text()
    tree=ast.parse(domain)
    imports=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):imports.extend(item.name for item in node.names)
        elif isinstance(node,ast.ImportFrom):imports.append(node.module or '')
    assert 'BoundSourceRoute' not in domain
    assert not any(name.split('.')[0] in {'os','pathlib','sqlite3','subprocess'} for name in imports)

    application=(ROOT/'application/work.py').read_text()
    transport=(ROOT/'infrastructure/hook_transport.py').read_text()
    assert 'prepare_bound_source' not in application
    assert '_dispatch_bound_source' not in transport
    assert 'POISE_NATIVE_BOUND_SOURCE' not in transport
    assert 'UPDATE tasks' not in transport and 'INSERT INTO tasks' not in transport

    transport_tree=ast.parse(transport)
    hook_service=next(node for node in transport_tree.body if isinstance(node,ast.ClassDef) and node.name=='HookService')
    bind=next(node for node in hook_service.body if isinstance(node,ast.FunctionDef) and node.name=='_bind')
    work=next(node for node in hook_service.body if isinstance(node,ast.FunctionDef) and node.name=='work')
    record=next(
        node.value for node in ast.walk(bind)
        if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=='record' for target in node.targets)
        and isinstance(node.value,ast.Dict)
    )
    keys={key.value for key in record.keys if isinstance(key,ast.Constant)}
    assert not keys.intersection({'task','task_id','worktree','selected_source'})
    assert not any(
        isinstance(node,ast.Call)
        and isinstance(node.func,ast.Attribute)
        and node.func.attr in {'bind','atomic_write'}
        for node in ast.walk(work)
    )
