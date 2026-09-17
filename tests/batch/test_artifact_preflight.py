"""Previously registered artifacts must reject before any semantic Task write."""
from copy import deepcopy
from pathlib import Path

import pytest

from conftest import add_test
from poise.common import PoiseError
from tests.verification.test_current_registry_mutation import (
    configure_public_registry_case, public_tools, change, operation,
)
from .helpers import bootstrap, request, result, text_artifact
from .test_atomic_verify_delivery import TABLES


AUDITED_TABLES = tuple(dict.fromkeys((*TABLES, 'task_methods', 'workflow_layers',
    'section_layers', 'trace_point_layers', 'content_contracts', 'handoffs')))


def snapshot(runtime):
    with runtime.store.transaction() as db:
        return {name: [tuple(row) for row in db.execute(f'SELECT * FROM {name} ORDER BY rowid')]
                for name in AUDITED_TABLES} | {'journal': [tuple(row) for row in db.execute(
                    "SELECT * FROM journal WHERE event IN ('stage.verified','content.gate',"
                    "'verification_registry_changed') ORDER BY seq")]}


@pytest.fixture
def prepared(project):
    configure_public_registry_case(project)
    tools = public_tools(project, 'preflight-owner')
    context = bootstrap(tools, project)
    add_test(context['worktree'])
    created = tools.invoke(request('artifacts', {'items': [text_artifact(path='registered.md')]}))
    record = created['artifacts'][0]
    payload = result(context)
    payload['method_additions'] = change(operation('remove', 'CLEANUP_FULL_GREEN'),
                                        request_id='preflight-registry')
    return tools, context, record, payload


def damage(runtime, record, kind, tmp_path):
    path = Path(record['path'])
    if kind == 'missing':
        path.unlink()
    elif kind == 'bytes':
        path.write_text('Unexpected replacement', encoding='utf-8')
    elif kind == 'outside':
        outside = tmp_path / 'foreign.md'
        outside.write_bytes(path.read_bytes())
        with runtime.store.transaction() as db:
            db.execute('UPDATE artifacts SET path=? WHERE id=?', (str(outside), record['id']))
    elif kind == 'symlink':
        outside = tmp_path / 'foreign.md'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    else:
        column, value = {'digest': ('digest', '0'*64), 'owner': ('owner', 'foreign-task'),
                         'scope': ('scope', 'sprint')}[kind]
        with runtime.store.transaction() as db:
            db.execute(f'UPDATE artifacts SET {column}=? WHERE id=?', (value, record['id']))


@pytest.mark.parametrize('kind', ['missing', 'bytes', 'outside', 'symlink', 'digest', 'owner', 'scope'])
@pytest.mark.parametrize('entry', ['public', 'runtime'])
def test_invalid_registered_artifact_leaves_complete_task_snapshot_unchanged(
        prepared, tmp_path, kind, entry):
    tools, context, record, payload = prepared
    runtime = tools.runtime
    damage(runtime, record, kind, tmp_path)
    before = snapshot(runtime)
    generated = Path(record['path']).parent / 'must-not-exist.md'
    try:
        with pytest.raises(PoiseError):
            if entry == 'public':
                tools.invoke(request('verify', {'result': payload,
                    'artifacts': [text_artifact(path=generated.name)]}))
            else:
                runtime.verify(payload, packet_digest=runtime.packet_digest(
                    {'result': payload, 'artifacts': []}))
    finally:
        assert snapshot(runtime) == before
        assert not generated.exists()


def test_valid_preflight_delivers_registry_change_exactly_once(prepared):
    tools, context, record, payload = prepared
    before = snapshot(tools.runtime)
    packet = request('verify', {'result': payload, 'artifacts': [text_artifact(path='new-proof.md')]})
    delivered = tools.invoke(packet)
    assert delivered['status'] == 'verified'
    after = snapshot(tools.runtime)
    assert len(after['submissions']) == len(before['submissions']) + 1
    registry = tools.runtime.task_queries.verification_registry(context['task'])
    assert registry['revision'] == 1
    assert tools.invoke(deepcopy(packet))['replayed'] is True
    assert snapshot(tools.runtime) == after
