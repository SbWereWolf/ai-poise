"""Public verification keeps real terminal failures distinct from missing evidence."""

from copy import deepcopy
import hashlib
from pathlib import Path
import sys

from batch.helpers import result, verify
from runtime_services.test_failed_check_rework import _scenario


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_real_progress_gap_timeout_is_durable_failed_check_without_reexecution(
    project, monkeypatch,
):
    policy = project['cfg']['runtime_services']['check_runner']
    policy.update(initial_seconds=2, progress_gap_seconds=0.1, poll_seconds=0.02)
    tools, context = _scenario(
        project,
        command=[sys.executable, '-S', '-c', 'import time; time.sleep(2)'],
    )
    packet = result(context, 'Observe an authentic silent check timeout.')
    calls = []
    real_run = tools.runtime.check_runner.run

    def counted(*args, **kwargs):
        calls.append(args[0])
        return real_run(*args, **kwargs)

    monkeypatch.setattr(tools.runtime.check_runner, 'run', counted)
    failed = verify(tools, deepcopy(packet))
    assert failed['status'] == 'checks_failed'
    assert len(calls) == 1
    receipt = failed['checks'][0]
    assert receipt['id'] == calls[0]
    assert receipt['actual_exit_code'] < 0
    assert receipt['timed_out'] is True
    assert receipt['timeout_reason'] == 'progress_gap'
    assert receipt['capture_complete'] is True
    assert receipt['interpretable'] is False
    assert receipt['passed'] is False
    assert receipt['source_unchanged'] is True
    assert _digest(receipt['stdout']) == receipt['stdout_digest']
    assert _digest(receipt['stderr']) == receipt['stderr_digest']
    assert tools.runtime.evidence_commands.list_for('T1') == [receipt]
    assert tools.runtime.current_task()['pending'] is None

    repeated = verify(tools, deepcopy(packet))
    assert repeated['status'] == 'checks_failed'
    assert repeated['checks'] == [receipt]
    assert calls == [receipt['id']]


def test_registered_child_environment_excludes_conflicting_native_parent_identity(
    project, monkeypatch,
):
    for name in ('CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'POISE_CALLER_BINDING'):
        monkeypatch.setenv(name, f'foreign-{name}')
    code = (
        'import os; '
        'names=("CODEX_SESSION_ID","CODEX_THREAD_ID","POISE_CALLER_BINDING"); '
        'assert all(name not in os.environ for name in names); '
        'assert os.environ["TASK_CHILD_BINDING"]=="owned-child"; '
        'print("isolated-child")'
    )
    tools, context = _scenario(
        project,
        passing_continuation=True,
        command=[sys.executable, '-S', '-c', code],
        environment={'TASK_CHILD_BINDING': 'owned-child'},
    )
    response = verify(tools, result(context, 'Check exact child environment.'))
    assert response['status'] == 'awaiting_continuation'
    receipt = response['checks'][0]
    assert receipt['actual_exit_code'] == 0
    assert receipt['timed_out'] is False
    assert receipt['passed'] is True
    assert Path(receipt['stdout']).read_text() == 'isolated-child\n'
    assert _digest(receipt['stdout']) == receipt['stdout_digest']


def test_positive_prefix_and_authentic_timeout_are_both_kept_as_one_failed_batch(
    project,
):
    policy = project['cfg']['runtime_services']['check_runner']
    policy.update(initial_seconds=2, progress_gap_seconds=0.1, poll_seconds=0.02)
    tools, context = _scenario(
        project,
        command=[sys.executable, '-S', '-c', 'print("green-prefix")'],
        extra_command=[sys.executable, '-S', '-c', 'import time; time.sleep(2)'],
    )
    outcome = verify(tools, result(context, 'Keep the complete mixed batch.'))
    assert outcome['status'] == 'checks_failed'
    first, second = outcome['checks']
    assert (first['method'], first['passed'], first['timed_out']) == (
        'CHECK', True, False,
    )
    assert Path(first['stdout']).read_text() == 'green-prefix\n'
    assert (second['method'], second['passed'], second['timed_out']) == (
        'CHECK2', False, True,
    )
    assert second['timeout_reason'] == 'progress_gap'
    for receipt in outcome['checks']:
        assert _digest(receipt['stdout']) == receipt['stdout_digest']
        assert _digest(receipt['stderr']) == receipt['stderr_digest']
    assert tools.runtime.evidence_commands.list_for('T1') == outcome['checks']
    assert tools.runtime.current_task()['pending'] is None
