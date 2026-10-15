"""Artifact batches reuse one delivery lookup without losing file validation."""
from pathlib import Path
from unittest.mock import patch

import pytest

from poise.common import PoiseError
from tests.verification.test_current_registry_mutation import public_tools
from .helpers import bootstrap, request, text_artifact


EXPECTED = (
    ('67ed790e-91c4-5226-bd92-1cbcea25e012',
     '9e631461e1c412ab7e9bf3824f79e16ec44ceb7d660b66b20f7e616c9ab3ba92'),
    ('7d5cfbef-33b5-53af-87ce-caf33b52c93d',
     '4887ab0778651ae99cd9ee6a36ecfdd4df22adb95cd55ec5b39764afd29403ae'),
    ('7d8dae3e-994c-5ac2-91d0-080a37cd48e7',
     '530fae6ad8d8c4bf30a2575105a09fe7c5c8f55defe3ebdcdf5cc29f48af7fc1'),
)


@pytest.fixture
def registered(project):
    tools = public_tools(project, 'delivery-snapshot-owner')
    context = bootstrap(tools, project)
    tools.invoke(request('artifacts', {'items': [
        text_artifact(path=f'snapshot-{i}.md', text=f'proof-{i}')
        for i in range(3)
    ]}))
    records = tools.runtime.store.artifact_records(context['task'])
    return tools.runtime, context, records


def inspect(runtime, data, records, entry):
    if entry == 'candidate':
        return runtime._candidate_artifacts(data, [records[0]['path']], runtime._roots(data))
    if entry == 'existing':
        return runtime._existing_artifacts(data)
    assert runtime._check_replay_files(data['id'], {'report': {'artifacts': records}}) is None
    return records


@pytest.mark.parametrize('entry', ['candidate', 'existing', 'replay'])
def test_artifact_batch_reads_delivery_once_and_preserves_each_file(registered, entry):
    runtime, context, records = registered
    data = runtime.task_queries.record(context['task'])
    repository = runtime.transfer_repository
    with patch.object(repository, 'delivery_for_task', wraps=repository.delivery_for_task) as lookup:
        actual = inspect(runtime, data, records, entry)
    assert lookup.call_args_list == [((context['task'],), {})]
    assert len(actual) == 3
    for i, record in enumerate(sorted(actual, key=lambda item: item['path'])):
        assert (record['id'], record['digest']) == EXPECTED[i]
        assert record['scope'] == 'task'
        assert record['owner'] == 'T1'
        if entry != 'replay':
            assert record['relative_path'] == f'artifacts/snapshot-{i}.md'
        assert record['path'] == str(Path(context['task_root']) / 'artifacts' / f'snapshot-{i}.md')
        assert Path(record['path']).read_bytes() == f'proof-{i}'.encode()


def test_existing_empty_batch_does_not_read_delivery(project):
    tools = public_tools(project, 'empty-delivery-snapshot-owner')
    context = bootstrap(tools, project)
    runtime = tools.runtime
    data = runtime.task_queries.record(context['task'])
    repository = runtime.transfer_repository
    with patch.object(repository, 'delivery_for_task', wraps=repository.delivery_for_task) as lookup:
        assert runtime._existing_artifacts(data) == []
    lookup.assert_not_called()


@pytest.mark.parametrize('fault', ['missing', 'bytes'])
def test_snapshot_keeps_candidate_existing_and_replay_damage_guards(registered, fault):
    runtime, context, records = registered
    data = runtime.task_queries.record(context['task'])
    damaged = Path(records[1]['path'])
    if fault == 'missing':
        damaged.unlink()
    else:
        damaged.write_bytes(b'tampered')
    with pytest.raises(PoiseError, match='Артефакт не существует|Артефакт изменён'):
        runtime._candidate_artifacts(data, [], runtime._roots(data))
    intact = runtime._existing_artifacts(data)
    assert sorted(item['id'] for item in intact) == sorted([EXPECTED[0][0], EXPECTED[2][0]])
    assert runtime._check_replay_files(data['id'], {'report': {'artifacts': records}}) == (
        'evidence_missing' if fault == 'missing' else 'evidence_changed'
    )
