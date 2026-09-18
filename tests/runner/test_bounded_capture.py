"""R02: capture teardown has its own finite budget, even after parent exit."""
import os
from pathlib import Path
import signal
import sys
import threading
import time

import pytest

from poise.execution import RegisteredCheckRunner, method_passed


def launch(tmp_path, code, **kw):
    runner = kw.pop('runner', RegisteredCheckRunner())
    return runner.run('bounded', [sys.executable, '-S', '-c', code], tmp_path,
                      dict(os.environ), kw.pop('timeout', 2),
                      tmp_path / 'stdout', tmp_path / 'stderr', **kw)


@pytest.mark.parametrize('stop', ['exit', 'timeout'])
def test_detached_writer_does_not_hold_capture_open(tmp_path, stop):
    # The escaped child is harmless and self-terminates; never kill by name.
    child = "import os,time;from pathlib import Path;Path('escaped').write_text(str(os.getpid()));time.sleep(2)"
    code = (f'import subprocess,sys,time\nfrom pathlib import Path\n'
            f'subprocess.Popen([sys.executable,"-S","-c",{child!r}],start_new_session=True)\n'
            'while not Path("escaped").exists():time.sleep(0.001)\n'
            'print("parent",flush=True)\n' + ('time.sleep(4)' if stop == 'timeout' else ''))
    runner = RegisteredCheckRunner()
    start = time.monotonic()
    result = launch(tmp_path, code, timeout=0.4, runner=runner)
    elapsed = time.monotonic() - start
    pid = int((tmp_path / 'escaped').read_text())
    try:
        assert elapsed < 1.3, (elapsed, result)
        assert result['timed_out'] is (stop == 'timeout')
        assert result['capture_complete'] is False
        assert result['capture_reason'] == 'drain_limit'
        assert result['cleanup_seconds'] == 0.25
        os.kill(pid, 0)  # escaped session was not killed by the runner
        assert runner.active_ids() == ()
        assert Path(result['stdout']).read_text() == 'parent\n'
        if stop == 'exit':
            assert result['actual_exit_code'] == 0
            method = {'expected_exit_code':0, 'stdout_contains':[], 'stderr_contains':[]}
            assert method_passed(method, result) is False
    finally:
        # Test owns this exact child and releases it, not any unrelated group.
        try: os.kill(pid, signal.SIGTERM)
        except ProcessLookupError: pass


def test_complete_capture_preserves_large_binary_outputs_and_no_reader_threads(tmp_path):
    before = set(threading.enumerate())
    result = launch(tmp_path, 'import os\nfor i in range(96):\n os.write(1,b"x"*65536);os.write(2,b"y"*65536)')
    assert result['capture_complete'] is True and result['capture_reason'] is None
    assert (tmp_path / 'stdout').read_bytes() == b'x'*(96*65536)
    assert (tmp_path / 'stderr').read_bytes() == b'y'*(96*65536)
    assert set(threading.enumerate()) == before


def test_continuous_output_does_not_starve_deadline(tmp_path):
    start = time.monotonic()
    result = launch(tmp_path, 'import os\nwhile True:os.write(1,b"x"*4096)', timeout=0.2)
    assert time.monotonic()-start < 1.3
    assert result['timed_out'] and result['timeout_reason'] == 'hard_limit'
    assert result['capture_complete'] is True


@pytest.mark.parametrize('field', ['timeout', 'poll_seconds', 'progress_gap_seconds', 'cleanup_seconds'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), 0, -1, True])
def test_invalid_budgets_rejected_before_files(tmp_path, field, value):
    with pytest.raises(ValueError, match='finite|positive'):
        launch(tmp_path, 'pass', **{field:value})
    assert not (tmp_path / 'stdout').exists()


def test_explicit_incomplete_capture_cannot_pass_a_method(tmp_path):
    (tmp_path/'stdout').write_text(''); (tmp_path/'stderr').write_text('')
    result={'timed_out':False,'actual_exit_code':0,'stdout':str(tmp_path/'stdout'),
            'stderr':str(tmp_path/'stderr'),'capture_complete':False}
    method={'expected_exit_code':0,'stdout_contains':[],'stderr_contains':[]}
    assert method_passed(method,result) is False
    result.pop('capture_complete')
    assert method_passed(method,result) is True  # unchanged legacy receipts


@pytest.mark.parametrize('boundary', ['register', 'read', 'write'])
def test_capture_fault_closes_descriptors_reaps_child_and_releases_slot(tmp_path, monkeypatch, boundary):
    from poise import execution
    original_selector = execution.selectors.DefaultSelector
    original_read = os.read
    original_open = Path.open
    original_popen = execution.subprocess.Popen
    captured, children = [], []

    class FaultySelector(original_selector):
        def register(self, fileobj, *args, **kwargs):
            captured.append(fileobj)
            if boundary == 'register':
                raise OSError('capture injected')
            return super().register(fileobj, *args, **kwargs)

    def read(fd, size):
        if boundary == 'read' and any(not f.closed and f.fileno() == fd for f in captured):
            raise OSError('capture injected')
        return original_read(fd, size)

    class FaultyTarget:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *exc): return self.stream.__exit__(*exc)
        def write(self, data): raise OSError('capture injected')

    def opened(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return FaultyTarget(stream) if boundary == 'write' and path == tmp_path/'stdout' and args == ('wb',) else stream

    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child

    runner = RegisteredCheckRunner()
    with monkeypatch.context() as patch:
        patch.setattr(execution.selectors, 'DefaultSelector', FaultySelector)
        patch.setattr(execution.os, 'read', read)
        patch.setattr(Path, 'open', opened)
        patch.setattr(execution.subprocess, 'Popen', popen)
        with pytest.raises(OSError, match='capture injected'):
            launch(tmp_path, 'import time;print("data",flush=True);time.sleep(4)', runner=runner)
    assert runner.active_ids() == ()
    assert len(children) == 1 and children[0].returncode is not None
    assert children[0].stdout.closed and children[0].stderr.closed
    assert launch(tmp_path, 'pass', runner=runner)['capture_complete'] is True


def test_runtime_does_not_interpret_explicitly_incomplete_capture(project, monkeypatch):
    from batch.helpers import result, verify
    from runtime_services.test_pending_check_recovery import _valid_scenario
    tools, context = _valid_scenario(project, passing_continuation=True)
    actual = tools.runtime.check_runner.run
    monkeypatch.setattr(tools.runtime.check_runner, 'run',
                        lambda *a, **k: {**actual(*a, **k), 'capture_complete':False, 'capture_reason':'drain_limit'})
    response = verify(tools, result(context, 'Incomplete output is not evidence'))
    assert response['status'] == 'checks_failed'
    receipt = tools.runtime.evidence_commands.list_for('T1')[-1]
    assert receipt['passed'] is False and receipt['interpretable'] is False


def test_cache_does_not_store_success_with_incomplete_capture(tmp_path, monkeypatch):
    from verification.test_ai_poise_test_package_cache import checkout
    from poise.infrastructure.test_package_cache import AiPoiseTestPackageCache
    root, catalog = checkout(tmp_path)
    cache = AiPoiseTestPackageCache(root, catalog)
    actual = cache.runner.run
    monkeypatch.setattr(cache.runner, 'run',
                        lambda *a, **k: {**actual(*a, **k), 'capture_complete':False, 'capture_reason':'drain_limit'})
    evidence = cache.run('sample', timeout_seconds=30)
    assert evidence['actual_exit_code'] == 0 and evidence['junit_error'] is None
    assert evidence['capture_complete'] is False and evidence['passed'] is False
    assert evidence['cache_record'] is None
