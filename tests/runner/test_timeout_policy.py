from pathlib import Path
import os
import sys

import pytest

from poise.execution import RegisteredCheckRunner, RunnerTimeoutPolicy, timeout_profile
from poise.modules.foundation.errors import PoiseError


def policy():
    return RunnerTimeoutPolicy.parse({
        'initial_seconds': 5,
        'history_multiplier': 1.10,
        'progress_gap_seconds': 2,
        'poll_seconds': 0.02,
        'diagnostic_override_max_seconds': 60,
    })


def test_timeout_profile_is_command_workload_runtime_not_checkout_root(tmp_path):
    left = timeout_profile(['python3', '-m', 'pytest', '-q'], 'SMOKE', {'PATH': os.environ.get('PATH', '')})
    right = timeout_profile(['python3', '-m', 'pytest', '-q'], 'SMOKE', {'PATH': os.environ.get('PATH', '')})
    assert left == right
    assert left['workload'] == 'SMOKE'
    assert left['command'] == ['python3', '-m', 'pytest', '-q']
    assert Path(left['runtime']['executable']).is_absolute()
    assert 'checkout' not in left and 'cwd' not in left


def test_timeout_uses_initial_limit_without_history_and_110_percent_of_longest_success():
    p = policy()
    initial = p.select([], None, ())
    assert initial['seconds'] == 5
    assert initial['basis'] == 'initial'
    learned = p.select([3.2, 8.1, 7.0], None, ())
    assert learned['seconds'] == 9
    assert learned['basis'] == 'verified_success_history'
    assert learned['history_count'] == 3
    assert learned['longest_verified_success_seconds'] == 8.1


def test_diagnostic_override_requires_named_evidence_and_is_bounded():
    p = policy()
    with pytest.raises(PoiseError, match='evidence'):
        p.select([8.0], {'seconds': 20, 'evidence_run_id': 'missing', 'reason': 'diagnose'}, ('run-1',))
    selected = p.select([8.0], {'seconds': 20, 'evidence_run_id': 'run-1', 'reason': 'diagnose'}, ('run-1',))
    assert selected['seconds'] == 20
    assert selected['basis'] == 'diagnostic_override'
    assert selected['diagnostic_evidence_run_id'] == 'run-1'
    with pytest.raises(PoiseError, match='maximum'):
        p.select([], {'seconds': 61, 'evidence_run_id': 'run-1', 'reason': 'diagnose'}, ('run-1',))


def test_runner_reports_progress_gap_timeout_separately_from_hard_limit(tmp_path):
    runner = RegisteredCheckRunner()
    result = runner.run(
        'gap',
        [sys.executable, '-c', 'import time; time.sleep(5)'],
        tmp_path,
        dict(os.environ),
        2,
        tmp_path / 'stdout',
        tmp_path / 'stderr',
        progress_gap_seconds=0.1,
        poll_seconds=0.02,
    )
    assert result['timed_out'] is True
    assert result['timeout_reason'] == 'progress_gap'


def test_output_activity_resets_progress_gap(tmp_path):
    runner = RegisteredCheckRunner()
    code = 'import time\nfor i in range(5):\n print(i, flush=True); time.sleep(0.04)'
    result = runner.run(
        'progress', [sys.executable, '-c', code], tmp_path, dict(os.environ), 2,
        tmp_path / 'stdout2', tmp_path / 'stderr2', progress_gap_seconds=1.0, poll_seconds=0.01,
    )
    assert result['actual_exit_code'] == 0
    assert result['timed_out'] is False
    assert result['timeout_reason'] is None


def test_sqlite_timeout_history_uses_only_successful_matching_receipts():
    import sqlite3
    from poise.infrastructure.sqlite.evidence import SqliteEvidenceRepository

    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE evidence(id TEXT PRIMARY KEY, task_id TEXT, stage TEXT, iteration INTEGER, data TEXT)')
    repo = SqliteEvidenceRepository(db)
    profile = timeout_profile([sys.executable, '-c', 'pass'], 'GREEN', dict(os.environ))
    base = {
        'tree': 'tree', 'method': 'GREEN', 'obligations': [], 'guard': True,
        'timeout_profile': profile, 'cancelled': False, 'timed_out': False,
    }
    repo.record('T', 'verify', 1, {**base, 'id': 'ok', 'passed': True, 'duration_seconds': 4.5})
    repo.record('T', 'verify', 1, {**base, 'id': 'fail', 'passed': False, 'duration_seconds': 20})
    repo.record('T', 'verify', 1, {**base, 'id': 'timeout', 'passed': True, 'timed_out': True, 'duration_seconds': 30})
    other = {**profile, 'workload': 'OTHER'}
    repo.record('T', 'verify', 1, {**base, 'id': 'other', 'passed': True, 'duration_seconds': 9, 'timeout_profile': other})
    assert repo.timeout_history(profile) == {'durations': [4.5], 'evidence_ids': ['ok']}
