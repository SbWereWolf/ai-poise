"""Verification templates: literal contracts, real owners and isolated public consumers.

Unit/typed subjects: GoalTypeDefinition, TaskBlueprint and CheckRegistry through
Task creation. Integration subjects: shipped project derivation, GoalConfig CLI
and WorkTools criteria/role handoff. Oracles are independent test-owned literals;
public fixture actors do not stand in for independent inspection of these tests.
"""
from copy import deepcopy
import hashlib
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from batch.helpers import request, result
from conftest import WorkPoise, bind_task_requirements, write_json
from poise.application.work import WorkTools
from poise.common import load_config
from poise.composition import project_tools
from poise.modules.catalogue.domain import TaskBlueprint
from poise.modules.foundation.errors import DomainError
from poise.modules.goal_config.domain import GoalTypeDefinition
from tests.projects.helpers import fill_markers, shipped_edits, shipped_setup
from tests.runtime_services.stage_criteria_helpers import (
    handoff, next_stage, show, verify_packet, work,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/catalogue/verification-rollout"
SEQUENCE = (
    "planning", "planning_inspection", "execution", "analysis", "execution_inspection",
)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _definition_maps(document):
    value = deepcopy(document)
    value["stages"] = {stage["id"]: stage for stage in document["stages"]}
    assert len(value["stages"]) == len(document["stages"])
    requirements = document["content_contract"]["requirements"]
    value["content_contract"]["requirements"] = {item["id"]: item for item in requirements}
    assert len(value["content_contract"]["requirements"]) == len(requirements)
    return value


def assert_accepted_verification_contract(document):
    assert len(document["stages"]) == 5, "verification: expected exactly five accepted nodes"
    actual = GoalTypeDefinition.from_configuration(document).data
    assert _definition_maps(actual) == _definition_maps(fixture("accepted-verification.json"))
    stages = {stage["id"]: stage for stage in actual["stages"]}
    assert set(stages) == set(SEQUENCE)
    assert stages["planning"]["criteria_authority"] == {"propose": ["execution"], "decide": []}
    assert stages["planning_inspection"]["criteria_authority"] == {"propose": [], "decide": ["execution"]}
    assert stages["planning_inspection"]["role"] == "reviewer"
    assert stages["execution_inspection"]["role"] == "reviewer"
    assert stages["planning_inspection"]["transitions"] == {"clear": "execution", "changes_requested": "planning"}
    assert stages["execution"]["transitions"] == {
        "satisfied": "analysis", "not_satisfied": "analysis", "inconclusive": "analysis",
    }
    assert stages["execution_inspection"]["transitions"] == {"clear": None, "changes_requested": "execution"}


def assert_accepted_blueprint(document):
    assert document == fixture("accepted-task-blueprint.json"), "verification: expected accepted five-stage blueprint"


def _parameters(home):
    home.mkdir(parents=True, exist_ok=True)
    value_file = write_json(home / "fixture-value.json", {"value": 4})
    return fill_markers(fixture("parameters.json"), {"PYTHON": sys.executable, "VALUE_FILE_LITERAL": json.dumps(str(value_file), ensure_ascii=False)})


def _instantiate(values, document=None, blueprint=None):
    return TaskBlueprint.parse(blueprint or fixture("accepted-task-blueprint.json")).instantiate(
        values, GoalTypeDefinition.from_configuration(document or fixture("accepted-verification.json")).data,
        [], fixture("decomposition-policy.json"),
    )


def _assert_preserved(created, original):
    for field, parameter in (
        ("id", "identity"), ("sprint_id", "membership"), ("goal", "goal"),
        ("requirements", "requirements"), ("definition_of_done", "dod"),
        ("methods", "methods"), ("method_inputs", "method_inputs"),
        ("artifact_requirements", "artifact_requirements"), ("content_contract", "contract"),
        ("evidence_plan", "evidence"), ("stage_contracts", "stage_contracts"),
        ("decomposition", "decomposition"),
    ):
        assert created[field] == original[parameter], field
    assert created["goal_type"] == "verification"
    assert created["checks"] == fixture("accepted-task-blueprint.json")["task"]["checks"]


def test_literal_pair_has_accepted_provenance():
    assert hashlib.sha256((FIXTURES / "accepted-verification.json").read_bytes()).hexdigest() == "65bbb67241c713722f20f3587ed275aa0561a2562c037b48acec7a58aeefd209"
    assert hashlib.sha256((FIXTURES / "accepted-task-blueprint.json").read_bytes()).hexdigest() == "1fe75777ce2f3839f4f81f6644e3f358eb3968fcde5386a426e4b9685098ef99"
    assert_accepted_verification_contract(fixture("accepted-verification.json"))
    assert TaskBlueprint.parse(fixture("accepted-task-blueprint.json")).goal_type == "verification"


def test_literal_pair_preserves_twelve_typed_parameters(tmp_path):
    values = _parameters(tmp_path)
    original = deepcopy(values)
    assert len(values) == 12
    created = _instantiate(values)
    _assert_preserved(created, original)
    assert values == original


@pytest.mark.parametrize("invalid", ["missing_membership", "extra_parameter", "wrong_identity"])
def test_literal_pair_rejects_invalid_parameter(tmp_path, invalid):
    values = _parameters(tmp_path)
    if invalid == "missing_membership":
        values.pop("membership")
        name = "membership"
    elif invalid == "extra_parameter":
        values["extra_parameter"] = "No implicit parameter."
        name = "extra_parameter"
    else:
        values["identity"] = 17
        name = "identity"
    with pytest.raises(DomainError) as error:
        _instantiate(values)
    assert name in str(error.value)


def test_literal_pair_rejects_unknown_verify_method(tmp_path):
    values = _parameters(tmp_path)
    values["methods"] = []
    values["method_inputs"] = []
    with pytest.raises(DomainError) as error:
        _instantiate(values)
    assert str(error.value) == "Расписание ссылается на неизвестные методы: ['VERIFY']"


def test_literal_pair_rejects_wrong_goal(tmp_path):
    document = fixture("accepted-verification.json")
    document["goal_type"] = "other"
    GoalTypeDefinition.from_configuration(document)
    with pytest.raises(DomainError) as error:
        _instantiate(_parameters(tmp_path), document=document)
    assert str(error.value) == "Контракт задачи не соответствует workflow snapshot"


@pytest.mark.parametrize("corruption", [
    "missing_execution_authority", "execution_returns_to_planning", "incomplete_blueprint_schedule",
])
def test_independent_oracle_rejects_valid_semantic_corruption(corruption):
    if corruption == "incomplete_blueprint_schedule":
        blueprint = fixture("accepted-task-blueprint.json")
        blueprint["task"]["checks"].pop("planning_inspection")
        TaskBlueprint.parse(blueprint)
        with pytest.raises(AssertionError):
            assert_accepted_blueprint(blueprint)
        return
    document = fixture("accepted-verification.json")
    stages = {stage["id"]: stage for stage in document["stages"]}
    if corruption == "missing_execution_authority":
        stages["planning"]["criteria_authority"]["propose"] = []
        stages["planning_inspection"]["criteria_authority"]["decide"] = []
    else:
        stages["execution"]["transitions"]["not_satisfied"] = "planning"
        stages["execution"]["rework_targets"].append("planning")
    GoalTypeDefinition.from_configuration(document)
    with pytest.raises(AssertionError):
        assert_accepted_verification_contract(document)


@pytest.mark.parametrize("form", ["workflow-templates", "workflows"])
def test_roots_match_accepted_verification_contract(form):
    assert_accepted_verification_contract(json.loads((ROOT / "config/catalogue" / form / "verification.json").read_text()))


def test_blueprint_matches_accepted_verification_pair():
    assert_accepted_blueprint(json.loads((ROOT / "config/catalogue/task-templates/verification.json").read_text()))


def test_reference_matches_accepted_verification_semantics():
    reference = json.loads((ROOT / "config/catalogue/reference.json").read_text())
    selected = next(goal for goal in reference["goal_types"] if goal["id"] == "verification")
    assert len(selected["stages"]) == 5, "verification: expected five active reference stages"
    projection = {
        "id": selected["id"],
        "stages": [{key: stage[key] for key in ("id", "handler", "before", "after")} for stage in selected["stages"]],
        "method_schedule": selected["method_schedule"],
        "feedback": sorted([
            {key: edge[key] for key in ("from_stage", "to_stage", "from_handler", "to_handler")}
            for edge in reference["feedback_edges"] if edge["goal_type"] == "verification"
        ], key=lambda edge: (edge["from_stage"], edge["to_stage"])),
    }
    expected = fixture("expected-reference-semantics.json")
    for value in (projection, expected):
        stages = value["stages"]
        value["stages"] = {stage["id"]: stage for stage in stages}
        assert len(value["stages"]) == len(stages)
    assert projection == expected
    assert reference["methods"]["verification"] == {"execution": "VERIFY"}


@pytest.mark.parametrize("family", ["linux-reference", "wsl-poise"])
def test_project_creation_uses_accepted_verification(project, family):
    tools = project["root"] / family
    settings = shipped_setup(tools)
    selected = json.loads(settings.read_text())["templates"][family]
    blueprint = json.loads((tools / selected["path"]).read_text())
    before = {goal: (tools / row["path"]).read_bytes() for goal, row in blueprint["workflow_sources"].items()}
    data = project["root"].parent / (family + "-verification-data")
    packet = fill_markers(fixture("project-request.json"), {"FAMILY": family})
    packet["edits"] = shipped_edits(
        blueprint, tools=tools, project="verification-" + family,
        repository=project["app"], data=data, trees=project["app"] / "verification-worktrees",
    )
    owner = project_tools(settings)
    reply = owner.apply(packet)
    manifest = Path(reply["config_path"])
    _, cfg, graphs = load_config(manifest)
    assert reply["status"] == "created"
    assert cfg["project"] == "verification-" + family
    assert cfg["git"]["repository"] == str(project["app"])
    assert cfg["git"]["author_name"] == "Fixture"
    assert cfg["git"]["author_email"] == "fixture@example.invalid"
    assert cfg["git"]["push_required"] is False
    assert cfg["paths"]["state"] == str(data)
    assert set(graphs) == set(fixture("expected-goal-ids.json"))
    assert all((tools / blueprint["workflow_sources"][goal]["path"]).read_bytes() == content for goal, content in before.items())
    for goal, content in before.items():
        if goal != "verification":
            assert graphs[goal] == json.loads(content)
    assert_accepted_verification_contract(graphs["verification"])
    values = _parameters(project["root"] / "caller")
    order = [stage["id"] for stage in graphs["verification"]["stages"]]
    contracts = {row["stage_id"]: row for row in values["stage_contracts"]}
    phases = {row["stage"]: row for row in values["decomposition"]["phases"]}
    values["stage_contracts"] = [contracts[name] for name in order]
    values["decomposition"]["phases"] = [phases[name] for name in order]
    original = deepcopy(values)
    current_blueprint = json.loads((tools / "config/catalogue/task-templates/verification.json").read_text())
    assert_accepted_blueprint(current_blueprint)
    _assert_preserved(_instantiate(values, document=graphs["verification"], blueprint=current_blueprint), original)
    assert values == original
    assert {row["project"]: row["config_path"] for row in owner.list()["projects"]} == {"verification-" + family: str(manifest)}


def _walkthrough():
    sys.path.insert(0, str(ROOT / "examples"))
    from catalogue_walkthrough import run
    return run


def test_public_verification_short_route_uses_independent_handoffs(tmp_path):
    reply = _walkthrough()(tmp_path / "verification-short", "verification", "short")
    assert reply["accepted_stages"] == list(SEQUENCE), "verification: expected independently inspected short route"
    assert reply["status"] == "PASS" and reply["task_status"] == "completed"
    assert len(reply["reports"]) == 5
    assert sum(call["operation"] == "release" and call["status"] == "ownership_released" for call in reply["calls"]) == 3
    assert reply["read_only_checks"] >= 5 and reply["ready_artifacts"] >= 1
    assert reply["target_unchanged"] and reply["remote_unchanged"]
    assert reply["open_findings_at_completion"] == 0


@pytest.mark.parametrize("from_stage,to_stage", [
    ("planning_inspection", "planning"), ("execution_inspection", "execution"),
])
def test_public_verification_feedback_returns_to_declared_stage(tmp_path, from_stage, to_stage):
    reference = json.loads((ROOT / "config/catalogue/reference.json").read_text())
    selected = [edge for edge in reference["feedback_edges"] if edge["goal_type"] == "verification" and edge["from_stage"] == from_stage and edge["to_stage"] == to_stage]
    assert len(selected) == 1, "verification: accepted feedback pair is absent"
    edge = selected[0]
    reply = _walkthrough()(tmp_path / (from_stage + "-return"), "verification", "feedback", edge["id"])
    assert reply["status"] == "PASS" and reply["task_status"] == "completed"
    assert edge["id"] in reply["traversed_feedback_edges"]
    assert reply["feedback_cycles"] >= 1
    assert sum(stage == to_stage for stage in reply["accepted_stages"]) >= 2
    assert max(report["iteration"] for report in reply["reports"] if report["stage"] == to_stage) >= 2
    assert reply["open_findings_at_completion"] == 0
    assert reply["target_unchanged"] and reply["remote_unchanged"]


def _goal_cli(settings, packet, evidence, label):
    evidence.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-B", "-m", "poise", "goal-config", "--settings", str(settings)]
    env = os.environ.copy()
    env.update(PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1", LANG="C.UTF-8")
    stdin = json.dumps(packet, ensure_ascii=False)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    reply = subprocess.run(argv, cwd=ROOT, input=stdin, capture_output=True, text=True, env=env, timeout=10)
    ended = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (evidence / (label + ".stdin.json")).write_text(stdin)
    (evidence / (label + ".stdout")).write_text(reply.stdout)
    (evidence / (label + ".stderr")).write_text(reply.stderr)
    (evidence / (label + ".command.json")).write_text(json.dumps({"argv": argv, "cwd": str(ROOT), "exit": reply.returncode, "started_utc": started, "ended_utc": ended, "environment_overlay": {key: env[key] for key in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "LANG")}}))
    assert reply.returncode == 0, reply.stderr + reply.stdout
    assert not reply.stderr
    brief = json.loads(reply.stdout)
    full = Path(brief["response_path"]).read_text() if "response_path" in brief else reply.stdout
    (evidence / (label + ".native.json")).write_text(full)
    return json.loads(full)


def test_existing_editor_updates_unmanaged_definition_and_replays(tmp_path):
    home = tmp_path / "owner"
    write_json(home / "editor.json", fixture("owner-editor.json"))
    write_json(home / "templates/writing.json", fixture("writing-template.json"))
    write_json(home / "workflows/verification.json", fixture("existing-verification.json"))
    evidence = Path(os.environ["POISE_RUN_OUTPUT_DIR"]) / "owner-update"
    seed = fixture("owner-create-request.json")
    _goal_cli(home / "editor.json", seed, evidence, "seed")
    other_before = (home / "workflows/writing.json").read_bytes()
    writing_before = _goal_cli(home / "editor.json", {"schema": "goal-config-status-1", "goal_type": "writing"}, evidence, "writing-before")
    initial = _goal_cli(home / "editor.json", {"schema": "goal-config-status-1", "goal_type": "verification"}, evidence, "before")
    assert initial["managed_revision"] is None and initial["pending_request"] is None
    assert initial["aligned"] is False and initial["live_revision"]
    packet = fixture("owner-update-request.json")
    packet["expected_revision"] = initial["live_revision"]
    first = _goal_cli(home / "editor.json", packet, evidence, "update")
    assert first["status"] == "applied" and first["changed"] is True and first["replayed"] is False
    assert first["previous_revision"] == initial["live_revision"]
    assert json.loads((home / "workflows/verification.json").read_text()) == fixture("expected-owner-after.json")
    after = _goal_cli(home / "editor.json", {"schema": "goal-config-status-1", "goal_type": "verification"}, evidence, "after")
    assert after["aligned"] is True and after["pending_request"] is None
    assert after["managed_revision"] == after["live_revision"] == first["revision"]
    replay = _goal_cli(home / "editor.json", packet, evidence, "replay")
    assert replay == {**first, "replayed": True, "current_revision": first["revision"]}
    writing_after = _goal_cli(home / "editor.json", {"schema": "goal-config-status-1", "goal_type": "writing"}, evidence, "writing-after")
    assert writing_after == writing_before
    assert (home / "workflows/writing.json").read_bytes() == other_before


@pytest.mark.parametrize("decision", ["accepted", "rejected"])
def test_public_criteria_decision_preserves_approved_revision(project, decision):
    values = _parameters(project["root"] / "criterion-input")
    task = _instantiate(values)
    bind_task_requirements(task, project["requirements_registry"])
    cfg = deepcopy(project["cfg"])
    cfg["automatic_checks"] = []
    cfg["task_decomposition"] = fixture("decomposition-policy.json")
    cfg["workflows"] = {"verification": "config/workflows/verification.json"}
    write_json(project["root"] / "config/workflows/verification.json", fixture("accepted-verification.json"))
    write_json(project["config_path"], cfg)
    executor = WorkTools(WorkPoise(project["config_path"], "VER-EXECUTOR"))
    context = executor.invoke(request("bootstrap", {"task": task, "decision": None, "feedback": None, "rework_stage": None}))
    assert context["stage"] == "planning"
    initial = show(executor, "execution")
    assert initial["execution"]["state"] == "unstarted"
    criteria = fixture("execution-criteria.json")
    assert initial["criteria"] == criteria["initial"]
    proposal = fixture("criteria-proposal.json")
    proposal["expected_criteria_revision"] = initial["revision"]
    value = result(context, "План наблюдения значения 4 подготовлен.")
    for section in value["sections"]:
        value["sections"][section] = "Проверить заданное значение 4 и сохранить наблюдение по согласованной программе."
    value["stage_work"]["criteria_work"] = work(executor, "verification-propose", proposals=[proposal])
    executor.invoke(request("verify", {"result": value, "artifacts": []}))
    pending = show(executor, "execution")
    assert pending["criteria"] == initial["criteria"] and pending["revision"] == initial["revision"]
    assert pending["execution"] == initial["execution"]
    assert len(pending["proposals"]) == 1
    proposed = pending["proposals"][0]
    handoff(executor, "criteria-to-reviewer")
    reviewer = WorkTools(WorkPoise(project["config_path"], "VER-REVIEWER"))
    reviewer.invoke(request("bootstrap", {"task": {"id": task["id"]}, "decision": None, "feedback": None, "rework_stage": None}))
    review_context = next_stage(reviewer)
    assert review_context["stage"] == "planning_inspection"
    declared_decision = fixture("criteria-decision.json")
    declared_decision.update(proposal_id=proposed["proposal_id"], proposal_digest=proposed["proposal_digest"], decision=decision)
    decision_work = work(reviewer, "verification-decide", decisions=[declared_decision])
    reviewer.invoke(verify_packet(review_context, decision_work))
    agreed = show(reviewer, "execution")
    expected = criteria["candidate"] if decision == "accepted" else criteria["initial"]
    assert agreed["criteria"] == expected
    if decision == "accepted":
        assert agreed["revision"] != initial["revision"]
        assert agreed["source"]["kind"] == "proposal"
        assert agreed["source"]["proposal_digest"] == proposed["proposal_digest"]
        assert agreed["history"][-1]["actor"] == "VER-REVIEWER"
        assert agreed["history"][-1]["stage"] == "planning_inspection"
    else:
        assert agreed["revision"] == initial["revision"]
    handoff(reviewer, "criteria-to-executor")
    executor.invoke(request("bootstrap", {"task": {"id": task["id"]}, "decision": None, "feedback": None, "rework_stage": None}))
    execution_context = next_stage(executor)
    assert execution_context["stage"] == "execution"
    bound = show(executor, "execution")
    assert bound["criteria"] == expected
    assert bound["execution"]["state"] == "started"
    assert bound["execution"]["bound_revision"] == agreed["revision"]
    handoff(executor, "criteria-observation-finished")
