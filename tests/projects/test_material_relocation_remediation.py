"""Missing admission, authority and transaction-boundary falsifiers from review."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from batch.helpers import request
from conftest import WorkPoise, write_json
from poise.application.artifact_materials import ArtifactMaterialResolver
from poise.application.work import WorkTools
from poise.common import PoiseError, load_config
from projects.test_update import update_tools
from .material_relocation_helpers import (
    STDERR, STDOUT, checksum, empty_contract, fresh, immutable, other_owner,
    placed, prepare, release, relocate, required,
)


def assert_admission_refusal(case, tools, task_id, worktree):
    before = immutable(tools.runtime)
    config_bytes = case['config_path'].read_bytes()
    claim = tools.runtime.task_queries.record(task_id)['claimed_by']
    operator = Path(worktree) / 'src/operator.txt'
    operator.write_bytes(b'foreign working bytes\n')
    error = None
    try:
        update_tools(case['settings']).apply(case['packet'])
    except PoiseError as exc:
        error = exc
    assert error is not None, 'The actual relocation route ignored a foreign live owner'
    assert str(error)
    assert case['source'].is_dir()
    assert case['config_path'].read_bytes() == config_bytes
    assert tools.runtime.task_queries.record(task_id)['claimed_by'] == claim
    assert immutable(tools.runtime) == before
    assert operator.read_bytes() == b'foreign working bytes\n'
    assert Path(case['receipt']['stdout']).read_bytes() == STDOUT


def test_actual_apply_refuses_foreign_task_writer(project, tmp_path):
    case = prepare(project, tmp_path, released=False)
    assert_admission_refusal(case, case['tools'], 'T1', case['context']['worktree'])


def test_actual_apply_refuses_shared_sprint_writer(project, tmp_path):
    from sprints.helpers import draft, publish
    case = prepare(project, tmp_path)
    planner = fresh(case, 'sprint-planner')
    contract_a = empty_contract(case, 'A', 'S')
    contract_b = empty_contract(case, 'B', 'S')
    planned = required(draft, planner, [contract_a, contract_b], edges=[{
        'predecessor': 'A', 'successor': 'B', 'kind': 'completion',
    }])
    required(publish, planner, planned['revision'])
    writer = fresh(case, 'shared-sprint-writer')
    context = required(writer.invoke, request('bootstrap', {
        'task': {'id': 'A'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    published = required(writer.invoke, request('artifacts', {'items': [{
        'scope': 'sprint', 'path': 'shared.txt',
        'source': {'kind': 'text', 'text': 'shared sprint bytes\n'},
    }]}))
    shared = Path(published['artifact_paths'][0])
    assert shared.read_bytes() == b'shared sprint bytes\n'
    assert writer.runtime.task_queries.record('A')['sprint_id'] == 'S'
    assert writer.runtime.task_queries.record('B')['sprint_id'] == 'S'
    assert_admission_refusal(case, writer, 'A', context['worktree'])
    assert shared.read_bytes() == b'shared sprint bytes\n'


def test_actual_apply_succeeds_after_foreign_writer_public_release(project, tmp_path):
    case = prepare(project, tmp_path, released=False)
    release(case['tools'])
    moved = relocate(case)
    assert moved.runtime.task_queries.record('T1')['claimed_by'] is None
    assert moved.runtime._receipt_files_intact(case['receipt'], case['method'])


def test_partial_copy_interruption_keeps_source_then_resumes(project, tmp_path, monkeypatch):
    import poise.infrastructure.project_config as owner
    case = prepare(project, tmp_path)
    before = immutable(case['tools'].runtime)
    config_bytes = case['config_path'].read_bytes()

    def partial(source, staging):
        staging.mkdir()
        target = staging / Path(case['receipt']['stdout']).relative_to(source)
        target.parent.mkdir(parents=True)
        shutil.copyfile(case['receipt']['stdout'], target)
        assert len([path for path in staging.rglob('*') if path.is_file()]) == 1
        raise OSError('Interrupted during an actually incomplete copy')

    with monkeypatch.context() as patch:
        patch.setattr(owner, 'copy_state_tree', partial)
        with pytest.raises(PoiseError, match='retry|повтор'):
            update_tools(case['settings']).apply(case['packet'])
    assert Path(case['receipt']['stdout']).read_bytes() == STDOUT
    assert case['config_path'].read_bytes() == config_bytes
    assert immutable(case['tools'].runtime) == before
    moved = relocate(case)
    assert immutable(moved.runtime) == before
    assert moved.runtime._receipt_files_intact(case['receipt'], case['method'])


@pytest.mark.parametrize('seam', [
    'copy_state_tree', 'publish_state_tree', 'switch_project_config', 'remove_source_state',
])
def test_failure_before_existing_effect_is_recoverable(project, tmp_path, monkeypatch, seam):
    import poise.infrastructure.project_config as owner
    case = prepare(project, tmp_path)
    before = immutable(case['tools'].runtime)

    def interrupted(*args, **kwargs):
        raise OSError('Interrupted before ' + seam)

    with monkeypatch.context() as patch:
        patch.setattr(owner, seam, interrupted)
        with pytest.raises(PoiseError, match='retry|повтор'):
            update_tools(case['settings']).apply(case['packet'])
    assert case['source'].is_dir()
    assert Path(case['receipt']['stdout']).read_bytes() == STDOUT
    assert immutable(case['tools'].runtime) == before
    moved = relocate(case)
    assert immutable(moved.runtime) == before
    assert moved.runtime._receipt_files_intact(case['receipt'], case['method'])


def pending_before_retirement(case, monkeypatch):
    import poise.infrastructure.project_config as owner

    def stop(source):
        raise OSError('Stop before source retirement')

    with monkeypatch.context() as patch:
        patch.setattr(owner, 'remove_source_state', stop)
        with pytest.raises(PoiseError, match='retry|повтор'):
            update_tools(case['settings']).apply(case['packet'])
    assert case['source'].is_dir() and case['destination'].is_dir()


def recovery_request(case, moved, records):
    return request('recover_artifacts', {
        'request_id': 'recover-interrupted-publication', 'task_id': 'T1',
        'expected_version': moved.runtime.task_queries.record('T1')['_version'],
        'artifact_ids': [row['id'] for row in records],
        'source_roots': {'task': case['context']['task_root']},
        'reason': 'Publish admitted destination registry through its existing owner.',
        'authorization': 'Disposable fixture owner approves exact relocation.',
    })


@pytest.mark.parametrize('timing', ['before', 'after'])
def test_registry_publication_interruption_rolls_back_and_replays_once(
        project, tmp_path, monkeypatch, timing):
    from poise.infrastructure.sqlite.artifacts import SqliteArtifactRepository
    case = prepare(project, tmp_path)
    records = case['tools'].runtime.store.artifact_records('T1')
    pending_before_retirement(case, monkeypatch)
    moved = fresh(case)
    before = immutable(moved.runtime)
    packet = recovery_request(case, moved, records)
    effect = SqliteArtifactRepository.rebind
    observed = []

    def interrupted(self, *args, **kwargs):
        observed.append(timing)
        if timing == 'after':
            effect(self, *args, **kwargs)
        raise OSError('Interrupted ' + timing + ' registry publication')

    with monkeypatch.context() as patch:
        patch.setattr(SqliteArtifactRepository, 'rebind', interrupted)
        with pytest.raises(OSError, match='registry publication'):
            moved.invoke(packet)
    assert observed == [timing]
    assert moved.runtime.store.artifact_records('T1') == records
    assert immutable(moved.runtime) == before
    assert Path(case['receipt']['stdout']).read_bytes() == STDOUT
    assert case['source'].is_dir()
    done = required(moved.invoke, packet)
    replay = required(moved.invoke, packet)
    assert done == replay
    assert done['status'] == 'artifacts_recovered'
    rows = moved.runtime.store.artifact_records('T1')
    assert len(rows) == len(records)
    for old, new in zip(records, rows, strict=True):
        assert new['id'] == old['id'] and new['digest'] == old['digest']
        assert Path(new['path']) == placed(case, old['path'])
        assert checksum(Path(new['path']).read_bytes()) == old['digest']
    with moved.runtime.store.transaction() as db:
        count = db.execute("SELECT COUNT(*) FROM journal WHERE event='artifacts.recovered'").fetchone()[0]
    assert count == 1
    assert immutable(moved.runtime) == before


@pytest.mark.parametrize('timing', ['before', 'after'])
@pytest.mark.parametrize('publication', ['definition', 'native-hooks'])
def test_hook_publication_interruption_replays_without_duplicate_groups(
        project, tmp_path, monkeypatch, timing, publication):
    import poise.infrastructure.hook_transport as owner
    from hook_transport.helpers import install, settings
    case = prepare(project, tmp_path, native_hooks=True)
    service = owner.HookService(settings(case['project'], tmp_path))
    before = immutable(case['tools'].runtime)
    effect = owner.atomic_write
    observed = []

    def interrupted(path, *args, **kwargs):
        selected = (Path(path) == service.settings.hooks_file if publication == 'native-hooks'
                    else Path(path).parent == service.settings.definitions)
        if not selected:
            return effect(path, *args, **kwargs)
        observed.append(timing)
        if timing == 'after':
            effect(path, *args, **kwargs)
        raise OSError('Interrupted ' + timing + ' hook publication')

    with monkeypatch.context() as patch:
        patch.setattr(owner, 'atomic_write', interrupted)
        with pytest.raises(OSError, match='hook publication'):
            install(service)
    assert observed == [timing]
    assert case['source'].is_dir()
    assert Path(case['receipt']['stdout']).read_bytes() == STDOUT
    assert immutable(case['tools'].runtime) == before
    done = required(install, service)
    hooks = service.settings.hooks_file.read_bytes()
    definition = Path(done['definition_path']).read_bytes()
    replay = required(install, service)
    assert replay == done
    assert service.settings.hooks_file.read_bytes() == hooks
    assert Path(done['definition_path']).read_bytes() == definition
    groups = json.loads(hooks)['hooks']
    assert len(groups) == 4 and all(len(items) == 1 for items in groups.values())
    assert immutable(case['tools'].runtime) == before


@pytest.mark.parametrize('fault', ['destination-bytes', 'destination-writer'])
def test_pending_retry_rejects_changed_destination_authority(project, tmp_path, monkeypatch, fault):
    case = prepare(project, tmp_path)
    pending_before_retirement(case, monkeypatch)
    if fault == 'destination-bytes':
        target = placed(case, case['receipt']['stdout'])
        target.write_bytes(b'foreign destination bytes\n')
    else:
        writer = fresh(case, 'destination-writer')
        context = writer.invoke(request('bootstrap', {
            'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
        }))
        target = Path(context['worktree']) / 'src/operator.txt'
        target.write_bytes(b'destination writer work\n')
        assert writer.runtime.task_queries.record('T1')['claimed_by'] == 'destination-writer'
    trusted = target.read_bytes()
    before = immutable(fresh(case).runtime)
    with pytest.raises(PoiseError):
        update_tools(case['settings']).apply(case['packet'])
    assert target.read_bytes() == trusted
    assert case['source'].is_dir()
    assert Path(case['receipt']['stdout']).read_bytes() == STDOUT
    assert immutable(fresh(case).runtime) == before


def test_failed_destination_readback_blocks_source_retirement(project, tmp_path, monkeypatch):
    import poise.infrastructure.project_config as owner
    case = prepare(project, tmp_path)
    before = immutable(case['tools'].runtime)
    switch = owner.switch_project_config
    observed = []

    def publish_then_damage(*args, **kwargs):
        switch(*args, **kwargs)
        placed(case, case['receipt']['stdout']).write_bytes(b'damaged before consumer readback\n')
        observed.append('configuration-published')

    with monkeypatch.context() as patch:
        patch.setattr(owner, 'switch_project_config', publish_then_damage)
        error = None
        try:
            update_tools(case['settings']).apply(case['packet'])
        except PoiseError as exc:
            error = exc
    assert observed == ['configuration-published']
    assert error is not None, 'Source retired despite failed destination material readback'
    assert case['source'].is_dir()
    assert Path(case['receipt']['stdout']).read_bytes() == STDOUT
    assert placed(case, case['receipt']['stdout']).read_bytes() == b'damaged before consumer readback\n'
    assert immutable(case['tools'].runtime) == before


def test_explicit_unmoved_live_receipt_is_readable(project, tmp_path):
    case = prepare(project, tmp_path)
    reader = fresh(case)
    before = immutable(reader.runtime)
    views = reader.runtime.result_views.command_views([case['receipt']])
    assert 'presentation_error' not in views[0]
    assert STDOUT.decode().strip() in views[0]['primary']
    assert STDERR.decode().strip() in views[0]['primary']
    assert reader.runtime._receipt_files_intact(case['receipt'], case['method'])
    assert immutable(reader.runtime) == before


@pytest.mark.parametrize('scope', ['task', 'runtime'])
def test_real_foreign_owner_and_scope_cannot_supply_task_material(project, tmp_path, scope):
    case = prepare(project, tmp_path)
    initial = fresh(case)
    own = initial.runtime.store.artifact_records('T1')
    authorized = required(ArtifactMaterialResolver(initial.runtime.delivery_tools).resolve,
                          'T1', own)
    assert set(authorized) == {record['id'] for record in own}
    for record in own:
        material = authorized[record['id']]
        assert material.matches(record)
        assert checksum(Path(material.physical_path).read_bytes()) == record['digest']
    foreign, _, record = other_owner(case, scope=scope)
    assert Path(record['path']).read_bytes() == b'foreign owner material\n'
    assert record['owner'] == ('OTHER' if scope == 'task' else 'foreign-writer')
    reader = fresh(case)
    before = immutable(reader.runtime)
    error = None
    try:
        ArtifactMaterialResolver(reader.runtime.delivery_tools).resolve('T1', [record])
    except PoiseError as exc:
        error = exc
    assert error is not None, 'Material authority accepted another Task or runtime scope'
    assert immutable(reader.runtime) == before
    assert reader.runtime.evidence_commands.list_for('T1')[0] == case['receipt']
    assert case['marker'].read_text() == 'run\n'


def test_unconfirmed_destination_decoy_does_not_authorize_readback(project, tmp_path, monkeypatch):
    import poise.infrastructure.project_config as owner
    case = prepare(project, tmp_path)
    positive = fresh(case).runtime.result_views.command_views([case['receipt']])
    assert 'presentation_error' not in positive[0]
    assert STDOUT.decode().strip() in positive[0]['primary']
    publish = owner.publish_state_tree

    def stop_after_copy(*args, **kwargs):
        publish(*args, **kwargs)
        raise OSError('Stop after physical copy before placement/config publication')

    with monkeypatch.context() as patch:
        patch.setattr(owner, 'publish_state_tree', stop_after_copy)
        with pytest.raises(PoiseError, match='retry|повтор'):
            update_tools(case['settings']).apply(case['packet'])
    # This separate disposable candidate is not the registered live manifest.
    # It cannot turn mere copied files into a confirmed relocation authority.
    root, cfg, _ = load_config(case['config_path'])
    candidate = deepcopy(cfg)
    candidate['paths']['state'] = str(case['destination'])
    path = write_json(root / 'unconfirmed-reader.json', candidate)
    decoy = placed(case, case['receipt']['stdout'])
    decoy.write_bytes(b'unconfirmed destination decoy\n')
    reader = WorkTools(WorkPoise(path, 'unconfirmed-reader'))
    before = immutable(reader.runtime)
    views = reader.runtime.result_views.command_views([case['receipt']])
    assert 'presentation_error' in views[0], 'Existing bytes were mistaken for placement authority'
    assert 'unconfirmed destination decoy' not in views[0].get('primary', '')
    assert immutable(reader.runtime) == before
    assert Path(case['receipt']['stdout']).read_bytes() == STDOUT
    assert decoy.read_bytes() == b'unconfirmed destination decoy\n'
