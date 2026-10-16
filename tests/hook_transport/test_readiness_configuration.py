"""F01 configuration admission and the actual runtime-template consumer boundary."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from conftest import write_json
from poise.common import PoiseError, digest
from poise.infrastructure.hook_transport import HookService, HookSettings, setup_runtime
from poise.interfaces.hook_transport import execute
from poise.modules.hook_transport.domain import HookDefinition
from .helpers import command_probe, definition, event, install, settings

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture
def configured(project, tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.delenv("POISE_CALLER_BINDING", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "configuration-session")
    path = settings(project, tmp_path)
    service = HookService(path)
    counter, state = tmp_path / "configuration.calls", tmp_path / "configuration.state"
    state.write_text("available", encoding="utf-8")
    probe = command_probe("configured-python")
    probe["argv"] = [sys.executable, "-B", str(FIXTURES / "readiness_probe.py"),
                     str(counter), str(state), "plain"]
    desired = definition()
    desired["probes"] = [probe]
    return SimpleNamespace(path=path, service=service, counter=counter, definition=desired)


def count(case):
    return case.counter.read_text().splitlines() if case.counter.exists() else []


def snapshot(case):
    """Observe only publication-owned files, logical rows and actual probe outputs."""
    s = case.service.settings
    files = {str(p): p.read_bytes() for p in [s.path, s.hooks_file] if p.is_file()}
    for directory in (s.definitions, s.observations, s.bindings):
        if directory.exists():
            for p in directory.rglob("*"):
                if p.is_file() and (directory != s.observations or p.name in
                                   {"stdout.txt", "stderr.txt", "receipt.json"}):
                    files[str(p)] = p.read_bytes()
    rows = {"installations": [], "operations": []}
    if s.database.exists():
        with sqlite3.connect(s.database) as db:
            for table in rows:
                exists = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                                    (table,)).fetchone()
                if exists:
                    rows[table] = db.execute(f"SELECT id,data FROM {table} ORDER BY id").fetchall()
    return files, rows, count(case)


def invoke(case, operation, inputs, *, definition_path=None):
    out, err = io.StringIO(), io.StringIO()
    command = "hook" if operation == "start" else "runtime-config"
    packet = inputs if command == "hook" else {"operation": operation, "input": inputs}
    code = execute(command, SimpleNamespace(settings=case.path, definition=definition_path),
                   io.BytesIO(json.dumps(packet).encode()), out, err)
    value = json.loads(out.getvalue()) if out.getvalue() else {}
    if "response_path" in value:
        value = json.loads(Path(value["response_path"]).read_text())
    return code, value, err.getvalue()


def native(case):
    value = event(session="configuration-session")
    value["cwd"] = str(case.service.settings.root)
    return value


@pytest.mark.parametrize("defect,field", [
    ("omit-file", "file"), ("omit-header", "header_template"),
    ("omit-item", "item_template"), ("empty-file", "file"),
    ("unknown-header", "header_template"), ("nonstring-item", "item_template"),
])
def test_invalid_known_summary_options(configured, defect, field):
    case = configured
    raw = json.loads(case.path.read_text())
    # Independent positive parser control, not the source of any expected value.
    HookSettings.proposed(case.path, raw)
    if defect.startswith("omit-"):
        del raw["capability_summary"][field]
    else:
        raw["capability_summary"][field] = {
            "empty-file": "", "unknown-header": "{undeclared_field}", "nonstring-item": 7,
        }[defect]
    before = snapshot(case)
    caught = None
    try:
        HookSettings.proposed(case.path, raw)
    except PoiseError as exc:
        caught = str(exc)
    assert caught is not None, "missing_summary_option_validation"
    assert "capability_summary" in caught and field in caught
    assert snapshot(case) == before


@pytest.mark.parametrize("operation", ["start", "summary", "refresh"])
def test_missing_summary_at_operation_boundary(configured, operation):
    case = configured
    installed = install(case.service, case.definition)
    # Valid-settings control must not fail this test before its intended RED assertion.
    binding = str(case.service.settings.bindings / "not-established" / "binding.json")
    inputs = native(case) if operation == "start" else {"binding_path": binding}
    control = invoke(case, operation, inputs, definition_path=installed["definition_path"])
    raw = json.loads(case.path.read_text())
    del raw["capability_summary"]
    write_json(case.path, raw)
    if operation == "start":
        # The rejected start is a fresh native session, not a stale-digest retry.
        inputs["session_id"] = "configuration-missing-session"
    before_calls = count(case)
    before_probes = {p: p.read_bytes() for p in case.service.settings.observations.rglob("*")
                     if p.is_file() and p.name in {"stdout.txt", "stderr.txt", "receipt.json"}}
    before_summaries = {p: p.read_bytes() for p in case.service.settings.bindings.rglob("capabilities.json")}
    code, result, error = invoke(case, operation, inputs, definition_path=installed["definition_path"])
    diagnosis = error if operation == "start" else result.get("reason", "")
    assert "capability_summary" in diagnosis, "missing_operation_prerequisite"
    if operation == "start":
        assert code == 1 and not result
        assert control[0] == 0 and not control[2]
    else:
        assert code == 2 and result["status"] == "rejected" and not error
        assert control[0] == 2 and control[1]["status"] == "rejected"
        assert "binding" in control[1]["reason"].lower()
    assert count(case) == before_calls
    assert {p: p.read_bytes() for p in case.service.settings.observations.rglob("*")
            if p.is_file() and p.name in {"stdout.txt", "stderr.txt", "receipt.json"}} == before_probes
    assert {p: p.read_bytes() for p in case.service.settings.bindings.rglob("capabilities.json")} == before_summaries


@pytest.mark.parametrize("operation", ["install", "reconcile", "setup"])
def test_missing_summary_before_publication(configured, operation):
    case = configured
    packet = {"request_id": "missing-summary", "expected_revision": None,
              "definition": deepcopy(case.definition)}
    if operation == "reconcile":
        old = deepcopy(case.definition)
        old["id"] = "previous-hooks"
        install(case.service, old)
        # Real reconciliation requires distinct identities and actual live groups.
        # Use the existing group producer only for arrangement, never as an oracle.
        live_path = write_json(case.service.settings.definitions /
                               (digest(case.definition) + ".json"), case.definition)
        document = json.loads(case.service.settings.hooks_file.read_text())
        for kind, group in case.service.repository._groups(HookDefinition.parse(case.definition), live_path):
            document["hooks"].setdefault(kind, []).append(group)
        write_json(case.service.settings.hooks_file, document)
        packet.update(expected_revision=case.service.revision(),
                      previous_installation_id="previous-hooks", live_definition_path=str(live_path))
        packet["definition"]["events"][0]["status_message"] = "Updated installed handler"
    raw = json.loads(case.path.read_text())
    del raw["capability_summary"]
    if operation == "setup":
        case.path.unlink()
        action = lambda: setup_runtime(case.path, {"settings": raw, "installation": packet})
    else:
        write_json(case.path, raw)
        service = HookService(case.path)
        action = lambda: getattr(service, operation)(packet)
    before = snapshot(case)
    caught = None
    try:
        action()
    except PoiseError as exc:
        caught = str(exc)
    assert caught is not None and "capability_summary" in caught, "missing_publication_prerequisite"
    assert snapshot(case) == before


def test_runtime_template_materializes_usable_summary(configured, monkeypatch):
    case = configured
    template = json.loads((ROOT / "config/hook-templates/settings.json").read_text())
    assert "capability_summary" in template, "missing_runtime_template_summary"
    expected = json.loads((FIXTURES / "readiness_template_options.json").read_text())
    assert template["capability_summary"] == expected
    monkeypatch.syspath_prepend(str(ROOT / "examples"))
    spec = importlib.util.spec_from_file_location("f01_hooks_example", ROOT / "examples/hooks_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = module.fill_template(template, {
        "FILESYSTEM_ROOT": str(case.service.settings.root), "python": sys.executable,
        "source_root": str(ROOT / "src"), "workspace_parent": str(case.service.settings.root),
    })
    # Isolate existing installation roots; retain ALL actual template feature values.
    fixture = json.loads(case.path.read_text())
    for field in ("root", "project_config", "hooks_file", "definitions", "state", "lock"):
        raw[field] = fixture[field]
    assert "${" not in json.dumps(raw)
    case.path.unlink()
    installed = setup_runtime(case.path, {"settings": raw, "installation": {
        "request_id": "actual-template", "expected_revision": None, "definition": case.definition}})
    assert installed["status"] == "installed" and installed["capability_checks"]["ready"] is True
    case.service = HookService(case.path)
    assert count(case) == ["called"]  # Existing setup observation is separately accounted.
    code, result, error = invoke(case, "start", native(case), definition_path=installed["definition_path"])
    assert code == 0 and not error
    assert "Infrastructure ready=True;" in result["hookSpecificOutput"]["additionalContext"]
    assert count(case) == ["called", "called"]
    binding = case.service.latest_binding("configuration-session", "primary")
    saved_path = Path(binding["binding_path"]).parent / "capabilities.json"
    saved = json.loads(saved_path.read_text())
    assert saved["ready"] is True and saved["missing_required"] == []
    assert len(saved["observations"]) == 1
    observation = saved["observations"][0]
    assert observation["id"] == "configured-python"
    assert observation["status"] == "available" and observation["required"] is True
    code, result, error = invoke(case, "summary", {"binding_path": binding["binding_path"]})
    assert code == 0 and not error and result["status"] == "summary"
    assert result["summary"] == saved
    assert count(case) == ["called", "called"]
