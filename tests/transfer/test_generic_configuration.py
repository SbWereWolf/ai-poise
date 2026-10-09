"""Shared fixture contract and real non-transfer config/proof boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import subprocess

import pytest

from batch.helpers import bootstrap, result, verify
from conftest import WorkPoise, add_test, git, write_json
from poise.application.work import WorkTools
from poise.common import PoiseError
from poise.modules.foundation.errors import DomainError


@pytest.mark.parametrize('owner', ['poise', 'project'])
def test_shared_fixture_binds_real_system_and_ordered_config_bytes(project, owner):
    config = json.loads(project['config_path'].read_text())
    transfer = config['runtime_services']['transfer']
    assert 'bundles_directory' not in transfer
    assert transfer['archive'] == 'work.tar.gz'
    recovery = transfer['recovery']
    assert recovery['format'] == 'poise-recovery-1'
    assert set(recovery['systems']) == {'poise', 'project'}
    spec = recovery['systems'][owner]
    expected_repository = project['root'] / 'installed-poise' if owner == 'poise' else project['app']
    assert spec['repository'] == str(expected_repository)
    assert len(git(expected_repository, 'rev-parse', 'HEAD')) == 40
    paths = ([project['config_path'], project['root'] / 'poise_overlay.json']
             if owner == 'poise' else [project['root'] / 'project_config.json',
                                      project['root'] / 'project_overlay.json'])
    assert spec['configs'] == [str(path) for path in paths]
    observed = [json.loads(path.read_bytes()) for path in paths]
    assert observed[1]['fixture_owner'] == owner
    assert all(path.is_relative_to(project['root']) for path in paths)
    assert recovery['repositories']['task'] == str(project['app'])
    assert recovery['tool_argv'] == [sys.executable, str(project['root'] / 'unexpected_flow.py')]
    assert not (project['root'] / 'unexpected-flow.txt').exists()
    assert not (project['root'] / 'state' / 'transfers').exists()


def test_transfer_tool_binding_reuses_shared_config_identity_without_rewriting_bytes(project):
    from .compact_helpers import configure
    before = {path: path.read_bytes() for path in project['root'].glob('*_*.json')}
    original = deepcopy(project['cfg']['runtime_services']['transfer']['recovery'])
    configure(project, [sys.executable, str(project['root'] / 'unexpected_flow.py')])
    assert project['cfg']['runtime_services']['transfer']['recovery'] == original
    assert {path: path.read_bytes() for path in before} == before
    assert not (project['root'] / 'unexpected-flow.txt').exists()


def test_generic_delivery_tripwire_observes_real_invocation(project):
    argv = project['cfg']['runtime_services']['transfer']['recovery']['tool_argv']
    completed = subprocess.run(argv, capture_output=True, text=True)
    assert completed.returncode == 97
    assert (project['root'] / 'unexpected-flow.txt').read_text() == 'unexpected delivery invocation\n'
    assert not (project['root'] / 'state' / 'transfers').exists()


def current_runtime(project):
    try:
        return WorkPoise(project['config_path'], 'generic-proof')
    except DomainError as exc:
        expected = (Path(__file__).parent / 'fixtures' / 'legacy-transfer-refusal.txt').read_text().strip()
        if str(exc) != expected:
            raise
        pytest.fail('Generic explicit recovery configuration is not implemented')


def test_work_and_verification_without_recovery(project):
    del project['cfg']['runtime_services']['transfer']['recovery']
    write_json(project['config_path'], project['cfg'])
    tools = WorkTools(WorkPoise(project['config_path'], 'without-recovery'))
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    payload = result(context)
    report = verify(tools, payload)
    assert report['status'] == 'verified'
    assert report['checks']
    assert all(receipt['systems'] == {} for receipt in report['checks'])
    replay = verify(tools, payload)
    assert replay['replayed'] is True
    assert replay['status'] == 'verified'
    assert replay['checks'] == report['checks']
    assert not (project['root'] / 'unexpected-flow.txt').exists()
    assert not (project['root'] / 'state' / 'transfers').exists()


@pytest.mark.parametrize('action', ['export', 'import'])
def test_transfer_without_recovery_refuses_before_effects(project, action):
    del project['cfg']['runtime_services']['transfer']['recovery']
    write_json(project['config_path'], project['cfg'])
    runtime = WorkPoise(project['config_path'], 'without-transfer-policy')
    with pytest.raises(PoiseError, match='transfer.recovery required'):
        runtime.transfer_tools.apply({
            'action': action, 'request_id': 'missing-recovery',
            **({'task_ids': ['T1'], 'sprint_id': None, 'handoff': None}
               if action == 'export' else
               {'package_path': str(project['root'] / 'absent.tar.gz'),
                'package_digest': 'a' * 64}),
        })
    assert runtime.current_task() is None
    assert not (project['root'] / 'unexpected-flow.txt').exists()
    assert not (project['root'] / 'state' / 'transfers').exists()


def test_generic_checks_capture_current_systems_without_delivery(project):
    tools = WorkTools(current_runtime(project))
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    report = verify(tools, result(context))
    assert report['status'] == 'verified'
    assert report['checks']
    for receipt in report['checks']:
        assert set(receipt['systems']) == {'poise', 'project'}
        assert receipt['systems']['poise']['commit'] == git(project['root'] / 'installed-poise', 'rev-parse', 'HEAD')
        assert receipt['systems']['project']['commit'] == git(project['app'], 'rev-parse', 'HEAD')
        for owner, names in [('poise', ['project.json', 'poise_overlay.json']),
                             ('project', ['project_config.json', 'project_overlay.json'])]:
            assert [Path(item['path']).name for item in receipt['systems'][owner]['configs']] == names
    assert not (project['root'] / 'unexpected-flow.txt').exists()
    assert not (project['root'] / 'state' / 'transfers').exists()
    assert tools.runtime.current_task()['stage_index'] == 0


@pytest.mark.parametrize('owner', ['poise', 'project'])
def test_generic_missing_system_input_cannot_create_state_or_run_delivery(project, owner):
    current_runtime(project)  # Positive loader control precedes the negative mutation.
    original = deepcopy(project['cfg'])
    del project['cfg']['runtime_services']['transfer']['recovery']['systems'][owner]
    write_json(project['config_path'], project['cfg'])
    state = project['root'] / 'state'
    before = {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()}
    with pytest.raises(PoiseError, match='systems|' + owner):
        WorkPoise(project['config_path'], 'missing-system')
    assert {path.relative_to(state): path.read_bytes() for path in state.rglob('*') if path.is_file()} == before
    assert not (project['root'] / 'unexpected-flow.txt').exists()
    assert project['cfg']['git'] == original['git']


@pytest.mark.parametrize('owner', ['poise', 'project'])
def test_missing_check_config_refuses_before_subject_command_or_receipt(project, owner):
    from .test_compact_denials import instrument_stage_commands, install_stage_observer
    counter = project['root'] / 'subject-effects.txt'
    instrument_stage_commands(project, counter)
    tools = WorkTools(current_runtime(project))
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    install_stage_observer(context)
    missing = project['root'] / (owner + '_overlay.json')
    missing.unlink()
    run_root = Path(context['task_root']) / project['cfg']['paths']['runs']
    before = set(run_root.rglob('*')) if run_root.exists() else set()
    with pytest.raises(PoiseError, match='config|Config|missing|Missing'):
        verify(tools, result(context))
    assert not counter.exists()
    assert tools.runtime.evidence_commands.list_for('T1') == []
    assert {path for path in run_root.rglob('*') if path.name in {'stdout.txt', 'stderr.txt'}} <= before
    assert not (project['root'] / 'unexpected-flow.txt').exists()
    assert tools.runtime.current_task()['stage_index'] == 0
