"""Check configuration/provenance remains independent of retired transport settings."""
from pathlib import Path

import pytest

from batch.helpers import bootstrap, result, verify
from conftest import WorkPoise, add_test, git, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError


@pytest.mark.parametrize('retired', [None, {}, {'future_option': True}, {'systems': None}])
def test_current_checks_ignore_retired_settings_and_replay_the_same_receipt(project, retired):
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
    del project['cfg']['runtime_services']['check_runner']['systems']
    write_json(project['config_path'], project['cfg'])
    tools = WorkTools(WorkPoise(project['config_path'], 'without-companions'))
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    report = verify(tools, result(context))
    assert report['status'] == 'verified' and report['checks']
    assert all(receipt['systems'] == {} for receipt in report['checks'])


def test_current_systems_capture_config_order_and_ignore_unknown_keys(project):
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
