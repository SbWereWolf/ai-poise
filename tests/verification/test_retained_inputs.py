"""Exact retained inputs and Task-local acceptance-proof provenance."""
from pathlib import Path
import hashlib

import pytest

from batch.helpers import request, result
from poise.common import PoiseError
from verification.retention_helpers import (
    BYTES, FIXTURES, PRODUCER, consumer, digest, file_item, invoke_required, manifest,
    source_file, start, verify_input,
)


def test_manifest_inventory_matches_exact_input_producer_and_real_proof(project, tmp_path):
    _, context, done, source, _ = verify_input(project, tmp_path)
    path, value = manifest(done, context)
    retained = Path(context['task_root']) / 'artifacts/inputs/accepted.whl'
    assert value['schema'] == 'acceptance-manifest-1'
    assert value['task_id'] == 'T1'
    assert value['method_id'] == 'READ_WHEEL'
    assert value['commit'] == done['commit']
    assert value['inputs'] == [{
        'id': 'wheel', 'path': str(retained), 'digest': digest(BYTES),
        'producer': PRODUCER, 'input_ref': 'accepted-wheel',
    }]
    assert retained.read_bytes() == b'\x00\xffaccepted-wheel\x80\n'
    evidence = {item['kind']: item for item in value['evidence']}
    assert set(evidence) == {'stdout', 'stderr'}
    assert Path(evidence['stdout']['path']).read_text() == (
        'retained-bytes=00ff61636365707465642d776865656c800a\n'
    )
    assert Path(evidence['stderr']['path']).read_bytes() == b''
    for item in evidence.values():
        proof = Path(item['path'])
        assert proof.is_relative_to(Path(context['task_root']))
        assert hashlib.sha256(proof.read_bytes()).hexdigest() == item['digest']
    source.unlink()
    assert retained.is_file() and path.is_file()


@pytest.mark.parametrize('fault', ['missing', 'bytes'])
def test_missing_or_modified_retained_input_rejects_before_consumer(project, tmp_path, fault):
    marker = tmp_path / 'not-executed.txt'
    source = source_file(tmp_path)
    tools, context = start(project, marker)
    invoke_required(tools, 'artifacts', {'items': [file_item(source)]})
    retained = Path(context['task_root']) / 'artifacts/inputs/accepted.whl'
    if fault == 'missing':
        retained.unlink()
    else:
        retained.write_bytes(b'modified')
    before = tools.runtime.task_queries.record('T1')
    with pytest.raises(PoiseError, match='exist|missing|digest|измен'):
        tools.invoke(request('verify', {'result': result(context), 'artifacts': []}))
    assert not marker.exists()
    assert tools.runtime.task_queries.record('T1') == before


@pytest.mark.parametrize('fault', ['manifest', 'stdout', 'missing-proof', 'missing-manifest'])
def test_corrupt_manifest_or_required_proof_blocks_acceptance(project, tmp_path, fault):
    tools, context, done, _, marker = verify_input(project, tmp_path)
    path, value = manifest(done, context)
    target = path if fault in ('manifest', 'missing-manifest') else Path(value['evidence'][0]['path'])
    if fault.startswith('missing-'):
        target.unlink()
    else:
        target.write_bytes(b'corrupt')
    before = tools.runtime.task_queries.record('T1')
    with pytest.raises(PoiseError, match='digest|changed|измен|proof|evidence|exist|missing'):
        tools.invoke(request('accept', {}))
    assert tools.runtime.task_queries.record('T1') == before
    assert marker.read_text() == 'run\n'


def test_changed_input_cannot_replay_old_green_and_intact_replay_is_exact(project, tmp_path):
    tools, context, first, source, marker = verify_input(project, tmp_path)
    payload = result(context, 'Retained package verified.')
    intact = invoke_required(tools, 'verify', {
        'result': payload, 'artifacts': [file_item(source)],
    })
    assert intact['replayed'] is True
    assert [item['id'] for item in intact['checks']] == [item['id'] for item in first['checks']]
    assert marker.read_text() == 'run\n'
    retained = Path(context['task_root']) / 'artifacts/inputs/accepted.whl'
    retained.write_bytes(b'replacement')
    with pytest.raises(PoiseError, match='digest|changed|измен|different content'):
        tools.invoke(request('verify', {'result': payload, 'artifacts': [file_item(source)]}))
    assert marker.read_text() == 'run\n'


def test_required_sprint_source_is_copied_into_task_closure(project, tmp_path):
    marker = tmp_path / 'execution-marker.txt'
    wheel = source_file(tmp_path)
    sprint = project['root'] / 'state/sprints/S1/artifacts/policy.txt'
    sprint.parent.mkdir(parents=True)
    sprint.write_bytes(b'accepted sprint policy\n')
    method = consumer(marker)
    method['argv'][-1] = (FIXTURES / 'retention_sprint_consumer.py').read_text()
    method['artifact_inputs']['files'].append({
        'id': 'policy', 'path': 'artifacts/inputs/policy.txt',
        'digest': digest(b'accepted sprint policy\n'), 'producer': PRODUCER,
        'input_ref': 'sprint:S1:policy', 'environment': 'ACCEPTED_POLICY',
    })
    tools, context = start(project, marker, method)
    done = invoke_required(tools, 'verify', {
        'result': result(context),
        'artifacts': [file_item(wheel), file_item(sprint, 'inputs/policy.txt',
                                                digest(b'accepted sprint policy\n'))],
    })
    _, value = manifest(done, context)
    assert [item['id'] for item in value['inputs']] == ['wheel', 'policy']
    policy = Path(context['task_root']) / 'artifacts/inputs/policy.txt'
    assert value['inputs'][1] == {
        'id': 'policy', 'path': str(policy), 'digest': digest(b'accepted sprint policy\n'),
        'producer': PRODUCER, 'input_ref': 'sprint:S1:policy',
    }
    sprint.unlink()
    wheel.unlink()
    assert policy.read_bytes() == b'accepted sprint policy\n'
    assert marker.read_text() == 'run\n'
