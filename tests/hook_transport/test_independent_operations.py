"""Independent native events cross a real process/SQLite boundary during preparation."""
from copy import deepcopy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Event, Thread, get_ident

import pytest

from poise.common import PoiseError
from poise.infrastructure.hook_transport import FileHookRepository, HookService
from hook_transport.helpers import definition, event, host_environment, install, settings
from hook_transport.test_installation_reconciliation import drift
from hook_transport.test_session_delivery_remediation import _interrupt_publication
from hook_transport.test_shared_session_entry import prepared


SOURCE = Path(__file__).resolve().parents[2] / "src"
EVENTS = ["UserPromptSubmit", "Stop", "SessionEnd", "SessionStart"]


def snapshot(service):
    """Observe explicit columns without using the registry as its own oracle."""
    with sqlite3.connect(f"file:{service.settings.database}?mode=ro", uri=True) as db:
        return {
            "bindings": db.execute(
                "SELECT id,data,latest_message FROM bindings ORDER BY id"
            ).fetchall(),
            "events": db.execute(
                "SELECT id,at,binding,event,data FROM hook_events ORDER BY id"
            ).fetchall(),
            "operations": db.execute(
                "SELECT id,data FROM operations ORDER BY id"
            ).fetchall(),
            "installations": db.execute(
                "SELECT id,data FROM installations ORDER BY id"
            ).fetchall(),
        }


def native_cli(service, definition_path, project, kind, session, turn="independent-turn"):
    payload = event(kind, session=session, turn=turn)
    payload["cwd"] = str(project["app"])
    environment = host_environment(session)
    environment["PYTHONPATH"] = str(SOURCE)
    return subprocess.run(
        [sys.executable, "-P", "-B", "-m", "poise", "hook", "--settings",
         str(service.settings.path), "--definition", str(definition_path)],
        input=json.dumps(payload), text=True, capture_output=True,
        cwd=project["app"], env=environment, timeout=20,
    )


def require_native_success(result):
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert result.stderr == ""
    return json.loads(result.stdout)


def arrange(project, tmp_path, operation):
    if operation == "reconfigure":
        service, packet, _, _ = prepared(project, tmp_path)
        definition_path = service.latest_binding("preserved-parent", "primary")["definition_path"]
    elif operation == "reconcile":
        service, packet, _ = drift(project, tmp_path)
        definition_path = packet["live_definition_path"]
    else:
        service = HookService(settings(project, tmp_path))
        receipt = install(service)
        definition_path = receipt["definition_path"]
        changed = definition()
        changed["events"][0]["status_message"] = "Updated independent preparation"
        packet = {"request_id": "independent-install", "expected_revision": service.revision(),
                  "definition": changed}
    for session in ("independent-existing", "independent-bystander"):
        require_native_success(native_cli(service, definition_path, project, "SessionStart", session))
        require_native_success(native_cli(service, definition_path, project, "UserPromptSubmit",
                                          session, "initial-turn"))
    return service, packet, definition_path


@contextmanager
def paused_preparation(service, packet, operation, monkeypatch):
    entered, release = Event(), Event()
    outcome = {}
    original = FileHookRepository._groups
    installer_thread = None

    def groups(repository, *args):
        if (get_ident() == installer_thread and repository.settings.path == service.settings.path
                and not entered.is_set()):
            entered.set()
            assert release.wait(40), "Preparation barrier was not released by test cleanup"
        return original(repository, *args)

    def run():
        nonlocal installer_thread
        installer_thread = get_ident()
        try:
            outcome["receipt"] = getattr(service, operation)(packet)
        except BaseException as exc:
            outcome["error"] = exc

    monkeypatch.setattr(FileHookRepository, "_groups", groups)
    worker = Thread(target=run, name="hook-preparation")
    worker.start()
    try:
        assert entered.wait(10), ("Preparation seam was not reached", outcome)
        yield outcome
    finally:
        release.set()
        worker.join(20)
        assert not worker.is_alive(), "Installer worker remained active after barrier release"


def assert_event_state(service, before, observed, kind, session, response):
    assert observed["events"][:len(before["events"])] == before["events"]
    added = [row for row in observed["events"][len(before["events"]):] if row[2] == session]
    assert [row[3] for row in added] == [kind]
    rows = {row[0]: row for row in observed["bindings"]}
    initial = {row[0]: row for row in before["bindings"]}
    assert rows["independent-bystander"] == initial["independent-bystander"]
    if kind == "SessionEnd":
        assert session not in rows
        record = json.loads(initial[session][1])
        assert not Path(record["binding_path"]).exists()
        assert not Path(record["launcher"]).exists()
        assert response == {}
        assert json.loads(added[0][4])["reason"] == "other"
    else:
        record = json.loads(rows[session][1])
        assert record["external_session"] == session
        assert json.loads(Path(record["binding_path"]).read_text()) == record
        assert Path(record["launcher"]).is_file()
        if kind == "UserPromptSubmit":
            message = json.loads(rows[session][2])
            assert message["conversation_id"] == session
            assert message["message_id"] == "independent-turn"
            assert rows[session][2] != initial[session][2]
        elif kind == "SessionStart":
            assert rows[session][2] is None
            assert record["launcher"] in response["hookSpecificOutput"]["additionalContext"]
        else:
            assert rows[session] == initial[session]
            assert "systemMessage" in response


@pytest.mark.parametrize("operation", ["install", "reconcile", "reconfigure"])
@pytest.mark.parametrize("kind", EVENTS)
def test_native_event_finishes_before_preparation(project, tmp_path, monkeypatch, operation, kind):
    service, packet, definition_path = arrange(project, tmp_path, operation)
    before = snapshot(service)
    native_before = service.settings.hooks_file.read_bytes()
    session = "independent-new" if kind == "SessionStart" else "independent-existing"
    with paused_preparation(service, packet, operation, monkeypatch) as outcome:
        assert service.settings.hooks_file.read_bytes() == native_before
        result = native_cli(service, definition_path, project, kind, session)
        if result.returncode:
            assert result.stderr == "Истёк лимит ожидания внешнего lock\n", (
                "Unexpected native failure, not the intended contention RED", result.stdout, result.stderr
            )
            pytest.fail("HOOK-PREP-WRITER-BLOCKS-EVENT", pytrace=False)
        response = require_native_success(result)
        observed = snapshot(service)
        assert_event_state(service, before, observed, kind, session, response)
        assert "receipt" not in outcome, "Installer published before preparation was released"

    current = HookService(service.settings.path)
    if "error" in outcome:
        # Inventory arrivals/retirement may reject preparation before any effect.
        assert operation == "reconfigure" and kind in ("SessionStart", "SessionEnd")
        assert isinstance(outcome["error"], PoiseError), repr(outcome["error"])
        assert snapshot(current)["operations"] == before["operations"]
        receipt = current.reconfigure(packet)
    else:
        receipt = outcome["receipt"]
    after = snapshot(current)
    assert after["events"][:len(observed["events"])] == observed["events"]
    after_rows = {row[0]: row for row in after["bindings"]}
    if kind == "SessionEnd":
        assert session not in after_rows
        retired = json.loads(dict((row[0], row[1]) for row in before["bindings"])[session])
        assert not Path(retired["binding_path"]).exists()
        assert not Path(retired["launcher"]).exists()
    else:
        assert after_rows[session][2] == dict((row[0], row[2]) for row in observed["bindings"])[session]
        if operation == "reconfigure":
            saved = json.loads(after_rows[session][1])
            assert saved["definition_path"] == receipt["definition_path"]
            assert json.loads(Path(saved["binding_path"]).read_text()) == saved
    if operation == "reconfigure":
        assert receipt["binding_count"] == len(after_rows)
    assert getattr(HookService(service.settings.path), operation)(packet) == receipt
    assert snapshot(current) == after


def test_pending_reconfiguration_handles_session_end(project, tmp_path, monkeypatch):
    service, packet, _, _ = prepared(project, tmp_path)
    parent = service.latest_binding("preserved-parent", "primary")
    _interrupt_publication(service, packet, monkeypatch, "source")
    current = HookService(service.settings.path)
    require_native_success(native_cli(current, parent["definition_path"], project,
                                      "SessionEnd", "preserved-parent"))
    ended = snapshot(current)
    assert ended["bindings"] == []
    assert ended["events"][-1][2:4] == ("preserved-parent", "SessionEnd")
    assert not Path(parent["binding_path"]).exists()
    assert not Path(parent["launcher"]).exists()
    try:
        receipt = HookService(service.settings.path).reconfigure(packet)
    except PoiseError as exc:
        assert str(exc) == "Binding inventory changed during pending reconfiguration", str(exc)
        pytest.fail("HOOK-PENDING-END-CANNOT-RESUME", pytrace=False)
    after = snapshot(HookService(service.settings.path))
    assert after["bindings"] == []
    assert after["events"][:len(ended["events"])] == ended["events"]
    assert receipt["binding_count"] == 0
    assert not Path(parent["binding_path"]).exists()
    assert not Path(parent["launcher"]).exists()
    assert HookService(service.settings.path).reconfigure(packet) == receipt
    assert snapshot(HookService(service.settings.path)) == after


@pytest.mark.parametrize("operation", ["install", "reconcile"])
def test_competing_publication_is_preserved(project, tmp_path, monkeypatch, operation):
    service, packet, _ = arrange(project, tmp_path, operation)
    competitor = deepcopy(packet)
    competitor["request_id"] = "competing-publication"
    competitor["definition"]["events"][0]["status_message"] = "Competing accepted definition"
    environment = dict(os.environ, PYTHONPATH=str(SOURCE))
    with paused_preparation(service, packet, operation, monkeypatch) as outcome:
        result = subprocess.run(
            [sys.executable, "-P", "-B", "-m", "poise", "runtime-config", "--settings",
             str(service.settings.path)],
            input=json.dumps({"operation": operation, "input": competitor}),
            text=True, capture_output=True, cwd=project["app"], env=environment, timeout=20,
        )
        envelope = json.loads(result.stdout)
        if result.returncode:
            assert envelope.get("reason") == "Истёк лимит ожидания внешнего lock", envelope
            pytest.fail("HOOK-PREP-WRITER-BLOCKS-COMPETITOR", pytrace=False)
        assert envelope["status"] == "installed", envelope
        winning = snapshot(service)
        native_winning = service.settings.hooks_file.read_bytes()
    assert isinstance(outcome.get("error"), PoiseError), outcome
    assert snapshot(service) == winning
    assert service.settings.hooks_file.read_bytes() == native_winning
    # An unchanged stale intent remains stale; changing its expected input is a new intent.
    with pytest.raises(PoiseError):
        getattr(HookService(service.settings.path), operation)(packet)
    assert snapshot(service) == winning
    with pytest.raises(PoiseError):
        reused = deepcopy(competitor)
        reused["definition"]["events"][0]["status_message"] = "Conflicting reused request"
        getattr(HookService(service.settings.path), operation)(reused)
    assert snapshot(service) == winning
