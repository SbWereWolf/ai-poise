"""Legacy historical fixtures are disposable, never installation database repairs."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from poise.common import PoiseError
from projects.test_update import update_tools
from .material_relocation_helpers import immutable, placed, prepare, relocate, required


def legacy(case, status, *, unbound=True, missing=('stdout', 'stderr')):
    """Arrange a pre-binding historical database only under pytest tmp_path.

    Public verification produces a real receipt; direct fixture SQL represents
    older persisted terminal history without inventing a production migration.
    """
    database = case['source'] / case['project']['cfg']['paths']['database']
    streams = [case['receipt'][name] for name in ('stdout', 'stderr')]
    with sqlite3.connect(database) as db:
        db.execute('UPDATE tasks SET status=? WHERE id=?', (status, 'T1'))
        if unbound:
            db.execute("DELETE FROM journal WHERE task_id=? AND event='artifact.materials_bound'",
                       ('T1',))
            for path in streams:
                db.execute('DELETE FROM task_artifacts WHERE artifact_id IN '
                           '(SELECT id FROM artifacts WHERE path=?)', (path,))
                db.execute('DELETE FROM artifacts WHERE path=?', (path,))
        assert db.execute("SELECT count(*) FROM journal WHERE event='task_delivery.state'").fetchone()[0] == 0
    for stream in missing:
        Path(case['receipt'][stream]).unlink()


def registry(case, root):
    with sqlite3.connect(root / case['project']['cfg']['paths']['database']) as db:
        db.row_factory = sqlite3.Row
        return {
            'artifacts': [dict(row) for row in db.execute(
                'SELECT id,owner,scope,path,digest FROM artifacts ORDER BY id')],
            'links': [tuple(row) for row in db.execute(
                'SELECT task_id,artifact_id FROM task_artifacts ORDER BY task_id,artifact_id')],
            'journal': [tuple(row) for row in db.execute(
                "SELECT seq,task_id,event,data FROM journal WHERE event LIKE 'artifact.%' "
                "OR event='artifacts.recovered' ORDER BY seq")],
        }


@pytest.mark.parametrize('status', ['completed', 'cancelled'])
@pytest.mark.parametrize('missing', [('stdout',), ('stderr',), ()])
def test_unbound_terminal_history_preserves_receipt_and_live_files(project, tmp_path, status, missing):
    case = prepare(project, tmp_path)
    legacy(case, status, missing=missing)
    before = immutable(case['tools'].runtime)
    receipt = deepcopy(case['receipt'])
    registrations = registry(case, case['source'])
    records = registrations['artifacts']
    assert records, 'Historical raw logs must coexist with unrelated live registrations'
    expected = {row['id']: Path(row['path']).read_bytes() for row in records}
    moved = relocate(case)
    assert immutable(moved.runtime) == before
    assert required(moved.runtime.evidence_commands.list_for, 'T1') == [receipt]
    relocated = registry(case, case['destination'])
    assert relocated['links'] == registrations['links']
    actual = relocated['artifacts']
    assert len(actual) == len(records)
    assert {row['id'] for row in actual} == {row['id'] for row in records}
    by_id = {row['id']: row for row in records}
    for row in actual:
        assert {key: row[key] for key in ('owner', 'scope', 'digest')} == {
            key: by_id[row['id']][key] for key in ('owner', 'scope', 'digest')}
        assert Path(row['path']) == placed(case, by_id[row['id']]['path'])
        assert Path(row['path']).read_bytes() == expected[row['id']]
    full, history, views = moved.runtime.result_views.present_result(
        {'checks': [receipt]}, moved.runtime.retired_receipt, moved.runtime.task_queries.receipt_task)
    expected_history = json.loads((Path(__file__).parent / 'fixtures/terminal_relocation_history.json').read_text())
    expected_history['historical_receipts'][0]['checks'][0]['id'] = receipt['id']
    assert history == expected_history
    assert full == {'checks': [receipt], **history}
    assert len(views) == 1 and views[0]['id'] == receipt['id']
    assert views == [{**expected_history['historical_receipts'][0]['checks'][0],
                     'availability': 'retired', 'primary': expected_history['notice']}]
    assert 'presentation_error' not in views[0]
    for stream in ('stdout', 'stderr'):
        assert placed(case, receipt[stream]).exists() == (stream not in missing)
    assert required(update_tools(case['settings']).apply, case['packet'])['replayed'] is True


@pytest.mark.parametrize('status', ['completed', 'cancelled'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_terminal_status_preserves_live_registration_guards(project, tmp_path, status, damage):
    case = prepare(project, tmp_path)
    legacy(case, status)
    record = registry(case, case['source'])['artifacts'][0]
    path = Path(record['path'])
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'independently corrupted registered file\n')
    config = case['config_path'].read_bytes()
    before = immutable(case['tools'].runtime)
    registered = registry(case, case['source'])
    with pytest.raises(PoiseError, match='^Destination material missing or digest changed$'):
        update_tools(case['settings']).apply(case['packet'])
    assert case['config_path'].read_bytes() == config
    assert immutable(case['tools'].runtime) == before
    assert case['source'].is_dir()
    assert registry(case, case['destination']) == registered


@pytest.mark.parametrize('status', ['verified', 'completed', 'cancelled'])
def test_bound_raw_material_without_retirement_remains_required(project, tmp_path, status):
    case = prepare(project, tmp_path)
    legacy(case, status, unbound=False)
    config = case['config_path'].read_bytes()
    with pytest.raises(PoiseError):
        update_tools(case['settings']).apply(case['packet'])
    assert case['config_path'].read_bytes() == config
    assert case['source'].is_dir()


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_nonterminal_unbound_damaged_raw_receipt_refuses(project, tmp_path, stream, damage):
    case = prepare(project, tmp_path)
    legacy(case, 'verified', missing=())
    path = Path(case['receipt'][stream])
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'changed raw receipt stream\n')
    config = case['config_path'].read_bytes()
    registered = registry(case, case['source'])
    with pytest.raises(PoiseError):
        update_tools(case['settings']).apply(case['packet'])
    assert case['config_path'].read_bytes() == config
    assert case['source'].is_dir()
    assert registry(case, case['destination']) == registered


def test_copied_retry_rolls_back_registry_and_preserves_original_seal(project, tmp_path, monkeypatch):
    from poise.infrastructure.sqlite.artifacts import SqliteArtifactRepository
    import poise.infrastructure.project_config as effects
    case = prepare(project, tmp_path)
    legacy(case, 'completed')
    config = case['config_path'].read_bytes()
    original = SqliteArtifactRepository.rebind
    observed = []

    def fail_after_rebind(self, *args, **kwargs):
        original(self, *args, **kwargs)
        observed.append('rebound')
        raise OSError('test interruption after actual registry write')

    with monkeypatch.context() as patch:
        patch.setattr(SqliteArtifactRepository, 'rebind', fail_after_rebind)
        with pytest.raises(PoiseError, match='^Project publication interrupted; retry the same request: '
                           'test interruption after actual registry write$'):
            update_tools(case['settings']).apply(case['packet'])
    assert observed == ['rebound']
    assert case['config_path'].read_bytes() == config
    pending_path = project['root'] / (case['packet']['receipt_path'] + '.pending')
    pending = json.loads(pending_path.read_text())
    assert pending['relocation']['phase'] == 'copied'
    assert registry(case, case['destination']) == registry(case, case['source'])
    before = immutable(case['tools'].runtime)
    import poise.infrastructure.project_state_relocation as relocation_owner
    validate = relocation_owner.validate_source
    seals = []

    def original_seal(root, seal):
        assert seal == pending['relocation']['seal']
        seals.append(deepcopy(seal))
        return validate(root, seal)

    def forbidden(*args, **kwargs):
        pytest.fail('Copied recovery must preserve the admitted seal without recopying/recomparing')

    with monkeypatch.context() as patch:
        patch.setattr(effects, 'same_tree', forbidden)
        patch.setattr(effects, 'copy_state_tree', forbidden)
        patch.setattr(relocation_owner, 'file_snapshot', forbidden)
        patch.setattr(relocation_owner, 'validate_source', original_seal)
        moved = relocate(case)
    assert seals
    assert immutable(moved.runtime) == before
    assert moved.runtime.evidence_commands.list_for('T1') == [case['receipt']]
    final = json.loads(pending_path.with_name(pending_path.name.removesuffix('.pending')).read_text())
    assert final['request_digest'] == pending['request_digest']
    publications = registry(case, case['destination'])
    assert required(update_tools(case['settings']).apply, case['packet'])['replayed'] is True
    assert registry(case, case['destination']) == publications


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_required_permanent_material_remains_guarded_after_relocation(project, tmp_path, damage):
    from batch.helpers import request
    from poise.application.artifact_materials import ArtifactMaterialResolver
    from poise.infrastructure.artifact_factory import FileArtifactFactory
    from terminal_materials.helpers import agree, cancel_and_discard
    from .material_relocation_helpers import INPUT, checksum
    case = prepare(project, tmp_path)
    tools = case['tools']
    required(tools.invoke, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    records = registry(case, case['source'])['artifacts']
    record = next(row for row in records if row['path'].endswith('/artifacts/inputs/accepted.whl'))
    permanent = tmp_path / 'permanent-input.bin'
    agree(tools, {'disposition': 'deliver', 'outputs': [{
        'id': 'accepted-input', 'kind': 'file', 'source': record['path'],
        'destination': str(permanent), 'digest': checksum(INPUT),
    }]})
    required(cancel_and_discard, tools, case['done']['commit'])
    assert permanent.read_bytes() == INPUT
    moved = relocate(case)
    before = immutable(moved.runtime)
    registrations = registry(case, case['destination'])
    consumer = tmp_path / 'material-consumer'
    consumer.mkdir()
    sentinel = consumer / 'operator.txt'
    sentinel.write_bytes(b'existing consumer work\n')
    cfg = moved.runtime.cfg
    factory = FileArtifactFactory({'task': consumer}, cfg['batch'])

    def consume():
        materials = ArtifactMaterialResolver(moved.runtime.delivery_tools).resolve('T1', [record])
        assert materials[record['id']].physical_path == str(permanent)
        assert materials[record['id']].digest == checksum(INPUT)
        return factory.plan_material_copy(
            [record], {'task': case['context']['task_root']}, {'task': 'T1'},
            {'task': 'CONSUMER'}, materials=materials)

    planned = consume()
    assert len(planned) == 1
    assert planned[0]['path'] == str(consumer / 'artifacts/inputs/accepted.whl')
    assert planned[0]['owner'] == 'CONSUMER' and planned[0]['digest'] == checksum(INPUT)
    assert planned[0]['source_id'] == record['id']
    if damage == 'missing':
        permanent.unlink()
    else:
        permanent.write_bytes(b'independently corrupted permanent bytes\n')
    with pytest.raises(PoiseError):
        consume()
    assert immutable(moved.runtime) == before
    assert registry(case, case['destination']) == registrations
    assert list(consumer.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b'existing consumer work\n'
    assert not case['source'].exists()
    assert permanent.exists() == (damage == 'corrupt')
