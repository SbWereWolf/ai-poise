"""F01 target-drift boundaries: real producers, legacy publication and entry recovery."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys

from conftest import git
from poise.common import PoiseError, digest, file_digest
from poise.infrastructure.hook_transport import HookService, HookSettings
from projects.helpers import fill_markers
from projects.legacy_hook_fixture import restore_legacy_hook
from projects.runtime_resources_bootstrap_helpers import arrange_bootstrap, apply_required
from .helpers import event
from .test_session_readiness import readiness, configure, probe, start, snapshot, calls
from .test_shared_session_entry import prepared, cli

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")


def publication_state(service, target):
    """Independent bytes/logical columns, not a publisher-generated expectation."""
    s = service.settings
    paths = [s.path, s.hooks_file, target, target.with_name("hooks.lock")]
    for directory in (s.definitions, s.bindings, s.observations):
        if directory.exists():
            paths.extend(p for p in directory.rglob("*") if p.is_file())
    files = {str(p): p.read_bytes() if p.exists() else None for p in paths}
    with sqlite3.connect(f"file:{s.database}?mode=ro", uri=True) as db:
        rows = {
            "installations": db.execute("SELECT id,data FROM installations ORDER BY id").fetchall(),
            "operations": db.execute("SELECT id,data FROM operations ORDER BY id").fetchall(),
            "bindings": db.execute("SELECT id,data,latest_message FROM bindings ORDER BY id").fetchall(),
            "events": db.execute("SELECT id,at,binding,event,data FROM hook_events ORDER BY id").fetchall(),
        }
    return files, rows


def test_shared_example_supplies_usable_summary(tmp_path):
    raw = json.loads((ROOT / "config/shared-hook-settings.example.json").read_text())
    assert "capability_summary" in raw, "shared_example_summary_missing"
    expected = json.loads((FIXTURES / "readiness_template_options.json").read_text())
    assert raw["capability_summary"] == expected
    filled = fill_markers(raw, {
        "FILESYSTEM_ROOT": tmp_path, "PYTHON_EXECUTABLE": sys.executable,
        "PYTHON_PACKAGE_ROOT": ROOT / "src", "REPOSITORY_ROOT": tmp_path,
        "CODEX_CONFIG_HOME": tmp_path / "native configuration",
        "LAUNCHER_SETTINGS_PATH": tmp_path / "project launchers.json",
    })
    assert "${" not in json.dumps(filled)
    settings = HookSettings.proposed(tmp_path / "shared-settings.json", filled)
    assert settings.require_summary() == expected
    assert not settings.path.exists() and not settings.state.exists() and not settings.hooks_file.exists()


def test_legacy_reconfigure_requires_summary_before_publication(project, tmp_path):
    case = arrange_bootstrap(project, tmp_path, native_hooks=True)
    old = restore_legacy_hook(case)
    assert "capability_summary" not in json.loads(old["settings"].read_text())
    assert apply_required(case)["status"] == "completed"
    service = HookService(old["settings"])
    target = case["root"] / "shared native" / "hooks.json"
    proposed = deepcopy(service.settings.raw)
    proposed.update(hooks_file=str(target), hooks_lock=str(case["root"] / "shared native" / "hooks.lock"),
                    repository_scope={"repository": str(project["app"]), "timeout_seconds": 3.0})
    packet = {"request_id": "legacy-missing-summary", "expected_settings_digest": digest(service.settings.raw),
              "expected_revision": file_digest(service.settings.hooks_file), "target_revision": None,
              "settings": proposed, "definition": json.loads(old["definition"].read_text())}
    # Authentic recorded installation, real public association and otherwise usable target.
    HookSettings.proposed(service.settings.path, proposed)
    assert service.definition(old["definition"]).data["id"] == packet["definition"]["id"]
    before = publication_state(service, target)
    reason = None
    try:
        service.reconfigure(packet)
    except PoiseError as exc:
        reason = str(exc)
    assert reason is not None and "capability_summary" in reason, "legacy_reconfigure_summary_prerequisite_missing"
    assert publication_state(service, target) == before


def test_fork_in_own_worktree_delivers_entry_and_observed_summary(project, tmp_path, monkeypatch):
    service, packet, _, _ = prepared(project, tmp_path)
    counter, state = tmp_path / "fork.calls", tmp_path / "fork.state"
    state.write_text("available", encoding="utf-8")
    packet["definition"]["probes"][0]["argv"] = [
        sys.executable, "-B", str(FIXTURES / "readiness_probe.py"), str(counter), str(state), "plain"]
    receipt = service.reconfigure(packet)
    current = HookService(service.settings.path)
    parent = current.latest_binding("preserved-parent", "primary")
    parent_summary = Path(parent["binding_path"]).parent / "capabilities.json"
    parent_before = parent_summary.read_bytes()
    worktree = tmp_path / "fork worktree"
    git(project["app"], "worktree", "add", "--detach", str(worktree), "HEAD")
    foreign = tmp_path / "foreign repository"
    foreign.mkdir()
    git(foreign, "init")
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    native = event(session="readiness-fork-child")
    native.update(source="fork", cwd=str(worktree))
    response = cli(current, receipt["definition_path"], native, worktree)
    child = current.latest_binding("readiness-fork-child", "primary")
    context = response["hookSpecificOutput"]["additionalContext"]
    assert child["launcher"] in context and parent["launcher"] not in context
    assert "Infrastructure ready=True;" in context and "workspace=" + str(worktree) in context
    value = json.loads((Path(child["binding_path"]).parent / "capabilities.json").read_text())
    assert value["session_id"] == "readiness-fork-child" and value["workspace"] == str(worktree)
    assert value["ready"] is True and value["missing_required"] == []
    assert value["observations"][0]["status"] == "available"
    assert counter.read_text().splitlines() == ["called"]
    actual_probe = json.loads(Path(value["observations"][0]["receipt"]).read_text())
    assert actual_probe["workspace"] == str(worktree) and actual_probe["status"] == "available"
    assert parent_summary.read_bytes() == parent_before


def test_subject_recovery_failure_keeps_entry_diagnostic_and_independent_summary(readiness, monkeypatch):
    r = readiness
    installed = configure(r, [probe(r, "python")])

    def failed_recovery(self, **request):
        raise PoiseError("Subject recovery fixture unavailable")

    monkeypatch.setattr("poise.runtime.Poise.restore_context", failed_recovery)
    code, response, error = start(r, installed)
    assert code == 0 and not error
    binding = r.service.latest_binding("A", "primary")
    context = response["hookSpecificOutput"]["additionalContext"]
    assert binding["launcher"] in context and "Infrastructure ready=True;" in context
    assert response["systemMessage"] == "Subject recovery fixture unavailable"
    value = snapshot(r)
    assert value["session_id"] == "A" and value["workspace"] == str(r.service.settings.root)
    assert value["ready"] is True and value["observations"][0]["status"] == "available"
    assert calls(r, "python") == 1
    complete = [json.loads(p.read_text()) for p in r.service.settings.observations.rglob("response.json")]
    assert any(item.get("systemMessage") == "Subject recovery fixture unavailable" and
               item.get("hookSpecificOutput", {}).get("additionalContext") == context for item in complete)
