"""Real readers preserve logical evidence while resolving confirmed physical bytes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from batch.helpers import request
from poise.common import PoiseError
from projects.material_relocation_helpers import (
    INPUT, PROOF, STDERR, STDOUT, checksum, immutable, placed, prepare, relocate, required,
)


def test_receipt_integrity_includes_captured_configurations(project, tmp_path):
    case = prepare(project, tmp_path)
    receipt = deepcopy(case['receipt'])
    assert set(receipt['systems']) == {'poise', 'project'}
    companions = [item for system in receipt['systems'].values() for item in system['configs']]
    assert companions, 'The test must exercise real configuration capture'
    expected = [(item['captured_path'], Path(item['captured_path']).read_bytes()) for item in companions]
    moved = relocate(case)
    for path, content in expected:
        assert placed(case, path).read_bytes() == content
    assert moved.runtime._receipt_files_intact(receipt, case['method'])
    assert moved.runtime.evidence_commands.list_for('T1')[0] == receipt


def test_strict_original_acceptance_manifest_remains_valid(project, tmp_path):
    case = prepare(project, tmp_path)
    receipt = case['receipt']
    original = Path(receipt['acceptance_manifest']['path'])
    manifest_bytes = original.read_bytes()
    saved = json.loads(manifest_bytes)
    expected_paths = [item['path'] for item in saved['inputs'] + saved['evidence']]
    assert all(path.startswith(str(case['source']) + '/') for path in expected_paths)
    moved = relocate(case)
    assert placed(case, original).read_bytes() == manifest_bytes
    task = moved.runtime.task_queries.record('T1')
    required(moved.runtime.retention.validate_receipt,
             task, receipt, case['done']['commit'], case['method'])
    assert placed(case, original).read_bytes() == manifest_bytes
    assert moved.runtime.evidence_commands.list_for('T1')[0] == receipt


def test_new_consumer_reads_relocated_input_with_unchanged_historical_inventory(project, tmp_path):
    case = prepare(project, tmp_path)
    original = Path(case['receipt']['acceptance_manifest']['path'])
    manifest_bytes = original.read_bytes()
    moved = relocate(case)
    task = moved.runtime.task_queries.record('T1')
    bindings = required(moved.runtime.retention.bind_inputs, task, case['method'])
    expected = placed(case, Path(case['context']['task_root']) / 'artifacts/inputs/accepted.whl')
    assert bindings['ACCEPTED_WHEEL'] == str(expected)
    assert expected.read_bytes() == INPUT
    output = tmp_path / 'new-consumer-outputs'
    output.mkdir()
    process = subprocess.run(case['method']['argv'], cwd=case['context']['worktree'],
                             env={**os.environ, **case['method']['environment'], **bindings,
                                  'POISE_RUN_OUTPUT_DIR': str(output)}, capture_output=True)
    assert process.returncode == 0, process.stderr
    assert process.stdout == STDOUT and process.stderr == STDERR
    assert (output / 'proof/verification.txt').read_bytes() == PROOF
    assert case['marker'].read_text() == 'run\nrun\n'
    assert placed(case, original).read_bytes() == manifest_bytes


def test_async_materialization_reads_original_receipt_at_confirmed_placement(project, tmp_path):
    case = prepare(project, tmp_path)
    receipt = deepcopy(case['receipt'])
    moved = relocate(case)
    directory = placed(case, Path(receipt['stdout']).parent) / 'new-materialization'
    publication = moved.runtime.result_views.capture(receipt, directory)
    moved.runtime.result_views.finish()
    assert publication['status'] == 'scheduled', publication
    value = json.loads(Path(publication['manifest']).read_text())
    assert value['status'] == 'ready', value
    assert value['receipt_id'] == receipt['id']
    full = value['representations']['full']
    assert Path(full['path']).read_bytes() == STDOUT + STDERR
    assert full['digest'] == checksum(STDOUT + STDERR)
    assert moved.runtime.evidence_commands.list_for('T1')[0] == receipt


@pytest.mark.parametrize('kind', ['stdout', 'stderr', 'output', 'configuration', 'timeout', 'manifest', 'input'])
@pytest.mark.parametrize('fault', ['missing', 'bytes'])
def test_bad_confirmed_material_blocks_real_consumer(project, tmp_path, kind, fault):
    case = prepare(project, tmp_path)
    receipt = case['receipt']
    original = {
        'stdout': receipt['stdout'], 'stderr': receipt['stderr'],
        'output': receipt['outputs'][0]['path'],
        'configuration': next(iter(receipt['systems'].values()))['configs'][0]['captured_path'],
        'timeout': receipt['timeout_maintenance']['path'],
        'manifest': receipt['acceptance_manifest']['path'],
        'input': str(Path(case['context']['task_root']) / 'artifacts/inputs/accepted.whl'),
    }[kind]
    moved = relocate(case)
    # Establish success first: a permanently broken consumer cannot satisfy refusal.
    assert moved.runtime._receipt_files_intact(receipt, case['method'])
    required(moved.runtime.retention.validate_receipt,
             moved.runtime.task_queries.record('T1'), receipt, case['done']['commit'], case['method'])
    target = placed(case, original)
    trusted = target.read_bytes()
    # A matching decoy at the obsolete location must never be a fallback.
    decoy = Path(original)
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_bytes(trusted)
    if fault == 'missing':
        target.unlink()
    else:
        target.write_bytes(b'changed confirmed material\n')
    before = immutable(moved.runtime)
    if kind == 'manifest' or kind == 'input':
        with pytest.raises(PoiseError):
            moved.runtime.retention.validate_receipt(
                moved.runtime.task_queries.record('T1'), receipt, case['done']['commit'], case['method'])
    else:
        assert moved.runtime._receipt_files_intact(receipt, case['method']) is False
    assert immutable(moved.runtime) == before
    assert case['marker'].read_text() == 'run\n'
    assert decoy.read_bytes() == trusted


def test_relocated_handoff_uses_same_task_and_registered_material(project, tmp_path):
    case = prepare(project, tmp_path)
    original = deepcopy(case['receipt'])
    moved = relocate(case)
    required(moved.invoke, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    handoff = required(moved.invoke, request('handoff', {
        'request_id': 'after-relocation', 'reason': 'Read retained evidence and transfer ownership.',
        'result': None, 'commit_message': 'Preserve relocated evidence', 'artifact_paths': [],
    }))
    assert handoff['status'] == 'handed_off' and handoff['verified'] is True
    assert moved.runtime.evidence_commands.list_for('T1')[0] == original
    assert not case['source'].exists()


@pytest.mark.parametrize('representation', ['full', 'extended'])
def test_public_saved_output_readback(project, tmp_path, representation):
    case = prepare(project, tmp_path)
    moved = relocate(case)
    required(moved.invoke, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    value = required(moved.runtime.show_output, case['receipt']['id'], representation, None)
    assert value['text'] == (STDOUT + STDERR).decode()
    assert not case['source'].exists()


@pytest.mark.parametrize('fault', ['receipt-id', 'digest', 'output-id'])
def test_unknown_material_identity_is_not_read_from_a_decoy(project, tmp_path, fault):
    case = prepare(project, tmp_path)
    receipt = deepcopy(case['receipt'])
    original_bytes = Path(receipt['stdout']).read_bytes()
    moved = relocate(case)
    assert moved.runtime._receipt_files_intact(receipt, case['method'])
    decoy = Path(receipt['stdout'])
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_bytes(original_bytes)
    if fault == 'receipt-id':
        receipt['id'] = 'unregistered-receipt'
    elif fault == 'digest':
        receipt['stdout_digest'] = checksum(b'unapproved bytes\n')
        decoy.write_bytes(b'unapproved bytes\n')
    else:
        receipt['outputs'][0]['id'] = 'unregistered-output'
    assert moved.runtime._receipt_files_intact(receipt, case['method']) is False
    assert case['marker'].read_text() == 'run\n'


def test_public_integration_reads_preserved_receipt_and_new_physical_input(project, tmp_path):
    from result_integration.helpers import integration_input
    case = prepare(project, tmp_path)
    original_manifest = Path(case['receipt']['acceptance_manifest']['path']).read_bytes()
    moved = relocate(case)
    required(moved.invoke, request('bootstrap', {
        'task': {'id': 'T1'}, 'decision': None, 'feedback': None, 'rework_stage': None,
    }))
    accepted = required(moved.invoke, request('accept', {}))
    assert accepted['status'] == 'completed'
    integrated = required(moved.invoke, request('integrate',
        integration_input(case['project'], case['done']['commit'])))
    assert integrated['status'] == 'integrated'
    assert case['marker'].read_text() == 'run\nrun\n'
    assert placed(case, case['receipt']['acceptance_manifest']['path']).read_bytes() == original_manifest
    assert not case['source'].exists()


def test_transfer_proof_reader_validates_the_same_moved_material(project, tmp_path):
    case = prepare(project, tmp_path)
    moved = relocate(case)
    # Actual export proof reader; no synthetic imported-history permission or map.
    required(moved.runtime.transfer_tools.port._proof_outputs, case['receipt'], None, {})
    target = placed(case, case['receipt']['outputs'][0]['path'])
    target.write_bytes(b'changed export proof\n')
    with pytest.raises(PoiseError, match='digest|Changed'):
        moved.runtime.transfer_tools.port._proof_outputs(case['receipt'], None, {})
    assert case['marker'].read_text() == 'run\n'
