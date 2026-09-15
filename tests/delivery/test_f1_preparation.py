import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PREP = ROOT / "docs/migrations/f1-preparation-2026-09-15.json"
CLOSURE = ROOT / "docs/migrations/sprint-dependency-closure-2026-09-15.json"
CURRENT = ROOT / "docs/migrations/current-state.md"
SPRINT_ID = "SPRINT-ERP-MIGRATION-F1-ROUTING-TOOLS"


def _load(path: Path):
    return json.loads(path.read_text())


def test_f1_preparation_matches_dependency_closed_draft_graph():
    prep = _load(PREP)
    closure = _load(CLOSURE)["results"][SPRINT_ID]
    sprint = prep["sprint"]
    assert sprint["id"] == SPRINT_ID
    assert sprint["revision"] == closure["revision"]
    assert sprint["state"] == "draft"
    assert sprint["tasks"] == closure["tasks"]
    assert sprint["edges"] == closure["edges"]
    assert sprint["topological_order"] == closure["topological_order"]
    assert set(sprint["tasks"]) == {item["task"] for item in prep["decisions"]}


def test_f1_preparation_records_reuse_and_implementation_boundaries():
    decisions = {item["node"]: item for item in _load(PREP)["decisions"]}
    assert decisions["C002"]["decision"] == "reuse-no-change"
    assert decisions["C003"]["decision"] == "implement"
    assert decisions["C004"]["decision"] == "implement"
    assert decisions["C026.2"]["decision"] == "implement-narrow-owner"
    assert decisions["C025"]["decision"] == "audit-and-fill-gaps"
    assert decisions["C027.4"]["decision"] == "implement"
    assert decisions["C043"]["decision"] == "reuse-then-gap-fill"
    assert all("cross-Sprint" not in item["reason"] for item in decisions.values())


def test_f1_preparation_keeps_retired_and_completed_standalone_work_out_of_sprint():
    tasks = set(_load(PREP)["sprint"]["tasks"])
    assert not tasks & {
        "ERP-MIG-F1-01-C026-1",
        "ERP-MIG-F1-05-C027-1",
        "ERP-MIG-F1-06-C027-2",
        "ERP-MIG-F1-07-C027-3",
        "ERP-MIG-F1-09-C051",
    }
    current = CURRENT.read_text()
    assert "[подготовке F1](f1-preparation-2026-09-15.md)" in current
    assert "[плане](f1-preparation-2026-09-15.json)" in current


def test_f1_execution_policy_keeps_task_checkpoints_and_bounded_tests():
    policy = _load(PREP)["execution_policy"]
    assert policy["checkpoint_after_each_task"] is True
    assert policy["run_full_suite"] is False
    assert policy["task_db_mutation_during_direct_migration"] is False
    assert len(policy["recommended_order"]) == 7
