"""Contract tests: stage gates are data, not goal_type branches."""
from copy import deepcopy
from pathlib import Path
import pytest
from poise.modules.content.domain import SectionValue, ContentState
from poise.modules.content_requirements.domain import ContentPolicy, ContentSnapshot, ArtifactFact
from poise.modules.foundation.errors import DomainError


def empty():
    return {"sections": [], "routes": [], "requirements": []}


def declarations():
    return {
        "sections": [{"id":"risk_note","template":"Fill risks","normalization":"strip", "write_stages":["plan","implement"]}],
        "routes": [
            {"id":"A","requirements":["R1"],"points":[
                {"id":"source","kind":"record","fields":{"state":["planned","documented"],"reference":[]},"write_stages":["plan","implement"]},
                {"id":"test","kind":"method","fields":{},"write_stages":["plan","tests"]},
                {"id":"verdict","kind":"record","fields":{"result":["satisfied","not_satisfied"],"basis":[]},"write_stages":["implement","review"]}]},
            {"id":"B","requirements":["R2"],"points":[
                {"id":"argument","kind":"record","fields":{"facts":[],"assumptions":[],"inference":[],"conclusion":[]},"write_stages":["implement","review"]}]}],
        "requirements":[
            {"id":"A-source","kind":"trace","route":"A","point":"source","stages":["tests","implement","review"],"phase":"pre","field_equals":{}},
            {"id":"A-published","kind":"trace","route":"A","point":"source","stages":["implement","review"],"phase":"pre","field_equals":{"state":"documented"}},
            {"id":"A-test","kind":"trace","route":"A","point":"test","stages":["tests","implement","review"],"phase":"pre","field_equals":{}},
            {"id":"A-verdict","kind":"trace","route":"A","point":"verdict","stages":["review"],"phase":"pre","field_equals":{}},
            {"id":"B-proof","kind":"trace","route":"B","point":"argument","stages":["review"],"phase":"pre","field_equals":{}},
            {"id":"risks","kind":"section","section":"risk_note","stages":["implement","review"],"phase":"pre","states":["populated"]},
            {"id":"fixtures","kind":"artifact","scope":"task","pattern":"fixtures/*.json","minimum":2,"maximum":2,"stages":["tests"],"phase":"pre"}]
    }


def policy(goal=None, task=None):
    return ContentPolicy.from_layers(declarations() if goal is None else goal, empty() if task is None else task,
                                    ("plan","tests","implement","review"),("R1","R2"),("RED","GREEN"),("report",))


def blank():
    return ContentSnapshot((), ())


def apply(p, snapshot, stage, sections=None, trace=None):
    return p.apply(snapshot, stage, {} if sections is None else sections, {} if trace is None else trace)


def failed(result):
    return {r.rule_id for r in result.results if not r.passed}


def test_each_route_has_independent_deadlines_and_future_points_are_not_required():
    p=policy(); s=apply(p,blank(),"plan",trace={"A":{"source":{"state":"planned","reference":"docs/new.md#P1"}}})
    assert p.evaluate("plan","pre",s,()).passed
    assert failed(p.evaluate("tests","pre",s,())) == {"A-test","fixtures"}
    assert failed(p.evaluate("review","pre",s,())) == {"A-test","A-published","A-verdict","B-proof","risks"}


def test_planned_document_is_allowed_early_but_must_be_documented_later():
    p=policy(); s=apply(p,blank(),"plan",trace={"A":{"source":{"state":"planned","reference":"docs/not-yet-created.md#P1"}}})
    assert p.evaluate("plan","pre",s,()).passed
    s=apply(p,s,"implement",trace={"A":{"source":{"state":"documented","reference":"docs/new.md#P1"}}})
    assert "A-published" not in failed(p.evaluate("review","pre",s,()))


def test_exact_method_reference_is_required_unknown_reference_rejected_even_when_not_due():
    p=policy()
    with pytest.raises(DomainError,match="метод"):
        apply(p,blank(),"plan",trace={"A":{"test":"INVENTED"}})
    s=apply(p,blank(),"plan",trace={"A":{"test":"RED"}})
    assert "A-test" not in failed(p.evaluate("tests","pre",s,()))


def test_verdict_presence_is_not_automatic_satisfaction_and_record_fields_are_checked():
    p=policy(); s=apply(p,blank(),"review",trace={"A":{"verdict":{"result":"not_satisfied","basis":"Contradiction recorded"}}})
    assert "A-verdict" not in failed(p.evaluate("review","pre",s,()))
    rule={"id":"must-satisfy","kind":"trace","route":"A","point":"verdict","stages":["review"],"phase":"post","field_equals":{"result":"satisfied"}}
    p=p.extend({"sections":[],"routes":[],"requirements":[rule]})
    assert failed(p.evaluate("review","post",s,()))=={"must-satisfy"}


def test_partial_argument_can_be_saved_but_cannot_pass_when_due():
    p=policy(); s=apply(p,blank(),"review",trace={"B":{"argument":{"facts":"F1"}}})
    assert "B-proof" in failed(p.evaluate("review","pre",s,()))
    s=apply(p,s,"review",trace={"B":{"argument":{"facts":"F1","assumptions":"A1","inference":"F1 and A1 imply C1","conclusion":"C1"}}})
    assert "B-proof" not in failed(p.evaluate("review","pre",s,()))


def test_extra_section_template_optional_until_its_stage_then_requires_real_content():
    p=policy(); s=apply(p,blank(),"plan",sections={"risk_note":" Fill risks "})
    assert "risks" not in failed(p.evaluate("plan","pre",s,()))
    assert "risks" in failed(p.evaluate("implement","pre",s,()))
    s=apply(p,s,"implement",sections={"risk_note":"Measured a retry risk"})
    assert "risks" not in failed(p.evaluate("implement","pre",s,()))


def test_section_can_be_declared_filled_and_required_in_one_packet():
    p=policy(); extra={"sections":[{"id":"decision","template":"TODO","normalization":"exact","write_stages":["plan"]}],"routes":[],"requirements":[
        {"id":"decision-now","kind":"section","section":"decision","stages":["plan"],"phase":"pre","states":["populated"]}]}
    p=p.extend(extra); s=apply(p,blank(),"plan",sections={"decision":"Use explicit state"})
    assert "decision-now" not in failed(p.evaluate("plan","pre",s,()))
    assert p.extend(extra)==p


def test_task_cannot_weaken_goal_minimum_or_replace_definition():
    goal=declarations(); altered=deepcopy(goal["requirements"][-1]); altered["minimum"]=0
    with pytest.raises(DomainError,match="замен|конфликт|Повтор"):
        policy(task={"sections":[],"routes":[],"requirements":[altered]})
    goal["requirements"][-1]["maximum"]=10
    p=policy(goal=goal); stronger=deepcopy(goal["requirements"][-1]); stronger["id"]="more-fixtures"; stronger["minimum"]=3
    # Independent additive constraints, both mechanically enforced.
    p=p.extend({"sections":[],"routes":[],"requirements":[stronger]})
    assert {r.rule_id for r in p.evaluate("tests","pre",blank(),()).results} >= {"fixtures","more-fixtures"}


def test_artifact_counts_distinct_observed_paths_not_agent_duplicates():
    p=policy(); f=ArtifactFact("id1","task","fixtures/a.json")
    records=(f,f,ArtifactFact("alias","task","fixtures/a.json"),ArtifactFact("id2","task","fixtures/b.json"),ArtifactFact("x","runtime","fixtures/c.json"))
    assert "fixtures" not in failed(p.evaluate("tests","pre",blank(),records))
    assert "fixtures" in failed(p.evaluate("tests","pre",blank(),records[:3]))


def test_unknown_or_missing_config_is_rejected_not_defaulted():
    d=declarations(); del d["requirements"][0]["phase"]
    with pytest.raises(DomainError): policy(goal=d)
    d=declarations(); d["requirements"][0]["stages"]=["absent"]
    with pytest.raises(DomainError): policy(goal=d)
    d=declarations(); d["requirements"][0]["point"]="absent"
    with pytest.raises(DomainError): policy(goal=d)
    d=declarations(); d["routes"][0]["requirements"]=["unknown"]
    with pytest.raises(DomainError): policy(goal=d)


def test_trace_references_requirements_from_task_and_no_unknown_updates():
    p=policy()
    with pytest.raises(DomainError): apply(p,blank(),"plan",trace={"C":{"x":"y"}})
    with pytest.raises(DomainError): apply(p,blank(),"plan",sections={"undeclared":"x"})
    with pytest.raises(DomainError): apply(p,blank(),"plan",trace={"A":{"source":{"state":"planned","unknown":"x"}}})


def test_cannot_write_verdict_in_wrong_stage():
    with pytest.raises(DomainError,match="[Ээ]тап"):
        apply(policy(),blank(),"tests",trace={"A":{"verdict":{"result":"satisfied","basis":"trust me"}}})


def test_upstream_change_invalidates_only_later_points_in_same_route():
    p=policy(); s=apply(p,blank(),"plan",trace={"A":{"source":{"state":"planned","reference":"P1"},"test":"RED"}})
    s=apply(p,s,"review",trace={"A":{"verdict":{"result":"satisfied","basis":"E1"}},"B":{"argument":{"facts":"F","assumptions":"A","inference":"I","conclusion":"C"}}})
    changed=apply(p,s,"tests",trace={"A":{"test":"GREEN"}})
    failures=failed(p.evaluate("review","pre",changed,()))
    assert "A-verdict" in failures and "B-proof" not in failures
    assert "A-verdict" not in failed(p.evaluate("review","pre",s,()))  # old snapshot intact


def test_unchanged_upstream_does_not_invalidate_review():
    p=policy(); s=apply(p,blank(),"plan",trace={"A":{"test":"RED"}})
    s=apply(p,s,"review",trace={"A":{"verdict":{"result":"satisfied","basis":"E1"}}})
    assert apply(p,s,"tests",trace={"A":{"test":"RED"}})==s


def test_explicit_pre_and_post_gates_do_not_run_at_other_phase():
    d=empty(); d["requirements"]=[{"id":"r","kind":"section","section":"report","stages":["plan"],"phase":"post","states":["populated"]}]
    p=policy(goal=d)
    assert p.evaluate("plan","pre",blank(),()).passed
    assert failed(p.evaluate("plan","post",blank(),()))=={"r"}
    s=ContentSnapshot((SectionValue("report","done",ContentState.POPULATED),),())
    assert p.evaluate("plan","post",s,()).passed


def test_policy_wire_roundtrip_is_explicit_and_preserves_origins():
    p=policy(task={"sections":[],"routes":[],"requirements":[{"id":"extra","kind":"section","section":"report","stages":["plan"],"phase":"pre","states":["populated"]}]})
    wire=p.to_layers()
    other=policy(goal=wire["goal"],task=wire["task"])
    assert other==p
    origins={r.rule_id:r.origin for r in p.evaluate("plan","pre",blank(),()).results}
    assert origins=={"A-source":"goal","extra":"task"}


def test_zero_rules_is_explicitly_valid():
    p=policy(goal=empty())
    assert p.evaluate("plan","pre",blank(),()).passed
    with pytest.raises(DomainError): policy(goal={})


def test_goal_coverage_requires_routes_for_all_task_requirements():
    d=empty(); d['requirements']=[{'id':'coverage','kind':'coverage','stages':['plan'],'phase':'pre','points':[]}]
    p=policy(goal=d)
    assert failed(p.evaluate('plan','pre',blank(),())) == {'coverage'}
    task=declarations(); task['sections']=[]; task['requirements']=[]
    p=policy(goal=d,task=task)
    assert p.evaluate('plan','pre',blank(),()).passed
    task['routes'].pop()  # R2 is not represented by a route anymore.
    p=policy(goal=d,task=task)
    assert failed(p.evaluate('plan','pre',blank(),())) == {'coverage'}


def test_goal_coverage_can_require_same_minimum_checkpoint_for_each_route():
    d=empty(); d['requirements']=[{'id':'source-for-each','kind':'coverage','stages':['plan'],'phase':'pre','points':['source']}]
    task=declarations(); task['sections']=[]; task['requirements']=[]
    p=policy(goal=d,task=task)
    s=apply(p,blank(),'plan',trace={'A':{'source':{'state':'planned','reference':'P1'}}})
    assert failed(p.evaluate('plan','pre',s,()))=={'source-for-each'}


def trace_schedule(kind, write_stages, due_stages, *, rule_id="scheduled", field_equals=None):
    fields = {"state": ["planned", "documented"]} if kind == "record" else {}
    return {
        "sections": [],
        "routes": [{"id": "delivery", "requirements": ["R1"], "points": [
            {"id": "value", "kind": kind, "fields": fields, "write_stages": write_stages},
        ]}],
        "requirements": [{"id": rule_id, "kind": "trace", "route": "delivery",
            "point": "value", "stages": due_stages, "phase": "pre",
            "field_equals": {} if field_equals is None else field_equals}],
    }


@pytest.mark.parametrize(("kind", "field_equals"), [
    ("method", {}),
    ("text", {}),
    ("record", {"state": "documented"}),
])
def test_trace_schedule_rejects_value_or_predicate_without_writable_due_stage(kind, field_equals):
    contract = trace_schedule(kind, ["plan"], ["tests", "review"],
                              rule_id=f"{kind}-required", field_equals=field_equals)
    with pytest.raises(DomainError) as caught:
        policy(goal=empty(), task=contract)
    message = str(caught.value)
    for expected in (f"{kind}-required", "delivery", "value", "write_stages", "plan",
                     "due", "tests", "review"):
        assert expected in message


def test_phase_aware_trace_schedule_allows_same_stage_post_and_historical_restore():
    stages = ("verification_planning", "test_implementation", "implementation")
    contract = trace_schedule("method", ["verification_planning"], list(stages),
                              rule_id="verification-before-write")
    contract["requirements"][0]["phase"] = "post"
    created = ContentPolicy.from_layers(
        empty(), contract, stages, ("R1",), ("RED",), ("report",)
    )
    assert created.requirements[0].stages == stages

    historical = deepcopy(contract)
    historical["requirements"][0]["phase"] = "pre"
    restored = ContentPolicy.restore_layers(
        empty(), historical, stages, ("R1",), ("RED",), ("report",)
    )
    assert restored.requirements[0].phase == "pre"


def test_trace_phase_reachability_documentation_contract():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / "docs/workflows/content-requirements.md").read_text()
    governance = (root / "docs/governance/development-rules.md").read_text()
    agent_rule = (root / "src/AGENTS.md").read_text()

    for text in (workflow, governance):
        assert "phase=pre" in text
        assert "phase=post" in text
        assert "earliest required stage" in text
    assert "earliest required stage" in agent_rule
    assert "direct Task creation" in agent_rule
    assert "newborn ready" in agent_rule
    assert "Sprint publication" in agent_rule


def test_another_writable_point_on_a_multi_point_route_does_not_satisfy_target_schedule():
    contract = trace_schedule("method", ["plan"], ["tests", "review"], rule_id="method-required")
    contract["routes"][0]["points"].insert(0, {
        "id": "context", "kind": "text", "fields": {}, "write_stages": ["tests"],
    })
    with pytest.raises(DomainError) as caught:
        policy(goal=empty(), task=contract)
    assert "method-required" in str(caught.value) and "value" in str(caught.value)


def test_stored_unschedulable_contract_is_readable_but_new_invalid_addition_is_rejected():
    legacy = trace_schedule("method", ["plan"], ["tests", "review"], rule_id="legacy-required")
    restored = ContentPolicy.restore_layers(
        empty(), legacy, ("plan", "tests", "review"), ("R1",), ("RED",), ("report",))
    assert restored.to_layers()["task"] == legacy
    assert restored.extend(empty()) == restored

    invalid = trace_schedule("text", ["plan"], ["review"], rule_id="new-required")
    invalid["routes"][0]["id"] = "new-route"
    invalid["requirements"][0]["route"] = "new-route"
    with pytest.raises(DomainError) as caught:
        restored.extend(invalid)
    assert "new-required" in str(caught.value)
