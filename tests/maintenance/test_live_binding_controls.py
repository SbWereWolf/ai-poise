"""Existing public owner controls; these are isolated fixtures, not live Tasks."""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from conftest import git
from live_application_support import configured_private_project, write
from live_binding_support import (
    assert_permanent, native_refresh_observation, owner_context,
    preparation_dependencies_are_permanent, working_binding,
)
from test_live_preparation_contract import FIXTURES, bootstrap, modern_case
from test_consumer_update_gate import consumer_case, sdk


@pytest.fixture
def owned_context(modern_case):
    case = modern_case
    control = json.loads((FIXTURES / 'modern-preparation-control.json').read_text())
    control['parameters'] = case['parameters']
    write(case['configuration'], control)
    previous = bootstrap(case)
    old = Path(previous['preparation']['receipt_path'])
    snapshot = case['project']['root'] / 'previous-receipt.json'
    snapshot.write_bytes(old.read_bytes())
    current = bootstrap(case, again=True, request_id='owner-control-second-attempt')
    baseline = {'task': case['project']['task']['id'], 'registered_projects': [
        {'id': 'demo', 'config': str(case['project']['config_path']),
         'repository': str(case['project']['app'])}]}
    context = owner_context(baseline, 'demo')
    declared = {'receipt_path': current['preparation']['receipt_path'],
        'receipt_sha256': current['preparation']['receipt_sha256'],
        'previous_attempt_id': previous['preparation']['attempt_id'],
        'previous_receipt_path': str(old), 'previous_receipt_snapshot': str(snapshot)}
    return baseline, context, declared, case


@pytest.mark.parametrize('receipt_kind', ['genuine', 'synthetic', 'wrong-task'])
def test_native_evidence_is_read_from_the_existing_preparation_owner(owned_context, receipt_kind):
    baseline, context, declared, case = owned_context
    if receipt_kind != 'genuine':
        original = json.loads(Path(declared['receipt_path']).read_text())
        original.update(schema='reviewer-negative-fixture/not-native' if receipt_kind == 'synthetic'
                        else original['schema'], task_id='DIFFERENT-TASK')
        detached = write(case['project']['root'] / 'detached.json', original)
        declared.update(receipt_path=str(detached),
                        receipt_sha256=hashlib.sha256(detached.read_bytes()).hexdigest())
        with pytest.raises(AssertionError):
            native_refresh_observation(context, declared)
    else:
        observed = native_refresh_observation(context, declared)
        assert observed['task_id'] == baseline['task']


@pytest.mark.parametrize('location', ['stable', 'task', 'worktree'])
def test_permanent_selection_cannot_depend_on_task_retirement(owned_context, location):
    _, context, _, case = owned_context
    path = {'stable': case['project']['root'] / 'operator/working.json',
            'task': context['task_root'] / 'working.json',
            'worktree': context['worktree'] / 'working.json'}[location]
    if location == 'stable':
        assert_permanent([path], context)
    else:
        with pytest.raises(AssertionError):
            assert_permanent([path], context)


@pytest.mark.parametrize('selection', ['working', 'wrong-sdk', 'task-owned-config'])
def test_working_selection_is_independent_of_a_healthy_supplied_sdk(owned_context, consumer_case, selection):
    baseline, context, _, case = owned_context
    repository = case['project']['app']
    git(repository, 'branch', 'codex/deployment-source')
    _, _, config = configured_private_project(case['project'], 'erp', repository, case['configuration'])
    settings = Path(config['runtime_resources']['project_setup'])
    from poise.composition import project_tools
    listing = project_tools(settings).list()
    registration = next(row for row in listing['projects'] if row['project'] == 'erp')
    baseline['registered_projects'].append({'id': 'erp', 'config': registration['config_path'],
                                          'repository': str(repository)})
    context = owner_context(baseline, 'demo')
    sdk = consumer_case['sdk']
    root = consumer_case['root']
    # The immutable accepted input is used as a byte oracle, not a synthesized
    # installation receipt. All actual module/version probes use the working binding.
    archive = Path(os.environ['AIP_LIVE_DEPLOYMENT_ARCHIVE'])
    baseline['source_hashes'] = {str(archive): hashlib.sha256(archive.read_bytes()).hexdigest()}
    runtime = dict(sdk, module_path='', git='/usr/bin/git')
    (repository / 'start-app').write_text('#!/bin/bash\nexit 0\n')
    python = sdk['sdk_python'] if selection != 'wrong-sdk' else sys.executable
    working = repository / '.runtime/environment-consumer/runtime.env'
    working.parent.mkdir(parents=True, exist_ok=True)
    working.write_text('ERP_ENVIRONMENT_PYTHON_BIN=' + python + '\n')
    config_root = root / 'working-profile'
    for operation in ('infra', 'deps', 'check', 'run'):
        write(config_root / (operation + '.json'), {'requirements': []})
    app = write(config_root / 'app.json', {'operations': {
        name: name + '.json' for name in ('infra', 'deps', 'check', 'run')}})
    parameters = write(config_root / 'parameters.json', {'instance': str(repository)})
    if selection == 'task-owned-config':
        app = write(context['task_root'] / 'bad-live-app.json', json.loads(app.read_text()))
    selected = {'base': str(repository), 'config': str(app), 'parameters': str(parameters)}
    if selection == 'working':
        observed = working_binding(runtime, 'erp', selected, baseline, context, root)
        assert observed['sdk']['python'] == sdk['sdk_python']
    else:
        with pytest.raises(AssertionError):
            working_binding(runtime, 'erp', selected, baseline, context, root)


@pytest.mark.parametrize('selection', ['working', 'wrong-sdk', 'wrong-state', 'fresh-stores'])
def test_poise_binding_uses_its_real_stride_and_bootstrap_selection(owned_context, consumer_case, selection):
    from poise.common import configured_root
    from poise.composition import project_tools
    from poise.infrastructure.projects import ProjectSettings

    baseline, _, _, case = owned_context
    repository = case['project']['app']
    git(repository, 'branch', 'codex/deployment-source')
    tools_owner, _, config = configured_private_project(case['project'], 'poise-op', repository, case['configuration'])
    # An actual invocation initializes this fixture runtime's accounting owner;
    # no parent/live Task is acquired or copied into the application store.
    tools_owner.invoke({'operation': 'show', 'input': {
        'queries': [{'id': 'state', 'kind': 'task'}]}, 'messages': []})
    settings = Path(config['runtime_resources']['project_setup'])
    listing = project_tools(settings).list()
    registration = next(row for row in listing['projects'] if row['project'] == 'poise-op')
    baseline['registered_projects'].append({'id': 'poise-op', 'config': registration['config_path'],
                                          'repository': str(repository)})
    context = owner_context(baseline, 'demo')
    sdk = consumer_case['sdk']
    root = consumer_case['root']
    archive = Path(os.environ['AIP_LIVE_DEPLOYMENT_ARCHIVE'])
    baseline['source_hashes'] = {str(archive): hashlib.sha256(archive.read_bytes()).hexdigest()}
    for name in ('stride.sh', 'bootstrap'):
        (repository / name).write_text('# Test-owned entry boundary; not a live source claim.\n')
    profile = root / 'working-published-profile'
    for operation in ('infra', 'deps', 'check', 'run'):
        write(profile / (operation + '.json'), {'requirements': []})
    app = write(profile / 'app.json', {'operations': {
        name: name + '.json' for name in ('infra', 'deps', 'check', 'run')}})
    state = configured_root(Path(registration['config_path']).parent, config['paths']['state'])
    parameters = write(profile / 'parameters.json', {'repository': str(repository),
        'state_root': str(state) if selection != 'wrong-state' else str(profile / 'fresh-state')})
    runtime_manifest = Path(registration['config_path'])
    if selection == 'fresh-stores':
        fresh = profile / 'fresh-runtime/project.json'
        body = json.loads(runtime_manifest.read_text())
        body['paths']['database'] = 'different/tasks.sqlite'
        write(fresh, body)
        for relative in body['workflows'].values():
            destination = fresh.parent / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((runtime_manifest.parent / relative).read_bytes())
        runtime_manifest = fresh
    resources = []
    for identifier in ('poise-task-database', 'poise-requirements-database', 'poise-telemetry-database'):
        module = 'poise_environment.repairs.' + identifier.removeprefix('poise-').replace('-', '_')
        action = {'argv': [sdk['service_python'], '-B', '-m', module,
            '--config', str(runtime_manifest), '--state-root', '${state_root}',
            '--requirement-id', identifier, '--action', 'check',
            '--schema', 'poise/maintenance-action/v2', '--recommendations',
            str(Path(__file__).resolve().parents[2] / 'config/maintenance/recommendations.example.json')],
            'cwd': str(profile), 'env': {},
            'interpreter': {'name': 'exit', 'options': {'accept': [0]}}}
        resources.append({'id': identifier, 'commands': [action],
                          'checks': {'presence': {'commands': [action]}}})
    write(profile / 'infra.json', {'requirements': resources})
    python = sdk['sdk_python'] if selection != 'wrong-sdk' else sys.executable
    commands = {name: [python, '-m', 'environment_maintenance', name, '--config', str(app),
        '--parameters', str(parameters), '--base-dir', str(repository)]
        for name in ('infra', 'deps', 'check', 'run')}
    bootstrap_file = write(profile / 'bootstrap.json', {'parameters': {}, 'commands': commands,
        'handler': {'callable': 'apps.bootstrap.environment_result:handle', 'options': {}}})
    tools = ProjectSettings(settings).root
    launcher = write(repository / '.runtime/stride-launcher.json', {
        'schema': 'stride-launcher-1', 'poise_root': str(tools),
        'data_root': str(configured_root(Path(registration['config_path']).parent, config['paths']['state'])),
        'project_id': 'poise-op', 'project_setup': str(settings.relative_to(tools)),
        'command': [sdk['service_python'], '-m', 'poise'], 'overrides': {
            name: [sdk['service_python'], '-I', '-B', str(repository / 'bootstrap'), name,
                   '--config', str(bootstrap_file), '--base-dir', str(repository)]
            for name in ('infra', 'deps', 'check', 'run')}, 'exit_codes': {'rejected': 2}})
    (repository / 'config').mkdir(exist_ok=True)
    (repository / 'config/stride.env').write_text('STRIDE_CONFIG=' + str(launcher) + '\n')
    selected = {'base': str(repository), 'config': str(app), 'parameters': str(parameters)}
    runtime = dict(sdk, module_path='', git='/usr/bin/git')
    if selection == 'working':
        observed = working_binding(runtime, 'poise-op', selected, baseline, context, root)
        assert observed['sdk']['python'] == sdk['sdk_python']
        assert observed['runtime_stores']['state_root'] == str(state)
    else:
        reason = ('different state root' if selection == 'wrong-state' else
                  'different working stores' if selection == 'fresh-stores' else None)
        with pytest.raises(AssertionError, match=reason):
            working_binding(runtime, 'poise-op', selected, baseline, context, root)


@pytest.mark.parametrize('dependency', ['configuration', 'source_root', 'python', 'start_app',
                                      'operator', 'input-source', 'literal-command', 'unused-option'])
def test_persistent_preparation_dependencies_are_distinct_from_task_coordinates(tmp_path, dependency):
    task, tree, operator = tmp_path / 'task', tmp_path / 'tree', tmp_path / 'operator'
    context = {'task_root': task, 'worktree': tree,
        'attempt': SimpleNamespace(coordinates={'task_root': str(task), 'worktree': str(tree),
            'project_id': 'demo', 'task_id': 'T1', 'base': 'fixture-base', 'branch': 'tasks/T1'})}
    raw = json.loads((FIXTURES / 'modern-preparation-control.json').read_text())
    raw['parameters'] = {'source_root': str(operator / 'source'), 'python': sys.executable,
        'start_app': str(operator / 'start-app'), 'operator': str(operator)}
    configuration = operator / 'preparation.json'
    if dependency == 'configuration':
        configuration = task / 'preparation.json'
    elif dependency in raw['parameters']:
        raw['parameters'][dependency] = str(task / dependency)
    elif dependency == 'input-source':
        raw['inputs'][0]['source'] = str(tree / 'source.sh')
    elif dependency == 'literal-command':
        raw['commands']['prepare'][0] = str(task / 'operator-command')
    else:
        raw['parameters']['unused_future_parameter'] = str(task / 'unused')
        raw['ignored_future_option'] = str(tree / 'unused')
    write(configuration, raw)
    if dependency == 'unused-option':
        preparation_dependencies_are_permanent(context, configuration)
    else:
        with pytest.raises(AssertionError):
            preparation_dependencies_are_permanent(context, configuration)


def test_actual_ready_preparation_with_disposable_tool_is_rejected(owned_context):
    baseline, context, _, case = owned_context
    before = context['attempt'].projection()
    previous = Path(before['receipt_path'])
    snapshot = case['project']['root'] / 'before-disposable-tool.json'
    snapshot.write_bytes(previous.read_bytes())
    raw = json.loads(case['configuration'].read_text())
    tool = context['task_root'] / 'own-disposable-tool/start-app'
    tool.parent.mkdir()
    tool.write_bytes(Path(raw['parameters']['start_app']).read_bytes())
    tool.chmod(0o700)
    raw['parameters']['start_app'] = str(tool)
    write(case['configuration'], raw)
    prepared = bootstrap(case, again=True, request_id='own-disposable-tool-proof')
    assert prepared['preparation']['status'] == 'ready'
    context = owner_context(baseline, 'demo')
    declared = {'receipt_path': prepared['preparation']['receipt_path'],
        'receipt_sha256': prepared['preparation']['receipt_sha256'],
        'previous_attempt_id': before['attempt_id'], 'previous_receipt_path': str(previous),
        'previous_receipt_snapshot': str(snapshot)}
    with pytest.raises(AssertionError):
        native_refresh_observation(context, declared)
    tool.unlink()  # Only the executable created by this control is removed.
    failed = bootstrap(case, again=True, request_id='own-deleted-tool-proof')
    assert failed['preparation']['status'] == 'error'
    assert 'FileNotFoundError' in failed['preparation']['reason']
