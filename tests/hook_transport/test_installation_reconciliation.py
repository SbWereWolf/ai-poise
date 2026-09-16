"""Explicit migration of registry/live-file identity drift, never foreign hooks."""
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import write_json
from poise.common import PoiseError, digest, encoded
from poise.infrastructure.hook_transport import HookService
from poise.interfaces.hook_transport import execute
from poise.modules.hook_transport.domain import HookDefinition
from .helpers import definition, install, settings


def drift(project, tmp_path):
    service = HookService(settings(project, tmp_path))
    legacy = definition(); legacy['id'] = 'harness-main'
    install(service, legacy)
    # The historical registry used the former module name; no production repair
    # is allowed to require this fixture-only registry mutation.
    with service.registry.transaction() as db:
        row = json.loads(db.execute('SELECT data FROM installations WHERE id=?',
                                    ('harness-main',)).fetchone()[0])
        for _, group in row['groups']:
            group['hooks'][0]['command'] = group['hooks'][0]['command'].replace('-B -m poise', '-m harness')
        db.execute('UPDATE installations SET data=? WHERE id=?', (encoded(row), 'harness-main'))
    live = definition(); live['id'] = 'poise-main'
    live['events'][0]['status_message'] = 'Previous Poise binding'
    live_path = write_json(service.settings.definitions / (digest(live) + '.json'), live)
    groups = service.repository._groups(HookDefinition.parse(live), live_path)
    foreign = {'hooks': [{'type': 'command', 'command': 'echo user-owned'}], 'matcher': 'foreign'}
    document = {'description': 'operator-owned', 'hooks': {'SessionStart': [foreign]}}
    for kind, group in groups:
        document['hooks'].setdefault(kind, []).append(group)
    write_json(service.settings.hooks_file, document)
    current = definition(); current['id'] = 'poise-main'
    packet = {'request_id': 'rename-1', 'expected_revision': service.revision(),
              'previous_installation_id': 'harness-main', 'live_definition_path': str(live_path),
              'definition': current}
    return service, packet, foreign


def unchanged_snapshot(service):
    with service.registry.transaction() as db:
        rows = {table: [tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY 1')]
                for table in ('operations', 'installations', 'bindings', 'hook_events')}
    return service.settings.hooks_file.read_bytes(), rows


def assert_single_current(service, receipt, foreign):
    document = json.loads(service.settings.hooks_file.read_text())
    assert document['description'] == 'operator-owned'
    assert foreign in document['hooks']['SessionStart']
    for kind, groups in document['hooks'].items():
        managed = [g for g in groups if g != foreign]
        assert len(managed) == 1, (kind, groups)
        assert receipt['definition_path'] in managed[0]['hooks'][0]['command']
        assert ' -B -m poise hook ' in managed[0]['hooks'][0]['command']


def test_ordinary_install_does_not_append_duplicate_after_identity_drift(project, tmp_path):
    service, packet, _ = drift(project, tmp_path)
    before = unchanged_snapshot(service)
    with pytest.raises(PoiseError, match='reconcile'):
        install(service, packet['definition'], 'ordinary', packet['expected_revision'])
    assert unchanged_snapshot(service) == before


def test_explicit_reconciliation_preserves_foreign_groups_and_replays(project, tmp_path):
    service, packet, foreign = drift(project, tmp_path)
    result = service.reconcile(packet)
    assert_single_current(service, result, foreign)
    assert result['reconciliation']['previous_installation_id'] == 'harness-main'
    assert result['reconciliation']['installation_id'] == 'poise-main'
    before = unchanged_snapshot(service)
    assert service.reconcile(packet) == result
    assert unchanged_snapshot(service) == before
    changed = deepcopy(packet); changed['live_definition_path'] += '.different'
    with pytest.raises(PoiseError, match='Request id'):
        service.reconcile(changed)
    assert unchanged_snapshot(service) == before
    # The old identity remains audit history, not a reusable second active owner.
    with service.registry.transaction() as db:
        prior = json.loads(db.execute('SELECT data FROM installations WHERE id=?',
                                      ('harness-main',)).fetchone()[0])
    assert prior['migrated_to'] == 'poise-main'
    legacy = definition(); legacy['id'] = 'harness-main'
    with pytest.raises(PoiseError, match='migrated'):
        install(service, legacy, 'resurrect', service.revision())
    edited = deepcopy(packet['definition']); edited['events'][0]['timeout_seconds'] = 18
    after = install(service, edited, 'normal-after', service.revision())
    assert_single_current(service, after, foreign)


@pytest.mark.parametrize('problem', ['revision', 'unknown_old', 'wrong_live_id', 'changed_group'])
def test_reconciliation_rejects_unproven_scope_without_mutation(project, tmp_path, problem):
    service, packet, _ = drift(project, tmp_path)
    if problem == 'revision':packet['expected_revision'] = '0' * 64
    elif problem == 'unknown_old':packet['previous_installation_id'] = 'foreign'
    elif problem == 'wrong_live_id':packet['definition']['id'] = 'other-installation'
    else:
        doc = json.loads(service.settings.hooks_file.read_text())
        doc['hooks']['Stop'][0]['hooks'][0]['command'] = 'echo foreign changed'
        write_json(service.settings.hooks_file, doc)
        packet['expected_revision'] = service.revision()
    before = unchanged_snapshot(service)
    with pytest.raises(PoiseError):
        service.reconcile(packet)
    assert unchanged_snapshot(service) == before


def test_reconciliation_cli_is_public_and_pending_write_is_recoverable(project, tmp_path, monkeypatch):
    import poise.infrastructure.hook_transport as transport
    service, packet, foreign = drift(project, tmp_path)
    original = transport.atomic_write
    def interrupted(path, *args, **kwargs):
        if Path(path) == service.settings.hooks_file:raise OSError('interrupted hooks publication')
        return original(path, *args, **kwargs)
    before = service.settings.hooks_file.read_bytes()
    monkeypatch.setattr(transport, 'atomic_write', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        service.reconcile(packet)
    assert service.settings.hooks_file.read_bytes() == before
    monkeypatch.setattr(transport, 'atomic_write', original)
    output = io.StringIO(); error = io.StringIO()
    code = execute('runtime-config', SimpleNamespace(settings=service.settings.path),
                   io.BytesIO(json.dumps({'operation': 'reconcile', 'input': packet}).encode()), output, error)
    assert code == 0, (output.getvalue(), error.getvalue())
    result = json.loads(output.getvalue())
    assert_single_current(service, result, foreign)
    assert result['capability_checks']['ready']


def test_reconcile_retry_after_file_replace_before_registry_commit(project, tmp_path, monkeypatch):
    import poise.infrastructure.hook_transport as transport
    service, packet, foreign = drift(project, tmp_path)
    original = transport.atomic_write
    def interrupted(path, *args, **kwargs):
        original(path, *args, **kwargs)
        if Path(path) == service.settings.hooks_file:raise OSError('after file replacement')
    monkeypatch.setattr(transport, 'atomic_write', interrupted)
    with pytest.raises(OSError, match='after file'):
        service.reconcile(packet)
    with service.registry.transaction() as db:
        prior = json.loads(db.execute('SELECT data FROM installations WHERE id=?',
                                      ('harness-main',)).fetchone()[0])
        assert 'migrated_to' not in prior
    monkeypatch.setattr(transport, 'atomic_write', original)
    result = service.reconcile(packet)
    assert_single_current(service, result, foreign)
    assert service.reconcile(packet) == result
    legacy = definition(); legacy['id'] = 'harness-main'
    with pytest.raises(PoiseError, match='migrated'):
        install(service, legacy)  # Original historical request cannot resurrect it.


def test_live_drift_may_use_prior_direct_no_bytecode_form(project, tmp_path):
    service, packet, foreign = drift(project, tmp_path)
    doc = json.loads(service.settings.hooks_file.read_text())
    for groups in doc['hooks'].values():
        for group in groups:
            if group != foreign:
                group['hooks'][0]['command'] = group['hooks'][0]['command'].replace(' -B -m poise', ' -m poise')
    write_json(service.settings.hooks_file, doc)
    packet['expected_revision'] = service.revision()
    result = service.reconcile(packet)
    assert_single_current(service, result, foreign)


def test_reconcile_removes_both_proven_live_and_current_registered_groups(project, tmp_path):
    service, packet, foreign = drift(project, tmp_path)
    current = HookDefinition.parse(packet['definition'])
    path = service.settings.definitions / (digest(current.data) + '.json')
    path.write_text(json.dumps(current.data, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    groups = service.repository._groups(current, path)
    doc = json.loads(service.settings.hooks_file.read_text())
    for event, group in groups:
        doc['hooks'][event].append(group)
    write_json(service.settings.hooks_file, doc)
    # Fixture for the former install having appended a second current group.
    with service.registry.transaction() as db:
        db.execute('INSERT INTO installations VALUES(?,?)', ('poise-main', encoded({
            'groups': groups, 'receipt': {'hooks_path': str(service.settings.hooks_file)}})))
    packet['expected_revision'] = service.revision()
    result = service.reconcile(packet)
    assert_single_current(service, result, foreign)
