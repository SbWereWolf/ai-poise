"""Actual child outcomes, complete streams, parser recognition and failure effects."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from .support import Scene, digest, literal, snapshot


def test_exact_argv_cwd_and_single_process_owner(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    from poise.execution import RegisteredCheckRunner
    original = RegisteredCheckRunner.run
    calls = []

    def observed(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(RegisteredCheckRunner, 'run', observed)
    s.child('--stdout', 'literal α\n', 'a b', 'c;d')
    item = s.run()
    record = s.record(item)
    assert len(calls) == 1 and len(s.markers()) == 1
    assert record['argv'] == s.profile()['argv']
    assert s.markers()[0]['remaining'] == ['a b', 'c;d']
    assert s.markers()[0]['cwd'] == record['cwd'] == str(s.checkout)
    assert s.outputs(record) == ['literal α\n'.encode(), b'']


def test_failed_step_stops_remaining_steps(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    from .test_packages import diamond
    diamond(s)
    left = next(v for v in s.packages['packages'] if v['package_id'] == 'left')
    left['steps'][0]['arguments'] += ['--exit', '7', '--stderr', 'left failed\n']
    item = s.run(selection=s.selection('consumer', kind='package'), status='failed')
    assert [v['marker'] for v in s.markers()] == ['base:1', 'left:1']
    assert [v['record']['actual_exit_code'] for v in item['steps']] == [0, 7]
    assert [v['selection']['id'] for v in item['not_executed']] == ['right', 'consumer']
    assert not item['cache']['stored']
    assert s.outputs(item['steps'][1]['record'])[1] == b'left failed\n'


def test_complete_utf8_and_empty_streams(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    for body in ['alpha\nИван 🐺\n', 'CRLF\r\nwithout-final-LF', 'Я' * 20000, '']:
        s.child('--stdout', body, '--stderr', 'problem\n', require_parser_confirmation=False)
        record = s.record(s.run(fresh=True))
        assert s.outputs(record) == [body.encode('utf-8'), b'problem\n']
        assert record['stdout']['sha256'] == digest(body.encode('utf-8'))
        assert record['termination']['capture_complete'] is True
    assert record['stdout']['sha256'] == 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'


def test_capture_failure_preserves_known_outcome(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    from poise.execution import RegisteredCheckRunner
    original = RegisteredCheckRunner.run
    observed = []

    def missing_retained_stream(self, *args, **kwargs):
        actual = original(self, *args, **kwargs)
        observed.append(deepcopy(actual))
        Path(actual['stdout']).unlink()  # Real readback failure after real terminal observation.
        return actual
    monkeypatch.setattr(RegisteredCheckRunner, 'run', missing_retained_stream)
    item = s.run(status='unconfirmed')
    record = s.record(item)
    assert len(observed) == 1 and observed[0]['actual_exit_code'] == 0
    assert record['actual_exit_code'] == 0 and not record['verified_success']
    assert not record['termination']['capture_complete']
    assert record['error']['code'] == 'capture_failed' and not item['cache']['stored']


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path('/proc') / str(pid) / 'stat'
    return not stat.exists() or stat.read_text().split()[2] != 'Z'


def test_terminal_timeout_and_cancel_stop_owned_group(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    sibling = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
    try:
        s.child('--sleep', '100', '--descendant', execution_timeout_seconds=1)
        item = s.run(status='timed_out', fresh=True)
        record = s.record(item)
        child = s.markers()[-1]
        assert not alive(child['pid']) and not alive(child['descendant_pid'])
        assert sibling.poll() is None
        assert record['termination']['child_reaped'] and record['termination']['process_group_stopped']
        from poise.execution import RegisteredCheckRunner
        original = RegisteredCheckRunner.run
        controllers = []

        def cancel_at_barrier(self, run_id, *args, **kwargs):
            def cancel():
                deadline = time.monotonic() + 5
                while not (s.events / 'started').exists():
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
                assert self.cancel(run_id)
            controller = threading.Thread(target=cancel)
            controllers.append(controller)
            controller.start()
            return original(self, run_id, *args, **kwargs)
        monkeypatch.setattr(RegisteredCheckRunner, 'run', cancel_at_barrier)
        s.child('--barrier', '--descendant', execution_timeout_seconds=10)
        record = s.record(s.run(status='cancelled', fresh=True))
        for controller in controllers:
            controller.join(5)
            assert not controller.is_alive()
        child = s.markers()[-1]
        assert not alive(child['pid']) and not alive(child['descendant_pid'])
        assert sibling.poll() is None and record['termination']['cancelled']
    finally:
        sibling.terminate()
        sibling.wait(timeout=5)


def test_regex_progress_and_unavailable_progress(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    s.child('--progress', progress={'mode': 'regex', 'pattern': r'PROGRESS (?P<done>\d+)/(?P<total>\d+)',
                                  'completed_group': 'done', 'total_group': 'total'}, stall_timeout_seconds=1)
    record = s.record(s.run(fresh=True))
    assert record['progress']['observed'] and record['progress']['completed'] == 2
    assert record['progress']['total'] == 2 and record['progress']['max_gap_seconds'] >= 0
    s.child('--stdout', 'noise\n', '--sleep', '5')
    record = s.record(s.run(status='stalled', fresh=True))
    assert record['termination']['timeout_reason'] == 'progress_gap'
    s.child(progress={'mode': 'unavailable'}, stall_timeout_seconds=0)
    record = s.record(s.run(fresh=True))
    assert record['progress']['observed'] is False and record['progress']['max_gap_seconds'] is None


def test_all_seven_parser_literal_logs(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    for case in literal('parser-cases.json'):
        s.child('--stdout', case['body'], parser=case['parser'], require_parser_confirmation=False)
        record = s.record(s.run(fresh=True))
        assert record['parser'] == case['expected']
        assert s.outputs(record)[0] == case['body'].encode()


def test_zero_exit_recognition_preserves_donor_outcomes(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    parsers = literal('parser-cases.json')
    failure = next(v for v in parsers if v['parser'] == 'phpunit' and v['expected']['counts'].get('failures') == 1)
    s.child('--stdout', failure['body'], parser='phpunit', require_parser_confirmation=True)
    record = s.record(s.run(fresh=True))
    assert record['actual_exit_code'] == 0 and record['verified_success'] is True
    assert record['parser']['counts']['failures'] == 1 and record['parser']['confirmed']
    s.child('--stdout', 'build failed\n', parser='build', require_parser_confirmation=True)
    record = s.record(s.run(fresh=True, status='unconfirmed'))
    assert record['actual_exit_code'] == 0 and record['parser']['confirmed'] is False
    assert record['verified_success'] is False
    success = next(v for v in parsers if v['parser'] == 'phpunit' and v['expected']['counts'].get('failures') == 0)
    s.child('--stdout', success['body'], '--exit', '7', parser='phpunit')
    record = s.record(s.run(fresh=True, status='failed'))
    assert record['actual_exit_code'] == 7 and record['parser']['confirmed']
    assert not record['verified_success']


def test_declared_evidence_completeness_and_limits(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    s.profiles['evidence_contracts'] = [{'evidence_id': 'fixture-evidence', 'required_globs': ['data.txt'],
                                      'optional_globs': [], 'max_files': 2, 'max_total_bytes': 100,
                                      'retention_class': 'diagnostic'}]
    s.child('--evidence', 'data.txt', evidence_id='fixture-evidence')
    record = s.record(s.run(fresh=True))
    assert record['evidence']['status'] == 'complete'
    retained = record['evidence']['files']
    assert len(retained) == 1 and Path(retained[0]['path']).read_bytes() == b'independent evidence\n'
    assert retained[0]['sha256'] == digest(b'independent evidence\n')
    assert Path(record['evidence']['manifest_path']).is_file()
    for missing, budget in [(True, 100), (False, 1)]:
        s.profiles['evidence_contracts'][0]['max_total_bytes'] = budget
        s.child(*([] if missing else ['--evidence', 'data.txt']))
        record = s.record(s.run(fresh=True, status='unconfirmed'))
        assert record['evidence']['status'] in ('missing', 'incomplete', 'invalid')
        assert not record['verified_success']


class BrokenOutput:
    def write(self, value):
        raise OSError('fixture caller output unavailable')


def test_caller_output_failure_keeps_committed_result(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    code, _, diagnostic = s.invoke(output=BrokenOutput())
    assert code == 1
    error = json.loads(diagnostic)
    assert error['code'] == 'output_write'
    saved = json.loads(Path(error['references']['response_path']).read_text())
    assert saved['status'] == 'passed'
    original = saved['items'][0]
    record = s.record(original)
    assert s.outputs(record) == [b'work done\n', b'']
    code, read, _ = s.invoke(s.request('result', items=[{'result_id': original['result_id']}]))
    assert code == 0 and read['items'] == [original] and len(s.markers()) == 1


def test_ledger_save_failure_keeps_full_streams(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    from poise.execution import RegisteredCheckRunner
    real_run = RegisteredCheckRunner.run
    real_connect = sqlite3.connect
    completed = []

    def observed(self, *args, **kwargs):
        result = real_run(self, *args, **kwargs)
        completed.append(result)
        return result

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if completed and sql.lstrip().upper().startswith(('INSERT', 'UPDATE', 'REPLACE')):
                raise sqlite3.OperationalError('fixture ledger write denied')
            return super().execute(sql, *args, **kwargs)

        def commit(self):
            if completed:
                raise sqlite3.OperationalError('fixture ledger commit denied')
            return super().commit()

    def connect(database, *args, **kwargs):
        if str(database).endswith('named-runs.sqlite'):
            kwargs['factory'] = FailingConnection
        return real_connect(database, *args, **kwargs)
    monkeypatch.setattr(RegisteredCheckRunner, 'run', observed)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    code, result, _ = s.invoke()
    assert code == 1 and result['status'] == 'unconfirmed'
    assert result['error']['code'] == 'result_save_failed'
    assert len(completed) == 1 and completed[0]['actual_exit_code'] == 0
    assert Path(completed[0]['stdout']).read_bytes() == b'work done\n'
    assert Path(completed[0]['stderr']).read_bytes() == b''
    assert not snapshot(s.state / 'cache')


def test_diagnostic_sinks_survive_transport_failure(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    from tests.skills.record_instruction_red import run
    original_out, original_err = sys.stdout, sys.stderr
    with monkeypatch.context() as patch:
        patch.setattr(sys, 'stdout', BrokenOutput())
        code, _, diagnostic = s.invoke(output=BrokenOutput())
    assert sys.stdout is original_out and sys.stderr is original_err
    assert code == 1 and 'output_write' in diagnostic and len(s.markers()) == 1
    # The actual reporter's durable sinks remain intact after the consumer fault.
    assert callable(run)


def test_presentation_error_does_not_change_run_truth(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    from poise.modules.result_views.domain import result_view
    import poise.modules.result_views.domain as views

    def fail(*args, **kwargs):
        raise OSError('fixture presentation unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(views, 'result_view', fail)
        # Raw transport uses complete machine result, never the broken display.
        code, value, _ = s.invoke()
    assert code == 0 and value['status'] == 'passed'
    record = s.record(value['items'][0])
    assert record['actual_exit_code'] == 0 and s.outputs(record) == [b'work done\n', b'']
    assert views.result_view is result_view and len(s.markers()) == 1


def test_partial_interruption_never_creates_success_sample(project, tmp_path, monkeypatch):
    s = Scene(project, tmp_path, monkeypatch)
    s.child('--barrier', execution_timeout_seconds=1)
    item = s.run(status='timed_out', fresh=True)
    record = s.record(item)
    assert (s.events / 'started').exists() and record['termination']['timed_out']
    assert not record['verified_success'] and not item['cache']['stored']
    assert record['actual_exit_code'] is None or record['actual_exit_code'] != 0
    s.child()
    next_run = s.record(s.run(fresh=True))
    assert next_run['timeout']['basis'] == 'initial'
