"""P005 C01: shipped routing contract and real Stride/Bootstrap/v2 transport.

The one-resource engine arrangement proves transport, not full ERP readiness.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
from string import Template
import subprocess
import sys

import pytest

from poise.modules.projects.launch import ProjectLaunchSettings
from tests.projects.helpers import setup_case
from test_catalog_v2_profile import consumer  # Existing accepted supply owner.

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / 'fixtures'
EXPECTED = FIXTURES / 'p005/maintenance-prefixes.expected.json'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def filled_example():
    """The shipped example is the subject; expected prefixes are independent."""
    text = (ROOT / 'config/stride-launcher.example.json').read_text()
    return json.loads(Template(text).substitute({
        'LAUNCHER_ROOT': '/selected/tools',
        'PROJECT_DATA_PARENT_ROOT': '/selected/data',
        'PROJECT_ID': 'selected-project',
        'PYTHON_EXECUTABLE': '/selected/python',
        'POISE_SOURCE_ROOT': '/selected/shared/app',
    }))


@pytest.mark.parametrize('operation', ['infra', 'deps', 'check'])
@pytest.mark.parametrize('profile', ['poise', 'erp'])
def test_shipped_example_selects_bootstrap_and_keeps_operation(operation, profile):
    selected = ProjectLaunchSettings.parse(filled_example())
    suffix = ['--config', f'/profiles/{profile}/bootstrap.json',
              '--catalog-dir', f'/profiles/{profile}', '--dry-run']
    expected_prefix = json.loads(EXPECTED.read_text())[operation]

    actual = list(selected.plan_command([operation, *suffix]))

    assert actual == [*expected_prefix, *suffix], 'Maintenance route must select Bootstrap'


@pytest.mark.parametrize('operation', ['work', 'next'])
def test_shipped_example_preserves_task_operation_prefixes(operation):
    selected = ProjectLaunchSettings.parse(filled_example())
    expected = ['/selected/python', '-P', '-s', '-B', '-m', 'poise', operation]
    if operation == 'next':
        expected += ['--settings', '/selected/shared/app/config/project-setup.json']
    assert list(selected.plan_command([operation])) == expected


@pytest.fixture
def routed_case(project, tmp_path, consumer, retained_previous_files):
    """Real project selection and a selected real external consumer, no fake ready."""
    setup, _, _ = setup_case(project)
    write_json(project['root'] / 'state/project-registry.json', {
        'schema': 'configured-project-registry-1',
        'projects': {project['cfg']['project']: {
            'config_path': str(project['config_path'].relative_to(project['root'])),
        }},
    })
    settings = {
        'schema': 'stride-launcher-1', 'poise_root': str(project['root']),
        'data_root': str(tmp_path), 'project_id': project['cfg']['project'],
        'project_setup': str(setup),
        'command': [sys.executable, '-P', '-s', '-B', '-m', 'poise'],
        'overrides': {name: [sys.executable, '-I', '-B', str(ROOT / 'bootstrap'), name]
                      for name in ('infra', 'deps', 'check')},
        'exit_codes': {'rejected': 2},
    }
    selector = write_json(tmp_path / 'operator/selector.json', settings)
    entry = tmp_path / 'operator/launcher.env'
    entry.write_text('STRIDE_PYTHON=' + shlex.quote(sys.executable)
                     + '\nSTRIDE_CONFIG=' + shlex.quote(str(selector)) + '\n')
    profiles = {}
    for name in ('poise', 'erp'):
        root = tmp_path / ('profile ' + name)
        root.mkdir()
        catalog = json.loads((FIXTURES / 'v2_catalog.json').read_text())
        catalog['parameters'] = {'selected': {'type': 'string', 'default': 'default'}}
        for role in ('check', 'install', 'repair'):
            action = catalog['requirements'][0][role]
            action['argv'] = [str(consumer), '-I', '-B', str(FIXTURES / 'v2_action.py'),
                              str(root), role, 'resource', 'normal']
            action['cwd'] = str(root)
            action['env'] = {'SELECTED': '${selected}'}
        write_json(root / 'catalog.json', catalog)
        (root / 'resource').write_text('valid')
        defaults = write_json(root / 'defaults.json', {'selected': 'default-values'})
        configuration = json.loads((ROOT / 'config/bootstrap.example.json').read_text())
        configuration['parameters'] = {'python': str(consumer), 'tool_config': str(defaults)}
        config = write_json(root / 'bootstrap.json', configuration)
        values = write_json(root / 'forwarded-values.json', {'selected': 'from-values'})
        profiles[name] = {'root': root, 'config': config, 'values': values}
    return {'entry': entry, 'project': project['cfg']['project'], 'profiles': profiles,
            'selector': selector, 'settings': settings, 'root': tmp_path,
            'previous_release': retained_previous_files}


def launch(case, operation, arguments):
    return subprocess.run([
        'bash', str(ROOT / 'stride.sh'), '--poise-root', str(ROOT),
        '--project', case['project'], str(case['entry']), operation, *arguments,
    ], cwd=case['root'], env=dict(os.environ), capture_output=True, text=True)


@pytest.fixture
def retained_previous_files(tmp_path):
    """C07 observes real installed bytes plus explicit test-owned retained files."""
    previous = os.environ.get('P005_LEGACY_CONSUMER_PYTHON')
    assert previous, 'Select retained previous consumer; missing input is setup failure'
    command = [previous, '-I', '-B', str(FIXTURES / 'p005/legacy_inventory.py')]
    before = subprocess.run(command, capture_output=True, text=True)
    assert before.returncode == 0 and before.stderr == ''
    assert json.loads(before.stdout), 'The previous distribution inventory must be nonempty'
    retained = tmp_path / 'retained-previous-files'
    retained.mkdir()
    # Control files are independent preservation inputs, not a legacy catalog oracle.
    (retained / 'operator-control.txt').write_text('Retained operator data Ω\n')
    (retained / 'profile-selection.json').write_text('{"selected":"previous"}\n')
    original = {str(path.relative_to(retained)): path.read_bytes()
                for path in retained.rglob('*') if path.is_file()}
    yield {'python': previous, 'installed_files': json.loads(before.stdout)}
    after = subprocess.run(command, capture_output=True, text=True)
    assert (after.returncode, after.stdout, after.stderr) == (0, before.stdout, '')
    observed = {str(path.relative_to(retained)): path.read_bytes()
                for path in retained.rglob('*') if path.is_file()}
    assert observed == original


@pytest.mark.parametrize('operation', ['infra', 'deps', 'check'])
@pytest.mark.parametrize('profile', ['poise', 'erp'])
def test_real_route_forwards_selected_profile_values_set_and_dry_run(routed_case, operation, profile):
    selected = routed_case['profiles'][profile]
    values_only = launch(routed_case, operation, [
        '--config', str(selected['config']), '--catalog-dir', str(selected['root']),
        '--values', str(selected['values']), '--dry-run',
    ])
    assert (values_only.returncode, values_only.stderr) == (0, '')
    first_report = json.loads(values_only.stdout)
    assert first_report['status'] == 'ready'
    assert json.loads(first_report['observations'][0]['stdout'])['dry_run'] is True
    first_calls = [json.loads(line) for line in (selected['root'] / 'calls.jsonl').read_text().splitlines()]
    assert [row['role'] for row in first_calls] == ['check']
    assert first_calls[0]['env']['SELECTED'] == 'from-values'

    result = launch(routed_case, operation, [
        '--config', str(selected['config']), '--catalog-dir', str(selected['root']),
        '--values', str(selected['values']), '--set', 'selected=from-cli', '--dry-run',
    ])

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stderr == ''
    report = json.loads(result.stdout)
    assert report['status'] == 'ready' and report['exit_code'] == 0
    assert json.loads(report['observations'][0]['stdout'])['dry_run'] is True
    assert report['actions'] == []
    calls = [json.loads(line) for line in (selected['root'] / 'calls.jsonl').read_text().splitlines()]
    assert [row['role'] for row in calls] == ['check', 'check']
    assert calls[1]['env']['SELECTED'] == 'from-cli'
    assert all(row['cwd'] == str(selected['root']) for row in calls)
    other = routed_case['profiles']['erp' if profile == 'poise' else 'poise']['root']
    assert not (other / 'calls.jsonl').exists()


@pytest.mark.parametrize('operation', ['infra', 'deps', 'check'])
@pytest.mark.parametrize('profile', ['poise', 'erp'])
def test_real_route_dry_run_observes_missing_resource_without_repair(routed_case, operation, profile):
    selected = routed_case['profiles'][profile]
    (selected['root'] / 'resource').unlink()
    result = launch(routed_case, operation, [
        '--config', str(selected['config']), '--catalog-dir', str(selected['root']),
        '--values', str(selected['values']), '--dry-run',
    ])
    assert result.returncode == 4 and result.stderr == ''
    report = json.loads(result.stdout)
    assert report['status'] == 'error' and report['actions'] == []
    assert len(report['observations']) == 1
    engine = json.loads(report['observations'][0]['stdout'])
    assert engine['dry_run'] is True
    assert engine['status'] == 'action_required'
    assert engine['requirements'][0]['state'] == 'missing'
    assert not (selected['root'] / 'resource').exists()
    calls = [json.loads(line) for line in (selected['root'] / 'calls.jsonl').read_text().splitlines()]
    assert [row['role'] for row in calls] == ['check']
    assert calls[0]['env']['SELECTED'] == 'from-values'


@pytest.mark.parametrize('arguments,missing', [
    ([], '--config'),
    (['--config', 'unused.json'], '--catalog-dir'),
])
def test_real_route_reports_required_argument_without_action(routed_case, arguments, missing):
    result = launch(routed_case, 'check', arguments)
    assert result.returncode == 2
    assert missing in result.stderr and 'required' in result.stderr
    for selected in routed_case['profiles'].values():
        assert not (selected['root'] / 'calls.jsonl').exists()


def test_real_route_reports_unknown_argument_without_action(routed_case):
    selected = routed_case['profiles']['poise']
    result = launch(routed_case, 'check', [
        '--config', str(selected['config']), '--catalog-dir', str(selected['root']),
        '--unknown-option',
    ])
    assert result.returncode == 2
    assert 'unrecognized arguments: --unknown-option' in result.stderr
    assert not (selected['root'] / 'calls.jsonl').exists()


def test_external_input_rejection_preserves_diagnostic_and_previous_selection(routed_case):
    selected = routed_case['profiles']['poise']
    selector_before = routed_case['selector'].read_bytes()
    resource_before = (selected['root'] / 'resource').read_bytes()
    result = launch(routed_case, 'check', [
        '--config', str(selected['config']), '--catalog-dir', str(selected['root']),
        '--set', 'unknown=1',
    ])
    assert result.returncode == 4 and result.stderr == ''
    report = json.loads(result.stdout)
    assert report['status'] == 'error'
    assert report['observations'][0]['exit_code'] == 2
    assert 'Unknown or duplicate override: unknown' in report['observations'][0]['stderr']
    assert routed_case['selector'].read_bytes() == selector_before
    assert (selected['root'] / 'resource').read_bytes() == resource_before
    assert not (selected['root'] / 'calls.jsonl').exists()


def test_return_to_previous_selection_executes_actual_previous_consumer(routed_case):
    """C05 route boundary only; this is not a claim that five real gates passed."""
    previous_python = os.environ.get('P005_LEGACY_CONSUMER_PYTHON')
    assert previous_python, 'Select the actual retained legacy consumer Python; absence is setup failure'
    previous = json.loads(json.dumps(routed_case['settings']))
    previous['overrides'] = {
        name: [previous_python, '-I', '-B', '-m', 'environment_maintenance', name]
        for name in ('infra', 'deps', 'check')
    }
    write_json(routed_case['selector'], previous)
    before = routed_case['selector'].read_bytes()
    old_route = launch(routed_case, 'check', ['--help'])
    assert old_route.returncode == 0 and old_route.stderr == ''
    assert '--catalog CATALOG' in old_route.stdout
    assert '--catalog-dir' not in old_route.stdout

    prepared = write_json(routed_case['root'] / 'prepared-selector.json', routed_case['settings'])
    os.replace(prepared, routed_case['selector'])
    selected = launch(routed_case, 'check', ['--help'])
    assert selected.returncode == 0 and selected.stderr == ''
    assert '--catalog-dir' in selected.stdout and '--config' in selected.stdout

    restored = routed_case['root'] / 'previous-selector.json'
    restored.write_bytes(before)
    os.replace(restored, routed_case['selector'])
    returned = launch(routed_case, 'check', ['--help'])
    assert (returned.returncode, returned.stdout, returned.stderr) == (0, old_route.stdout, '')
    assert routed_case['selector'].read_bytes() == before
    for profile in routed_case['profiles'].values():
        assert not (profile['root'] / 'calls.jsonl').exists()
