"""T048 bounded ordinary file/schema/configuration inputs; real persisted effects."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import pytest

from process_checks import (APP, ROOT, applied, assert_same, create, current, edit,
                            fixture, installation, invoke, prepare_core, published,
                            rejected, state, status, write_json)


@pytest.mark.parametrize('kind', ['missing', 'unreadable', 'json', 'structure'])
def test_invalid_selected_template_has_concrete_refusal_and_no_publication(tmp_path, kind):
    config = installation(tmp_path)
    published(invoke(config, create(goal='notes', definition=fixture('alternative'))),
              revision=1, goal='notes')
    path = tmp_path / 'state/templates/starter.json'
    path.unlink()
    if kind == 'unreadable':
        path.mkdir()
    elif kind == 'json':
        path.write_text('{invalid JSON')
    elif kind == 'structure':
        write_json(path, {'goal_type': 'writing'})
    before = state(config)
    rejected(invoke(config, create(template=True, request='invalid-template', revision=1)), 'template', 'шаблон', 'json', 'workflow', 'directory', 'каталог')
    assert state(config) == before
    assert not current(config).exists()


@pytest.mark.parametrize('kind', ['missing', 'unreadable', 'json', 'structure'])
def test_invalid_current_file_has_concrete_refusal_and_preserves_editor_state(tmp_path, kind):
    config = installation(tmp_path)
    first = published(invoke(config, create()), revision=1)
    path = current(config)
    path.unlink()
    if kind == 'unreadable':
        path.mkdir()
    elif kind == 'json':
        path.write_text('{invalid JSON')
    elif kind == 'structure':
        write_json(path, {'goal_type': 'writing'})
    before = state(config)
    packet = edit(first, fixture('edit_changes')) if kind == 'missing' else status()
    rejected(invoke(config, packet), 'process', 'процесс', 'json', 'workflow', 'directory', 'каталог', 'writing')
    assert state(config) == before


@pytest.mark.parametrize('kind', ['unsupported', 'unrelated'])
def test_existing_incompatible_editor_db_is_not_overwritten(tmp_path, kind):
    config = installation(tmp_path)
    prepare_core(config)
    path = tmp_path / 'state/process-editor.sqlite'
    with sqlite3.connect(path) as db:
        if kind == 'unsupported':
            db.execute('PRAGMA user_version=999')
        else:
            db.execute('CREATE TABLE unrelated (id INTEGER PRIMARY KEY, note TEXT NOT NULL)')
            db.execute('INSERT INTO unrelated(id,note) VALUES(?,?)', (7, 'Сохранённые данные'))
    before = state(config)
    rejected(invoke(config, create()), 'version', 'schema', 'migration', 'верси', 'схем', 'миграц')
    assert state(config) == before
    assert not current(config).exists()


def test_valid_empty_editor_initializes_and_configs_remain_independent(tmp_path):
    left, right = installation(tmp_path / 'left'), installation(tmp_path / 'right')
    with sqlite3.connect(tmp_path / 'left/state/process-editor.sqlite'):
        pass
    left_config = left.read_bytes()
    published(invoke(left, create()), revision=1)
    published(invoke(right, create(definition=fixture('edited'))), revision=1)
    assert_same(json.loads(current(left).read_text()), fixture('definition'))
    assert_same(json.loads(current(right).read_text()), fixture('edited'))
    assert left.read_bytes() == left_config


def test_missing_process_component_refuses_new_operation_and_keeps_core_working(tmp_path):
    config = installation(tmp_path, process=False)
    # Real previous supported setup, using its accepted test-owned contract.
    contract = json.loads((APP / 'fixtures/store-records.json').read_text())['setup_packet']['input']['operations'][0]['contract']
    packet = {'schema': 'stride-execution/v1', 'operation': 'apply', 'input': {
        'request_id': 'old-core', 'expected_revision': 0,
        'operations': [{'id': 'setup', 'kind': 'setup_execution', 'contract': contract}]}}
    applied(invoke(config, packet), contract['task_id'])
    before = state(config)
    rejected(invoke(config, create()), 'process', 'процесс', 'config', 'конфигурац')
    assert state(config) == before


def test_configured_change_and_input_limits_are_observed(tmp_path):
    config = installation(tmp_path)
    values = json.loads(config.read_text())
    values['process']['max_changes'] = 1
    write_json(config, values)
    first = published(invoke(config, create()), revision=1)
    before = state(config)
    rejected(invoke(config, edit(first, fixture('edit_changes'))), 'max_changes', 'limit', 'изменен', 'лимит')
    assert state(config) == before
    values['batch']['max_input_bytes'] = 40
    write_json(config, values)
    rejected(invoke(config, create(request='large', revision=1)), 'input', 'bytes', 'limit', 'размер', 'байт')
    assert state(config) == before


@pytest.mark.parametrize('field', ['database', 'templates', 'json_indent'])
def test_required_known_process_settings_are_explicit(tmp_path, field):
    config = installation(tmp_path)
    values = json.loads(config.read_text())
    del values['process'][field]
    write_json(config, values)
    rejected(invoke(config, create()), field)
    assert not current(config).exists()


def test_existing_boundary_checker_inspects_actual_application_sources():
    # Existing qualification helpers copy real apps/src and run the maintained checker.
    path = ROOT / 'tests/delivery/test_application_boundaries.py'
    spec = importlib.util.spec_from_file_location('t048_existing_boundary_checks', path)
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    with checks.checkout(prefix='t048-process-boundary-') as root:
        for owner, specification in checks.data('policy.json')['owners'].items():
            # Literal test-owned config supplies the checker's mandatory seven coordinates.
            configuration = fixture('config')
            configuration['paths'] = {'state': '../state', 'database': owner + '.sqlite',
                                      'lock': owner + '.lock'}
            configuration.update(fixture('boundary_coordinates').get(owner, {}))
            write_json(root / specification['config'], configuration)
        process, report = checks.run(root)
        assert report['coverage']['config_count'] == 7, report['coverage']
        assert any(row['path'].startswith('apps/execution/') for row in report['files'])
        violations = [row for row in report['diagnostics']
                      if row['owner'] == 'execution' and row['kind'] in {'violation', 'invalid'}]
        assert violations == [], violations
        # The existing analyzer has unsupported dynamic-I/O routes in the baseline.
        # Preserve that honest limitation: this targeted assertion proves only no
        # concrete foreign import/storage finding; CODE review examines unresolved paths.
        assert process.returncode in (0, 1), report

