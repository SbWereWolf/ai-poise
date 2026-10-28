"""V04/V05: opt-in GREEN operational checks, never run as missing-feature RED."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import socket
import sqlite3
from urllib.parse import urlparse

import pytest

from batch.helpers import request
from conftest import git
from live_application_support import (
    application_runtime, capture, clone_source, configured_private_project, profile_inputs,
    receipt, sdk_operation, verify_launch, write,
)


@pytest.fixture(scope='module')
def operational():
    value = json.loads(Path(os.environ['AIP_LIVE_DEPLOYMENT_OPERATIONAL']).read_text())
    assert value['schema'] == 'live-deployment-operational-inputs-1'
    root = Path(value['fixture_root'])
    assert root.is_dir() and root.is_relative_to(Path(value['task_root']))
    return value


@pytest.fixture
def tmp_path(operational):
    # Required consumers can inspect these cases until parent-Task retirement.
    # Pytest cache retention must not silently delete an earlier native receipt.
    return Path(tempfile.mkdtemp(prefix='application-', dir=operational['fixture_root']))


@pytest.mark.parametrize('application', ['erp', 'poise-op'])
def test_public_owner_prepares_a_real_isolated_application(project, operational, tmp_path, application):
    selected = operational['applications'][application]
    repository = tmp_path / 'application/source'
    repository.parent.mkdir()
    commit = clone_source(operational, selected['source_repository'], repository, tmp_path)
    tools, task, config = configured_private_project(project, application,
        repository, selected['preparation_configuration'])
    reply = tools.invoke(request('bootstrap', {'task': task, 'decision': None,
        'feedback': None, 'rework_stage': None}))
    report = receipt(reply)
    root = Path(reply['worktree'])
    assert root.is_relative_to(repository.parent) and not root.is_relative_to(project['root'])
    assert git(root, 'rev-parse', 'HEAD') == commit
    assert git(root, 'symbolic-ref', '--short', 'HEAD') == 'tasks/T1'
    assert report['project_id'] == application and report['worktree'] == str(root)
    assert report['configuration']['sha256'] == hashlib.sha256(
        Path(selected['preparation_configuration']).read_bytes()).hexdigest()
    app, parameters = profile_inputs(report)
    assert app.is_relative_to(Path(report['task_root']))
    values = json.loads(parameters.read_text())
    identity_key = 'instance' if application == 'erp' else 'repository'
    assert values[identity_key] == str(root)
    runtime = application_runtime(operational, application, root)
    checked = sdk_operation(runtime, 'check', app, parameters, root, tmp_path, 'ready-check')
    assert checked['resources'] and all(row['presence']['success'] for row in checked['resources'])
    assert all(row['availability']['status'] != 'failed' for row in checked['resources'])
    expected = (['environment', 'base', 'applications'] if application == 'erp'
                else ['target-repository', 'poise-task-database', 'poise-python-dependencies'])
    assert set(expected) <= {row['id'] for row in checked['resources']}
    environment = {'PATH': runtime['path'], 'LANG': 'C.UTF-8',
                   'PYTHONPATH': runtime['module_path']}
    check_argv = [runtime['sdk_entry'], 'check', '--config', str(app),
        '--parameters', str(parameters), '--base-dir', str(root)]
    if application == 'erp':
        endpoint = urlparse(selected['launch_observation']['health_url'])
        with socket.socket() as occupied:
            occupied.bind((endpoint.hostname, endpoint.port))
            occupied.listen(1)
            failed, stdout = capture(check_argv, cwd=root, env=environment,
                root=tmp_path, label='occupied-port-check')
            packet = json.loads(stdout)
            assert failed['actual_exit_code'] != 0 and packet['status'] == 'failed'
            assert any(row['id'] == 'ports' and not row['result']['success']
                       for row in packet['checks']), packet
    else:
        manifest = json.loads((app.parent / 'selected-project/project.json').read_text())
        database = Path(manifest['paths']['requirements_database'])
        assert database.is_file() and database.is_relative_to(tmp_path)
        held = database.with_name(database.name + '.test-held')
        database.rename(held)
        try:
            failed, stdout = capture(check_argv, cwd=root, env=environment,
                root=tmp_path, label='missing-resource-check')
            packet = json.loads(stdout)
            assert failed['actual_exit_code'] != 0 and packet['status'] == 'failed'
            assert any(row['id'] == 'poise-requirements-database'
                and not row['presence']['success'] for row in packet['resources']), packet
            assert not database.exists(), 'Read-only check must not replace missing user data'
        finally:
            if not database.exists():
                held.rename(database)
    sdk_operation(runtime, 'check', app, parameters, root, tmp_path, 'restored-ready-check')
    preserved_configuration = {}
    preserved_requirement = None
    if application == 'erp':
        for relative in (
            'platform-infrastructure/config/dev/local-development.settings.json',
            'platform-infrastructure/config/dev/local-dev-host-runtime.settings.json',
            'platform-infrastructure/tests/playwright/cjm.local-development.settings.json',
        ):
            file = root / relative
            preserved_configuration[file] = file.read_bytes()
    else:
        from poise.application.requirements_registry import RequirementsCommands
        from poise.infrastructure.requirements_registry import RequirementsStore

        paths, limits = manifest['paths'], manifest['limits']
        store = RequirementsStore(database, Path(paths['requirements_lock']),
            limits['lock_seconds'], limits['lock_poll_seconds'])
        preserved_requirement = ('DEPLOYMENT-DATA-SENTINEL', 'system', 'current',
                                 'Existing application data survives preparation.')
        RequirementsCommands(store, 10).apply({
            'request_id': 'seed-existing-application-data',
            'expected_revision': store.revision(),
            'operations': [{'kind': 'put_requirement', 'requirement': dict(zip(
                ('id', 'level', 'status', 'text'), preserved_requirement))}],
        })
    sentinel = root / '.runtime/kept-user-data.bin'
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_bytes(b'existing application data\x00')
    old = Path(reply['preparation']['receipt_path'])
    old_bytes = old.read_bytes()
    repeated = tools.invoke(request('bootstrap', {'task': {'id': task['id']},
        'decision': None, 'feedback': None, 'rework_stage': None,
        'preparation_request_id': 'independent-second-preparation'}))
    receipt(repeated)
    assert sentinel.read_bytes() == b'existing application data\x00'
    assert old.read_bytes() == old_bytes and repeated['worktree'] == str(root)
    assert repeated['preparation']['attempt_id'] != reply['preparation']['attempt_id']
    assert {path: path.read_bytes() for path in preserved_configuration} == preserved_configuration
    if preserved_requirement is not None:
        with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as connection:
            row = connection.execute(
                'SELECT id, level, status, text FROM requirements WHERE id = ?',
                (preserved_requirement[0],),
            ).fetchone()
        assert row == preserved_requirement
    verify_launch(operational, application, app, parameters, root,
        tmp_path, selected['launch_observation'])
    proof = {'application': application, 'source_commit': commit, 'worktree': str(root),
        'first_preparation': reply['preparation'], 'second_preparation': repeated['preparation'],
        'config': str(app), 'parameters': str(parameters), 'isolated_config': config,
        'scope': 'Real public API/Git/application fixture; not a second live host-native Task.'}
    write(tmp_path / 'public-preparation-proof.json', proof)
    # Release the synthetic fixture claim; keep its exact material for review.
    released = tools.invoke(request('release', {'task_id': task['id'],
        'request_id': 'release-isolated-fixture', 'expected_version': repeated['version'],
        'reason': 'Fixture observation complete; parent Task retains the working materials.'}))
    assert released['after']['task_id'] is None and released['after']['worktree_task_id'] is None


@pytest.mark.parametrize('application', ['erp', 'poise-op'])
def test_live_configuration_really_prepares_checks_and_launches(operational, tmp_path, application):
    selected = operational['live_applications'][application]
    config, parameters, root = (Path(selected[key]) for key in ('config', 'parameters', 'base'))
    runtime = application_runtime(operational, application, root)
    stages = ['infra', 'deps'] + (['adjust'] if application == 'erp' else []) + ['check']
    for operation in stages:
        sdk_operation(runtime, operation, config, parameters, root,
                      tmp_path, 'live-' + operation)
    verify_launch(operational, application, config, parameters, root,
        tmp_path, selected['launch_observation'])


def test_real_host_native_refresh_and_all_consumer_update_are_confirmed(operational, tmp_path):
    declared = operational['native_refresh']
    path = Path(declared['receipt_path'])
    data = json.loads(path.read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == declared['receipt_sha256']
    assert data['task_id'] == operational['task_id'] and data['project_id'] == 'poise-op'
    assert data['attempt_id'] != declared['previous_attempt_id'] and data['status'] == 'ready'
    assert Path(declared['previous_receipt_path']).read_bytes() == Path(
        declared['previous_receipt_snapshot']).read_bytes()
    profile_inputs(data)
    configured = Path(operational['consumer_check_config'])
    parameters = Path(operational['consumer_check_parameters'])
    result = sdk_operation(operational, 'check', configured, parameters,
        Path(operational['operator_root']), tmp_path, 'all-live-consumers')
    assert result['checks'] and all(row['result']['success'] for row in result['checks'])
