"""Public restart/replay with genuine retained inputs and immutable proof."""

from copy import deepcopy
from pathlib import Path
import hashlib
import json

import pytest

from batch.helpers import request
from poise.common import PoiseError
from runtime_services.test_task_restart import restart
from verification.retention_helpers import verify_input


def resume_accepted(tools, *, change=None):
    current = tools.runtime.task_queries.record('T1')
    born = restart(tools, 'T1', current['version'])
    revision = born['revision']
    if change is not None:
        methods = deepcopy(current['contract']['methods'])
        change(methods[0]['artifact_inputs']['files'][0])
        edited = tools.invoke(request('task', {
            'action': 'edit', 'task_id': 'T1', 'request_id': 'change-input',
            'expected_revision': revision, 'patch': {'methods': methods},
            'remove': [],
        }))
        revision = edited['revision']
    ready = tools.invoke(request('task', {
        'action': 'ready', 'task_id': 'T1', 'request_id': 'ready-retained',
        'expected_revision': revision,
    }))
    assert ready['ready'] is True
    return tools.invoke(request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None,
        'feedback': None, 'rework_stage': None,
    }))


def repeat(tools):
    return tools.invoke(request('advance', {
        'task_id': 'T1', 'request_id': 'repeat-retained-input',
    }))


def test_retained_input_replay_consumes_permanent_bytes_after_restart(project, tmp_path):
    tools, context, accepted, original, marker = verify_input(project, tmp_path)
    old_receipt = deepcopy(accepted['checks'][0])
    old_stored = deepcopy(next(
        item for item in tools.runtime.evidence_commands.list_for('T1')
        if item['id'] == old_receipt['id']
    ))
    owner = Path(context['task_root'])
    retained = owner / 'artifacts/inputs/accepted.whl'
    old_manifest = Path(accepted['acceptance_manifests'][0]['path'])
    old_manifest_bytes = old_manifest.read_bytes()
    original.unlink()
    resumed = resume_accepted(tools)
    assert resumed['stage'] == 'implementation'

    response = repeat(tools)

    assert response['status'] == 'progression_stopped'
    assert response['replay']['reason'] == 'task_acceptance_required'
    assert len(response['replay']['passed']) == 1
    assert marker.read_text().splitlines() == ['run', 'run']
    assert not original.exists()
    assert retained.read_bytes() == b'\x00\xffaccepted-wheel\x80\n'
    assert old_manifest.read_bytes() == old_manifest_bytes
    receipts = tools.runtime.evidence_commands.list_for('T1')
    fresh = [item for item in receipts if item['id'] != old_receipt['id']]
    assert len(fresh) == 1
    assert next(item for item in receipts if item['id'] == old_receipt['id']) == old_stored
    receipt = fresh[0]
    assert receipt['actual_exit_code'] == 0 and receipt['passed'] is True
    assert receipt['capture_complete'] is True
    assert receipt['commit'] == old_receipt['commit']
    assert receipt['tree'] == old_receipt['tree']
    assert receipt['expectation_digest'] == old_receipt['expectation_digest']
    assert Path(receipt['stdout']).is_relative_to(owner)
    assert Path(receipt['stdout']).read_bytes() == (
        b'retained-bytes=00ff61636365707465642d776865656c800a\n'
    )
    assert Path(receipt['stderr']).read_bytes() == b''
    manifest = receipt['acceptance_manifest']
    assert Path(manifest['path']).is_relative_to(owner)
    assert manifest['path'] != str(old_manifest)
    assert hashlib.sha256(Path(manifest['path']).read_bytes()).hexdigest() == manifest['digest']
    document = json.loads(Path(manifest['path']).read_text())
    assert document['schema'] == 'acceptance-manifest-1'
    assert document['task_id'] == 'T1' and document['method_id'] == 'READ_WHEEL'
    assert document['commit'] == old_receipt['commit']
    assert len(document['inputs']) == 1
    item = document['inputs'][0]
    assert item['id'] == 'wheel' and item['path'] == str(retained)
    assert item['digest'] == hashlib.sha256(b'\x00\xffaccepted-wheel\x80\n').hexdigest()
    assert item['producer'] == {'task_id': 'PRODUCER', 'commit': 'a' * 40}
    assert item['input_ref'] == 'accepted-wheel'
    output = next(item for item in receipt['outputs'] if item['id'] == 'proof')
    assert output['status'] == 'captured' and output['required'] is True
    assert Path(output['path']).is_relative_to(owner)
    assert Path(output['path']).read_bytes() == b'wheel verified with accepted bytes\n'
    evidence = {entry['kind']: entry for entry in document['evidence']}
    assert len(document['evidence']) == 3
    assert set(evidence) == {'stdout', 'stderr', 'output'}
    assert evidence['output']['id'] == 'proof'
    for kind, path in (
        ('stdout', receipt['stdout']), ('stderr', receipt['stderr']),
        ('output', output['path']),
    ):
        assert evidence[kind]['path'] == path
        assert evidence[kind]['digest'] == hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.mark.parametrize('field,value', [
    ('digest', 'f' * 64),
    ('input_ref', 'different-accepted-input'),
])
def test_changed_retained_obligation_cannot_inherit_historical_pass(project, tmp_path, field, value):
    tools, _, _, original, marker = verify_input(project, tmp_path)
    original.unlink()
    resume_accepted(tools, change=lambda item: item.update({field: value}))
    admitted = deepcopy(tools.runtime.task_queries.record('T1')['contract'])

    response = repeat(tools)

    assert response['status'] == 'progression_stopped'
    assert response['replay']['reason'] == 'proof_contract_changed'
    assert response['replay']['passed'] == []
    assert marker.read_text().splitlines() == ['run']
    assert tools.runtime.task_queries.record('T1')['contract'] == admitted
    assert admitted['methods'][0]['artifact_inputs']['files'][0][field] == value


def test_malformed_current_retained_declaration_is_not_admitted(project, tmp_path):
    tools, _, _, original, marker = verify_input(project, tmp_path)
    original.unlink()
    current = tools.runtime.task_queries.record('T1')
    born = restart(tools, 'T1', current['version'])
    methods = deepcopy(current['contract']['methods'])
    methods[0]['artifact_inputs']['files'][0]['digest'] = 7
    edited = tools.invoke(request('task', {
        'action': 'edit', 'task_id': 'T1', 'request_id': 'bad-retained-input',
        'expected_revision': born['revision'],
        'patch': {'methods': methods}, 'remove': [],
    }))
    before = deepcopy(tools.runtime.task_queries.record('T1'))

    with pytest.raises(PoiseError, match='digest|SHA|sha|хеш|контроль'):
        tools.invoke(request('task', {
            'action': 'ready', 'task_id': 'T1', 'request_id': 'ready-bad-input',
            'expected_revision': edited['revision'],
        }))

    assert tools.runtime.task_queries.record('T1') == before
    assert marker.read_text().splitlines() == ['run']


@pytest.mark.parametrize('target', ['input', 'manifest', 'output', 'stdout'])
@pytest.mark.parametrize('damage', ['missing', 'bytes'])
def test_damaged_retained_bundle_refuses_repeat(project, tmp_path, target, damage):
    tools, context, accepted, original, marker = verify_input(project, tmp_path)
    original.unlink()
    resume_accepted(tools)
    receipt = accepted['checks'][0]
    if target == 'input':
        path = Path(context['task_root']) / 'artifacts/inputs/accepted.whl'
    elif target == 'manifest':
        path = Path(accepted['acceptance_manifests'][0]['path'])
    elif target == 'stdout':
        path = Path(receipt['stdout'])
    else:
        path = Path(next(item['path'] for item in receipt['outputs'] if item['id'] == 'proof'))
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'changed accepted bytes')
    historical = deepcopy(tools.runtime.evidence_commands.list_for('T1'))

    response = repeat(tools)

    assert response['status'] == 'progression_stopped'
    assert response['replay']['reason'] == (
        'evidence_missing' if damage == 'missing' else 'evidence_changed'
    )
    assert response['replay']['passed'] == []
    assert marker.read_text().splitlines() == ['run']
    assert tools.runtime.evidence_commands.list_for('T1') == historical
