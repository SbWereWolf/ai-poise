"""Declared sources and independently observed proof at real integration boundaries."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

from conftest import git
from poise.common import PoiseError
from poise.infrastructure.result_integration import RuntimeResultIntegration
from poise.modules.result_integration.domain import IntegrationRun
from .helpers import advance_ref_with_same_tree, integration_input, prepare_completed_task, request


FIXTURES = Path(__file__).with_name("fixtures")


def source_method():
    method = json.loads((FIXTURES / "source_method.json").read_text())
    method["argv"] = [sys.executable, "-B", "-c", (FIXTURES / "source_candidate_probe.py").read_text()]
    return method


def candidate_source(worktree):
    (worktree / "src/fractions.py").write_text("SOURCE_MARKER = 'candidate-source'\n")


def prepare(project, method=None, change=candidate_source):
    method = source_method() if method is None else method
    tools, worktree, accepted = prepare_completed_task(
        project, change, methods=[method], checks=[method["id"]],
    )
    return tools, worktree, accepted, request("integrate", integration_input(project, accepted))


def expected_environment(worktree):
    text = (FIXTURES / "source_expected_environment.json").read_text()
    return json.loads(text.replace("$ROOT", str(worktree)))


def independent_digest(value):
    # Independent standard hash/encoding contract; never call the producer's serializer.
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def assert_receipt(receipt, facts, head, tree):
    assert receipt.get("source_provenance") == facts
    assert receipt.get("provenance_digest") == independent_digest(facts)
    assert receipt["integration_head"] == head
    assert receipt["verified_tree"] == tree
    assert receipt["method"] == "SOURCE_RESULT"
    assert receipt["passed"] is True


def test_repository_environment_selects_candidate_sources(project):
    tools, worktree, accepted, packet = prepare(project)
    done = tools.invoke(packet)
    receipt = done["checks"][-1]
    observed = json.loads(Path(receipt["stdout"]).read_text())
    assert observed["marker"] == "candidate-source", "SOURCE-CANDIDATE-NOT-RESOLVED"
    assert observed["file"] == str(worktree / "src/fractions.py")
    assert observed["cwd"] == str(worktree)
    assert done["status"] == "integrated"
    assert_receipt(receipt, expected_environment(worktree), done["target_after"], git(project["app"], "rev-parse", "HEAD^{tree}"))
    assert done["accepted_commit"] == accepted


def test_cwd_binding_matches_actual_command_cwd(project):
    method = source_method()
    method["cwd"] = "src"
    method["source_under_test"] = {"kind": "repository", "bindings": [{"kind": "cwd", "path": "src"}]}
    tools, worktree, _, packet = prepare(project, method)
    done = tools.invoke(packet)
    receipt = done["checks"][-1]
    observed = json.loads(Path(receipt["stdout"]).read_text())
    assert observed["cwd"] == str(worktree / "src")
    assert observed["file"] == str(worktree / "src/fractions.py")
    facts = json.loads((FIXTURES / "source_expected_cwd.json").read_text().replace("$ROOT", str(worktree / "src")))
    facts["bindings"][0]["path"] = "src"
    assert_receipt(receipt, facts, done["target_after"], git(project["app"], "rev-parse", "HEAD^{tree}"))


@pytest.mark.parametrize("kind", ["missing", "directory", "escape", "cwd", "method_environment", "runner_environment"])
def test_source_binding_failures_do_not_launch_check(project, monkeypatch, kind):
    outside = project["root"] / "foreign-source"
    outside.mkdir()

    def change(worktree):
        candidate_source(worktree)
        (worktree / "src/foreign").symlink_to(outside, target_is_directory=True)

    tools, worktree, _, packet = prepare(project, change=change)
    existing_started = set(Path(tools.runtime.state).rglob("started.txt"))
    method = source_method()
    if kind == "missing":
        method.pop("source_under_test")
    elif kind == "directory":
        method["source_under_test"]["bindings"][0]["path"] = "absent"
    elif kind == "escape":
        method["source_under_test"]["bindings"][0]["path"] = "src/foreign"
    elif kind == "cwd":
        method["source_under_test"] = {"kind": "repository", "bindings": [{"kind": "cwd", "path": "src"}]}
    elif kind == "method_environment":
        method["environment"]["PYTHONPATH"] = str(outside)
    else:
        method["source_under_test"]["bindings"][0]["name"] = "POISE_RUN_OUTPUT_DIR"
    # Malformed late projection only; command preparation/execution/publication remain real.
    monkeypatch.setattr(RuntimeResultIntegration, "_select_checks", lambda self, record: [method])
    target = git(project["app"], "rev-parse", "HEAD")
    with pytest.raises(PoiseError, match="source|provenance|binding|environment|collision|конфликт"):
        tools.invoke(packet)
    assert git(project["app"], "rev-parse", "HEAD") == target
    assert worktree.is_dir()
    assert set(Path(tools.runtime.state).rglob("started.txt")) == existing_started


def test_external_source_receipt_preserves_reason(project):
    method = source_method()
    facts = {"kind": "external", "reason": "Intentional standard-library fixture source."}
    method["source_under_test"] = deepcopy(facts)
    method["stdout_contains"] = ["stdlib"]
    tools, _, _, packet = prepare(project, method)
    done = tools.invoke(packet)
    receipt = done["checks"][-1]
    assert json.loads(Path(receipt["stdout"]).read_text())["marker"] == "stdlib"
    assert_receipt(receipt, facts, done["target_after"], git(project["app"], "rev-parse", "HEAD^{tree}"))


def proof_method():
    method = source_method()
    method["argv"] = [sys.executable, "-B", "-c", "print('checked')"]
    method["stdout_contains"] = ["checked"]
    method["source_under_test"] = {"kind": "repository", "bindings": [{"kind": "cwd", "path": "."}]}
    return method


def corrupt(receipt, kind, worktree):
    item = deepcopy(receipt)
    if kind == "missing_facts":
        item.pop("source_provenance", None)
    elif kind == "missing_digest":
        item.pop("provenance_digest", None)
    elif kind == "wrong_digest":
        item["provenance_digest"] = "a" * 64
    elif kind == "wrong_kind":
        item["source_provenance"] = {"kind": "external", "reason": "Forged fixture source."}
    else:
        facts = json.loads((FIXTURES / "source_expected_cwd.json").read_text().replace("$ROOT", str(worktree.parent / "foreign")))
        item["source_provenance"] = facts
        if kind == "self_consistent":
            item["provenance_digest"] = independent_digest(facts)
    return item


@pytest.mark.parametrize("kind", ["missing_facts", "wrong_kind", "foreign_path", "missing_digest", "wrong_digest", "self_consistent"])
def test_consumer_rejects_invalid_source_provenance(project, monkeypatch, kind):
    tools, worktree, _, packet = prepare(project, proof_method())
    with monkeypatch.context() as patch:
        patch.setattr(RuntimeResultIntegration, "_publish", lambda self, run, repository: run)
        tools.invoke(packet)
    record = tools.runtime.task_queries.record("T1")
    run = IntegrationRun.restore(record["pending"])
    adapter = RuntimeResultIntegration(tools.runtime)
    assert adapter._complete_candidate_proof(record, run) is True
    receipts = list(run.checks)
    receipts[-1] = corrupt(receipts[-1], kind, worktree)
    assert adapter._complete_candidate_proof(record, replace(run, checks=tuple(receipts))) is False


def test_publication_refuses_forged_source_proof(project, monkeypatch):
    tools, worktree, _, packet = prepare(project, proof_method())
    target = git(project["app"], "rev-parse", "HEAD")
    original = RuntimeResultIntegration._run_checks

    def forged_checks(self, record, run):
        checked = original(self, record, run)
        receipts = list(checked.checks)
        receipts[-1] = corrupt(receipts[-1], "self_consistent", worktree)
        forged = replace(checked, checks=tuple(receipts), version=checked.version + 1)
        self._save(run.intent.task_id, forged, checked.version)
        return forged

    monkeypatch.setattr(RuntimeResultIntegration, "_run_checks", forged_checks)
    done = tools.invoke(packet)
    assert done["status"] == "blocked"
    assert git(project["app"], "rev-parse", "HEAD") == target
    assert worktree.is_dir()
    assert done["checks"] and done["publication"] is None
    assert all(check["source_provenance"]["bindings"][0]["resolved_path"] != str(worktree) for check in done["checks"])


def test_retry_preserves_old_receipts_and_refreshes_source_proof(project):
    tools, worktree, accepted, packet = prepare(project)
    (project["app"] / "target-marker").write_text("force a genuine check failure\n")
    git(project["app"], "add", "target-marker")
    git(project["app"], "commit", "-m", "test: incompatible target")
    packet = request("integrate", integration_input(project, accepted))
    first = tools.invoke(packet)
    assert first["phase"] == "checks_failed"
    old = deepcopy(first["checks"])
    git(worktree, "rm", "target-marker")
    (worktree / "src/fractions.py").write_text("SOURCE_MARKER = 'candidate-source-repaired'\n")
    git(worktree, "add", "src/fractions.py")
    git(worktree, "commit", "-m", "test: repaired candidate")
    repaired = git(worktree, "rev-parse", "HEAD")
    done = tools.invoke(packet)
    assert done["status"] == "integrated"
    assert done["accepted_commit"] == accepted
    assert done["checks"][:len(old)] == old
    assert done["history"][:len(first["history"])] == first["history"]
    fresh = done["checks"][-1]
    assert fresh["id"] != old[-1]["id"]
    assert_receipt(fresh, expected_environment(worktree), repaired, git(project["app"], "rev-parse", "HEAD^{tree}"))
    assert json.loads(Path(fresh["stdout"]).read_text())["marker"] == "candidate-source-repaired"


def test_target_drift_creates_proof_for_updated_candidate(project, monkeypatch):
    tools, worktree, accepted, packet = prepare(project)
    original = RuntimeResultIntegration._run
    drifted = []

    def drift(self, cwd, *args, env=None):
        if args[:2] == ("merge", "--ff-only") and not drifted:
            parent = git(project["app"], "rev-parse", "HEAD")
            drifted.append(advance_ref_with_same_tree(project["app"], parent, "test: drift"))
        return original(self, cwd, *args, env=env)

    monkeypatch.setattr(RuntimeResultIntegration, "_run", drift)
    done = tools.invoke(packet)
    assert done["status"] == "integrated"
    assert done["accepted_commit"] == accepted
    assert len(done["checks"]) == 2
    first, fresh = done["checks"]
    assert first["integration_head"] != fresh["integration_head"]
    assert first["id"] != fresh["id"]
    facts = expected_environment(worktree)
    assert first["source_provenance"] == facts
    assert_receipt(fresh, facts, done["target_after"], git(project["app"], "rev-parse", "HEAD^{tree}"))
    replay = tools.invoke(packet)
    assert replay["replayed"] is True
    assert replay["checks"] == done["checks"]
