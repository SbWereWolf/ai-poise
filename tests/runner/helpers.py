from poise.modules.evidence.domain import EvidencePlan
import copy
from poise.modules.tasks.domain import Task, StageSpec
from poise.modules.content.domain import SectionRule
from poise.modules.content_requirements.domain import ContentPolicy
from poise.modules.verification.domain import CheckRegistry

EMPTY = {"sections": [], "routes": [], "requirements": []}

def process(goal="writing"):
    return {"goal_type":goal, "worktree_required":True, "benefit":{"git_categories":["code","documentation"],"sections":[]}, "route":{"entry":"draft"},
      "content_contract":copy.deepcopy(EMPTY), "stages":[
       stage("draft","produce",{"complete":"audit"},False,["docs/**","src/**"],["draft"]),
       stage("audit","inspect",{"clear":None,"changes_requested":"amend"},True,[],["audit","draft"]),
       stage("amend","revise",{"complete":"follow_up"},False,["docs/**","src/**"],["amend"]),
       stage("follow_up","inspect",{"clear":None,"changes_requested":"amend"},True,[],["follow_up","draft"])]}

def stage(name,handler,transitions,readonly,paths,rework):
    return {"id":name,"handler":handler,"transitions":transitions,"rework_targets":rework,
      "instruction":"Выполнить один этап и доложить.", "read_only":readonly,"allowed_paths":paths,
      "normalization":"strip", "sections":{"report":"Заполнить."},
      "required_sections":["report"], "artifact_requirements":[]}

def task(cfg=None):
    from poise.modules.workflow.domain import RouteDefinition
    cfg=process() if cfg is None else cfg
    stages=tuple(StageSpec(s["id"],(SectionRule("report","Заполнить.","strip",True),)) for s in cfg["stages"])
    ids=tuple(s.stage_id for s in stages)
    policy=ContentPolicy.from_layers(EMPTY,EMPTY,ids,(),(),("report",))
    registry=CheckRegistry.from_task([],{i:[] for i in ids},ids)
    return Task.new("T",stages,"S",policy,registry,RouteDefinition.from_process(cfg), EvidencePlan.parse(
        {s["id"]:{"subject_methods":{},"arguments":[],"review_arguments":[]} for s in cfg["stages"]},
        {s["id"]:s["handler"] for s in cfg["stages"]}, {s["id"]:[] for s in cfg["stages"]}))

def inspect(findings=(),decisions=()):
    return {"coverage":"Проверен текущий результат по требованиям.","findings":list(findings),"resolution_decisions":list(decisions)}

def finding(id="F1"):
    return {"id":id,"subject":"result","description":"Не выполнен критерий.","evidence":"Контрпример: получено A вместо B."}

def resolution(id="R1",fid="F1"):
    return {"id":id,"finding_id":fid,"description":"Исправлен путь B.","evidence":"Проверка B выполнена."}

def decision(id="R1",outcome="accepted"):
    return {"resolution_id":id,"decision":outcome,"reason":"Проверено на текущем результате."}

def submit(t,work):
    return t.submit("S",{"report":"Результат этапа."},(),"feat: result",copy.deepcopy(EMPTY),{},[],work,{"phase":"prepare","arguments":[],"decisions":[]}).task

def verify(t,work):
    t=submit(t,work)
    return t.mark_verified("S",t.state.submission_digest,()).task
