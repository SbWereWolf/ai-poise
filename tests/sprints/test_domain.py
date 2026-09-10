from copy import deepcopy
import pytest
from harness.modules.foundation.errors import HarnessError
from harness.modules.sprints.domain import SprintPolicy, SprintPlan, Sprint
from .helpers import policy,changes


def plan(tasks=()):
    p=SprintPolicy.parse(policy())
    value=SprintPlan.create('S',{'id':'basic','version':'1'},p)
    return value.apply(changes(tasks),p),p


def test_explicit_template_creates_independent_draft():
    p=SprintPolicy.parse(policy());a=SprintPlan.create('S',{'id':'basic','version':'1'},p)
    b=a.apply([{'kind':'sections','values':{'plan':'Заполнено'}}],p)
    assert a.data['sections']['plan']=='Заполнить план.'
    assert b.data['sections']['plan']=='Заполнено'


def test_unknown_template_or_missing_required_policy_no_default():
    with pytest.raises(HarnessError):SprintPolicy.parse({})
    with pytest.raises(HarnessError):SprintPlan.create('S',{'id':'other','version':'1'},SprintPolicy.parse(policy()))


def test_atomic_batch_final_graph_not_item_order():
    p=SprintPolicy.parse(policy());a=SprintPlan.create('S',{'id':'basic','version':'1'},p)
    tasks=[{'id':'A','sprint_id':'S'},{'id':'B','sprint_id':'S'}]
    edits=changes(tasks,[{'predecessor':'A','successor':'B','kind':'completion'}])
    assert a.apply(edits,p).data==a.apply(list(reversed(edits)),p).data


@pytest.mark.parametrize('edges',[
    [{'predecessor':'A','successor':'A','kind':'completion'}],
    [{'predecessor':'Z','successor':'B','kind':'completion'}],
    [{'predecessor':'A','successor':'B','kind':'completion'}, {'predecessor':'B','successor':'A','kind':'result'}],
])
def test_graph_failures_have_explicit_diagnostics(edges):
    value,p=plan([{'id':'A','sprint_id':'S'},{'id':'B','sprint_id':'S'}])
    value=value.apply([{'kind':'dependencies','items':edges}],p)
    assert value.graph_errors(p)


def test_duplicate_task_batch_not_last_writer_wins():
    value,p=plan()
    with pytest.raises(HarnessError):value.apply([{'kind':'upsert_tasks','tasks':[{'id':'A'},{'id':'A'}]}],p)


def test_cross_sprint_contract_rejected():
    value,p=plan([{'id':'A','sprint_id':'OTHER'}])
    assert value.graph_errors(p)


def test_dependency_waits_for_completion_not_verified():
    value,p=plan([{'id':'A','sprint_id':'S'},{'id':'B','sprint_id':'S'}])
    value=value.apply([{'kind':'dependencies','items':[{'predecessor':'A','successor':'B','kind':'completion'}]}],p)
    s=Sprint.draft(value,p).publish()
    state=s.overview({'A':'verified','B':'available'},())
    assert state['eligible']==[] and state['blocked'][0]['task']=='B'
    assert s.overview({'A':'completed','B':'available'},())['eligible']==['B']


def test_cancelled_predecessor_needs_user_edge_decision():
    value,p=plan([{'id':'A','sprint_id':'S'},{'id':'B','sprint_id':'S'}])
    value=value.apply([{'kind':'dependencies','items':[{'predecessor':'A','successor':'B','kind':'completion'}]}],p)
    s=Sprint.draft(value,p).publish()
    assert s.overview({'A':'cancelled','B':'available'},())['eligible']==[]
    s=s.waive([{'predecessor':'A','successor':'B','reason':'Пользователь разрешил продолжить'}])
    assert s.overview({'A':'cancelled','B':'available'},())['eligible']==['B']


def test_completed_projection_and_cascade_scope():
    value,p=plan([{'id':x,'sprint_id':'S'} for x in 'ABC'])
    value=value.apply([{'kind':'dependencies','items':[{'predecessor':'A','successor':'B','kind':'result'}]}],p)
    s=Sprint.draft(value,p).publish()
    assert s.cancellation_scope(['A'],'cascade')==('A','B')
    assert s.cancellation_scope(['A'],'single')==('A',)
    assert s.overview(dict.fromkeys('ABC','completed'),())['status']=='completed'


def test_published_plan_is_not_silently_rewritten():
    value,p=plan([{'id':'A','sprint_id':'S'}]);s=Sprint.draft(value,p).publish()
    with pytest.raises(HarnessError):s.revise(value)


def test_section_minimum_uses_sectionbook():
    value,p=plan([{'id':'A','sprint_id':'S'}]);value=value.apply([{'kind':'sections','values':{'plan':'  Заполнить план.  '}}],p)
    assert value.content_errors(p)
