"""Independent graph obligations for paired reference starters."""
import json
from pathlib import Path

import pytest

from poise.modules.goal_config.domain import GoalTypeDefinition

ROOT = Path(__file__).resolve().parents[2]
PAIRED = (
    "design",
    "development",
    "documentation",
    "environment_remediation",
    "integration",
    "sprint_planning",
    "task_planning",
    "test_development",
)

DEVELOPMENT_SEQUENCE = (
    "baseline", "baseline_inspection",
    "solution_planning", "solution_planning_inspection",
    "verification_planning", "verification_planning_inspection",
    "test_implementation", "test_inspection",
    "implementation_design", "implementation_design_inspection",
    "implementation", "implementation_inspection",
    "documentation_planning", "documentation_planning_inspection",
    "documentation", "documentation_inspection",
)
RETIRED_DEVELOPMENT_IDS = {
    "test_remediation", "test_remediation_inspection",
    "implementation_remediation_planning",
    "implementation_remediation_planning_inspection",
    "implementation_remediation", "implementation_remediation_inspection",
    "implementation_remediation_plan",
}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def assert_development_direct_flow(raw):
    """The same literal oracle checks source, prepared and published inputs."""
    assert len(raw["stages"]) == 16
    assert {stage["id"] for stage in raw["stages"]} == set(DEVELOPMENT_SEQUENCE)
    assert not (set(_strings(raw)) & RETIRED_DEVELOPMENT_IDS)
    data = GoalTypeDefinition.parse(raw).data
    stages = {stage["id"]: stage for stage in data["stages"]}
    assert data["route"]["entry"] == "baseline"
    for offset in range(0, 16, 2):
        writer, inspector = DEVELOPMENT_SEQUENCE[offset:offset + 2]
        following = DEVELOPMENT_SEQUENCE[offset + 2] if offset < 14 else None
        phase = {writer, inspector}
        produced, inspected = stages[writer], stages[inspector]
        assert produced["handler"] == ("observe" if writer == "baseline" else "produce")
        assert produced["role"] == "executor"
        assert produced["transitions"] == {"complete": inspector}
        assert writer in produced["rework_targets"]
        assert set(produced["rework_targets"]) <= phase
        assert inspected["handler"] == "inspect"
        assert inspected["role"] == "reviewer"
        assert inspected["read_only"] is True
        assert inspected["allowed_paths"] == []
        assert inspected["transitions"] == {
            "clear": following, "changes_requested": writer,
        }
        assert writer in inspected["rework_targets"]
        assert set(inspected["rework_targets"]) <= phase
        for stage in (produced, inspected):
            for targets in stage["criteria_authority"].values():
                assert set(targets) <= set(DEVELOPMENT_SEQUENCE)
    assert "test_registry" in stages["test_implementation"]["sections"]
    assert "test_registry" not in stages["verification_planning"]["required_sections"]


@pytest.mark.parametrize("form", ["workflow-templates", "workflows"])
def test_development_direct_flow_has_sixteen_ordinary_nodes(form):
    data = json.loads((ROOT / "config/catalogue" / form / "development.json").read_text())
    assert_development_direct_flow(data)


def test_development_feedback_references_use_surviving_ordinary_nodes():
    reference = json.loads((ROOT / "config/catalogue/reference.json").read_text())
    stages = {stage["id"]: stage for stage in workflow("development")["stages"]}
    declared = next(goal for goal in reference["goal_types"] if goal["id"] == "development")
    assert tuple(stage["id"] for stage in declared["stages"]) == DEVELOPMENT_SEQUENCE
    edges = [edge for edge in reference["feedback_edges"] if edge["goal_type"] == "development"]
    assert {edge["id"]: (edge["from_stage"], edge["to_stage"]) for edge in edges} == {
        "FB-DEV-01": ("test_inspection", "test_implementation"),
        "FB-DEV-03": ("implementation_inspection", "implementation"),
    }
    for edge in edges:
        assert edge["from_stage"] in DEVELOPMENT_SEQUENCE
        assert edge["to_stage"] in DEVELOPMENT_SEQUENCE
        assert edge["from_handler"] == "inspect"
        assert edge["to_handler"] == "produce"
        assert stages[edge["from_stage"]]["transitions"]["changes_requested"] == edge["to_stage"]


def workflow(goal):
    return json.loads(
        (ROOT / "config/catalogue/workflow-templates" / f"{goal}.json").read_text()
    )


@pytest.mark.parametrize("goal", PAIRED)
def test_every_executor_result_has_an_independent_immediate_inspection(goal):
    data = GoalTypeDefinition.parse(workflow(goal)).data
    stages = {stage["id"]: stage for stage in data["stages"]}
    for stage in stages.values():
        if stage["role"] != "executor" or stage["handler"] not in (
            "produce", "observe", "revise", "apply_plan",
        ):
            continue
        target = stage["transitions"]["complete"]
        assert target is not None, (goal, stage["id"])
        inspection = stages[target]
        assert inspection["handler"] == "inspect", (goal, stage["id"], target)
        assert inspection["role"] == "reviewer"
        assert inspection["read_only"] is True
        assert inspection["allowed_paths"] == []


def test_development_has_the_six_explicit_design_and_delivery_pairs():
    stages = {stage["id"]: stage for stage in workflow("development")["stages"]}
    expected = {
        "solution_planning": "solution_planning_inspection",
        "verification_planning": "verification_planning_inspection",
        "test_implementation": "test_inspection",
        "implementation_design": "implementation_design_inspection",
        "implementation": "implementation_inspection",
        "documentation": "documentation_inspection",
    }
    for source, target in expected.items():
        assert stages[source]["transitions"] == {"complete": target}
    assert stages["test_inspection"]["transitions"]["clear"] == "implementation_design"
    assert stages["implementation_inspection"]["transitions"]["clear"] == "documentation_planning"
    for stage_id in ("solution_planning", "verification_planning", "implementation_design",
                     "documentation_planning"):
        assert stages[stage_id]["read_only"] is True
    # Future test commands are supplied when real method sources are known.
    assert "test_registry" not in stages["verification_planning"]["required_sections"]
    assert "test_registry" in stages["test_implementation"]["sections"]


@pytest.mark.parametrize("goal", PAIRED)
def test_paired_instructions_explain_boundaries_and_inspection(goal):
    if goal == "development":
        from tests.catalogue.test_development_root_rollout import assert_accepted_development_contract

        assert_accepted_development_contract(workflow(goal))
        return
    for stage in workflow(goal)["stages"]:
        if stage["handler"] in ("produce", "observe", "revise", "apply_plan", "inspect"):
            assert "Inputs:" in stage["instruction"]
            assert "Output:" in stage["instruction"]
            assert "Forbidden:" in stage["instruction"]
            if stage["role"] == "reviewer":
                assert "allowed_paths" in stage["instruction"]
                assert "restart" in stage["instruction"]


@pytest.mark.parametrize("goal", PAIRED)
def test_paired_task_starters_do_not_predict_future_check_names(goal):
    template = json.loads(
        (ROOT / "config/catalogue/task-templates" / f"{goal}.json").read_text()
    )
    assert template["parameters"]["checks"] == "object"
    assert template["task"]["checks"] == {"$input": "checks"}
