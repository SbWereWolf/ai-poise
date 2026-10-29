"""Existing Sprint consumers preserve genuine late registry input ownership."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from batch.helpers import request, text_artifact
from conftest import write_json
from poise.infrastructure.sqlite.tasks import SqliteTaskRepository
from poise.modules.foundation.errors import DomainError, PoiseError, VersionConflict
from runtime_services.test_restart_late_method_inputs import claims
from runtime_services.test_restart_registry_preservation import persisted, wire_hash
from runtime_services.test_task_restart import restart
from sprints.helpers import bootstrap, draft, publish, setup, task
from sprints.test_unpublish import client, git_state, release_member, reopen, unchanged
from verification.test_current_registry_mutation import change, operation


DATA = json.loads((Path(__file__).parent / "fixtures/sprint_existing_inputs.json").read_text())


def claim_rows(tools):
    return {key: [list(row) for row in values] for key, values in claims(tools).items()}


def late_member(project, *, mixed):
    """Register the late method through real result verification, then reopen."""
    setup(project)
    project["workflow"]["stages"][0]["sections"]["test_registry"] = "Register the actual late Sprint method."
    write_json(project["root"] / "config/workflows/development.json", project["workflow"])
    planner = client(project, "planner")
    member = task(project, "A")
    if not mixed:
        member["methods"] = []
        member["method_inputs"] = []
        member["checks"] = {"work": []}
    born = draft(planner, [member])
    published = publish(planner, born["revision"])
    worker = client(project, "executor")
    context = bootstrap(worker, "A")
    payload = deepcopy(context["result_template"])
    payload["sections"]["report"] = "Actual old Sprint result and late registered proof."
    payload["commit_message"] = "test: preserve late Sprint proof"
    payload["method_additions"] = change(operation("add", "LATE", registration={
        "method": DATA["late_method"], "stages": ["work"],
    }), executable_obligations=())
    report = worker.invoke(request("verify", {"result": payload, "artifacts": [
        text_artifact("task", "late-sprint-proof.txt", "permanent late Sprint artifact\n"),
    ]}))
    assert report["status"] == "verified"
    expected = {"CHECK": b"checked\n", "LATE": b"late-sprint-check\n"} if mixed else {
        "LATE": b"late-sprint-check\n",
    }
    assert {item["method"]: Path(item["stdout"]).read_bytes() for item in report["checks"]} == expected
    paths = [Path(item[key]) for item in report["checks"] for key in ("stdout", "stderr")]
    paths.extend(Path(item["path"]) for item in report["artifacts"])
    proof = {path: path.read_bytes() for path in paths}
    assert Path(report["artifacts"][0]["path"]).read_bytes() == b"permanent late Sprint artifact\n"
    release_member(worker, "late-sprint-proof")
    root = Path(context["worktree"])
    wip = root / "src/late-sprint-wip.txt"
    wip.write_bytes(b"staged late Sprint work\n")
    subprocess.run(["git", "-C", str(root), "add", "--", "src/late-sprint-wip.txt"], check=True)
    wip.write_bytes(b"unstaged late Sprint work\x00\xff\n")
    registry = deepcopy(planner.runtime.task_queries.verification_registry("A"))
    assert registry["revision"] == 1
    assert [item["method"]["id"] for item in registry["current"]] == (["CHECK", "LATE"] if mixed else ["LATE"])
    assert registry["current"][-1]["method"] == DATA["late_method"]
    foreign = client(project, "independent-owner")
    independent = task(project, "C")
    independent["sprint_id"] = None
    assert foreign.invoke(request("bootstrap", {
        "task": independent, "decision": None, "feedback": None, "rework_stage": None,
    }))["task"] == "C"
    # Establish the Git conservation baseline after the independent public setup.
    material = (proof, wip, wip.read_bytes(), git_state(root))
    opened = reopen(planner, published["revision"])
    owner = client(project, "restart-owner")
    before = owner.runtime.task_queries.record("A")
    newborn = restart(owner, "A", before["version"], request_id="restart-late-sprint-inputs")
    owner.runtime.ownership.acquire_worktree("A")
    assert claim_rows(owner) == DATA["claims_owned"]
    declarations = deepcopy(member["method_inputs"])
    assert newborn["draft"]["method_inputs"] == declarations
    assert newborn["draft"]["methods"] == member["methods"]
    return planner, owner, opened, newborn, registry, declarations, material


def assert_material(material):
    proof, wip, contents, git = material
    assert {path: path.read_bytes() for path in proof} == proof
    assert wip.read_bytes() == contents
    assert git_state(wip.parent.parent) == git


def current_methods(registry):
    values = [deepcopy(item["method"]) for item in registry["current"]]
    values[-1]["argv"] = DATA["replacement_argv"]
    return values


@pytest.mark.parametrize("mixed", [False, True], ids=["late-only", "mixed"])
@pytest.mark.parametrize("entry", ["deferred-ready", "draft-upsert"])
def test_existing_sprint_late_inputs_publish_atomically(project, monkeypatch, mixed, entry):
    planner, owner, opened, newborn, registry, declarations, material = late_member(project, mixed=mixed)
    methods = current_methods(registry)
    checks = {"work": ["CHECK", "LATE"] if mixed else ["LATE"]}
    if entry == "deferred-ready":
        edited = owner.invoke(request("task", {
            "action": "edit", "task_id": "A", "request_id": "edit-late-sprint-methods",
            "expected_revision": newborn["revision"], "patch": {"methods": methods, "checks": checks}, "remove": [],
        }))
        readied = owner.invoke(request("task", {
            "action": "ready", "task_id": "A", "request_id": "ready-late-sprint-methods",
            "expected_revision": edited["revision"],
        }))
        assert readied["status"] == "newborn" and readied["ready"] is True
        assert claim_rows(owner) == DATA["claims_owned"]
        release_member(owner, "late-sprint-deferred")
    else:
        release_member(owner, "late-sprint-before-upsert")
        candidate = {"id": "A", "sprint_id": "S", **deepcopy(newborn["draft"]),
                     "methods": methods, "checks": checks}
        opened = draft(planner, [], revision=opened["revision"], request_id="upsert-existing-late-sprint",
            updates=[{"kind": "upsert_tasks", "tasks": [candidate]}])
        record = planner.runtime.task_queries.record("A")
        assert record["draft"]["methods"] == methods
        assert record["ready"] is True, "Existing late Sprint materialisation must mark the valid member ready"
        assert opened["errors"] == []
    assert planner.runtime.task_queries.record("A")["draft"]["method_inputs"] == declarations
    assert claim_rows(planner) == DATA["claims_released"]
    before = unchanged(planner)
    requirements = planner.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}])
    assert_material(material)
    with pytest.raises(VersionConflict, match="Publish revision changed"):
        publish(planner, opened["revision"] - 1, "stale-late-sprint-publication")
    assert unchanged(planner) == before
    assert claim_rows(planner) == DATA["claims_released"]
    promote = SqliteTaskRepository.promote_prepared

    def interrupt(repository, *args, **kwargs):
        promote(repository, *args, **kwargs)
        raise PoiseError(DATA["interruption"])

    with monkeypatch.context() as fault:
        fault.setattr(SqliteTaskRepository, "promote_prepared", interrupt)
        with pytest.raises(PoiseError, match=DATA["interruption"]):
            publish(planner, opened["revision"], "publish-existing-late-sprint")
    reloaded = client(project, "planner")
    assert unchanged(reloaded) == before
    assert claim_rows(reloaded) == DATA["claims_released"]
    assert reloaded.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}]) == requirements
    assert_material(material)
    result = publish(reloaded, opened["revision"], "publish-existing-late-sprint")
    assert result["status"] == "planned" and result["eligible"] == ["A"]
    record = reloaded.runtime.task_queries.record("A")
    assert record["status"] == "available"
    assert record["contract"]["method_inputs"] == declarations
    assert record["requirements_snapshot"] == newborn["draft"]["requirements_snapshot"]
    saved = unchanged(reloaded)
    for name in ("submissions", "results", "evidence", "artifacts"):
        assert saved[0][name] == before[0][name]
    assert saved[0]["events"][:len(before[0]["events"])] == before[0]["events"]
    assert reloaded.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}]) == requirements
    after = reloaded.runtime.task_queries.verification_registry("A")
    replacement = {**deepcopy(DATA["late_method"]), "argv": DATA["replacement_argv"]}
    expected_entries = [*registry["current"][:-1], {**registry["current"][-1], "method": replacement}]
    delta = {
        "request_id": "publish-existing-late-sprint:A", "expected_revision": 1,
        "operations": [{"kind": "replace", "method_id": "LATE",
                        "registration": {"method": replacement, "stages": ["work"]}}],
        "executable_obligations": [],
    }
    audit = DATA["ready_audit"]
    assert after == {
        **registry, "revision": 2, "current": expected_entries,
        "history": registry["history"] + [{"revision": 1, "entries": registry["current"]}],
        "requests": registry["requests"] + [{
            "request_id": "publish-existing-late-sprint:A", "digest": wire_hash(delta), "revision": 2,
            "audit": {**audit, "receipt_id": wire_hash(audit)},
        }],
    }
    assert claim_rows(reloaded) == DATA["claims_released"]
    assert_material(material)


def test_brand_new_sprint_member_still_requires_complete_creation_inputs(project):
    setup(project)
    planner = client(project, "planner")
    member = task(project, "A")
    member["method_inputs"] = []
    born = draft(planner, [member])
    assert planner.runtime.task_queries.record("A")["ready"] is False
    before = unchanged(planner)
    with pytest.raises(DomainError, match=DATA["binding_error"]):
        publish(planner, born["revision"], "reject-new-sprint-input-mismatch")
    assert unchanged(planner) == before
    assert claims(planner) == {"tasks": [], "worktrees": []}


@pytest.mark.parametrize("invalid", ["missing", "profile"])
def test_existing_sprint_historical_input_refusal_preserves_all_state(project, invalid):
    planner, owner, opened, newborn, registry, declarations, material = late_member(project, mixed=True)
    inputs = deepcopy(declarations)
    if invalid == "missing":
        inputs = []
    else:
        inputs[0]["reference_profile"]["version"] = 999
    edited = owner.invoke(request("task", {
        "action": "edit", "task_id": "A", "request_id": "edit-invalid-historical-sprint-inputs",
        "expected_revision": newborn["revision"],
        "patch": {"methods": current_methods(registry), "checks": {"work": ["CHECK", "LATE"]},
                  "method_inputs": inputs}, "remove": [],
    }))
    before = unchanged(owner)
    requirements = owner.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}])
    with pytest.raises(DomainError, match=DATA["binding_error"] if invalid == "missing" else DATA["profile_error"]):
        owner.invoke(request("task", {
            "action": "ready", "task_id": "A", "request_id": "reject-historical-sprint-inputs",
            "expected_revision": edited["revision"],
        }))
    assert unchanged(owner) == before
    assert claim_rows(owner) == DATA["claims_owned"]
    assert owner.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}]) == requirements
    assert_material(material)


@pytest.mark.parametrize("invalid", ["missing", "profile"])
@pytest.mark.parametrize("entry", ["draft-upsert", "publish"])
def test_existing_sprint_consumers_reject_historical_input_edits(project, invalid, entry):
    planner, owner, opened, newborn, registry, declarations, material = late_member(project, mixed=True)
    original_rows = persisted(planner, "A")
    original_requirements = planner.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}])
    inputs = deepcopy(declarations)
    if invalid == "missing":
        inputs = []
    else:
        inputs[0]["reference_profile"]["version"] = 999
    # Input-only edits keep original CHECK methods/checks beside authoritative
    # CHECK+LATE registry. The four positives separately materialize all methods.
    if entry == "publish":
        edited = owner.invoke(request("task", {
            "action": "edit", "task_id": "A", "request_id": "edit-only-sprint-historical-inputs",
            "expected_revision": newborn["revision"], "patch": {"method_inputs": inputs}, "remove": [],
        }))
        assert edited["ready"] is False
        release_member(owner, "historical-input-publication")
    else:
        release_member(owner, "historical-input-upsert")
        candidate = {"id": "A", "sprint_id": "S", **deepcopy(newborn["draft"]), "method_inputs": inputs}
        opened = draft(planner, [], revision=opened["revision"], request_id="upsert-sprint-historical-inputs",
            updates=[{"kind": "upsert_tasks", "tasks": [candidate]}])
        assert opened["errors"] == ["Task creation intent: " + DATA["historical_refusals"][invalid]]
    current = planner.runtime.task_queries.record("A")
    assert current["ready"] is False
    assert current["draft"]["methods"] == newborn["draft"]["methods"]
    assert current["draft"]["checks"] == {"work": ["CHECK"]}
    assert current["draft"]["method_inputs"] == inputs
    assert current["draft"]["requirements_snapshot"] == newborn["draft"]["requirements_snapshot"]
    assert planner.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}]) == original_requirements
    after_edit = persisted(planner, "A")
    for name in ("methods", "workflow", "execution", "results", "proofs", "submissions", "evidence", "layers"):
        assert after_edit[name] == original_rows[name]
    assert after_edit["events"][:len(original_rows["events"])] == original_rows["events"]
    historical = planner.runtime.task_queries.verification_registry("A")
    assert historical["active_contract"] is False and historical["current"] == []
    assert historical["historical_registry"] == {
        "revision": 1, "entries": registry["current"], "history": registry["history"],
        "requests": registry["requests"], "executable_obligations": [],
    }
    before = unchanged(planner)
    registry_rows = persisted(planner, "A")
    requirements = planner.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}])
    assert claim_rows(planner) == DATA["claims_released"]
    assert_material(material)
    with pytest.raises(DomainError) as refusal:
        publish(planner, opened["revision"], "reject-existing-sprint-historical-inputs")
    assert str(refusal.value) == "Task creation intent: " + DATA["historical_refusals"][invalid]
    reloaded = client(project, "planner")
    assert unchanged(reloaded) == before
    assert reloaded.runtime.task_queries.verification_registry("A") == historical
    assert persisted(reloaded, "A") == registry_rows
    assert reloaded.runtime.requirements_commands.query([{"id": "registry", "kind": "registry"}]) == requirements
    assert claim_rows(reloaded) == DATA["claims_released"]
    assert_material(material)
