"""Accepted external nine-stage contract and actual root-derived creation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from poise.common import load_config
from poise.composition import project_tools
from poise.modules.catalogue.domain import TaskBlueprint
from poise.modules.goal_config.domain import GoalTypeDefinition
from tests.projects.helpers import fill_markers, shipped_edits, shipped_setup

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/catalogue/test-development-rollout"
SEQUENCE = (
    "baseline", "baseline_inspection", "coverage_planning", "coverage_planning_inspection",
    "test_planning", "test_planning_inspection", "test_implementation",
    "sensitivity_verification", "test_inspection",
)
GOALS = (
    "development", "test_development", "verification", "review", "design", "analysis",
    "profiling", "environment_diagnostics", "environment_remediation", "documentation",
    "task_planning", "sprint_planning", "integration",
)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _definition_maps(document):
    result = deepcopy(document)
    result["stages"] = {stage["id"]: stage for stage in document["stages"]}
    requirements = document["content_contract"]["requirements"]
    result["content_contract"]["requirements"] = {item["id"]: item for item in requirements}
    assert len(result["stages"]) == len(document["stages"])
    assert len(result["content_contract"]["requirements"]) == len(requirements)
    return result


def assert_accepted_test_development_contract(document):
    """Only list-definition order is normalized; all values remain contractual."""
    assert len(document["stages"]) == 9, "test_development: expected exactly nine accepted nodes"
    stages = {stage["id"]: stage for stage in document["stages"]}
    assert set(stages) == set(SEQUENCE)
    assert document["route"]["entry"] == "baseline"
    for producer, inspector, following in (
        ("baseline", "baseline_inspection", "coverage_planning"),
        ("coverage_planning", "coverage_planning_inspection", "test_planning"),
        ("test_planning", "test_planning_inspection", "test_implementation"),
    ):
        writer, review = stages[producer], stages[inspector]
        assert writer["role"] == "executor"
        assert writer["handler"] == ("observe" if producer == "baseline" else "produce")
        assert writer["transitions"] == {"complete": inspector}
        assert review["transitions"] == {"clear": following, "changes_requested": producer}
    for name in ("baseline_inspection", "coverage_planning_inspection",
                 "test_planning_inspection", "test_inspection"):
        review = stages[name]
        assert review["role"] == "reviewer" and review["handler"] == "inspect"
        assert review["read_only"] is True and review["allowed_paths"] == []
    assert stages["test_implementation"]["role"] == "executor"
    assert stages["test_implementation"]["handler"] == "produce"
    assert stages["test_implementation"]["transitions"] == {"complete": "sensitivity_verification"}
    assert stages["sensitivity_verification"]["handler"] == "check"
    assert stages["sensitivity_verification"]["transitions"] == {
        "satisfied": "test_inspection", "not_satisfied": "test_implementation",
        "inconclusive": "test_implementation",
    }
    assert stages["test_inspection"]["transitions"] == {
        "clear": None, "changes_requested": "test_implementation",
    }
    for name, stage in stages.items():
        assert stage["criteria_authority"] == {
            "propose": ["test_implementation"] if name == "test_planning" else [],
            "decide": ["test_implementation"] if name == "test_planning_inspection" else [],
        }, name
    assert _definition_maps(document) == _definition_maps(fixture("accepted-test_development.json"))
    GoalTypeDefinition.parse(document)


def _materialize(document):
    values = fixture("parameters.json")
    order = [stage["id"] for stage in document["stages"]]
    contracts = {item["stage_id"]: item for item in values["stage_contracts"]}
    phases = {item["stage"]: item for item in values["decomposition"]["phases"]}
    values["stage_contracts"] = [contracts[name] for name in order]
    values["decomposition"]["phases"] = [phases[name] for name in order]
    raw = json.loads((ROOT / "config/catalogue/task-templates/test_development.json").read_text())
    assert raw == fixture("unchanged-blueprint.json")
    before = deepcopy(values)
    created = TaskBlueprint.parse(raw).instantiate(
        values, GoalTypeDefinition.parse(document).data, [], fixture("decomposition-policy.json"),
    )
    assert values == before
    for field, parameter in (
        ("id", "identity"), ("sprint_id", "membership"), ("goal", "goal"),
        ("requirements", "requirements"), ("definition_of_done", "dod"),
        ("executable_obligations", "executable_obligations"), ("methods", "methods"),
        ("method_inputs", "method_inputs"), ("artifact_requirements", "artifact_requirements"),
        ("content_contract", "contract"), ("evidence_plan", "evidence"),
        ("checks", "checks"), ("stage_contracts", "stage_contracts"),
        ("decomposition", "decomposition"),
    ):
        assert created[field] == before[parameter], field
    return created


def test_accepted_fixture_materializes_with_current_blueprint():
    assert hashlib.sha256((FIXTURES / "accepted-test_development.json").read_bytes()).hexdigest() == (
        "244a69876c8a43d151d9942ebc5477aef8e2b463c82a5efa23a2bd8c71eedcab"
    )
    document = fixture("accepted-test_development.json")
    assert_accepted_test_development_contract(document)
    _materialize(document)


@pytest.mark.parametrize("corruption", [
    "missing_implementation_authority", "sensitivity_returns_to_design",
])
def test_oracle_rejects_structurally_valid_semantic_corruption(corruption):
    document = fixture("accepted-test_development.json")
    stages = {stage["id"]: stage for stage in document["stages"]}
    if corruption == "missing_implementation_authority":
        stages["test_planning"]["criteria_authority"]["propose"] = []
    else:
        stages["sensitivity_verification"]["transitions"]["not_satisfied"] = "test_planning"
        stages["sensitivity_verification"]["rework_targets"].append("test_planning")
    GoalTypeDefinition.parse(document)
    with pytest.raises(AssertionError):
        assert_accepted_test_development_contract(document)


@pytest.mark.parametrize("form", ["workflow-templates", "workflows"])
def test_roots_match_accepted_test_development_contract(form):
    actual = json.loads((ROOT / "config/catalogue" / form / "test_development.json").read_text())
    assert_accepted_test_development_contract(actual)
    _materialize(actual)


def test_reference_matches_accepted_test_development_contract():
    reference = json.loads((ROOT / "config/catalogue/reference.json").read_text())
    declared = next(goal for goal in reference["goal_types"] if goal["id"] == "test_development")
    assert len(declared["stages"]) == 9, "test_development: expected nine active reference stages"
    assert declared == fixture("accepted-reference.json")
    edges = [edge for edge in reference["feedback_edges"] if edge["goal_type"] == "test_development"]
    assert len(edges) == 1
    edge = edges[0]
    assert (edge["id"], edge["from_stage"], edge["to_stage"],
            edge["from_handler"], edge["to_handler"]) == (
        "FB-TST-01", "test_inspection", "test_implementation", "inspect", "produce",
    )


@pytest.mark.parametrize("family", ["linux-reference", "wsl-poise"])
def test_project_creation_uses_accepted_test_development(project, family):
    tools = project["root"] / family
    settings = shipped_setup(tools)
    selected = json.loads(settings.read_text())["templates"][family]
    blueprint = json.loads((tools / selected["path"]).read_text())
    sources_before = {
        goal: (tools / value["path"]).read_bytes()
        for goal, value in blueprint["workflow_sources"].items()
    }
    data = project["root"].parent / (family + "-data")
    request = fill_markers(fixture("project-request.json"), {"FAMILY": family})
    request["edits"] = shipped_edits(
        blueprint, tools=tools, project="rollout-" + family,
        repository=project["app"], data=data, trees=project["app"] / "task-worktrees",
    )
    owner = project_tools(settings)
    result = owner.apply(request)
    manifest = Path(result["config_path"])
    _, current, graphs = load_config(manifest)
    assert result["status"] == "created"
    assert current["project"] == "rollout-" + family
    assert current["git"]["repository"] == str(project["app"])
    assert current["git"]["author_name"] == "Fixture"
    assert current["git"]["author_email"] == "fixture@example.invalid"
    assert current["git"]["push_required"] is False
    assert current["paths"]["state"] == str(data)
    assert sorted(graphs) == sorted(GOALS)
    assert {row["project"]: row["config_path"] for row in owner.list()["projects"]} == {
        "rollout-" + family: str(manifest),
    }
    assert all((tools / value["path"]).read_bytes() == sources_before[goal]
               for goal, value in blueprint["workflow_sources"].items())
    for goal in graphs:
        path = Path(current["workflows"][goal])
        path = path if path.is_absolute() else manifest.parent / path
        assert json.loads(path.read_text()) == json.loads(sources_before[goal])
    assert_accepted_test_development_contract(graphs["test_development"])
    _materialize(graphs["test_development"])
    manifest_before = manifest.read_bytes()
    assert owner.apply(request)["replayed"] is True
    assert manifest.read_bytes() == manifest_before
    assert not (data / current["paths"]["database"]).exists()
