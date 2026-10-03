"""Required byte retention through the existing declarative artifact batch."""
from pathlib import Path
import hashlib

import pytest

from batch.helpers import request
from poise.common import PoiseError
from verification.retention_helpers import (
    BYTES, digest, file_item, invoke_required, source_file, start,
)


def test_binary_batch_preserves_exact_bytes_and_identical_retry(project, tmp_path):
    tools, context = start(project)
    source = source_file(tmp_path)
    packet = {'items': [file_item(source)]}
    first = invoke_required(tools, 'artifacts', packet)
    target = Path(context['task_root']) / 'artifacts/inputs/accepted.whl'
    assert target.read_bytes() == b'\x00\xffaccepted-wheel\x80\n'
    assert first['artifact_paths'] == [str(target)]
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest(BYTES)
    repeated = invoke_required(tools, 'artifacts', packet)
    assert repeated['artifact_paths'] == first['artifact_paths']
    assert repeated['artifacts'] == first['artifacts']
    source.write_bytes(b'other accepted bytes')
    with pytest.raises(PoiseError, match='digest|different content|conflict'):
        tools.invoke(request('artifacts', packet))
    assert target.read_bytes() == b'\x00\xffaccepted-wheel\x80\n'


@pytest.mark.parametrize('fault', ['missing', 'bytes', 'symlink', 'directory'])
def test_invalid_binary_source_rejects_before_valid_member_or_registry_write(
    project, tmp_path, fault,
):
    tools, context = start(project)
    source = source_file(tmp_path)
    bad = tmp_path / 'bad.whl'
    if fault == 'bytes':
        bad.write_bytes(b'different bytes')
    elif fault == 'symlink':
        bad.symlink_to(source)
    elif fault == 'directory':
        bad.mkdir()
    before = tools.runtime.store.artifact_records('T1')
    before_task = tools.runtime.task_queries.record('T1')
    with pytest.raises(PoiseError, match='source|digest|symlink|regular|exist') as failure:
        tools.invoke(request('artifacts', {
            'items': [file_item(source), file_item(bad, 'inputs/bad.whl')],
        }))
    assert str(bad) in str(failure.value), 'refusal must identify the invalid file'
    assert not (Path(context['task_root']) / 'artifacts/inputs/accepted.whl').exists()
    assert tools.runtime.store.artifact_records('T1') == before
    assert tools.runtime.task_queries.record('T1') == before_task


def test_conflicting_binary_destination_never_overwrites_first_bytes(project, tmp_path):
    tools, context = start(project)
    source = source_file(tmp_path)
    invoke_required(tools, 'artifacts', {'items': [file_item(source)]})
    alternate = tmp_path / 'rebuilt.whl'
    alternate.write_bytes(b'rebuilt-wheel')
    before = tools.runtime.store.artifact_records('T1')
    with pytest.raises(PoiseError, match='different content|conflict'):
        tools.invoke(request('artifacts', {
            'items': [file_item(alternate, expected=digest(b'rebuilt-wheel'))],
        }))
    assert (Path(context['task_root']) / 'artifacts/inputs/accepted.whl').read_bytes() == BYTES
    assert tools.runtime.store.artifact_records('T1') == before


@pytest.mark.parametrize('destination', ['../escape.whl', '/outside.whl', 'inputs/../../escape.whl'])
def test_binary_destination_escape_refuses_whole_batch(project, tmp_path, destination):
    tools, context = start(project)
    source = source_file(tmp_path)
    before = tools.runtime.task_queries.record('T1')
    records = tools.runtime.store.artifact_records('T1')
    with pytest.raises(PoiseError, match='relative|escape|outside|invalid|недопуст') as failure:
        tools.invoke(request('artifacts', {
            'items': [file_item(source), file_item(source, destination)],
        }))
    assert 'Unknown artifact source kind' not in str(failure.value)
    assert not (Path(context['task_root']) / 'artifacts/inputs/accepted.whl').exists()
    assert tools.runtime.store.artifact_records('T1') == records
    assert tools.runtime.task_queries.record('T1') == before
    assert source.read_bytes() == BYTES


@pytest.mark.parametrize('link', ['destination-parent', 'owner-root'])
def test_binary_destination_symlink_cannot_write_foreign_owner(tmp_path, link):
    from batch.test_artifact_factory import factory

    publisher, roots = factory(tmp_path)
    source = source_file(tmp_path)
    outside = tmp_path / 'foreign-owner'
    outside.mkdir()
    foreign = outside / 'accepted.whl'
    foreign.write_bytes(b'foreign operator bytes')
    if link == 'owner-root':
        roots['task'].rmdir()
        roots['task'].symlink_to(outside, target_is_directory=True)
    else:
        (roots['task'] / 'artifacts').mkdir()
        (roots['task'] / 'artifacts/inputs').symlink_to(outside, target_is_directory=True)
    with pytest.raises(PoiseError, match='symlink|owner|escape') as failure:
        publisher.prepare([file_item(source)])
    assert 'Unknown artifact source kind' not in str(failure.value)
    assert foreign.read_bytes() == b'foreign operator bytes'
    assert sorted(path.name for path in outside.iterdir()) == ['accepted.whl']


def test_late_mixed_binary_batch_conflict_publishes_no_earlier_member(project, tmp_path):
    tools, context = start(project)
    source = source_file(tmp_path)
    invoke_required(tools, 'artifacts', {'items': [
        {'scope': 'task', 'path': 'occupied.txt', 'source': {'kind': 'text', 'text': 'original'}},
    ]})
    before = tools.runtime.task_queries.record('T1')
    records = tools.runtime.store.artifact_records('T1')
    with pytest.raises(PoiseError, match='different content|conflict'):
        tools.invoke(request('artifacts', {'items': [
            file_item(source),
            {'scope': 'task', 'path': 'occupied.txt', 'source': {'kind': 'text', 'text': 'replacement'}},
        ]}))
    owner = Path(context['task_root'])
    assert not (owner / 'artifacts/inputs/accepted.whl').exists()
    assert (owner / 'artifacts/occupied.txt').read_bytes() == b'original'
    assert tools.runtime.store.artifact_records('T1') == records
    assert tools.runtime.task_queries.record('T1') == before


@pytest.mark.parametrize('fault', ['bytes', 'missing'])
def test_binary_source_changed_after_preparation_is_refused_before_publication(project, tmp_path, fault):
    tools, context = start(project)
    source = source_file(tmp_path)
    publisher = tools.resources.factory()
    try:
        prepared = publisher.prepare([file_item(source)])
    except PoiseError as exc:
        raise AssertionError(f'Required binary preparation contract refused: {exc}') from exc
    before = tools.runtime.task_queries.record('T1')
    records = tools.runtime.store.artifact_records('T1')
    if fault == 'bytes':
        source.write_bytes(b'changed after preparation')
    else:
        source.unlink()
    with pytest.raises(PoiseError, match='source|digest|changed|missing|exist') as failure:
        publisher.materialize(prepared)
    assert str(source) in str(failure.value)
    assert not (Path(context['task_root']) / 'artifacts/inputs/accepted.whl').exists()
    assert tools.runtime.store.artifact_records('T1') == records
    assert tools.runtime.task_queries.record('T1') == before
