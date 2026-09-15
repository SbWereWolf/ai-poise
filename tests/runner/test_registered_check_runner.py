from pathlib import Path
import os
import sys
import threading
import time

from poise.execution import RegisteredCheckRunner


def run(runner, tmp_path, run_id, code, *, cwd=None, timeout=10):
    return runner.run(
        run_id,
        [sys.executable, '-c', code],
        tmp_path if cwd is None else cwd,
        dict(os.environ),
        timeout,
        tmp_path / f'{run_id}.stdout',
        tmp_path / f'{run_id}.stderr',
    )


def test_registered_runner_uses_exact_supplied_cwd_and_keeps_full_logs(tmp_path):
    checkout = tmp_path / 'arbitrary-checkout'
    checkout.mkdir()
    result = run(
        RegisteredCheckRunner(preview_chars=5),
        tmp_path,
        'ok',
        'import os; print(os.getcwd()); print("abcdefghij")',
        cwd=checkout,
    )
    assert result['actual_exit_code'] == 0
    assert not result['timed_out'] and not result['cancelled']
    assert Path(result['stdout']).read_text().splitlines()[0] == str(checkout)
    assert Path(result['stdout']).read_text().endswith('abcdefghij\n')
    assert len(result['stdout_preview']) <= 5


def test_registered_runner_preserves_failure_exit_code(tmp_path):
    result = run(
        RegisteredCheckRunner(), tmp_path, 'failed',
        'import sys; print("diagnostic", file=sys.stderr); raise SystemExit(7)',
    )
    assert result['actual_exit_code'] == 7
    assert not result['timed_out'] and not result['cancelled']
    assert 'diagnostic' in Path(result['stderr']).read_text()


def test_registered_runner_timeout_kills_owned_process_group(tmp_path):
    result = run(
        RegisteredCheckRunner(), tmp_path, 'timeout',
        'import time; time.sleep(100)', timeout=0.2,
    )
    assert result['timed_out'] and not result['cancelled']
    assert result['actual_exit_code'] < 0


def test_registered_runner_explicit_cancel_kills_only_owned_run(tmp_path):
    runner = RegisteredCheckRunner()
    outcome = {}

    def target():
        outcome.update(run(
            runner, tmp_path, 'cancel-me',
            'import time; time.sleep(100)', timeout=30,
        ))

    thread = threading.Thread(target=target)
    thread.start()
    deadline = time.monotonic() + 5
    while 'cancel-me' not in runner.active_ids() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.active_ids() == ('cancel-me',)
    assert runner.cancel('missing') is False
    assert runner.cancel('cancel-me') is True
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert outcome['cancelled'] and not outcome['timed_out']
    assert outcome['actual_exit_code'] < 0
    assert runner.active_ids() == ()
