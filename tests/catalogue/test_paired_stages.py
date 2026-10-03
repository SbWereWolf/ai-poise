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


def process(goal):
    return json.loads(
        (ROOT / "config/catalogue/process-templates" / f"{goal}.json").read_text()
    )


@pytest.mark.parametrize("goal", PAIRED)
def test_every_executor_result_has_an_independent_immediate_inspection(goal):
    data = GoalTypeDefinition.parse(process(goal)).data
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
    stages = {stage["id"]: stage for stage in process("development")["stages"]}
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
    for stage in process(goal)["stages"]:
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
