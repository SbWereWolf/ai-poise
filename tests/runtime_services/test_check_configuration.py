"""Check configuration/provenance remains independent of retired transport settings."""
from copy import deepcopy
from pathlib import Path

import pytest

from batch.helpers import bootstrap, result, verify
from conftest import WorkPoise, add_test, git, write_json
from check_configuration_helpers import prepare_check_systems
from poise.application.work import WorkTools
from poise.common import PoiseError


def configure_companions(project):
    project['cfg']['runtime_services']['check_runner']['systems'] = prepare_check_systems(
        project['root'], project['app'], project['config_path'])
    write_json(project['config_path'], project['cfg'])


@pytest.mark.parametrize('retired', [None, {}, {'future_option': True}, {'systems': None}])
def test_current_checks_ignore_retired_settings_and_replay_the_same_receipt(project, retired):
    configure_companions(project)
    project['cfg']['runtime_services']['transfer'] = retired
    write_json(project['config_path'], project['cfg'])
    tools = WorkTools(WorkPoise(project['config_path'], 'current-checks'))
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    payload = result(context)
    report = verify(tools, payload)
    assert report['status'] == 'verified'
    assert report['checks']
    assert all(set(receipt['systems']) == {'poise', 'project'} for receipt in report['checks'])
    replay = verify(tools, payload)
    assert replay['status'] == 'verified' and replay['replayed'] is True
    assert replay['checks'] == report['checks']
    assert not (project['root'] / 'state/transfers').exists()


def test_absent_optional_check_companions_are_explicitly_supported(project):
    assert 'systems' not in project['cfg']['runtime_services']['check_runner']
    write_json(project['config_path'], project['cfg'])
    tools = WorkTools(WorkPoise(project['config_path'], 'without-companions'))
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    report = verify(tools, result(context))
    assert report['status'] == 'verified' and report['checks']
    assert all(receipt['systems'] == {} for receipt in report['checks'])


def test_current_systems_capture_config_order_and_ignore_unknown_keys(project):
    configure_companions(project)
    systems = project['cfg']['runtime_services']['check_runner']['systems']
    systems['future_option'] = None
    for owner in ('poise', 'project'):
        systems[owner]['future_option'] = False
    write_json(project['config_path'], project['cfg'])
    tools = WorkTools(WorkPoise(project['config_path'], 'system-provenance'))
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    report = verify(tools, result(context))
    assert report['status'] == 'verified' and report['checks']
    for receipt in report['checks']:
        assert set(receipt['systems']) == {'poise', 'project'}
        assert receipt['systems']['poise']['commit'] == git(project['root'] / 'installed-poise', 'rev-parse', 'HEAD')
        assert receipt['systems']['project']['commit'] == git(project['app'], 'rev-parse', 'HEAD')
        for owner, names in [('poise', ['project.json', 'poise_overlay.json']),
                             ('project', ['project_config.json', 'project_overlay.json'])]:
            assert [Path(item['path']).name for item in receipt['systems'][owner]['configs']] == names
    assert tools.runtime.current_task()['stage_index'] == 0
    assert not (project['root'] / 'state/transfers').exists()


@pytest.mark.parametrize('owner', ['poise', 'project'])
def test_missing_check_config_refuses_before_subject_command_or_receipt(project, owner):
    configure_companions(project)
    counter = project['root'] / 'subject-effects.txt'
    for method in project['task']['methods']:
        method['environment']['CHECK_STAGE_COUNTER'] = str(counter)
    tools = WorkTools(WorkPoise(project['config_path'], 'missing-check-config'))
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    source = Path(context['worktree']) / 'tests/test_double.py'
    observer = Path(__file__).parent / 'fixtures/observe_check_command.py'
    source.write_bytes(source.read_bytes() + observer.read_bytes())
    (project['root'] / (owner + '_overlay.json')).unlink()
    run_root = Path(context['task_root']) / project['cfg']['paths']['runs']
    before = set(run_root.rglob('*')) if run_root.exists() else set()
    with pytest.raises(PoiseError, match='config|Config|missing|Missing'):
        verify(tools, result(context))
    assert not counter.exists()
    assert tools.runtime.evidence_commands.list_for('T1') == []
    assert {path for path in run_root.rglob('*') if path.name in {'stdout.txt', 'stderr.txt'}} <= before


@pytest.mark.parametrize('path,operation,value,diagnostic', [
    pytest.param((), 'set', None, r'check_runner\.systems', id='null'),
    pytest.param((), 'set', 'invalid', r'check_runner\.systems', id='not-object'),
    pytest.param(('poise',), 'remove', None, r'check_runner\.systems', id='missing-poise'),
    pytest.param(('project',), 'remove', None, r'check_runner\.systems', id='missing-project'),
    pytest.param(('poise',), 'set', None, r'check_runner\.systems\.poise', id='invalid-owner'),
    pytest.param(('poise', 'repository'), 'remove', None, r'check_runner\.systems\.poise', id='missing-repository'),
    pytest.param(('project', 'configs'), 'remove', None, r'check_runner\.systems\.project', id='missing-configs'),
    pytest.param(('poise', 'repository'), 'set', None, r'check_runner\.systems\.poise\.repository', id='null-repository'),
    pytest.param(('project', 'repository'), 'set', 'relative', r'check_runner\.systems\.project\.repository', id='relative-repository'),
    pytest.param(('poise', 'configs'), 'set', 'invalid', r'check_runner\.systems\.poise\.configs', id='configs-not-list'),
    pytest.param(('project', 'configs'), 'set', [], r'check_runner\.systems\.project\.configs', id='empty-configs'),
    pytest.param(('poise', 'configs'), 'set', [None], r'check_runner\.systems\.poise\.configs', id='null-config-item'),
    pytest.param(('project', 'configs'), 'set', ['relative'], r'check_runner\.systems\.project\.configs', id='relative-config-item'),
])
def test_malformed_known_companions_refuse_before_subject_or_new_receipt(project, path, operation, value, diagnostic):
    # An actual ordinary verification is the positive old/new configuration control.
    counter = project['root'] / 'subject-effects.txt'
    for method in project['task']['methods']:
        method['environment']['CHECK_STAGE_COUNTER'] = str(counter)
    tools = WorkTools(WorkPoise(project['config_path'], 'known-config-boundary'))
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    subject = Path(context['worktree']) / 'tests/test_double.py'
    subject.write_bytes(subject.read_bytes() + (Path(__file__).parent / 'fixtures/observe_check_command.py').read_bytes())
    payload = result(context)
    positive = verify(tools, payload)
    assert positive['status'] == 'verified' and positive['checks']
    assert counter.read_bytes() == b'stage-command\n'
    receipts = deepcopy(tools.runtime.evidence_commands.list_for('T1'))
    task = deepcopy(tools.runtime.task_queries.record('T1'))
    systems = prepare_check_systems(project['root'], project['app'], project['config_path'])
    if not path:
        systems = value
    else:
        target = systems
        for key in path[:-1]:
            target = target[key]
        if operation == 'remove':
            del target[path[-1]]
        else:
            target[path[-1]] = value
    project['cfg']['runtime_services']['check_runner']['systems'] = systems
    write_json(project['config_path'], project['cfg'])
    with pytest.raises(PoiseError, match=diagnostic):
        candidate = WorkTools(WorkPoise(project['config_path'], tools.runtime.session))
        verify(candidate, payload)
    assert counter.read_bytes() == b'stage-command\n'
    assert tools.runtime.evidence_commands.list_for('T1') == receipts
    assert tools.runtime.task_queries.record('T1') == task
