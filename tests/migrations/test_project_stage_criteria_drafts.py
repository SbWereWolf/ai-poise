"""Integration subject: explicit draft migration through real CLI/persistence.

Literal source/annotation fixtures own expectations; direct SQL/file observations
prove preservation. Production factories, transformers and serializers are not
oracles. Existing full-contract checks remain a separate GREEN guard.
"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from tests.migrations.project_criteria_draft_helpers import (
    DATA, assert_contextual_refusal, assert_drafts_preserved, bind_resource,
    draft_case, fresh_backup, metadata_digest, replace_metadata, resource, rows,
)
from tests.migrations.project_criteria_helpers import (
    fresh_work_read, observe, project_receipts, run_migration,
)


def successful(case, *, fresh=True, patch=None):
    code, result, stderr = run_migration(case, fresh=fresh, patch=patch)
    assert code == 0, (result, stderr)
    assert result["status"] == "migrated"
    assert result["request_id"] == "project-owned-stage-criteria"
    assert result["replayed"] is False
    return result


def refused_without_effects(case, context):
    before = observe(case)
    code, result, stderr = run_migration(case, fresh=True)
    assert observe(case) == before
    assert_contextual_refusal(code, result, stderr, context)


def test_untyped_draft_preserved(project):
    case = draft_case(project, "untyped")
    before = rows(case)
    result = successful(case)
    assert_drafts_preserved(case, before, result)


def test_typed_partial_preserved(project):
    case = draft_case(project, "typed")
    before = rows(case)
    result = successful(case)
    assert_drafts_preserved(case, before, result)


def test_mixed_inventory_fresh_read_replay(project):
    case = draft_case(project)
    before = observe(case)
    source_rows = rows(case)
    result = successful(case)
    assert_drafts_preserved(case, source_rows, result)
    expected_ids = {("workflow_definition", "development"), ("workflow_definition", "design"),
                    ("newborn_task", "N1"), ("executable_task", "T1"),
                    ("executable_task", "T2"), ("newborn_draft", "U1"),
                    ("newborn_draft", "P1")}
    assert {(r["kind"], r.get("task_id", r.get("goal_type"))) for r in result["resources"]} == expected_ids
    assert len(result["resources"]) == 7
    after = observe(case)
    for table in ("events", "submissions", "evidence", "execution", "methods", "proofs", "content"):
        assert after["task"][table] == before["task"][table]
    for key in ("config", "setup_receipt", "catalogue", "catalogue_bytes", "unrelated", "owner_inputs"):
        assert after[key] == before[key]
    for path, content in before["publication"].items():
        assert after["publication"][path] == content
    assert len(project_receipts(case)) == 1
    query = fresh_work_read(case)
    assert query.returncode == 0, query.stderr
    overview = json.loads(query.stdout)
    assert {t["id"] for t in overview["standalone_tasks"]} == {"N1", "T1", "T2", "U1", "P1"}
    code, replay, stderr = run_migration(case, fresh=True)
    assert code == 0, stderr
    assert replay == {**result, "replayed": True}
    assert observe(case) == after


@pytest.mark.parametrize("fault", [
    "workflow_null", "workflow_array", "type_without_workflow", "contracts_null",
    "contracts_object", "contracts_empty", "contracts_incomplete",
])
def test_malformed_present_draft_rejected(project, fault):
    case = draft_case(project)
    task_id = "U1" if fault in {"workflow_null", "workflow_array", "type_without_workflow"} else "P1"
    metadata = json.loads(rows(case)[task_id][7])
    if fault == "workflow_null":
        metadata["workflow"] = None
    elif fault == "workflow_array":
        metadata["workflow"] = []
    elif fault == "type_without_workflow":
        metadata["newborn"]["draft"]["goal_type"] = "development"
    else:
        contracts = {"contracts_null": None, "contracts_object": {}, "contracts_empty": [],
                     "contracts_incomplete": [{"stage_id": "tests", "allowed_paths": [],
                                               "entry_requirements": [], "exit_requirements": []}]}
        metadata["newborn"]["draft"]["stage_contracts"] = contracts[fault]
    replace_metadata(case, task_id, metadata)
    bind_resource(case, task_id)
    fresh_backup(case)
    refused_without_effects(case, ["draft"])


@pytest.mark.parametrize("task_id", ["U1", "P1"])
def test_complete_inventory_required(project, task_id):
    case = draft_case(project)
    case["packet"]["mapping"].remove(resource(case, task_id))
    refused_without_effects(case, ["inventory"])


@pytest.mark.parametrize("field", ["revision", "metadata"])
def test_draft_source_drift_before_effects(project, field):
    case = draft_case(project)
    if field == "revision":
        with sqlite3.connect(case["database"]) as connection:
            connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", ("U1",))
    else:
        metadata = json.loads(rows(case)["U1"][7])
        metadata["goal"] = "Concurrent source change after backup."
        replace_metadata(case, "U1", metadata)
    refused_without_effects(case, ["backup"])


@pytest.mark.parametrize("field", ["digest", "revision"])
def test_supplied_draft_binding_must_match(project, field):
    case = draft_case(project)
    supplied = resource(case, "U1")
    if field == "digest":
        supplied["expected_metadata_digest"] = "0" * 64
        assert supplied["expected_metadata_digest"] != metadata_digest(json.loads(rows(case)["U1"][7]))
    else:
        supplied["expected_revision"] += 1
    # The actual DB and named backup are identical; only the supplied field is stale.
    refused_without_effects(case, ["digest" if field == "digest" else "revision"])


@pytest.mark.parametrize("field,value", [
    ("expected_revision", True), ("expected_revision", -1),
    ("expected_metadata_digest", "a" * 63), ("expected_metadata_digest", "g" * 64),
    ("expected_metadata_digest", "A" * 64),
], ids=["boolean-revision", "negative-revision", "short-digest", "nonhex-digest", "uppercase-digest"])
def test_strict_draft_source_field_shapes(project, field, value):
    case = draft_case(project)
    resource(case, "U1")[field] = value
    refused_without_effects(case, ["revision" if field == "expected_revision" else "digest"])


@pytest.mark.parametrize("task_id", ["N1", "T1"])
def test_full_or_executable_source_is_not_partial_draft(project, task_id):
    case = draft_case(project)
    original = resource(case, task_id)
    row = rows(case)[task_id]
    case["packet"]["mapping"][case["packet"]["mapping"].index(original)] = {
        "kind": "newborn_draft", "task_id": task_id, "expected_revision": row[5],
        "expected_metadata_digest": metadata_digest(json.loads(row[7])),
        "stages": deepcopy(DATA["expected_stages"]),
    }
    refused_without_effects(case, ["draft"])


@pytest.mark.parametrize("fault", ["untyped_nonempty", "typed_empty", "missing", "duplicate", "order"])
def test_draft_stage_mapping_matches_actual_source(project, fault):
    case = draft_case(project)
    task_id = "U1" if fault == "untyped_nonempty" else "P1"
    mapping = resource(case, task_id)
    if fault == "untyped_nonempty":
        mapping["stages"] = deepcopy(DATA["expected_stages"])
    elif fault == "typed_empty":
        mapping["stages"] = []
    elif fault == "missing":
        mapping["stages"].pop()
    elif fault == "duplicate":
        mapping["stages"].append(deepcopy(mapping["stages"][0]))
    else:
        mapping["stages"].reverse()
    refused_without_effects(case, ["stage"])


@pytest.mark.parametrize("fault", ["missing", "mismatch"])
def test_typed_partial_type_binding_required(project, fault):
    case = draft_case(project)
    metadata = json.loads(rows(case)["P1"][7])
    if fault == "missing":
        metadata["newborn"]["draft"].pop("goal_type")
    else:
        metadata["newborn"]["draft"]["goal_type"] = "design"
    replace_metadata(case, "P1", metadata)
    bind_resource(case, "P1")
    fresh_backup(case)
    refused_without_effects(case, ["type"])


def test_draft_identity_cannot_be_duplicated_across_kinds(project):
    case = draft_case(project)
    original = deepcopy(resource(case, "N1"))
    original["task_id"] = "P1"
    original["expected_revision"] = rows(case)["P1"][5]
    case["packet"]["mapping"].append(original)
    refused_without_effects(case, ["duplicate"])


@pytest.mark.parametrize("task_id", ["U1", "P1"])
def test_draft_source_drift_after_publication(project, monkeypatch, task_id):
    case = draft_case(project)
    original_rows = rows(case)
    from poise.infrastructure.project_config import FileProjectConfigUpdate
    original = FileProjectConfigUpdate.publish_stage_criteria
    reached = []
    changed_rows = []

    def publish_then_drift(self, publication):
        result = original(self, publication)
        receipts = project_receipts(case)
        assert len(receipts) == 1
        assert receipts[0][1]["result"]["status"] == "updated"
        assert all("purpose" in s for p in case["workflow_paths"] for s in json.loads(p.read_text())["stages"])
        reached.append(receipts[0][0].read_bytes())
        metadata = json.loads(rows(case)[task_id][7])
        if task_id == "U1":
            metadata["goal"] = "Concurrent draft payload after publication."
        else:
            metadata["workflow"]["stages"][0]["instruction"] = "Concurrent process change after publication."
        replace_metadata(case, task_id, metadata)
        changed_rows.append(rows(case))
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(FileProjectConfigUpdate, "publish_stage_criteria", publish_then_drift)
        code, partial, stderr = run_migration(case, patch=scoped)
    assert len(reached) == 1, (partial, stderr)
    assert_contextual_refusal(code, partial, stderr, ["drift"], "recovery_required")
    assert rows(case) == changed_rows[0]
    assert all(rows(case)[key] == row for key, row in original_rows.items() if key != task_id)
    assert {r["kind"] for r in partial["resources"]} == {"workflow_definition"}
    assert not any(json.loads(row[5]).get("phase") == "completed" for row in observe(case)["task"]["journal"])
    after = observe(case)
    code, retry, stderr = run_migration(case, fresh=True)
    assert_contextual_refusal(code, retry, stderr, ["changed"], "recovery_required")
    assert retry["receipt"] == partial["receipt"]
    assert observe(case) == after
    assert project_receipts(case)[0][0].read_bytes() == reached[0]


def test_draft_publication_fault_resume(project, monkeypatch):
    case = draft_case(project)
    before = rows(case)
    from poise.infrastructure import project_config
    original = project_config.atomic_write
    reached = []

    def write_then_interrupt(path, content, mode):
        original(path, content, mode)
        if json.loads(content).get("schema") == "project-config-receipt-1":
            reached.append((Path(path), Path(path).read_bytes()))
            raise OSError("Fixture interruption after durable project receipt.")

    with monkeypatch.context() as scoped:
        scoped.setattr(project_config, "atomic_write", write_then_interrupt)
        code, partial, stderr = run_migration(case, patch=scoped)
    assert len(reached) == 1, (partial, stderr)
    assert_contextual_refusal(code, partial, stderr, ["fixture interruption"], "recovery_required")
    assert rows(case) == before
    assert partial["resources"] == []  # Durable receipt still pending/unverified.
    result = successful(case)
    assert result["receipt"] == partial["receipt"]
    assert_drafts_preserved(case, before, result)
    assert reached[0][0].read_bytes() == reached[0][1]
    after = observe(case)
    code, replay, stderr = run_migration(case, fresh=True)
    assert code == 0, stderr
    assert replay == {**result, "replayed": True}
    assert observe(case) == after
