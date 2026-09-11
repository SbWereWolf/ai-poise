from copy import deepcopy
import pytest
from poise.modules.foundation.errors import DomainError
from poise.modules.actions.domain import PlanSpec, ActionRun, Publication


def merge_plan():
    return {'kind':'git_merge','base_commit':'a'*40,
            'sources':[{'commit':'b'*40,'checkpoint_message':'WIP: first source'},
                       {'commit':'c'*40,'checkpoint_message':'WIP: second source'}]}


def test_explicit_merge_plan_preserves_order_and_identity():
    p=PlanSpec.parse(merge_plan(),10)
    assert p.kind=='git_merge' and len(p.steps)==2
    assert p.digest==PlanSpec.parse(deepcopy(merge_plan()),10).digest
    assert p.steps[1]['commit']=='c'*40


@pytest.mark.parametrize('change', ['missing_base','duplicate','empty','unknown','overflow'])
def test_invalid_plan_has_no_interpretive_defaults(change):
    p=merge_plan(); limit=10
    if change=='missing_base':del p['base_commit']
    if change=='duplicate':p['sources'][1]=p['sources'][0]
    if change=='empty':p['sources']=[]
    if change=='unknown':p['kind']='guess_merge'
    if change=='overflow':limit=1
    with pytest.raises(DomainError):PlanSpec.parse(p,limit)


def test_running_plan_never_retries_unknown_effect_automatically():
    plan=PlanSpec.parse(merge_plan(),10)
    state=ActionRun.new(plan)
    state=state.start(0, {'head':'a'*40})
    assert state.waiting_for_probe and state.cursor==0
    with pytest.raises(DomainError):state.start(0, {'head':'a'*40})
    state=state.record('awaiting_resolution',{'conflicts':['src/a.py']})
    assert state.status=='awaiting_resolution'
    state=state.record('ready',{'commit':'d'*40})
    assert state.cursor==1


def test_publication_requires_authority_full_ref_and_commit():
    p=Publication.parse({'target_ref':'refs/heads/main','expected_commit':'a'*40,
                         'authorization':'User: publish the reviewed result'})
    assert p.target_ref=='refs/heads/main'
    for key,value in [('target_ref','main'),('authorization',''),('expected_commit','main')]:
        bad=p.to_dict();bad[key]=value
        with pytest.raises(DomainError):Publication.parse(bad)


def test_publish_and_apply_handlers_cannot_self_certify_completion():
    from poise.modules.workflow.handlers import handler
    from poise.modules.workflow.domain import HandlerKind
    from poise.modules.inspection.domain import FeedbackBook
    work={'plan':merge_plan(),'phase':'prepare','resolutions':[],'finding_resolutions':[]}
    assert handler(HandlerKind.APPLY_PLAN).evaluate(work,FeedbackBook.empty(),'apply',1).outcome is None
    p={'target_ref':'refs/heads/main','expected_commit':'a'*40,'authorization':'user publish'}
    assert handler(HandlerKind.PUBLISH).evaluate(p,FeedbackBook.empty(),'publish',1).outcome is None
