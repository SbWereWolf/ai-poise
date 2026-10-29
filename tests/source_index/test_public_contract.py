"""Public policy/binding producer and real external consumer contracts."""
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from .helpers import Scene, assert_failure, literal, phases


def test_explicit_registered_copy_uses_product_local_storage(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    code, result = scene.query()
    assert (code, result['status']) == (0, 'complete')
    assert set(result) == set(literal('result-fields.json'))
    assert result['operation'] == 'query'
    assert result['reason'] is result['recovery'] is None
    assert result['checkout'] == str(scene.checkout)
    assert result['database'] == str(scene.database)
    assert scene.output(result)
    assert subprocess.run(['git', 'check-ignore', '-q', str(scene.database)],
                          cwd=scene.checkout).returncode == 0
    rows = scene.records()
    assert all(row['cwd'] == row['root'] == str(scene.checkout) for row in rows)
    assert all(row['database'] == str(scene.database) for row in rows)
    assert phases(scene).index('update') < phases(scene).index('usages')
    sibling = scene.sibling()
    code, result = scene.invoke('rebuild', checkout=sibling)
    assert (code, result['database']) == (0, str(sibling / '.runtime/ast-index/index.db'))
    assert scene.database.is_file()


def test_missing_index_exit_zero_requires_explicit_rebuild(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require()
    assert not scene.database.exists()
    code, result = scene.query()
    assert_failure(code, result, 'unavailable', 'index')
    assert 'rebuild' in str(result['recovery'])
    assert 'update' not in phases(scene) and 'rebuild' not in phases(scene)
    scene.ready()
    code, result = scene.invoke('status')
    assert (code, result['status']) == (0, 'ready')


def test_missing_tool_reports_selected_executable(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require()
    missing = scene.parent / 'not installed ast-index'
    scene.policy_data['command_argv'] = [str(missing)]; scene.save()
    code, result = scene.invoke('status')
    assert_failure(code, result, 'unavailable', 'tool')
    assert str(missing) in str(result)
    assert scene.records() == []


@pytest.mark.parametrize('case', ['missing-command', 'nonpositive-lock',
                                 'database-outside-copy', 'database-not-ignored'])
def test_invalid_policy_has_no_index_effect(project, tmp_path, case):
    scene = Scene(project, tmp_path); scene.require()
    foreign = scene.checkout.parent / 'foreign'; foreign.mkdir()
    sentinel = foreign / 'index.db'; sentinel.write_bytes(b'FOREIGN_SENTINEL')
    current = scene.checkout / 'src/index.db'; current.write_bytes(b'CURRENT_SENTINEL')
    if case == 'missing-command':
        del scene.policy_data['command_argv']
    elif case == 'nonpositive-lock':
        scene.policy_data['limits']['lock_seconds'] = 0
    elif case == 'database-outside-copy':
        scene.policy_data['paths']['database'] = '../foreign/index.db'
    else:
        scene.policy_data['paths']['database'] = 'src/index.db'
    scene.save()
    before = {str(path.relative_to(scene.checkout)): path.read_bytes()
              for path in scene.checkout.rglob('*')
              if path.is_file() and '.git' not in path.parts}
    code, result = scene.invoke('rebuild')
    assert_failure(code, result, 'rejected', 'database' if case.startswith('database') else
                   ('command' if case == 'missing-command' else 'lock'))
    if case == 'database-outside-copy':
        assert 'outside' in result['reason'].lower() or 'contain' in result['reason'].lower()
    if case == 'database-not-ignored':
        assert 'ignor' in result['reason'].lower()
    assert scene.records() == []
    after = {str(path.relative_to(scene.checkout)): path.read_bytes()
             for path in scene.checkout.rglob('*')
             if path.is_file() and '.git' not in path.parts}
    assert after == before
    assert sentinel.read_bytes() == b'FOREIGN_SENTINEL'
    assert current.read_bytes() == b'CURRENT_SENTINEL'
    assert sorted(path.name for path in foreign.iterdir()) == ['index.db']


def test_foreign_checkout_is_rejected(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require()
    foreign = tmp_path / 'other repository'; foreign.mkdir()
    subprocess.run(['git', 'init', str(foreign)], check=True, capture_output=True)
    code, result = scene.invoke('update', checkout=foreign)
    assert_failure(code, result, 'rejected', 'checkout')
    assert scene.records() == []
    assert sorted(path.name for path in foreign.iterdir()) == ['.git']


def test_disabled_policy_does_not_launch_tool(project, tmp_path):
    scene = Scene(project, tmp_path); scene.require(); scene.ready()
    before = scene.database.read_bytes()
    scene.policy_data['enabled'] = False; scene.save()
    for operation in ('status', 'update', 'query', 'rebuild'):
        code, result = scene.invoke(operation, ['usages', 'net_total'] if operation == 'query' else [])
        assert (code, result['status'], result['operation']) == (0, 'disabled', operation)
    assert scene.records() == []
    assert scene.database.read_bytes() == before
