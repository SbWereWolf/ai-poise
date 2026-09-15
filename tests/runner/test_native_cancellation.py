"""C020: native observations control the existing registered runner, not Task state."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from poise.common import PoiseError
from poise.execution import RegisteredCheckRunner
from poise.infrastructure.hook_transport import HookService
from hook_transport.helpers import settings, install, event


def native(service, installed, kind="SessionStart", session="A"):
    packet = event(kind, session=session)
    packet["cwd"] = str(service.settings.root)
    return service.event(installed["definition_path"], packet)


def bound(project, tmp_path):
    service = HookService(settings(project, tmp_path))
    installed = install(service)
    native(service, installed)
    binding = service.latest_binding("A", "primary")
    runner = RegisteredCheckRunner()
    runner.bind_cancellation(service.registry.runner_cancellation_check(binding["session_id"]))
    return service, installed, binding, runner


def launch(runner, path, code="print('ok')", run_id="owned"):
    return runner.run(run_id, [sys.executable, "-B", "-c", code], path,
                      dict(os.environ), 5, path / "out", path / "err")


def running(runner, path):
    result = {}; errors = []
    def execute():
        try: result.update(launch(runner, path, "import time;print('started',flush=True);time.sleep(4)"))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=execute)
    thread.start()
    limit = time.monotonic() + 3
    while not (path / "out").exists() or not (path / "out").read_bytes():
        assert time.monotonic() < limit
        time.sleep(.01)
    return thread, result, errors


def test_native_end_from_another_process_cancels_only_its_runner(project, tmp_path):
    service, installed, binding, runner = bound(project, tmp_path)
    native(service, installed, session="B")
    thread, result, errors = running(runner, tmp_path)
    try:
        native(service, installed, "Stop")
        native(service, installed, "SessionEnd", session="B")
        assert thread.is_alive() and runner.active_ids() == ("owned",)
        packet = event("SessionEnd", session="A"); packet["cwd"] = str(service.settings.root)
        command = service.settings.command("hook", "--definition", installed["definition_path"])
        import json
        receipt = subprocess.run(command, shell=True, input=json.dumps(packet),
                                 text=True, capture_output=True, timeout=5)
        assert receipt.returncode == 0, receipt.stderr
        assert json.loads(receipt.stdout) == {}
        thread.join(3)
        assert not thread.is_alive() and not errors
        assert result["cancelled"] and result["cancellation_reason"] == "native_session_end"
        assert result["actual_exit_code"] < 0 and not result["timed_out"]
        assert runner.active_ids() == ()
    finally:
        runner.cancel("owned"); thread.join(5)


def test_closed_session_rejects_before_spawn_and_resume_does_not_erase_old_end(project, tmp_path):
    service, installed, binding, runner = bound(project, tmp_path)
    native(service, installed, "SessionEnd")
    native(service, installed, "SessionStart")
    with pytest.raises(PoiseError, match="session.*ended"):
        launch(runner, tmp_path, "from pathlib import Path;Path('side-effect').touch()")
    assert not (tmp_path / "side-effect").exists()
    assert not (tmp_path / "out").exists()
    assert runner.active_ids() == ()
    runner.bind_cancellation(service.registry.runner_cancellation_check(binding["session_id"]))
    assert launch(runner, tmp_path)["actual_exit_code"] == 0


def test_duplicate_run_rejected_before_spawn_or_log_truncation(tmp_path):
    runner = RegisteredCheckRunner()
    thread, result, errors = running(runner, tmp_path)
    try:
        before = (tmp_path / "out").read_bytes()
        with pytest.raises(PoiseError, match="уже запущена"):
            launch(runner, tmp_path, "from pathlib import Path;Path('duplicate').touch()")
        assert not (tmp_path / "duplicate").exists()
        assert (tmp_path / "out").read_bytes() == before
    finally:
        runner.cancel("owned"); thread.join(5)
    assert runner.active_ids() == ()


def test_cancellation_read_error_releases_owned_child_and_slot(tmp_path):
    runner = RegisteredCheckRunner()
    def broken():
        if (tmp_path / "out").exists() and (tmp_path / "out").read_bytes():
            raise OSError("registry unavailable")
        return False
    runner.bind_cancellation(broken)
    with pytest.raises(OSError, match="registry unavailable"):
        launch(runner, tmp_path, "import os,time;print(os.getpid(),flush=True);time.sleep(4)")
    pid = int((tmp_path / "out").read_text())
    with pytest.raises(ProcessLookupError): os.kill(pid, 0)
    assert runner.active_ids() == ()
    runner.bind_cancellation(None)
    assert launch(runner, tmp_path)["actual_exit_code"] == 0


def test_launch_error_releases_reservation(tmp_path):
    runner = RegisteredCheckRunner()
    with pytest.raises(PoiseError):
        runner.run("owned", ["/nonexistent/c020-check"], tmp_path, {}, 1,
                   tmp_path / "out", tmp_path / "err")
    assert runner.active_ids() == ()
    assert launch(runner, tmp_path)["actual_exit_code"] == 0


def test_work_adapter_attaches_registry_observation_to_existing_owner(project, tmp_path, monkeypatch):
    service, installed, binding, _ = bound(project, tmp_path)
    h = service.bound_runtime(binding["binding_path"])
    owner = h.check_runner
    from poise.infrastructure import hook_transport as transport
    seen = []
    monkeypatch.setattr(transport.WorkTools, "invoke", lambda self, req: seen.append(self) or {"status":"test"})
    req = {"operation":"verify"}
    # No capability probes: this test isolates composition, not external IDE checks.
    service._execute(h, binding, req, {"gate_operations":[]})
    assert h.check_runner is owner and owner.cancellation_requested is not None
    native(service, installed, "SessionEnd")
    assert owner.cancellation_requested() is True


def test_native_session_start_is_required_before_hook_launch(project, tmp_path):
    service = HookService(settings(project, tmp_path))
    installed = install(service)
    native(service, installed, 'UserPromptSubmit')
    binding = service.latest_binding('A', 'primary')
    with pytest.raises(PoiseError, match='observed SessionStart'):
        service.registry.runner_cancellation_check(binding['session_id'])


def test_stop_has_no_cancel_semantics_for_a_completed_check(project, tmp_path):
    service, installed, binding, runner = bound(project, tmp_path)
    result = launch(runner, tmp_path)
    assert result['actual_exit_code'] == 0
    assert not result['cancelled'] and result['cancellation_reason'] is None
    native(service, installed, 'Stop')
    assert runner.cancellation_requested() is False
    assert runner.active_ids() == ()


def test_output_error_cannot_silently_produce_success(tmp_path, monkeypatch):
    from poise import execution
    original = execution.threading.Thread
    def failing_start(*args, **kwargs):
        thread = original(*args, **kwargs)
        thread.start = lambda: (_ for _ in ()).throw(OSError('cannot start capture'))
        return thread
    monkeypatch.setattr(execution.threading, 'Thread', failing_start)
    runner = RegisteredCheckRunner()
    with pytest.raises(OSError, match='cannot start capture'):
        launch(runner, tmp_path, 'import time;time.sleep(4)')
    assert runner.active_ids() == ()
