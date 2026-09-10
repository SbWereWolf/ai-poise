import copy
import pytest
from harness.modules.foundation.errors import DomainError
from .helpers import process, task, verify, inspect, finding, resolution, decision, submit

def route(cfg):
    from harness.modules.workflow.domain import RouteDefinition
    return RouteDefinition.from_process(cfg)

def test_graph_is_not_list_order():
    cfg=process(); cfg["stages"]=[cfg["stages"][2],cfg["stages"][0],cfg["stages"][3],cfg["stages"][1]]
    t=task(cfg); assert t.stage.stage_id=="draft"
    t=verify(t,{}).accept("S",True).task
    assert t.stage.stage_id=="audit"
    t=verify(t,inspect()).accept("S",False).task
    assert t.state.status=="completed"

@pytest.mark.parametrize("damage",["missing-route","missing-handler","unknown-handler","unknown-target","wrong-outcome","no-terminal","unreachable","write-inspection","missing-limits","bad-limit","bad-rework"])
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
    elif damage=="missing-limits": del cfg["route"]["max_transitions"]
    elif damage=="bad-limit": cfg["route"]["max_stage_visits"]=0
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

@pytest.mark.parametrize("key,value",[("max_transitions",1),("max_stage_visits",1)])
def test_limits_persist_and_do_not_block_cancellation(key,value):
    cfg=process(); cfg["route"][key]=value
    t=verify(task(cfg),{}).accept("S",True).task
    t=verify(t,inspect([finding()]))
    if key=="max_stage_visits":
        t=t.accept("S",True).task
        t=verify(t,{"resolutions":[resolution()]}).accept("S",True).task
        t=verify(t,inspect(decisions=[decision(outcome="rejected")]))
    with pytest.raises(DomainError,match="[Лл]имит"): t.accept("S",True)
    assert t.cancel("S","Пользователь отменил").task.state.status=="cancelled"

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
