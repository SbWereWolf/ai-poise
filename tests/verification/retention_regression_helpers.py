"""Test-owned observations and concrete controls, never production oracles."""
from copy import deepcopy
import hashlib
from pathlib import Path

from conftest import git
from batch.test_artifact_preflight import snapshot as artifact_snapshot


CONTROL_CASES = ['passed-int', 'passed-float', 'exit-bool', 'exit-float', 'combined']


def assert_control_types(actual, case):
    receipt = actual['receipt']
    passed_type = int if case in ('passed-int', 'combined') else float if case == 'passed-float' else bool
    exit_type = bool if case in ('exit-bool', 'combined') else float if case == 'exit-float' else int
    assert type(receipt['passed']) is passed_type
    assert type(receipt['actual_exit_code']) is exit_type
    assert receipt['passed'] == 1
    assert receipt['actual_exit_code'] == 0


def malformed(value, case):
    actual = deepcopy(value)
    if case in ('passed-int', 'combined'):
        actual['receipt']['passed'] = 1
        assert type(actual['receipt']['passed']) is int
    elif case == 'passed-float':
        actual['receipt']['passed'] = 1.0
        assert type(actual['receipt']['passed']) is float
    if case in ('exit-bool', 'combined'):
        actual['receipt']['actual_exit_code'] = False
        assert type(actual['receipt']['actual_exit_code']) is bool
    elif case == 'exit-float':
        actual['receipt']['actual_exit_code'] = 0.0
        assert type(actual['receipt']['actual_exit_code']) is float
    assert_control_types(actual, case)
    assert actual == value  # Equality hides the concrete JSON type defect.
    return actual


def semantic_snapshot(runtime):
    state = artifact_snapshot(runtime)
    with runtime.store.transaction() as db:
        for table in ('sessions',):
            columns = ','.join('"' + row[1].replace('"', '""') + '"'
                               for row in db.execute(f'PRAGMA table_info("{table}")'))
            state[table] = [tuple(row) for row in db.execute(
                f'SELECT {columns} FROM "{table}" ORDER BY rowid')]
    return state


def git_snapshot(directory):
    directory = Path(directory)
    index = Path(git(directory, 'rev-parse', '--path-format=absolute', '--git-path', 'index'))
    return {'head': git(directory, 'rev-parse', 'HEAD'),
            'status': git(directory, 'status', '--porcelain=v2'),
            'index': hashlib.sha256(index.read_bytes()).hexdigest(),
            'files': {str(p.relative_to(directory)): p.read_bytes()
                      for p in directory.rglob('*')
                      if p.is_file() and not p.is_symlink()
                      and '.git' not in p.relative_to(directory).parts}}


def file_snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in Path(root).rglob('*')
            if p.is_file() and not p.is_symlink()}


def damage_destination(root, case):
    root = Path(root)
    if case == 'prefix':
        return 'runtime/manifests'
    if case == 'directory-file':
        path = root / 'artifacts/blocked'
        path.write_bytes(b'preserve destination file\n')
        return 'artifacts/blocked'
    if case == 'parent-file':
        path = root / 'artifacts/blocked'
        path.write_bytes(b'preserve parent file\n')
        return 'artifacts/blocked/manifests'
    target = (root.parent / 'foreign-directory' if case == 'symlink-outside'
              else root / 'artifacts/real-directory')
    target.mkdir()
    path = root / 'artifacts/alias'
    path.symlink_to(target, target_is_directory=True)
    return 'artifacts/alias/manifests'


def integration_ready(project, methods):
    """Public completed fixture whose repair-stage GREENs are selected at integration."""
    import json
    from batch.helpers import request, result
    from conftest import WorkPoise, bind_task_requirements, write_json
    from poise.application.work import WorkTools
    from verification.retention_helpers import FIXTURES

    cfg = deepcopy(project['cfg'])
    cfg['automatic_checks'] = []
    write_json(project['config_path'], cfg)
    process = deepcopy(project['process'])
    stages = json.loads((FIXTURES / 'retention_integration_route.json').read_text())
    process.update(route={'entry': 'implementation'}, stages=stages)
    write_json(project['root'] / 'config/processes/development.json', process)
    task = deepcopy(project['task'])
    task['methods'] = deepcopy(methods)
    for method in task['methods']:
        method['verification_plan']['green_stages'] = ['repair']
    task['method_inputs'] = [{
        'method_id': method['id'], 'repository_inputs': [], 'future_outputs': [],
        'reference_profile': {'runner': 'python', 'parser': 'inline-no-path-arguments', 'version': 1},
    } for method in methods]
    task['checks'] = {'implementation': [], 'inspection': [], 'repair': [m['id'] for m in methods]}
    task['evidence_plan'] = {s['id']: {'subject_methods': {}, 'arguments': [], 'review_arguments': []}
                             for s in stages}
    task['stage_contracts'] = [{'stage_id': s['id'], 'allowed_paths': s['allowed_paths'],
                               'entry_requirements': [], 'exit_requirements': []} for s in stages]
    task['decomposition']['phases'] = [{'stage': s['id'], 'skills': ['task-domain'], 'areas': []}
                                     for s in stages]
    bind_task_requirements(task, project['requirements_registry'])
    tools = WorkTools(WorkPoise(project['config_path'], 'integration-fixture'))
    context = tools.invoke(request('bootstrap', {
        'task': task, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    worktree = Path(context['worktree'])
    (worktree / 'src/double.py').write_text('def double(n):\n    return n * 2\n')
    initial = tools.invoke(request('verify', {'result': result(context), 'artifacts': []}))
    assert initial['status'] == 'verified'
    tools.invoke(request('advance', {'request_id': 'fixture-to-inspection',
                                     'task_id': 'T1', 'target_stage': 'inspection'}))
    context = tools.invoke(request('bootstrap', {
        'task': None, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    assert context['stage'] == 'inspection'
    inspected = result(context)
    inspected['stage_work'] = {'coverage': 'Fixture result has no findings.',
                               'findings': [], 'resolution_decisions': []}
    done = tools.invoke(request('verify', {'result': inspected, 'artifacts': []}))
    assert done['stage_outcome'] == 'clear'
    assert tools.invoke(request('accept', {}))['status'] == 'completed'
    return tools, worktree, done['commit']
