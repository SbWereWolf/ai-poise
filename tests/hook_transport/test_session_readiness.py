"""F01: observe session readiness through real hooks, CLI and independent effects."""
from __future__ import annotations

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from poise.infrastructure.hook_transport import HookService
from poise.interfaces.hook_transport import execute
from poise.modules.accounting.clock import ClockObservation
from .helpers import command_probe, definition, event, host_environment, install, settings
from .test_mcp import spec as mcp_spec

FIXTURES = Path(__file__).with_name("fixtures")
CASES = json.loads((FIXTURES / "readiness_cases.json").read_text(encoding="utf-8"))
T1 = CASES["times"]["first"]
T2 = CASES["times"]["second"]
SOURCE = Path(__file__).resolve().parents[2] / "src"


class FakeClock:
    """Controllable audit value; monotonic observations stay in one test domain."""

    fork_safe = True

    def __init__(self):
        self.at = T1
        self.tick = 0

    def observe(self):
        self.tick += 1
        return ClockObservation(self.at, self.tick, "f01-fixture-clock")


@pytest.fixture
def readiness(project, tmp_path, monkeypatch):
    from poise.infrastructure import clock as clock_module

    clock = FakeClock()
    monkeypatch.setattr(clock_module, "SystemClock", lambda: clock)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.delenv("POISE_CALLER_BINDING", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "A")
    project["cfg"]["source_reader"] = {
        "receipt_file": "reader.json",
        "policy": str(Path(__file__).resolve().parents[2] / "config/development/source-reader.json"),
    }
    path = settings(project, tmp_path)
    service = HookService(path)
    return SimpleNamespace(service=service, path=path, root=tmp_path, clock=clock,
                           monkeypatch=monkeypatch)


def probe(r, name, *, required=True, mode="plain", available=True):
    state = r.root / f"{name}.state"
    state.write_text("available" if available else "unavailable", encoding="utf-8")
    counter = r.root / f"{name}.calls"
    item = command_probe(name, required=required)
    item.update(argv=[sys.executable, "-B", str(FIXTURES / "readiness_probe.py"),
                      str(counter), str(state), mode],
                remediation=CASES["command_remediation"])
    if mode == "project":
        item.update(project_bound=True, stdout_contains=[],
                    json_assertions=[{"path": ["project"], "equals": "${workspace}"}])
    if mode == "timeout":
        item["timeout_seconds"] = 0.1
    if mode == "output":
        item["max_output_bytes"] = 100
    return item


def calls(r, name):
    path = r.root / f"{name}.calls"
    return 0 if not path.exists() else len(path.read_text().splitlines())


def receipts(r):
    return {p: p.read_bytes() for p in r.service.settings.observations.rglob("receipt.json")}


def configure(r, items, *, context_limit=None):
    d = definition()
    d["probes"] = items
    if context_limit is not None:
        d["events"][0]["context_limit"] = context_limit
    return install(r.service, d)


def invoke(r, command, packet, *, session="A", definition_path=None, child=False):
    if child:
        argv = [sys.executable, "-B", "-m", "poise", command, "--settings", str(r.path)]
        if definition_path is not None:
            argv += ["--definition", definition_path]
        environment = host_environment(session)
        environment["PYTHONPATH"] = str(SOURCE)
        result = subprocess.run(argv, input=json.dumps(packet), text=True,
                                capture_output=True, cwd=r.service.settings.root,
                                env=environment)
        code, text, error = result.returncode, result.stdout, result.stderr
    else:
        r.monkeypatch.setenv("CODEX_THREAD_ID", session)
        out, err = io.StringIO(), io.StringIO()
        code = execute(command, SimpleNamespace(settings=r.path, definition=definition_path),
                       io.BytesIO(json.dumps(packet).encode()), out, err)
        text, error = out.getvalue(), err.getvalue()
    if command == "hook" and code != 0:
        return code, {}, error
    assert not error, error
    value = json.loads(text)
    if "response_path" in value:
        value = json.loads(Path(value["response_path"]).read_text(encoding="utf-8"))
    return code, value, error


def start(r, installed, *, session="A", source="startup", workspace=None, child=False):
    wire = event(session=session)
    wire.update(source=source, cwd=str(workspace or r.service.settings.root))
    return invoke(r, "hook", wire, session=session,
                  definition_path=installed["definition_path"], child=child)


def summary_path(r, session="A"):
    binding = r.service.latest_binding(session, "primary")
    return Path(binding["binding_path"]).parent / "capabilities.json"


def snapshot(r, session="A"):
    path = summary_path(r, session)
    assert path.is_file(), "startup-summary-missing"
    return json.loads(path.read_text(encoding="utf-8"))


def operation(r, action, *, session="A", binding_session=None, child=False):
    binding = r.service.latest_binding(binding_session or session, "primary")
    return invoke(r, "runtime-config",
                  {"operation": action, "input": {"binding_path": binding["binding_path"]}},
                  session=session, child=child)


def assert_summary(value, session, workspace, at, *, ready=True, missing=()):
    assert set(value) == {"schema", "session_id", "workspace", "checked_at", "ready",
                          "missing_required", "observations"}
    assert value["schema"] == "capability-summary-1"
    assert value["session_id"] == session
    assert value["workspace"] == str(workspace)
    assert value["checked_at"] == at
    assert value["ready"] is ready
    assert value["missing_required"] == list(missing)


def assert_available(item, name, required):
    assert item["id"] == name and item["required"] is required
    assert {k: item[k] for k in CASES["available_projection"]} == CASES["available_projection"]
    assert item["remediation"] == CASES["command_remediation"]
    assert Path(item["receipt"]).is_file()


@pytest.mark.parametrize("source", ["startup", "resume", "compact", "clear"])
def test_start_delivers_complete_configured_summary(readiness, source):
    r = readiness
    installed = configure(r, [probe(r, "python"), probe(r, "optional", required=False)])
    code, result, _ = start(r, installed, source=source)
    assert code == 0
    value = snapshot(r)
    assert_summary(value, "A", r.service.settings.root, T1)
    assert_available(value["observations"][0], "python", True)
    assert_available(value["observations"][1], "optional", False)
    context = result["hookSpecificOutput"]["additionalContext"]
    binding = r.service.latest_binding("A", "primary")
    assert str(binding["launcher"]) in context and "Current state: idle." in context
    assert "Context generation:" in context and "Use restore_context" in context
    expected = CASES["expected_start_header"].replace("${workspace}", str(r.service.settings.root))
    expected = expected.replace("${snapshot}", str(summary_path(r)))
    assert expected in context
    for line in CASES["expected_start_rows"]:
        assert line in context
    assert len(context) <= 2000
    assert (calls(r, "python"), calls(r, "optional")) == (1, 1)


@pytest.mark.parametrize("required", [True, False])
def test_requiredness_controls_readiness_and_cli_exit(readiness, required):
    r = readiness
    installed = configure(r, [probe(r, "failed", required=required, available=False),
                              probe(r, "working")])
    code, _, _ = start(r, installed)
    assert code == 0
    value = snapshot(r)
    assert_summary(value, "A", r.service.settings.root, T1,
                   ready=not required, missing=["failed"] if required else [])
    bad, good = value["observations"]
    assert bad["status"] == "unavailable" and bad["required"] is required
    assert bad["reason"] == "Probe exit code did not match"
    assert bad["remediation"] == CASES["command_remediation"]
    assert_available(good, "working", True)
    code, result, _ = operation(r, "summary")
    assert code == (1 if required else 0)
    assert result == {"status": "summary", "summary": value}


def test_one_failed_probe_preserves_later_observations(readiness):
    r = readiness
    installed = configure(r, [probe(r, "first"), probe(r, "middle", available=False),
                              probe(r, "last")])
    assert start(r, installed)[0] == 0
    value = snapshot(r)
    assert [i["id"] for i in value["observations"]] == ["first", "middle", "last"]
    assert [i["status"] for i in value["observations"]] == ["available", "unavailable", "available"]
    assert [calls(r, name) for name in ("first", "middle", "last")] == [1, 1, 1]
    assert len(receipts(r)) == 3


def test_saved_cli_reads_execute_no_probes(readiness):
    r = readiness
    installed = configure(r, [probe(r, "python")])
    assert start(r, installed, child=True)[0] == 0
    saved = snapshot(r)
    before, observed = summary_path(r).read_bytes(), receipts(r)
    (r.root / "python.state").write_text("unavailable")
    for _ in range(2):
        code, result, _ = operation(r, "summary", child=True)
        assert code == 0 and result == {"status": "summary", "summary": saved}
    assert summary_path(r).read_bytes() == before and receipts(r) == observed
    assert calls(r, "python") == 1


def test_refresh_replaces_data_and_time_then_read_is_saved(readiness):
    r = readiness
    installed = configure(r, [probe(r, "python", available=False)])
    assert start(r, installed)[0] == 0
    initial = snapshot(r)
    assert initial["checked_at"] == T1 and initial["ready"] is False
    (r.root / "python.state").write_text("available")
    r.clock.at = T2
    code, result, _ = operation(r, "refresh")
    assert code == 0 and result["status"] == "refreshed", "refresh-summary-missing"
    value = snapshot(r)
    assert_summary(value, "A", r.service.settings.root, T2)
    assert_available(value["observations"][0], "python", True)
    assert result["summary"] == value and calls(r, "python") == 2
    assert operation(r, "summary")[1]["summary"] == value
    assert calls(r, "python") == 2


def test_refresh_works_through_actual_cli(readiness):
    r = readiness
    installed = configure(r, [probe(r, "python", available=False)])
    assert start(r, installed, child=True)[0] == 0
    first = snapshot(r)
    (r.root / "python.state").write_text("available")
    code, result, _ = operation(r, "refresh", child=True)
    assert code == 0 and result["status"] == "refreshed", "refresh-summary-missing"
    assert result["summary"]["ready"] is True
    assert result["summary"]["checked_at"] != first["checked_at"]
    assert calls(r, "python") == 2


def test_session_isolation_and_existing_identity_admission(readiness):
    r = readiness
    installed = configure(r, [probe(r, "python")])
    assert start(r, installed, session="A")[0] == 0
    a = snapshot(r, "A")
    r.clock.at = T2
    (r.root / "python.state").write_text("unavailable")
    assert start(r, installed, session="B")[0] == 0
    b = snapshot(r, "B")
    assert a["ready"] is True and b["ready"] is False
    before = (summary_path(r, "A").read_bytes(), summary_path(r, "B").read_bytes(), calls(r, "python"))
    code, result, _ = operation(r, "refresh", session="A", binding_session="B")
    assert code == 2 and result["status"] == "rejected"
    assert "identity" in result["reason"].lower() or "own" in result["reason"].lower()
    assert before == (summary_path(r, "A").read_bytes(), summary_path(r, "B").read_bytes(), calls(r, "python"))
    (r.root / "python.state").write_text("available")
    assert operation(r, "refresh", session="A")[0] == 0
    assert snapshot(r, "B") == b


def test_refresh_preserves_session_workspace_binding(readiness):
    r = readiness
    first, second = r.root / "W1", r.root / "W2"
    first.mkdir(); second.mkdir()
    installed = configure(r, [probe(r, "project", mode="project")])
    assert start(r, installed, session="A", workspace=first)[0] == 0
    assert start(r, installed, session="B", workspace=second)[0] == 0
    a, b = snapshot(r, "A"), snapshot(r, "B")
    assert a["workspace"] == a["observations"][0]["project_path"] == str(first)
    assert b["workspace"] == b["observations"][0]["project_path"] == str(second)
    assert operation(r, "refresh", session="A")[0] == 0
    assert snapshot(r, "A")["workspace"] == str(first)
    assert snapshot(r, "B") == b
    # A contradictory configured assertion makes that observation unavailable.
    bad = command_probe("wrong-project")
    bad.update(project_bound=True, stdout_contains=[],
               json_assertions=[{"path": ["project"], "equals": "${workspace}"}],
               argv=[sys.executable, "-c", 'print(\'{"project":"/other"}\')'])
    from .test_probes import run_probes
    wrong = r.root / "wrong"
    wrong.mkdir()
    result = run_probes(wrong, [bad])
    assert result["observations"][0]["reason"] == "Probe JSON/project binding assertion failed"


@pytest.mark.parametrize("damage", ["missing", "json", "structure"])
def test_bad_saved_input_never_runs_probes_and_refresh_recovers(readiness, damage):
    r = readiness
    installed = configure(r, [probe(r, "python")])
    assert start(r, installed)[0] == 0
    snapshot(r)
    path = summary_path(r)
    if damage == "missing":
        path.unlink()
    elif damage == "json":
        path.write_text("{broken")
    else:
        invalid = deepcopy(CASES["invalid_structure"])
        invalid["workspace"] = str(r.service.settings.root)
        path.write_text(json.dumps(invalid))
    before = calls(r, "python"), receipts(r)
    code, result, _ = operation(r, "summary", child=True)
    assert code == 2 and result["status"] == "rejected"
    assert result["reason"] and "Unknown runtime-config operation" not in result["reason"]
    assert (calls(r, "python"), receipts(r)) == before
    r.clock.at = T2
    code, result, _ = operation(r, "refresh")
    assert code == 0 and result["status"] == "refreshed", "refresh-summary-missing"
    assert_summary(result["summary"], "A", r.service.settings.root, T2)
    assert operation(r, "summary")[1]["summary"] == result["summary"]
    assert calls(r, "python") == before[0] + 1


@pytest.mark.parametrize("mode,reason", [("timeout", "Capability probe timed out"),
                                         ("output", "Probe result exceeds configured output bound")])
def test_failed_probe_boundaries_preserve_other_results(readiness, mode, reason):
    r = readiness
    installed = configure(r, [probe(r, "bounded", mode=mode), probe(r, "working")])
    assert start(r, installed)[0] == 0
    value = snapshot(r)
    bad, good = value["observations"]
    assert bad["status"] == "unavailable" and bad["reason"] == reason
    assert_available(good, "working", True)
    detail = json.loads(Path(bad["receipt"]).read_text())
    assert detail["timed_out"] is (mode == "timeout")
    assert value["ready"] is False and value["missing_required"] == ["bounded"]


@pytest.mark.parametrize("defect", ["duplicate", "required"])
def test_invalid_batch_is_rejected_before_execution(readiness, defect):
    r = readiness
    installed = configure(r, [probe(r, "python")])
    assert start(r, installed)[0] == 0
    snapshot(r)
    before = summary_path(r).read_bytes(), calls(r, "python"), receipts(r)
    bad = definition()
    first = probe(r, "invalid")
    bad["probes"] = [first, deepcopy(first)]
    if defect == "required":
        first["required"] = "yes"
        bad["probes"] = [first]
    code, result, _ = invoke(r, "runtime-config", {"operation": "install", "input": {
        "request_id": "invalid-readiness", "expected_revision": installed["revision"], "definition": bad}})
    assert code == 2 and result["status"] == "rejected"
    assert calls(r, "invalid") == 0
    assert before == (summary_path(r).read_bytes(), calls(r, "python"), receipts(r))


def test_failed_snapshot_replacement_preserves_previous_finished_data(readiness, monkeypatch):
    r = readiness
    installed = configure(r, [probe(r, "python", available=False)])
    assert start(r, installed)[0] == 0
    old = snapshot(r)
    path = summary_path(r)
    before = path.read_bytes()
    replace = os.replace

    def fail_summary(source, destination, *args, **kwargs):
        if Path(destination) == path:
            raise OSError("fixture snapshot replacement unavailable")
        return replace(source, destination, *args, **kwargs)

    (r.root / "python.state").write_text("available")
    r.clock.at = T2
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_summary)
        code, result, _ = operation(r, "refresh")
    assert code == 2 and result["status"] == "rejected"
    assert "fixture snapshot replacement unavailable" in result["reason"]
    assert path.read_bytes() == before and calls(r, "python") == 2
    assert operation(r, "summary")[1]["summary"] == old
    assert calls(r, "python") == 2


@pytest.mark.parametrize("budget", ["context", "output"])
def test_oversize_native_context_reports_error_but_keeps_finished_snapshot(readiness, budget):
    r = readiness
    if budget == "output":
        document = json.loads(r.path.read_text())
        document["output_chars"] = 700
        r.path.write_text(json.dumps(document))
        r.service = HookService(r.path)
    installed = configure(r, [probe(r, "python")], context_limit=64 if budget == "context" else None)
    code, _, error = start(r, installed)
    value = snapshot(r)
    assert code != 0 and error
    assert "limit" in error.lower() or "context" in error.lower() or "budget" in error.lower()
    before = calls(r, "python")
    code, result, _ = operation(r, "summary")
    assert code == 0 and result["summary"] == value
    assert calls(r, "python") == before


@pytest.mark.parametrize("required", [True, False])
def test_mcp_summary_preserves_data_saved_reads_and_refresh(readiness, required):
    r = readiness
    state, counter = r.root / "ide.state", r.root / "ide.calls"
    state.write_text("ok")
    item = mcp_spec()
    item.update(required=required, remediation=CASES["mcp_remediation"],
                argv=[sys.executable, "-B", str(FIXTURES / "mcp_readiness_peer.py"),
                      str(counter), str(state), str(Path(__file__).with_name("mcp_fixture.py"))])
    installed = configure(r, [item, probe(r, "working")])
    assert start(r, installed)[0] == 0
    first = snapshot(r)
    observed = first["observations"][0]
    assert {k: observed[k] for k in CASES["mcp_available_projection"]} == CASES["mcp_available_projection"]
    assert observed["required"] is required and observed["remediation"] == CASES["mcp_remediation"]
    assert observed["project_path"] == str(r.service.settings.root)
    before = summary_path(r).read_bytes(), receipts(r)
    state.write_text("version")
    for _ in range(2):
        assert operation(r, "summary")[1]["summary"] == first
    assert calls(r, "ide") == calls(r, "working") == 1
    assert before == (summary_path(r).read_bytes(), receipts(r))
    r.clock.at = T2
    code, result, _ = operation(r, "refresh")
    assert code == (1 if required else 0) and result["status"] == "refreshed"
    value = snapshot(r)
    assert_summary(value, "A", r.service.settings.root, T2,
                   ready=not required, missing=["ide"] if required else [])
    observed = value["observations"][0]
    assert {k: observed[k] for k in CASES["mcp_unavailable_projection"]} == CASES["mcp_unavailable_projection"]
    assert observed["required"] is required and observed["remediation"] == CASES["mcp_remediation"]
    assert observed["project_path"] == str(r.service.settings.root)
    assert_available(value["observations"][1], "working", True)
    assert operation(r, "summary")[1]["summary"] == value
    assert calls(r, "ide") == calls(r, "working") == 2


def test_saved_read_accepts_independently_arranged_snapshot(readiness):
    """Isolate the read boundary from missing automatic delivery during RED."""
    r = readiness
    installed = configure(r, [probe(r, "python")])
    assert start(r, installed)[0] == 0
    value = deepcopy(CASES["saved_snapshot"])
    value["workspace"] = str(r.service.settings.root)
    receipt = r.root / "arranged-receipt.json"
    receipt.write_text("{}")
    value["observations"][0]["receipt"] = str(receipt)
    summary_path(r).write_text(json.dumps(value))
    before = calls(r, "python"), receipts(r)
    code, result, _ = operation(r, "summary", child=True)
    assert code == 0 and result == {"status": "summary", "summary": value}, "saved-read-summary-missing"
    assert (calls(r, "python"), receipts(r)) == before


def test_explicit_refresh_with_binding_and_no_snapshot(readiness):
    """Isolate refresh from automatic delivery; no alternate summary implementation."""
    r = readiness
    installed = configure(r, [probe(r, "python")])
    assert start(r, installed)[0] == 0
    path = summary_path(r)
    if path.exists():
        path.unlink()
    before = calls(r, "python")
    r.clock.at = T2
    code, result, _ = operation(r, "refresh")
    assert code == 0 and result["status"] == "refreshed", "refresh-summary-missing"
    assert_summary(result["summary"], "A", r.service.settings.root, T2)
    assert_available(result["summary"]["observations"][0], "python", True)
    assert calls(r, "python") == before + 1
