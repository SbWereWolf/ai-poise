"""Integration subject: public migration, actual project publisher and Task DB.

Expected stage values are test-owned literals. Persistence is observed directly;
the production migration/serializer does not calculate the expected result.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from tests.migrations.project_criteria_helpers import (
    assert_fixture_consistent, fresh_locked_migration, fresh_work_read, historical_project_owned, observe,
    project_receipts, run_migration,
)
from tests.migrations.stage_criteria_helpers import identity


CRITERIA = {
    "requirements": ["Результат этапа доступен проверяющему."],
    "definition_of_ready": [], "definition_of_done": [],
}


def assert_success(case, code, result, stderr):
    assert code == 0, (result, stderr)
    assert result["status"] == "migrated"
    assert result["request_id"] == "project-owned-stage-criteria"
    assert len(result["resources"]) == 5


def assert_preserved(before, after):
    old, new = before["task"], after["task"]
    for table in ("events", "submissions", "evidence", "execution", "methods", "proofs", "content"):
        assert new[table] == old[table]
    for row in old["journal"]:
        assert row in new["journal"]
    for row in new["journal"]:
        if row not in old["journal"]:
            assert row[2:5] == ("task-workflow-migrate", None, "task_stage_criteria_migration")
            recorded = json.loads(row[5])
            assert recorded["request_id"] == "project-owned-stage-criteria"
            assert recorded["phase"] in {"intent", "published", "completed"}
    assert len(new["workflows"]) == len(old["workflows"])
    for (old_task, old_raw), (new_task, new_raw) in zip(old["workflows"], new["workflows"], strict=True):
        assert new_task == old_task
        for key, value in json.loads(old_raw).items():
            assert json.loads(new_raw)[key] == value
    assert len(new["tasks"]) == len(old["tasks"])
    for prior, current in zip(old["tasks"], new["tasks"], strict=True):
        assert current[:7] == prior[:7]
        old_metadata, new_metadata = json.loads(prior[7]), json.loads(current[7])
        for key, value in old_metadata.items():
            if key not in ("workflow", "contract", "newborn"):
                assert new_metadata[key] == value
        workflow = deepcopy(new_metadata["workflow"])
        for stage in workflow["stages"]:
            stage.pop("purpose")
            stage.pop("criteria_authority")
        assert workflow == old_metadata["workflow"]
        if prior[1] == "newborn":
            container = deepcopy(new_metadata["newborn"])
            contract = container["draft"]
            for stage in contract["stage_contracts"]:
                assert stage.pop("criteria") == CRITERIA
            assert container == old_metadata["newborn"]
        else:
            contract = deepcopy(new_metadata["contract"])
            for stage in contract["stage_contracts"]:
                assert stage.pop("criteria") == CRITERIA
            assert contract == old_metadata["contract"]
    for key in ("config", "setup_receipt", "catalogue", "catalogue_bytes", "unrelated", "owner_inputs"):
        assert after[key] == before[key]
    for path, content in before["publication"].items():
        assert after["publication"][path] == content
    additions = set(after["publication"]) - set(before["publication"])
    assert len(additions) == 1
    receipt = json.loads(after["publication"][additions.pop()])
    assert receipt["schema"] == "project-config-receipt-1"
    assert receipt["result"]["config_path"] == json.loads(before["setup_receipt"])["result"]["config_path"]
    assert receipt["result"]["prior_revision"] == json.loads(next(iter(before["publication"].values())))["result"]["revision"]
    assert receipt["result"]["revision"] != receipt["result"]["prior_revision"]


def assert_literals(case):
    for path in case["workflow_paths"]:
        stages = json.loads(path.read_text())["stages"]
        assert [{"stage_id": s["id"], "purpose": s["purpose"],
                 "criteria_authority": s["criteria_authority"]} for s in stages] == case["mapping_stages"]
    with sqlite3.connect(case["database"]) as connection:
        rows = connection.execute("SELECT id,metadata FROM tasks ORDER BY id").fetchall()
    for task, raw in rows:
        metadata = json.loads(raw)
        assert [{"stage_id": stage["id"], "purpose": stage["purpose"],
                 "criteria_authority": stage["criteria_authority"]}
                for stage in metadata["workflow"]["stages"]] == case["mapping_stages"]
        contract = metadata["newborn"]["draft"] if task == "N1" else metadata["contract"]
        assert [(stage["stage_id"], stage["criteria"]) for stage in contract["stage_contracts"]] == [
            (stage["stage_id"], CRITERIA) for stage in case["mapping_stages"]]
        book = metadata["stage_criteria"]["stages"]
        for stage in case["mapping_stages"]:
            actual = book[stage["stage_id"]]
            assert actual["purpose"] == stage["purpose"]
            assert actual["revision"] == 0
            assert actual["criteria"] == {
                "requirements": ["Результат этапа доступен проверяющему."],
                "definition_of_ready": [], "definition_of_done": [],
            }
            assert actual["source"]["kind"] == "historical"
            state = "unstarted" if task == "N1" else "started" if task == "T2" and stage["stage_id"] == "tests" else "unknown"
            assert actual["execution"]["state"] == state


def test_native_project_owner_migrates_preserves_history_and_fresh_replay(project):
    case = historical_project_owned(project)
    before = observe(case)
    legacy_code, legacy, legacy_stderr = run_migration(case, fresh=True, select_project_owner=False)
    assert legacy_code == 2, legacy_stderr
    assert legacy == {"status": "rejected", "reason": "Configured workflow and registered publication target differ"}
    assert observe(case) == before
    code, result, stderr = run_migration(case, fresh=True)
    assert_success(case, code, result, stderr)
    assert result["replayed"] is False
    assert_preserved(before, observe(case))
    assert_literals(case)
    receipts = project_receipts(case)
    assert len(receipts) == 1
    _, receipt = receipts[0]
    assert receipt["result"]["prior_revision"] == case["source_revision"]
    assert receipt["result"]["config_path"] == str(case["config"])
    assert receipt["result"]["revision"] != case["source_revision"]
    after = observe(case)
    replay_code, replay, err = run_migration(case, fresh=True)
    assert_success(case, replay_code, replay, err)
    assert replay == {**result, "replayed": True}
    assert observe(case) == after
    assert project_receipts(case) == receipts
    read = fresh_work_read(case)
    assert read.returncode == 0, read.stderr
    assert [task["id"] for task in json.loads(read.stdout)["standalone_tasks"]] == ["N1", "T1", "T2"]


@pytest.mark.parametrize("fault,reason", [
    ("missing_receipt", "Project setup receipt is missing"),
    ("foreign_receipt", "Project setup receipt belongs to another configuration"),
    ("foreign_registry", "Project registry binding differs from the selected configuration"),
    ("unknown_revision", "Live project revision is not known"),
    ("workflow_drift", "Process source revision mismatch"),
    ("settings_binding", "Project settings do not own the selected configuration"),
    ("missing_resource", "Mapping does not match the complete process and nonterminal Task inventory"),
    ("missing_stage", "Migration must map every historical stage exactly once in order"),
    ("task_drift", "Task DB differs from the named backup"),
    ("foreign_pending", "Project has an unrelated pending publication"),
])
def test_project_owner_conflict_refuses_before_effects(project, fault, reason):
    case = historical_project_owned(project)
    if fault == "missing_receipt":
        case["setup_receipt"].unlink()
    elif fault in ("foreign_receipt", "unknown_revision"):
        receipt_path = case["setup_receipt"] if fault == "foreign_receipt" else case["history_receipt"]
        saved = json.loads(receipt_path.read_text())
        if fault == "foreign_receipt":
            saved["result"]["config_path"] = str(case["config"].parent / "foreign.json")
        else:
            saved["result"]["revision"] = "0" * 64
        receipt_path.write_text(json.dumps(saved))
    elif fault == "foreign_registry":
        path = project["root"] / "state/project-registry.json"
        value = json.loads(path.read_text())
        value["projects"][project["cfg"]["project"]]["config_path"] = "foreign/project.json"
        path.write_text(json.dumps(value))
    elif fault == "workflow_drift":
        path = case["workflow_paths"][0]
        value = json.loads(path.read_text())
        value["stages"][0]["instruction"] = "Unrecorded process modification."
        path.write_text(json.dumps(value))
    elif fault == "settings_binding":
        path = case["settings"]
        value = json.loads(path.read_text())
        value["root"] = "other-installation"
        (path.parent / "other-installation").mkdir()
        path.write_text(json.dumps(value))
    elif fault == "foreign_pending":
        source = json.loads(case["config"].read_text())
        components = {"config": identity(source), "workflows": {
            goal: identity(json.loads((case["bundle"] / relative).read_text()))
            for goal, relative in source["workflows"].items()}}
        pending = {"schema": "project-config-pending-1", "request_id": "foreign-publication",
                   "request_digest": identity({"request_id": "foreign-publication"}),
                   "before": components, "after": components}
        (project["root"] / "operations/foreign.json.pending").write_text(json.dumps(pending))
    elif fault == "missing_resource":
        case["packet"]["mapping"].pop(1)
    elif fault == "missing_stage":
        case["packet"]["mapping"][0]["stages"].pop()
    else:
        with sqlite3.connect(case["database"]) as connection:
            connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", ("T1",))
    before = observe(case)
    code, value, stderr = run_migration(case, fresh=True)
    assert code == 2
    assert value.get("status") == "rejected", stderr
    assert value["reason"] == reason
    assert observe(case) == before
    assert project_receipts(case) == []


@pytest.mark.parametrize("boundary", ["first_process", "before_receipt", "after_receipt"])
def test_project_publication_fault_resumes_original_intent(project, monkeypatch, boundary):
    case = historical_project_owned(project)
    if boundary == "after_receipt":
        other_config = {**json.loads(case["config"].read_text()), "project": "unrelated"}
        components = {"config": identity(other_config), "workflows": {
            json.loads(path.read_text())["goal_type"]: identity(json.loads(path.read_text()))
            for path in case["workflow_paths"]}}
        request = {**case["update_request"], "request_id": "unrelated-publication",
                   "config_path": str(case["bundle"].parent / "unrelated/project.json")}
        unrelated = project["root"] / "operations/unrelated-project.json.pending"
        unrelated.write_text(json.dumps({"schema": "project-config-pending-1",
            "request_id": request["request_id"], "request_digest": identity(request),
            "before": components, "after": components}))
    before = observe(case)
    from poise.infrastructure import project_config
    original = project_config.atomic_write
    written = []

    def interrupt(path, content, mode):
        path = Path(path)
        if boundary == "before_receipt" and json.loads(content).get("schema") == "project-config-receipt-1":
            raise OSError("Fixture interruption before project receipt.")
        original(path, content, mode)
        if boundary == "after_receipt" and json.loads(content).get("schema") == "project-config-receipt-1":
            raise OSError("Fixture interruption after durable project receipt.")
        if path in case["workflow_paths"]:
            written.append(path)
            if boundary == "first_process" and len(written) == 1:
                raise OSError("Fixture interruption after first process.")

    with monkeypatch.context() as scoped:
        scoped.setattr(project_config, "atomic_write", interrupt)
        code, value, stderr = run_migration(case, patch=scoped)
    assert code == 2, stderr
    assert value.get("status") == "recovery_required", (value, stderr)
    assert "Fixture interruption" in value["reason"]
    assert len(written) == (1 if boundary == "first_process" else 2)
    assert observe(case)["task"]["tasks"] == before["task"]["tasks"]
    durable_receipt = None
    if boundary == "after_receipt":
        durable_receipt, pending = assert_durable_receipt_pending(case, before)
    code, completed, err = run_migration(case, fresh=True)
    assert_success(case, code, completed, err)
    assert_preserved(before, observe(case))
    assert_literals(case)
    assert len(project_receipts(case)) == 1
    if boundary == "after_receipt":
        receipt_path, receipt_bytes = durable_receipt
        assert receipt_path.read_bytes() == receipt_bytes
        assert not pending.exists()
        assert observe(case)["publication"] == {**before["publication"], str(receipt_path): receipt_bytes}
        completed_state = observe(case)
        code, replay, err = run_migration(case, fresh=True)
        assert_success(case, code, replay, err)
        assert replay == {**completed, "replayed": True}
        assert observe(case) == completed_state


def assert_durable_receipt_pending(case, before):
    receipts = project_receipts(case)
    assert len(receipts) == 1
    receipt_path, receipt = receipts[0]
    assert receipt["result"]["prior_revision"] == case["source_revision"]
    assert receipt["result"]["config_path"] == str(case["config"])
    workflows = {}
    for path in case["workflow_paths"]:
        document = json.loads(path.read_text())
        workflows[document["goal_type"]] = document
        assert [{"stage_id": stage["id"], "purpose": stage["purpose"],
                 "criteria_authority": stage["criteria_authority"]}
                for stage in document["stages"]] == case["mapping_stages"]
    config = json.loads(case["config"].read_text())
    assert receipt["result"]["revision"] == identity({"config": config, "workflows": workflows})
    pending_path = receipt_path.with_name(receipt_path.name + ".pending")
    pending = json.loads(pending_path.read_text())
    assert pending["schema"] == "project-config-pending-1"
    assert pending["request_id"] == receipt["request_id"]
    assert pending["request_digest"] == receipt["request_digest"]
    assert pending["before"]["config"] == pending["after"]["config"] == identity(config)
    assert pending["before"]["workflows"] == {
        json.loads(raw)["goal_type"]: identity(json.loads(raw)) for raw in before["source"].values()}
    assert pending["after"]["workflows"] == {goal: identity(doc) for goal, doc in workflows.items()}
    assert observe(case)["task"]["tasks"] == before["task"]["tasks"]
    return (receipt_path, receipt_path.read_bytes()), pending_path


@pytest.mark.parametrize("fault,reason", [
    ("request_identity", "Different request uses the pending request receipt"),
    ("before_candidate", "Project pending publication candidate changed"),
    ("after_candidate", "Project pending publication candidate changed"),
])
def test_completed_receipt_recovery_preserves_changed_pending(project, monkeypatch, fault, reason):
    case = historical_project_owned(project)
    before = observe(case)
    from poise.infrastructure import project_config
    original = project_config.atomic_write

    def interrupt(path, content, mode):
        original(path, content, mode)
        if json.loads(content).get("schema") == "project-config-receipt-1":
            raise OSError("Fixture interruption after durable project receipt.")

    with monkeypatch.context() as scoped:
        scoped.setattr(project_config, "atomic_write", interrupt)
        code, value, stderr = run_migration(case, patch=scoped)
    assert code == 2, stderr
    assert value["status"] == "recovery_required"
    assert value["reason"] == "Fixture interruption after durable project receipt."
    durable, pending_path = assert_durable_receipt_pending(case, before)
    pending = json.loads(pending_path.read_text())
    if fault == "request_identity":
        pending["request_digest"] = "0" * 64
    else:
        component = "before" if fault == "before_candidate" else "after"
        pending[component]["workflows"]["development"] = "0" * 64
    pending_path.write_text(json.dumps(pending))
    altered = observe(case)
    code, refusal, stderr = run_migration(case, fresh=True)
    assert code == 2, stderr
    assert refusal["status"] == "recovery_required"
    assert refusal["reason"] == reason
    assert observe(case) == altered
    receipt_path, receipt_bytes = durable
    assert receipt_path.read_bytes() == receipt_bytes
    assert pending_path.exists()


def interrupt_first_workflow(case, monkeypatch):
    from poise.infrastructure import project_config
    original = project_config.atomic_write

    def interrupt(path, content, mode):
        original(path, content, mode)
        if Path(path) == case["workflow_paths"][0]:
            raise OSError("Fixture interruption after process.")

    with monkeypatch.context() as scoped:
        scoped.setattr(project_config, "atomic_write", interrupt)
        code, partial, stderr = run_migration(case, patch=scoped)
    assert code == 2
    assert partial.get("status") == "recovery_required", (partial, stderr)
    assert "Fixture interruption after process." in partial["reason"]
    return partial


@pytest.mark.parametrize("fault,status,reason", [
    ("workflow_edit", "recovery_required", "Unmanaged process change during recovery"),
    ("pending_intent", "recovery_required", "Different request uses the pending request receipt"),
    ("pending_candidate", "recovery_required", "Project pending publication candidate changed"),
    ("owner", "rejected", "Migration publication owner changed"),
    ("request", "rejected", "Migration request_id belongs to another intent"),
])
def test_partial_publication_rejects_changed_recovery_inputs(project, monkeypatch, fault, status, reason):
    case = historical_project_owned(project)
    interrupt_first_workflow(case, monkeypatch)
    if fault == "workflow_edit":
        path = case["workflow_paths"][0]
        value = json.loads(path.read_text())
        value["stages"][0]["instruction"] = "A valid-shaped intervening operator edit."
        path.write_text(json.dumps(value))
    elif fault in ("pending_intent", "pending_candidate"):
        candidates = [(Path(path), json.loads(raw)) for path, raw in observe(case)["publication"].items()
                      if path.endswith(".pending")]
        assert len(candidates) == 1
        path, pending = candidates[0]
        assert pending["schema"] == "project-config-pending-1"
        if fault == "pending_intent":
            pending["request_digest"] = "0" * 64
        else:
            pending["after"]["workflows"]["development"] = "0" * 64
        path.write_text(json.dumps(pending))
    elif fault == "owner":
        selected = case["settings"].with_name("other-owner.json")
        settings = json.loads(case["settings"].read_text())
        settings["lock"] = "state/other-project-setup.lock"
        selected.write_text(json.dumps(settings))
        case["settings"] = selected
        case["owner_inputs"].append(selected)
    else:
        case["packet"]["authorization"] = "A different payload must not reuse this intent."
    before = observe(case)
    code, blocked, stderr = run_migration(case, fresh=True)
    assert code == 2, stderr
    assert blocked["status"] == status
    assert blocked["reason"] == reason
    assert observe(case) == before


def test_final_task_transaction_rolls_back_and_recovers_published_project(project, monkeypatch):
    case = historical_project_owned(project)
    before = observe(case)
    from poise.infrastructure import task_workflow_migration
    constructor = task_workflow_migration.SqliteTaskWorkflowMigration.__init__

    def initialize(self, *args, **kwargs):
        constructor(self, *args, **kwargs)

        def interrupt(task_id):
            raise OSError("Fixture interruption inside final Task transaction.")

        self.after_update = interrupt

    with monkeypatch.context() as scoped:
        scoped.setattr(task_workflow_migration.SqliteTaskWorkflowMigration, "__init__", initialize)
        code, partial, stderr = run_migration(case, patch=scoped)
    assert code == 2, stderr
    assert partial.get("status") == "recovery_required", (partial, stderr)
    assert "inside final Task transaction" in partial["reason"]
    assert observe(case)["task"]["tasks"] == before["task"]["tasks"]
    assert len(project_receipts(case)) == 1
    code, result, stderr = run_migration(case, fresh=True)
    assert_success(case, code, result, stderr)
    assert_preserved(before, observe(case))
    assert_literals(case)


@pytest.mark.parametrize("recovering", [False, True])
def test_task_migration_does_not_acquire_shared_project_lock(project, monkeypatch, recovering):
    case = historical_project_owned(project)
    if recovering:
        interrupt_first_workflow(case, monkeypatch)
    from poise.infrastructure import locking, project_config, stage_criteria_migration
    actual = locking.exclusive_lock
    settings = json.loads(case["settings"].read_text())
    project_lock = (case["settings"].parent / settings["lock"]).resolve()
    config = json.loads(case["config"].read_text())
    task_lock = (case["config"].parent / config["paths"]["state"] / config["paths"]["lock"]).resolve()
    if recovering:
        probe = fresh_locked_migration(case)
        assert probe.returncode == 0, probe.stderr
        observed = json.loads(probe.stdout)
        assert_success(case, observed["code"], observed["result"], observed["stderr"])
        assert str(project_lock) not in observed["events"]
        assert str(task_lock) in observed["events"]
        return
    events, held = [], set()

    @contextmanager
    def tracked(path, seconds, poll):
        selected = Path(path).resolve()
        assert selected not in held, "Recursive acquisition of a held migration lock."
        assert selected != project_lock, "Migration acquired the shared project lock."
        with actual(path, seconds, poll):
            held.add(selected)
            events.append(selected)
            try:
                yield
            finally:
                held.remove(selected)

    with monkeypatch.context() as scoped:
        for module in (locking, project_config, stage_criteria_migration):
            scoped.setattr(module, "exclusive_lock", tracked)
        code, result, err = run_migration(case, patch=scoped)
    assert_success(case, code, result, err)
    assert project_lock not in events
    assert task_lock in events
    assert held == set()


def test_obsolete_project_lock_does_not_block_migration(project):
    case = historical_project_owned(project)
    from poise.infrastructure.locking import exclusive_lock
    settings = json.loads(case["settings"].read_text())
    project_lock = case["settings"].parent / settings["lock"]
    before = observe(case)
    with exclusive_lock(project_lock, settings["lock_seconds"], settings["lock_poll_seconds"]):
        code, result, stderr = run_migration(case, fresh=True)
    assert_success(case, code, result, stderr)
    assert_preserved(before, observe(case))
    assert_literals(case)


def test_historical_fixture_layout_and_source_identities(project):
    case = historical_project_owned(project)
    assert_fixture_consistent(case)


@pytest.mark.parametrize("input_index", range(7))
def test_preservation_observer_detects_owner_input_changes(project, input_index):
    case = historical_project_owned(project)
    before = observe(case)
    path = case["owner_inputs"][input_index]
    value = json.loads(path.read_text())
    value["observer_sentinel"] = "Changed fixture owner input."
    path.write_text(json.dumps(value))
    assert observe(case) != before


def test_preservation_oracle_accepts_literal_criteria_and_rejects_loss(project):
    case = historical_project_owned(project)
    before = observe(case)
    after = deepcopy(before)
    rows = []
    for row in after["task"]["tasks"]:
        metadata = json.loads(row[7])
        for stage, literal in zip(metadata["workflow"]["stages"], case["mapping_stages"], strict=True):
            stage["purpose"] = literal["purpose"]
            stage["criteria_authority"] = literal["criteria_authority"]
        contract = metadata["newborn"]["draft"] if row[1] == "newborn" else metadata["contract"]
        for stage in contract["stage_contracts"]:
            stage["criteria"] = deepcopy(CRITERIA)
        rows.append((*row[:7], json.dumps(metadata)))
    after["task"]["tasks"] = rows
    after["publication"]["literal-oracle-receipt.json"] = json.dumps({
        "schema": "project-config-receipt-1", "result": {
            "config_path": str(case["config"]), "prior_revision": case["source_revision"],
            "revision": "1" * 64}}).encode()
    assert_preserved(before, after)
    bad = deepcopy(after)
    row = bad["task"]["tasks"][0]
    metadata = json.loads(row[7])
    metadata["newborn"]["draft"]["stage_contracts"][0]["criteria"]["requirements"] = []
    bad["task"]["tasks"][0] = (*row[:7], json.dumps(metadata))
    with pytest.raises(AssertionError):
        assert_preserved(before, bad)
    bad = deepcopy(after)
    bad["publication"][str(case["history_receipt"])] = b"{}"
    with pytest.raises(AssertionError):
        assert_preserved(before, bad)
