"""Public ERP profile acceptance; expected catalogue is test-owned literal data."""
import json
import pytest

pytest.register_assert_rewrite('erp_support')
from erp_support import CONTRACT, IDS, materialize, read_json, snapshot

pytest_plugins = ['erp_support']


def test_public_materialization_provides_selected_erp_profile(erp_case):
    result = materialize(erp_case)
    assert result.returncode == 0, ('ERP profile capability is not delivered',
                                  result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report['status'] == 'created' and report['state_created'] is False
    catalog = read_json(erp_case['profile'] / 'catalog.json')
    assert catalog['schema'] == 'environment-maintenance/catalog/v2'
    assert sorted(entry['id'] for entry in catalog['requirements']) == sorted(IDS)


def test_catalog_has_every_independent_group_and_strict_action_contract(erp_deployed):
    catalog = read_json(erp_deployed['profile'] / 'catalog.json')
    assert len(CONTRACT['groups']) == 21
    actual_ids = [entry['id'] for entry in catalog['requirements']]
    assert sorted(actual_ids) == sorted(IDS)
    for prerequisite, dependent in CONTRACT['required_precedence']:
        assert actual_ids.index(prerequisite) < actual_ids.index(dependent)
    assert set(catalog) == {'schema', 'parameters', 'inherit_env', 'requirements'}
    for entry in catalog['requirements']:
        assert set(entry) == {'id', 'check', 'install', 'repair'}
        for role in ('check', 'install', 'repair'):
            assert set(entry[role]) == {'argv', 'cwd', 'env', 'output',
                                       'max_output_bytes', 'execution_bound_seconds'}
            assert isinstance(entry[role]['argv'], list) and entry[role]['argv']
            command = ' '.join(entry[role]['argv'])
            assert 'local-dev.mjs' not in command and 'start-dev' not in command


def test_full_working_supply_and_selected_parameters_are_materialized(erp_deployed):
    case = erp_deployed
    for relative in CONTRACT['working_profile_files']:
        assert (case['profile'] / relative).is_file(), relative
    catalog = read_json(case['profile'] / 'catalog.json')
    assert catalog['parameters']['repository']['default'] == str(case['repository'])
    assert catalog['parameters']['state_root']['default'] == str(case['state'])
    assert catalog['parameters']['project_id']['default'] == 'erp-acceptance'
    assert 'Ω' in str(case['profile']) and ' ' in str(case['repository'])
    assert not case['state'].exists(), 'Materialization is not performed maintenance'


def test_repeat_materialization_replaces_only_selected_supply(erp_deployed):
    case = erp_deployed
    case['state'].mkdir()
    retained = case['state'] / 'retained.txt'
    retained.write_bytes(b'Test-owned retained data\n')
    before = snapshot(case['state'])
    stale = case['profile'] / 'retired-supply.txt'
    stale.write_text('old selected supply\n')
    result = materialize(case, project='erp-replacement')
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)['status'] == 'replaced'
    assert not stale.exists()
    assert snapshot(case['state']) == before
    assert read_json(case['profile'] / 'catalog.json')['parameters']['project_id']['default'] == 'erp-replacement'
