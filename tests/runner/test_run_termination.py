"""The actual process owner proves cleanup; child exit alone is insufficient."""

import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from poise.common import PoiseError
from poise.execution import RegisteredCheckRunner, RunnerTimeoutPolicy


EXPECTED = json.loads((Path(__file__).parents[1] / 'runtime_services' / 'fixtures' /
                       'check_termination_contract.json').read_text())['runner']


@pytest.mark.parametrize(('code', 'timeout', 'exit_code'), [
    ('print("finished")', 1.0, 0),
    ('raise SystemExit(7)', 1.0, 7),
    ('import time; time.sleep(2)', 0.1, -9),
], ids=['success', 'failure', 'timeout'])
def test_real_runner_proves_terminal_group_and_capture(tmp_path, code, timeout, exit_code):
    runner = RegisteredCheckRunner()
    outcome = runner.run('owner', [sys.executable, '-S', '-c', code], tmp_path,
                         dict(os.environ), timeout, tmp_path / 'out', tmp_path / 'err')
    assert outcome['actual_exit_code'] == exit_code
    assert outcome.get('termination') == EXPECTED
    assert runner.active_ids() == ()


def test_owner_cancellation_proves_cleanup_before_return(tmp_path):
    runner = RegisteredCheckRunner()
    outcome = {}

    def execute():
        outcome.update(runner.run('cancel-proof',
            [sys.executable, '-S', '-c', 'import time; time.sleep(2)'],
            tmp_path, dict(os.environ), 3, tmp_path / 'out', tmp_path / 'err'))

    thread = threading.Thread(target=execute)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not runner.active_ids() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runner.cancel('cancel-proof') is True
    finally:
        thread.join(timeout=4)
    assert not thread.is_alive()
    assert outcome['cancelled'] is True
    assert outcome['actual_exit_code'] == -9
    assert outcome.get('termination') == EXPECTED


def test_leader_exit_does_not_certify_a_surviving_group(tmp_path):
    marker = tmp_path / 'descendant-effect'
    source = Path(__file__).parent / 'fixtures' / 'termination_descendant.py'
    outcome = RegisteredCheckRunner().run('descendant',
        [sys.executable, '-S', str(source)], tmp_path, dict(os.environ), 1,
        tmp_path / 'out', tmp_path / 'err')
    # A bounded test-owned effect disproves group cleanup after leader exit.
    time.sleep(0.5)
    assert not marker.exists()
    assert outcome.get('termination') == EXPECTED


def test_incomplete_capture_never_becomes_complete_termination_proof(tmp_path):
    source = Path(__file__).parent / 'fixtures' / 'termination_escaped_writer.py'
    outcome = RegisteredCheckRunner().run('escaped-writer',
        [sys.executable, '-S', str(source)], tmp_path, dict(os.environ), 1,
        tmp_path / 'out', tmp_path / 'err', cleanup_seconds=0.05)
    assert outcome['actual_exit_code'] == 0
    assert outcome['capture_complete'] is False
    assert outcome.get('termination') == {**EXPECTED, 'capture_complete': False}


INVALID_WORKLOADS = [
    ('missing-mode', {'inner_budget_seconds': 0.3, 'teardown_seconds': 0.1}),
    ('missing-budget', {'progress_mode': 'non_streaming', 'teardown_seconds': 0.1}),
    ('missing-teardown', {'progress_mode': 'non_streaming', 'inner_budget_seconds': 0.3}),
    ('extra', {'progress_mode': 'non_streaming', 'inner_budget_seconds': 0.3,
               'teardown_seconds': 0.1, 'hidden': 1}),
    ('unknown-mode', {'progress_mode': 'silent', 'inner_budget_seconds': 0.3,
                     'teardown_seconds': 0.1}),
]


@pytest.mark.parametrize('field', ['inner_budget_seconds', 'teardown_seconds'])
@pytest.mark.parametrize('value', [None, True, '0.3', 0, -1, float('nan'), float('inf')],
                         ids=['null', 'bool', 'string', 'zero', 'negative', 'nan', 'infinity'])
def test_nonstreaming_numeric_contract_rejects_invalid_values(field, value):
    policy = RunnerTimeoutPolicy.parse({'initial_seconds': 0.8, 'history_multiplier': 1.1,
        'progress_gap_seconds': 0.1, 'poll_seconds': 0.01,
        'diagnostic_override_max_seconds': 2})
    contract = {'progress_mode': 'non_streaming', 'inner_budget_seconds': 0.3,
                'teardown_seconds': 0.1}
    contract[field] = value
    with pytest.raises(PoiseError, match='workload|budget|teardown|finite|positive'):
        policy.select([], workload_contract=contract)


@pytest.mark.parametrize('contract', [item[1] for item in INVALID_WORKLOADS],
                         ids=[item[0] for item in INVALID_WORKLOADS])
def test_nonstreaming_schema_is_exact(contract):
    policy = RunnerTimeoutPolicy.parse({'initial_seconds': 0.8, 'history_multiplier': 1.1,
        'progress_gap_seconds': 0.1, 'poll_seconds': 0.01,
        'diagnostic_override_max_seconds': 2})
    with pytest.raises(PoiseError, match='workload|budget|teardown|mode|fields'):
        policy.select([], workload_contract=contract)
