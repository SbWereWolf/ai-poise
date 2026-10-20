"""C02-C07: real coupled procedure in disposable operator/project state.

This executes the agreed operator procedure, not an invented autonomous gate.
Only implementation-stage execution supplies environment/readiness evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.register_assert_rewrite('erp_support')

from conftest import git
from erp_support import (
    CONTRACT, IDS, bootstrap_configuration, export_erp_source,
    materialize as materialize_erp, targets, values as erp_values,
)
from test_catalog_v2_profile import (
    IDENTITIES, consumer, installation, materialize as materialize_poise, source,
)
from erp_support import erp_case
from test_erp_environment_acceptance import (
    arrange_shared_environment, assert_real_dependencies, publish_configuration_inputs,
)
from test_erp_worktree_preparation import (
    assert_database_tables_and_rows, assert_ready_engine_and_inputs, selected_shared_source,
)
from test_operator_v2_routing import launch, retained_previous_files, routed_case, write_json
from runtime_services.worktree_preparation_support import arrange, boot

ROOT = Path(__file__).resolve().parents[2]
ORDER = Path(__file__).parent / 'fixtures/p005/gate-order.json'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def capture(evidence, label, result):
    """Persist complete real child results; this helper never executes a command."""
    folder = evidence['root'] / label
    folder.mkdir()
    streams = {}
    for name in ('stdout', 'stderr'):
        raw = getattr(result, name)
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
        path = folder / name
        path.write_bytes(raw)
        streams[name] = {'path': str(path), 'sha256': digest(path)}
    row = {'label': label, 'argv': list(map(str, result.args)),
           'exit_code': result.returncode,
           'status': 'PASS' if result.returncode == 0 else 'FAIL', **streams}
    write_json(folder / 'actual-command.json', row)
    evidence['commands'].append(row)
    return row


def bootstrap(evidence, case, profile, operation):
    result = subprocess.run([
        sys.executable, '-I', '-B', str(ROOT / 'bootstrap'), operation,
        '--config', str(profile['config']), '--catalog-dir', str(case['profile']),
    ], capture_output=True)
    capture(evidence, profile['id'] + '-' + operation, result)
    return result, json.loads(result.stdout)


def old_selection(operator):
    selected = os.environ.get('P005_LEGACY_CONSUMER_PYTHON')
    assert selected, 'Select retained previous consumer; missing input is setup failure'
    settings = json.loads(json.dumps(operator['settings']))
    settings['overrides'] = {
        name: [selected, '-I', '-B', '-m', 'environment_maintenance', name]
        for name in ('infra', 'deps', 'check')
    }
    write_json(operator['selector'], settings)
    result = launch(operator, 'check', ['--help'])
    assert result.returncode == 0 and '--catalog CATALOG' in result.stdout
    return operator['selector'].read_bytes(), result.stdout


def assert_previous(operator, before, old_help):
    assert operator['selector'].read_bytes() == before
    observed = launch(operator, 'check', ['--help'])
    assert (observed.returncode, observed.stdout, observed.stderr) == (0, old_help, '')


def prepared_worktree(project, selected_source, selected_consumer, monkeypatch):
    project['cfg']['batch']['max_artifact_bytes'] = 4194304  # Measured P004 full capture.
    preparation = arrange(project, monkeypatch)
    export_erp_source({'repository': project['app']})
    git(project['app'], 'add', '.')
    git(project['app'], 'commit', '-m', 'Selected test-owned ERP source closure')
    future = preparation['runtime'].state / 'worktrees/T1'
    case = {
        'root': project['root'], 'supply': selected_source, 'repository': future,
        'profile': project['root'] / 'operator/erp-task-profile',
        'state': project['root'] / 'separate-task-maintenance',
        'preparation': preparation,
    }
    result = materialize_erp(case, project='erp-cutover-test', overrides={
        'python_minimum': '3.15', 'python_maximum': '3.16',
        'consumer_python': str(selected_consumer),
    })
    assert result.returncode == 0, (result.stdout, result.stderr)
    selected = erp_values(case, {'python': sys.executable, 'python_minimum': '3.15',
                                 'python_maximum': '3.16', 'consumer_python': str(selected_consumer)})
    write_json(case['profile'] / 'values.json', selected)
    shared = project['root'] / 'operator/shared.env'
    selected_shared_source(project['app'], shared)
    execution = project['app'] / 'platform-infrastructure/tests/playwright/contracts/fixtures/cjm-configuration/test-execution.json'
    configured = bootstrap_configuration(case)
    configured['catalog_dir'] = str(case['profile'])
    configured['operations'] = ['infra', 'deps', 'check']
    configured['inputs'] = [
        {'source': str(shared), 'destination': 'platform-infrastructure/config/dev/local-development.env'},
        {'source': str(execution), 'destination': CONTRACT['normal_cjm']['execution_configuration']},
    ]
    preparation['fixture_streams'] = False
    write_json(preparation['configuration'], configured)
    return preparation, case


@pytest.mark.parametrize('failed_gate', [None, 'supply', 'profiles', 'poise-clean-room',
                                       'erp-clean-room', 'erp-task-worktree'])
def test_real_five_gate_procedure_and_previous_route_preservation(
        routed_case, installation, erp_case, source, consumer, project,
        tmp_path, monkeypatch, failed_gate):
    monkeypatch.setenv('P003_CONSUMER_PYTHON', str(consumer))
    for name in ('COMPOSER_HOME', 'COMPOSER_CACHE_DIR', 'npm_config_cache'):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setenv(name, str(directory))
    root = tmp_path / 'complete-gate-evidence'
    root.mkdir()
    evidence = {'root': root, 'commands': [], 'gates': []}
    before, previous_help = old_selection(routed_case)

    supply_environment = dict(os.environ)
    if failed_gate == 'supply':
        original = Path(os.environ['AIP_GENERIC_WHEEL'])
        corrupted = tmp_path / 'invalid-supply' / original.name
        corrupted.parent.mkdir()
        corrupted.write_bytes(original.read_bytes() + b'damaged test input')
        supply_environment['AIP_GENERIC_WHEEL'] = str(corrupted)
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'tests/maintenance/test_generic_v2_supply.py')],
                            env=supply_environment, capture_output=True)
    capture(evidence, 'supply', result)
    if failed_gate == 'supply':
        assert result.returncode == 1 and b'SUPPLY_INVALID:' in result.stderr
        assert_previous(routed_case, before, previous_help)
        return
    assert result.returncode == 0 and result.stdout == b'SUPPLY_VERIFIED\n'
    evidence['gates'].append('supply')

    if failed_gate == 'profiles':
        path = source / 'config/maintenance/environment-maintenance.example.json'
        invalid = json.loads(path.read_text())
        invalid['schema'] = 'invalid-test-schema'
        write_json(path, invalid)  # Disposable selected inputs, never candidate sources.
    result = materialize_poise(installation)
    capture(evidence, 'poise-materialization', result)
    if failed_gate == 'profiles':
        assert result.returncode == 2 and json.loads(result.stdout)['status'] == 'invalid'
        assert_previous(routed_case, before, previous_help)
        return
    assert result.returncode == 0, (result.stdout, result.stderr)
    result = materialize_erp(erp_case, overrides={'python_minimum': '3.15', 'python_maximum': '3.16'})
    capture(evidence, 'erp-materialization', result)
    assert result.returncode == 0, (result.stdout, result.stderr)
    poise_catalog = json.loads((installation['profile'] / 'catalog.json').read_text())
    erp_catalog = json.loads((erp_case['profile'] / 'catalog.json').read_text())
    assert [row['id'] for row in poise_catalog['requirements']] == IDENTITIES
    assert sorted(row['id'] for row in erp_catalog['requirements']) == sorted(IDS)
    assert len(IDENTITIES) == 14 and len(IDS) == 56 and len(CONTRACT['groups']) == 21
    assert_previous(routed_case, before, previous_help)
    evidence['gates'].append('profiles')

    values = write_json(tmp_path / 'poise-selected-values.json', {
        'python': sys.executable, 'python_minimum': '99.0' if failed_gate == 'poise-clean-room' else '3.15',
        'python_maximum': '100.0' if failed_gate == 'poise-clean-room' else '3.16',
    })
    config = json.loads((ROOT / 'config/bootstrap.example.json').read_text())
    config['parameters'] = {'python': str(consumer), 'tool_config': str(values)}
    poise = {'id': 'poise', 'config': write_json(tmp_path / 'poise-bootstrap.json', config)}
    for operation in ('deps', 'infra', 'check'):
        result, response = bootstrap(evidence, installation, poise, operation)
        if failed_gate == 'poise-clean-room':
            assert result.returncode != 0 and response['status'] == 'error'
            assert_previous(routed_case, before, previous_help)
            return
        assert result.returncode == 0 and response['status'] == 'ready', response
    evidence['gates'].append('poise-clean-room')
    assert_previous(routed_case, before, previous_help)

    export_erp_source(erp_case)
    working_values = erp_values(erp_case, {'python': sys.executable, 'python_minimum': '3.15',
                                          'python_maximum': '3.16', 'consumer_python': str(consumer)})
    if failed_gate == 'erp-clean-room':
        working_values['php_executable'] = str(tmp_path / 'unavailable-php')
    write_json(erp_case['profile'] / 'values.json', working_values)
    publish_configuration_inputs(erp_case)
    arrange_shared_environment(erp_case)
    configured = bootstrap_configuration(erp_case)
    erp = {'id': 'erp', 'config': write_json(tmp_path / 'erp-bootstrap.json', configured)}
    for operation in ('infra', 'deps', 'check'):
        result, response = bootstrap(evidence, erp_case, erp, operation)
        if failed_gate == 'erp-clean-room':
            assert result.returncode != 0 and response['status'] == 'error'
            assert_previous(routed_case, before, previous_help)
            return
        assert result.returncode == 0 and response['status'] == 'ready', response
    assert_real_dependencies(erp_case)
    assert_database_tables_and_rows(erp_case, insert=True)
    evidence['gates'].append('erp-clean-room')
    assert_previous(routed_case, before, previous_help)

    preparation, task_case = prepared_worktree(project, source, consumer, monkeypatch)
    if failed_gate == 'erp-task-worktree':
        configured = json.loads(preparation['configuration'].read_text())
        configured['inputs'][0]['source'] = str(tmp_path / 'unavailable-shared-input.env')
        write_json(preparation['configuration'], configured)
        refused = boot(preparation)
        assert refused['status'] == 'action_failed'
        assert refused['preparation']['status'] == 'error'
        assert 'unavailable-shared-input.env' in refused['preparation']['reason']
        write_json(root / 'actual-task-failure.json', refused)
        assert_database_tables_and_rows(erp_case, insert=False)
        assert_previous(routed_case, before, previous_help)
        return
    desired = {target['id']: (task_case['profile'] / 'documents' / (target['id'] + '.json')).read_bytes()
               for target in targets(task_case)}
    observed = boot(preparation)
    assert observed['worktree'] == str(task_case['repository'])
    assert observed['preparation']['status'] == 'ready'
    assert_ready_engine_and_inputs(task_case, observed, desired)
    write_json(root / 'actual-task-bootstrap.json', observed)
    evidence['gates'].append('erp-task-worktree')
    assert evidence['gates'] == json.loads(ORDER.read_text())
    assert_previous(routed_case, before, previous_help)

    prepared = write_json(tmp_path / 'prepared-complete-selector.json', routed_case['settings'])
    os.replace(prepared, routed_case['selector'])
    for identifier, case, selected in [('poise', installation, poise), ('erp', erp_case, erp)]:
        result = launch(routed_case, 'check', ['--config', str(selected['config']),
                                             '--catalog-dir', str(case['profile'])])
        capture(evidence, identifier + '-selected-route', result)
        assert result.returncode == 0 and json.loads(result.stdout)['status'] == 'ready'
    previous = tmp_path / 'previous-selector.json'
    previous.write_bytes(before)
    os.replace(previous, routed_case['selector'])
    assert_previous(routed_case, before, previous_help)
    assert_database_tables_and_rows(erp_case, insert=False)
    profiles = {}
    for name, case in [('poise', installation), ('erp', erp_case)]:
        inventory = {str(path.relative_to(case['profile'])): digest(path)
                     for path in sorted(case['profile'].rglob('*')) if path.is_file()}
        profiles[name] = {
            'path': str(case['profile']), 'files': inventory,
            'sha256': hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest(),
        }
    metadata = {
        'scope': 'disposable coupled validation, not live operator activation',
        'source_commit': git(ROOT, 'rev-parse', 'HEAD'), 'source_tree': git(ROOT, 'rev-parse', 'HEAD^{tree}'),
        'python': sys.executable, 'python_version': sys.version, 'consumer_python': str(consumer),
        'gate_order': evidence['gates'], 'commands': evidence['commands'],
        'profiles': profiles,
        'previous_release': routed_case['previous_release'],
        'worktree': observed['worktree'], 'previous_selector_sha256': hashlib.sha256(before).hexdigest(),
        'rollback_selector_sha256': digest(routed_case['selector']),
    }
    write_json(root / 'complete-result.json', metadata)
    assert metadata['previous_selector_sha256'] == metadata['rollback_selector_sha256']
