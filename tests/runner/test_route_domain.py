import copy
import pytest
from poise.modules.foundation.errors import DomainError
from .helpers import process, stage, task, verify, inspect, finding, resolution, decision, submit

def route(cfg):
    from poise.modules.workflow.domain import RouteDefinition
    return RouteDefinition.from_process(cfg)

def test_graph_is_not_list_order():
    cfg=process(); cfg["stages"]=[cfg["stages"][2],cfg["stages"][0],cfg["stages"][3],cfg["stages"][1]]
    t=task(cfg); assert t.stage.stage_id=="draft"
    t=verify(t,{}).accept("S",True).task
    assert t.stage.stage_id=="audit"
    t=verify(t,inspect()).accept("S",False).task
    assert t.state.status=="completed"

@pytest.mark.parametrize("damage",["missing-route","missing-handler","unknown-handler","unknown-target","wrong-outcome","no-terminal","unreachable","write-inspection","unknown-route-field","bad-rework"])
def test_invalid_graph_rejected(damage):
    cfg=process()
    if damage=="missing-route": del cfg["route"]
    elif damage=="missing-handler": del cfg["stages"][0]["handler"]
    elif damage=="unknown-handler": cfg["stages"][0]["handler"]="not_supported"
    elif damage=="unknown-target": cfg["stages"][0]["transitions"]["complete"]="missing"
    elif damage=="wrong-outcome": cfg["stages"][0]["transitions"]={"passed":"audit"}
    elif damage=="no-terminal":
        cfg["stages"][1]["transitions"]["clear"]="draft"; cfg["stages"][3]["transitions"]["clear"]="draft"
    elif damage=="unreachable": cfg["stages"][2]["transitions"]["complete"]="audit"
    elif damage=="write-inspection": cfg["stages"][1]["read_only"]=False
    elif damage=="unknown-route-field": cfg["route"]["depth"]=1
    elif damage=="bad-rework": cfg["stages"][0]["rework_targets"]=["missing"]
    with pytest.raises(DomainError): route(cfg)

def test_verify_selects_next_but_does_not_start_it():
    t=verify(task(),{})
    assert t.stage.stage_id=="draft" and t.state.status=="verified"
    a=t.accept("S",False).task
    assert a.state.status=="accepted" and a.stage.stage_id=="draft"
    assert a.accept("S",False).task==a
    assert a.accept("S",True).task.stage.stage_id=="audit"

def test_full_feedback_rejection_then_acceptance_same_task():
    t=verify(task(),{}).accept("S",True).task
    t=verify(t,inspect([finding()])).accept("S",True).task
    assert t.stage.stage_id=="amend"
    t=verify(t,{"resolutions":[resolution()]}).accept("S",True).task
    assert t.stage.stage_id=="follow_up"
    t=verify(t,inspect(decisions=[decision(outcome="rejected")])).accept("S",True).task
    assert t.stage.stage_id=="amend" and t.state.iteration==2
    t=verify(t,{"resolutions":[resolution("R2")]}).accept("S",True).task
    assert t.stage.stage_id=="follow_up" and t.state.iteration==2
    t=verify(t,inspect(decisions=[decision("R2")])).accept("S",False).task
    assert t.state.task_id=="T" and t.state.status=="completed"
    data=t.workflow_context()
    assert len(data["feedback"]["resolutions"])==2
    assert [x["decision"] for x in data["feedback"]["decisions"]]==["rejected","accepted"]

def test_explicit_user_rework_can_return_to_allowed_stage():
    t=verify(task(),{}).accept("S",True).task
    t=verify(t,inspect())
    new=t.rework("S","Пересмотреть результат","draft").task
    assert new.stage.stage_id=="draft" and new.state.iteration==2
    with pytest.raises(DomainError): t.rework("S","Пересмотреть","amend")

def test_route_terminal_not_last_list_position():
    t=verify(task(),{}).accept("S",True).task
    t=verify(t,inspect())
    assert t.state.stage_index==1
    assert t.workflow_context()["next_stage"] is None
    assert t.accept("S",True).task.state.status=="completed"


def test_every_inspection_rework_outcome_has_a_real_target():
    cfg=process();cfg["stages"][1]["transitions"]["changes_requested"]=None
    with pytest.raises(DomainError): route(cfg)


def test_valid_cycle_with_positive_terminal_path_is_accepted():
    cfg = process()
    cfg["stages"][2]["transitions"]["complete"] = "follow_up"
    cfg["stages"][3]["transitions"] = {
        "clear": None,
        "changes_requested": "amend",
    }

    parsed = route(cfg)

    assert parsed.node("amend").target("complete") == "follow_up"
    assert parsed.node("follow_up").target("clear") is None


def test_check_route_with_only_negative_terminal_is_rejected():
    cases = {
        "not_satisfied_terminal": [stage(
            "check", "check",
            {"satisfied": "check", "not_satisfied": None, "inconclusive": "check"},
            False, ["src/**"], ["check"],
        )],
        "inconclusive_terminal": [stage(
            "check", "check",
            {"satisfied": "check", "not_satisfied": "check", "inconclusive": None},
            False, ["src/**"], ["check"],
        )],
        "reachable_trap_after_positive_exit": [
            stage(
                "check", "check",
                {"satisfied": None, "not_satisfied": "trap", "inconclusive": "check"},
                False, ["src/**"], ["check"],
            ),
            stage(
                "trap", "check",
                {"satisfied": "trap", "not_satisfied": None, "inconclusive": "trap"},
                False, ["src/**"], ["trap"],
            ),
        ],
    }
    accepted = []

    for case, stages in cases.items():
        cfg = process()
        cfg["route"] = {"entry": "check"}
        cfg["stages"] = stages
        try:
            route(cfg)
        except DomainError as exc:
            assert "путь положительного завершения" in str(exc)
        else:
            accepted.append(case)

    if accepted:
        pytest.fail(f"DID NOT RAISE for invalid CHECK routes: {accepted}")


def test_check_route_with_positive_terminal_and_rework_cycle_is_accepted():
    cfg = process()
    cfg["route"] = {"entry": "check"}
    cfg["stages"] = [stage(
        "check", "check",
        {"satisfied": None, "not_satisfied": "check", "inconclusive": "check"},
        False, ["src/**"], ["check"],
    )]

    parsed = route(cfg)

    assert parsed.node("check").target("satisfied") is None


@pytest.mark.parametrize("negative_terminal", ["not_satisfied", "inconclusive"])
def test_check_route_with_mixed_positive_and_negative_terminals_is_accepted(
    negative_terminal,
):
    transitions = {
        "satisfied": None,
        "not_satisfied": "check",
        "inconclusive": "check",
    }
    transitions[negative_terminal] = None
    cfg = process()
    cfg["route"] = {"entry": "check"}
    cfg["stages"] = [stage(
        "check", "check", transitions,
        False, ["src/**"], ["check"],
    )]

    parsed = route(cfg)

    assert parsed.node("check").target("satisfied") is None
    assert parsed.node("check").target(negative_terminal) is None
