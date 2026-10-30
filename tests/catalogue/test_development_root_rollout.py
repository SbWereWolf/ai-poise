"""Accepted external starter contracts and real root-derived project creation."""
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
FIXTURES = ROOT / "tests/fixtures/catalogue/development-rollout"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _definition_maps(document):
    """Only definition-list order is noncontractual; preserve all values."""
    result = deepcopy(document)
    result["stages"] = {stage["id"]: stage for stage in document["stages"]}
    requirements = document["content_contract"]["requirements"]
    result["content_contract"]["requirements"] = {item["id"]: item for item in requirements}
    assert len(result["stages"]) == len(document["stages"])
    assert len(result["content_contract"]["requirements"]) == len(requirements)
    return result


def assert_accepted_development_contract(document):
    """Independent literal data, never a production-derived expected value."""
    expected = fixture("accepted-development.json")
    obligations = fixture("obligations.json")
    stages = {stage["id"]: stage for stage in document["stages"]}
    pairs = obligations["pairs"]
    assert len(document["stages"]) == 16
    assert set(stages) == {name for pair in pairs for name in pair}
    assert document["route"]["entry"] == obligations["entry"]
    for index, (producer, inspector) in enumerate(pairs):
        writer, review = stages[producer], stages[inspector]
        following = pairs[index + 1][0] if index + 1 < len(pairs) else None
        assert writer["role"] == "executor"
        assert writer["handler"] == ("observe" if producer == "baseline" else "produce")
        assert writer["transitions"] == {"complete": inspector}
        assert review["role"] == "reviewer"
        assert review["handler"] == "inspect"
        assert review["transitions"] == {"clear": following, "changes_requested": producer}
        assert producer in writer["rework_targets"]
        assert producer in review["rework_targets"]
        assert set(writer["rework_targets"]) <= {producer, inspector}
        assert set(review["rework_targets"]) <= {producer, inspector}
        assert review["read_only"] is True
        assert review["allowed_paths"] == []
    for name, stage in stages.items():
        assert stage["criteria_authority"] == obligations["authority"][name], name
        assert stage["allowed_paths"] == obligations["execution_scopes"].get(name, []), name
        if name not in obligations["execution_scopes"]:
            assert stage["read_only"] is True, name
    assert stages["solution_planning"]["required_sections"] == obligations["early_required_sections"]
    doc_gate = next(item for item in document["content_contract"]["requirements"]
                    if item["id"] == "documentation-input-documentation_plan")
    assert doc_gate["phase"] == "pre"
    assert doc_gate["stages"] == obligations["documentation_plan_pre_stages"]
    assert _definition_maps(document) == _definition_maps(expected)
    GoalTypeDefinition.parse(document)


def _materialize(document):
    blueprint = fixture("task-parameters.json")
    # Native request order follows the supplied definition order. Every value
    # remains literal fixture data; ordering is arrangement, not the oracle.
    order = [stage["id"] for stage in document["stages"]]
    contracts = {item["stage_id"]: item for item in blueprint["stage_contracts"]}
    phases = {item["stage"]: item for item in blueprint["decomposition"]["phases"]}
    blueprint["stage_contracts"] = [contracts[name] for name in order]
    blueprint["decomposition"]["phases"] = [phases[name] for name in order]
    raw = json.loads((ROOT / "config/catalogue/task-templates/development.json").read_text())
    created = TaskBlueprint.parse(raw).instantiate(
        blueprint, GoalTypeDefinition.parse(document).data, [], fixture("decomposition-policy.json"),
    )
    assert created["checks"] == blueprint["checks"]
    assert created["stage_contracts"] == blueprint["stage_contracts"]
    assert created["decomposition"] == blueprint["decomposition"]
    return created


def test_accepted_fixture_materializes_with_current_blueprint():
    expected = fixture("accepted-development.json")
    assert hashlib.sha256((FIXTURES / "accepted-development.json").read_bytes()).hexdigest() == (
        "ff952f844659dd6e317b6ca63b157af08355ed8c2fabf0ce87eb2a1034d4d1d6"
    )
    assert_accepted_development_contract(expected)
    _materialize(expected)


@pytest.mark.parametrize("form", ["workflow-templates", "workflows"])
def test_roots_match_accepted_development_contract(form):
    actual = json.loads((ROOT / "config/catalogue" / form / "development.json").read_text())
    assert_accepted_development_contract(actual)
    _materialize(actual)


def test_reference_matches_accepted_documentation_scope():
    reference = json.loads((ROOT / "config/catalogue/reference.json").read_text())
    development = next(goal for goal in reference["goal_types"] if goal["id"] == "development")
    solution = next(stage for stage in development["stages"] if stage["id"] == "solution_planning")
    assert solution["before"] == fixture("obligations.json")["reference_early_before"]


@pytest.mark.parametrize("family", ["common"])
def test_project_creation_uses_accepted_root(project, family):
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
    assert sorted(graphs) == fixture("obligations.json")["project_goal_types"]
    assert {row["project"]: row["config_path"] for row in owner.list()["projects"]} == {
        "rollout-" + family: str(manifest),
    }
    assert all((tools / value["path"]).read_bytes() == sources_before[goal]
               for goal, value in blueprint["workflow_sources"].items())
    for goal in graphs:
        path = Path(current["workflows"][goal])
        path = path if path.is_absolute() else manifest.parent / path
        assert json.loads(path.read_text()) == json.loads(sources_before[goal])
    assert_accepted_development_contract(graphs["development"])
    _materialize(graphs["development"])
    manifest_before = manifest.read_bytes()
    assert owner.apply(request)["replayed"] is True
    assert manifest.read_bytes() == manifest_before
    assert not (data / current["paths"]["database"]).exists()
