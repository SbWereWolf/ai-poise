"""Subjects: explicit route scopes and real public report-review lifecycle.

The arrangement and oracles are test-owned; shipped review is a separate subject.
No product wrapper generates expected outcomes, feedback IDs or history.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from conftest import WorkPoise, bind_task_requirements, git, write_json
from poise.application.work import WorkTools
from poise.modules.foundation.errors import DomainError
from poise.modules.inspection.domain import FeedbackBook, Finding, Resolution
from poise.modules.workflow.domain import RouteDefinition
from poise.modules.workflow.handlers import evaluate_stage
from .helpers import task as domain_task, verify as domain_verify


FIXTURES = Path(__file__).parent / "fixtures/review-report"
ROOT = Path(__file__).resolve().parents[2]


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def inspection(findings=(), decisions=(), resolutions=None):
    value = {"coverage": fixture("records.json")["coverage"],
             "findings": list(findings), "resolution_decisions": list(decisions)}
    if resolutions is not None:
        value["resolutions"] = list(resolutions)
    return value


def book(*, external=False, report=False, plan=False, pending=False):
    records = fixture("records.json")
    findings = []
    for selected, key, stage in ((external, "external", "review_subject"),
                                 (report, "report", "audit_report"),
                                 (plan, "plan", "approve_plan")):
        if selected:
            findings.append(Finding(**records[key], stage=stage, iteration=1))
    resolutions = (Resolution(**records["resolution"], stage="review_subject", iteration=2),) if pending else ()
    return FeedbackBook(tuple(findings), resolutions, ())


@pytest.mark.parametrize("stage,expected", [
    ("draft", ("approve_plan",)),
    ("approve_plan", ("approve_plan",)),
    ("review_subject", ("review_subject",)),
    ("audit_report", ("audit_report",)),
], ids=["plan-producer", "plan-inspector", "external-inspector", "report-inspector"])
def test_report_graph_keeps_three_inspection_subjects_distinct(stage, expected):
    route = RouteDefinition.from_workflow(fixture("workflow.json"))
    assert route.inspection_scope(stage) == expected


def test_external_negative_finding_does_not_reject_accurate_report():
    route = RouteDefinition.from_workflow(fixture("workflow.json"))
    before = book(external=True)
    result = evaluate_stage(route, inspection(), before, "audit_report", 1)
    assert result.outcome == "clear"
    assert [(f.id, f.stage, f.iteration) for f in result.feedback.findings] == [
        ("EXTERNAL", "review_subject", 1),
    ]
    assert [f.id for f in result.feedback.open_findings] == ["EXTERNAL"]
    assert result.feedback.resolutions == result.feedback.decisions == ()


def test_report_correction_proposes_only_report_fix_and_keeps_external_finding():
    route = RouteDefinition.from_workflow(fixture("workflow.json"))
    before = book(external=True, report=True)
    work = inspection(resolutions=[fixture("records.json")["resolution"]])
    proposed = evaluate_stage(route, work, before, "review_subject", 2)
    assert [f.id for f in proposed.feedback.open_findings] == ["EXTERNAL", "REPORT"]
    assert [(r.id, r.finding_id, r.stage) for r in proposed.feedback.pending_resolutions] == [
        ("REPORT-R1", "REPORT", "review_subject"),
    ]
    decided = evaluate_stage(route, inspection(decisions=[{
        "resolution_id": "REPORT-R1", "decision": "accepted", "reason": "Evidence checked.",
    }]), proposed.feedback, "audit_report", 2)
    assert decided.outcome == "clear"
    assert [f.id for f in decided.feedback.open_findings] == ["EXTERNAL"]
    assert [f.id for f in decided.feedback.findings] == ["EXTERNAL", "REPORT"]
    assert before.resolutions == before.decisions == ()


def test_plan_clear_preserves_foreign_report_finding_and_pending_proposal():
    route = RouteDefinition.from_workflow(fixture("workflow.json"))
    before = book(external=True, report=True, pending=True)
    result = evaluate_stage(route, inspection(), before, "approve_plan", 2)
    assert result.outcome == "clear"
    assert result.feedback == before
    assert [r.id for r in result.feedback.pending_resolutions] == ["REPORT-R1"]


def test_plan_cannot_accept_foreign_report_proposal():
    route = RouteDefinition.from_workflow(fixture("workflow.json"))
    before = book(report=True, pending=True)
    with pytest.raises(DomainError, match="ожидающее решение исправление"):
        evaluate_stage(route, inspection(decisions=[{
            "resolution_id": "REPORT-R1", "decision": "accepted", "reason": "Wrong inspector.",
        }]), before, "approve_plan", 2)
    assert before.decisions == ()


def test_report_correction_cannot_accept_itself_in_same_packet():
    route = RouteDefinition.from_workflow(fixture("workflow.json"))
    before = book(report=True)
    with pytest.raises(DomainError, match="Report correction cannot find/accept its own correction"):
        evaluate_stage(route, inspection(decisions=[{
            "resolution_id": "REPORT-R1", "decision": "accepted", "reason": "Self acceptance.",
        }], resolutions=[fixture("records.json")["resolution"]]), before, "review_subject", 2)
    assert before.resolutions == before.decisions == ()


def test_scope_rule_is_independent_of_stage_names_and_physical_order():
    workflow = fixture("workflow.json")
    rename = {"draft": "p", "approve_plan": "q", "review_subject": "x", "audit_report": "y"}
    workflow["route"]["entry"] = "p"
    for stage in workflow["stages"]:
        stage["id"] = rename[stage["id"]]
        stage["transitions"] = {k: None if v is None else rename[v]
                                for k, v in stage["transitions"].items()}
        stage["rework_targets"] = [rename[v] for v in stage["rework_targets"]]
    workflow["stages"].reverse()
    route = RouteDefinition.from_workflow(workflow)
    assert route.inspection_scope("p") == ("q",)
    assert route.inspection_scope("q") == ("q",)
    assert route.inspection_scope("x") == ("x",)
    assert route.inspection_scope("y") == ("y",)


@pytest.mark.parametrize("pending", [False, True], ids=["open", "pending"])
def test_report_clear_keeps_foreign_internal_obligation_and_blocks_completion(pending):
    current = domain_verify(domain_task(fixture("workflow.json")), {}).accept("S", True).task
    current = domain_verify(current, inspection()).accept("S", True).task
    current = domain_verify(current, inspection()).accept("S", True).task
    retained = book(plan=True)
    if pending:
        proposal = fixture("records.json")["resolution"]
        proposal.update(id="PLAN-R1", finding_id="PLAN")
        retained = replace(retained, resolutions=(Resolution(**proposal, stage="draft", iteration=2),))
    current = replace(current, feedback=retained)
    reviewed = domain_verify(current, inspection())
    assert reviewed.progress.outcome == "clear"
    assert [f.id for f in reviewed.feedback.open_findings] == ["PLAN"]
    assert [r.id for r in reviewed.feedback.pending_resolutions] == (["PLAN-R1"] if pending else [])
    with pytest.raises(DomainError, match="открытыми внутренними находками"):
        reviewed.accept("S", True)
    assert reviewed.state.status == "verified"
    assert reviewed.feedback == retained


def test_shipped_review_retains_literal_graph_and_separate_subject_scopes():
    workflow = json.loads((ROOT / "config/catalogue/workflows/review.json").read_text())
    nodes = {s["id"]: s for s in workflow["stages"]}
    assert set(nodes) == {"planning", "planning_inspection", "inspection", "execution_inspection"}
    assert nodes["planning"]["transitions"] == {"complete": "planning_inspection"}
    assert nodes["planning_inspection"]["transitions"] == {
        "clear": "inspection", "changes_requested": "planning",
    }
    assert nodes["inspection"]["transitions"] == {
        "clear": "execution_inspection", "changes_requested": "execution_inspection",
    }
    assert nodes["execution_inspection"]["transitions"] == {
        "clear": None, "changes_requested": "inspection",
    }
    assert [(nodes[s]["role"], nodes[s]["handler"]) for s in
            ("planning", "planning_inspection", "inspection", "execution_inspection")] == [
        ("executor", "produce"), ("reviewer", "inspect"),
        ("executor", "inspect"), ("reviewer", "inspect"),
    ]
    route = RouteDefinition.from_workflow(workflow)
    assert route.inspection_scope("planning_inspection") == ("planning_inspection",)
    assert route.inspection_scope("inspection") == ("inspection",)
    assert route.inspection_scope("execution_inspection") == ("execution_inspection",)


def call(actor, operation, **inputs):
    return WorkTools(actor).invoke({"operation": operation, "input": inputs, "messages": []})


def boot(actor, task=None, decision=None, target=None):
    return call(actor, "bootstrap", task=task, decision=decision,
                feedback="Fixture user requests the declared report correction." if target else None,
                rework_stage=target)


def submit(actor, context, work, text):
    result = deepcopy(context["result_template"])
    result["sections"] = {name: text for name in result["sections"]}
    result["stage_work"] = work
    result["commit_message"] = "Record the exact reviewed subject"
    output = call(actor, "verify", result=result, artifacts=[])
    assert output["status"] == "verified"
    return output


def transfer(sender, receiver, *, target=None):
    state = call(sender, "show", queries=[{"id": "task", "kind": "task"}])["results"][0]["value"]
    released = call(sender, "release", task_id="REPORT-CASE",
                    request_id=f"report-transfer-{state['version']}", expected_version=state["version"],
                    reason="The fixture hands the exact result to its other assigned role.")
    assert released["status"] == "ownership_released"
    assert released["after"]["task_id"] is None
    assert released["after"]["worktree_task_id"] is None
    boot(receiver, {"id": "REPORT-CASE"})
    return boot(receiver, decision="rework" if target else "continue", target=target)


def start(project):
    workflow = fixture("workflow.json")
    cfg = deepcopy(project["cfg"])
    cfg["automatic_checks"] = []
    write_json(project["root"] / "config/workflows/development.json", workflow)
    write_json(project["config_path"], cfg)
    task = deepcopy(project["task"])
    task.update(id="REPORT-CASE", methods=[], method_inputs=[],
                checks={s["id"]: [] for s in workflow["stages"]},
                goal="Review the frozen subject and independently accept the accurate report.",
                requirements=["The report retains the external subject verdict and its own review history."],
                definition_of_done=["The exact report is accepted independently of the subject verdict."],
                artifact_requirements=[], content_contract={"sections": [], "routes": [], "requirements": []},
                evidence_plan={s["id"]: {"subject_methods": {}, "arguments": [], "review_arguments": []}
                               for s in workflow["stages"]},
                stage_contracts=[{"stage_id": s["id"], "allowed_paths": [],
                                  "entry_requirements": [], "exit_requirements": [],
                                  "criteria": {"requirements": [], "definition_of_ready": [],
                                               "definition_of_done": []}}
                                 for s in workflow["stages"]],
                decomposition={"kind": "ordinary", "phases": [
                    {"stage": s["id"], "skills": ["task-domain"], "areas": []}
                    for s in workflow["stages"]], "integration": None})
    bind_task_requirements(task, project["requirements_registry"])
    executor = WorkPoise(project["config_path"], "report-executor")
    reviewer = WorkPoise(project["config_path"], "report-reviewer")
    context = boot(executor, task)
    assert context["stage"] == "draft"
    return executor, reviewer, context


def plan_text(actor):
    response = call(actor, "show", queries=[{
        "id": "plan", "kind": "section", "stage": "draft", "name": "report",
        "submission": None, "range": None,
    }])
    return response["results"][0]["value"]["text"]


@pytest.mark.parametrize("negative,repair", [(False, False), (True, False), (True, True)],
                         ids=["clean", "negative", "negative-report-rework"])
def test_public_report_acceptance_preserves_subject_and_internal_history(project, negative, repair):
    records = fixture("records.json")
    source = project["app"] / "src/double.py"
    source_bytes = source.read_bytes()
    source_head = git(project["app"], "rev-parse", "refs/heads/main")
    remote_head = git(project["remote"], "rev-parse", "refs/heads/main")
    executor, reviewer, context = start(project)
    submit(executor, context, {}, records["accepted_plan"])
    context = transfer(executor, reviewer)
    assert context["stage"] == "approve_plan"
    approved = submit(reviewer, context, inspection(), "The exact plan is accepted.")
    assert approved["stage_outcome"] == "clear"
    context = transfer(reviewer, executor)
    assert context["stage"] == "review_subject"
    text = records["negative_report"] if negative else records["clean_report"]
    observed = submit(executor, context, inspection([records["external"]] if negative else []), text)
    assert observed["stage_outcome"] == ("changes_requested" if negative else "clear")
    context = transfer(executor, reviewer)
    assert context["stage"] == "audit_report"
    if repair:
        rejected = submit(reviewer, context, inspection([records["report"]]), "Missing report evidence.")
        assert rejected["stage_outcome"] == "changes_requested"
        assert [f["id"] for f in rejected["feedback"]["open_findings"]] == ["EXTERNAL", "REPORT"]
        for number, decision in ((1, "rejected"), (2, "accepted")):
            context = transfer(reviewer, executor, target="review_subject")
            assert context["stage"] == "review_subject"
            assert plan_text(executor) == records["accepted_plan"]
            resolution = dict(records["resolution"], id=f"REPORT-R{number}")
            proposed = submit(executor, context, inspection(resolutions=[resolution]), text)
            assert [r["finding_id"] for r in proposed["feedback"]["pending_resolutions"]] == ["REPORT"]
            assert [f["id"] for f in proposed["feedback"]["open_findings"]] == ["EXTERNAL", "REPORT"]
            context = transfer(executor, reviewer)
            assert context["stage"] == "audit_report"
            reviewed = submit(reviewer, context, inspection(decisions=[{
                "resolution_id": f"REPORT-R{number}", "decision": decision,
                "reason": "The independent fixture checks the current report evidence.",
            }]), text)
            assert reviewed["stage_outcome"] == ("clear" if number == 2 else "changes_requested")
        history = reviewed["feedback"]
        assert [(r["id"], r["finding_id"]) for r in history["resolutions"]] == [
            ("REPORT-R1", "REPORT"), ("REPORT-R2", "REPORT"),
        ]
        assert [(r["resolution_id"], r["decision"]) for r in history["decisions"]] == [
            ("REPORT-R1", "rejected"), ("REPORT-R2", "accepted"),
        ]
        assert [f["id"] for f in history["open_findings"]] == ["EXTERNAL"]
    else:
        reviewed = submit(reviewer, context, inspection(), text)
        assert reviewed["stage_outcome"] == "clear"
    state = call(reviewer, "show", queries=[{"id": "task", "kind": "task"}])["results"][0]["value"]
    assert state["workflow"]["visits"]["draft"] == 1
    assert state["workflow"]["visits"]["approve_plan"] == 1
    assert state["workflow"]["visits"]["review_subject"] == (3 if repair else 1)
    assert state["workflow"]["visits"]["audit_report"] == (3 if repair else 1)
    completed = call(reviewer, "accept")
    assert completed["status"] == "completed"
    assert [f["id"] for f in completed["feedback"]["findings"]] == (
        ["EXTERNAL", "REPORT"] if repair else ["EXTERNAL"] if negative else [])
    assert [f["id"] for f in completed["feedback"]["open_findings"]] == (["EXTERNAL"] if negative else [])
    assert completed["feedback"]["pending_resolutions"] == []
    assert source.read_bytes() == source_bytes
    assert git(project["app"], "rev-parse", "refs/heads/main") == source_head
    assert git(project["remote"], "rev-parse", "refs/heads/main") == remote_head
    assert git(Path(context["worktree"]), "status", "--porcelain") == ""
