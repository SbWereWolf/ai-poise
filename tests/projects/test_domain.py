from copy import deepcopy
import pytest
from harness.modules.projects.domain import ProjectBlueprint, Survey
from harness.modules.foundation.errors import HarnessError
from .helpers import setup_case


def test_explicit_patch_is_one_candidate_without_mutating_template(project):
    _,raw,req=setup_case(project)
    before=deepcopy(raw)
    candidate=ProjectBlueprint.parse(raw).build(req['edits'],1000)
    assert candidate['project']=='pilot'
    assert candidate['limits']==raw['config']['limits']
    assert raw==before


@pytest.mark.parametrize('case',['missing_answer','unknown_path','duplicate','overlap','wrong_type'])
def test_invalid_batch_never_invents_or_orders_conflicting_edits(project,case):
    _,raw,req=setup_case(project)
    edits=deepcopy(req['edits'])
    if case=='missing_answer':edits.pop()
    if case=='unknown_path':edits.append({'path':['git','guess'],'value':1})
    if case=='duplicate':edits.append({'path':['project'],'value':'other'})
    if case=='overlap':edits.append({'path':['git'],'value':raw['config']['git']})
    if case=='wrong_type':edits[-1]['value']='yes'
    with pytest.raises(HarnessError):ProjectBlueprint.parse(raw).build(edits,1000)


def test_explicit_false_is_not_replaced_by_template_true(project):
    _,raw,req=setup_case(project);req['edits'][-1]['value']=False
    assert ProjectBlueprint.parse(raw).build(req['edits'],1000)['git']['push_required'] is False


def test_survey_back_changes_answer_and_keep_preserves_exact_previous_value(project):
    _,raw,_=setup_case(project);s=Survey(ProjectBlueprint.parse(raw))
    assert s.current()['id']=='project'
    s.answer('first');s.answer('/new/repository');s.back()
    assert s.value()=='/new/repository'
    s.answer('/correct/repository');s.answer(False)
    assert s.complete
    s.back();assert s.value() is False
    s.keep();assert s.complete
    s.back();s.back();s.back();s.answer('second');s.keep();s.keep()
    result=s.candidate(1000)
    assert result['project']=='second' and result['git']['repository']=='/correct/repository'
    assert result['git']['push_required'] is False


def test_survey_does_not_advance_on_invalid_or_blank_text(project):
    _,raw,_=setup_case(project);s=Survey(ProjectBlueprint.parse(raw))
    with pytest.raises(HarnessError):s.answer('')
    assert s.current()['id']=='project'
    with pytest.raises(HarnessError):s.candidate(1000)


def test_unbounded_or_empty_edit_request_is_rejected(project):
    _,raw,req=setup_case(project)
    with pytest.raises(HarnessError):ProjectBlueprint.parse(raw).build(req['edits'],1)
