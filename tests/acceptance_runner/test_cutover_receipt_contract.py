"""C03/C06 receipt-contract guards, not execution of real clean-room gates.

Synthetic test-owned streams exercise the existing evidence consumer only.
Actual environment readiness still requires the separate real five-gate run.
"""
import hashlib
import json
from pathlib import Path

import pytest

from acceptance.release_evidence import validate_acceptance

ORDER = Path(__file__).resolve().parents[1] / 'maintenance/fixtures/p005/gate-order.json'


@pytest.fixture
def accepted_receipts(tmp_path):
    identifiers = json.loads(ORDER.read_text())
    manifest = {'steps': [{'id': identifier} for identifier in identifiers]}
    report = {'status': 'PASS', 'environment_errors': [], 'steps': []}
    for identifier in identifiers:
        folder = tmp_path / identifier
        folder.mkdir()
        step = {'id': identifier, 'status': 'PASS', 'exit_code': 0, 'timed_out': False}
        for name, data in [('result', b'{"status":"PASS"}'),
                           ('stdout', b'fixture observation\n'), ('stderr', b'')]:
            path = folder / name
            path.write_bytes(data)
            step[name] = str(path)
            step[name + '_sha256'] = hashlib.sha256(data).hexdigest()
        report['steps'].append(step)
    return report, manifest


def test_complete_ordered_receipt_contract_is_accepted(accepted_receipts):
    report, manifest = accepted_receipts
    assert validate_acceptance(report, manifest) is None


@pytest.mark.parametrize('position', [0, 1, 2, 3, 4])
def test_each_failed_gate_blocks_receipt_acceptance(accepted_receipts, position):
    report, manifest = accepted_receipts
    report['steps'][position]['status'] = 'FAIL'
    report['steps'][position]['exit_code'] = 7
    with pytest.raises(ValueError, match='has not passed'):
        validate_acceptance(report, manifest)


@pytest.mark.parametrize('status', ['FAIL', 'BLOCKED'])
def test_nonpass_overall_result_is_not_relabelled_pass(accepted_receipts, status):
    report, manifest = accepted_receipts
    report['status'] = status
    with pytest.raises(ValueError, match='complete successful acceptance'):
        validate_acceptance(report, manifest)


def test_out_of_order_gates_are_rejected(accepted_receipts):
    report, manifest = accepted_receipts
    report['steps'][2], report['steps'][3] = report['steps'][3], report['steps'][2]
    with pytest.raises(ValueError, match='membership/order'):
        validate_acceptance(report, manifest)


def test_missing_gate_is_rejected(accepted_receipts):
    report, manifest = accepted_receipts
    report['steps'].pop()
    with pytest.raises(ValueError, match='membership/order'):
        validate_acceptance(report, manifest)


@pytest.mark.parametrize('stream', ['result', 'stdout', 'stderr'])
def test_changed_raw_result_or_stream_is_rejected(accepted_receipts, stream):
    report, manifest = accepted_receipts
    Path(report['steps'][0][stream]).write_bytes(b'changed after capture')
    with pytest.raises(ValueError, match='evidence changed'):
        validate_acceptance(report, manifest)
