"""A read-only planning result is published through the same Task/Sprint owners."""
from copy import deepcopy
import json
import pytest
from conftest import write_json
from conftest import WorkPoise as Poise
from poise.application.work import WorkTools
from poise.common import PoiseError


def call(h,op,inp):
    return WorkTools(h).invoke({'operation':op,'input':inp,'messages':[]})


def start(h,task=None,decision=None):
    return call(h,'bootstrap',{'task':task,'decision':decision,'feedback':None,'rework_stage':None})


def verify(h,ctx,work,sections):
    p=deepcopy(ctx['result_template']);p['sections'].update(sections);p['stage_work']=work
    return call(h,'verify',{'result':p,'artifacts':[]})


def setup(project):
    stage=deepcopy(project['process']['stages'][0]);stage.update(id='draft',handler='produce',
        sections={'planned_tasks':'Write the reviewed contracts.'},required_sections=['planned_tasks'],
        read_only=True,allowed_paths=[],transitions={'complete':'review'},rework_targets=['draft'])
    review=deepcopy(stage);review.update(id='review',handler='inspect',sections={'report':'Review.'},
        required_sections=['report'],transitions={'clear':'publish','changes_requested':'draft'},rework_targets=['draft','review'])
    publish=deepcopy(stage);publish.update(id='publish',handler='publish',sections={},required_sections=[],
        transitions={'complete':None},rework_targets=['draft'])
    p=deepcopy(project['process']);p.update(goal_type='planning',stages=[stage,review,publish],
        route={'entry':'draft','max_transitions':30,'max_stage_visits':6})
    write_json(project['root']/'config/processes/planning.json',p)
    cfg=deepcopy(project['cfg']);cfg['processes']['planning']='config/processes/planning.json';cfg['automatic_checks']=[]
    write_json(project['config_path'],cfg)
    task=deepcopy(project['task']);task.update(id='PLAN',goal_type='planning',methods=[],
        checks={s['id']:[] for s in p['stages']},evidence_plan={s['id']:{'subject_methods':{},'arguments':[],'review_arguments':[]} for s in p['stages']})
    child=deepcopy(project['task']);child['id']='CHILD'
    h=Poise(project['config_path'],'PLANNER');ctx=start(h,task)
    return h,ctx,child


def to_publish(h,ctx,children):
    verify(h,ctx,{}, {'planned_tasks':json.dumps(children)})
    ctx=start(h,decision='continue')
    verify(h,ctx,{'coverage':'Reviewed each full child contract.','findings':[],'resolution_decisions':[]},
           {'report':'Contract and exact checks reviewed.'})
    call(h,'accept',{})
    assert h.task_queries.record(children[0]['id']) is None
    return start(h,decision='continue')


def test_publish_reviewed_task_batch_and_bootstrap_child(project):
    h,ctx,child=setup(project);ctx=to_publish(h,ctx,[child])
    payload={'kind':'tasks','section':'planned_tasks','authorization':'User: publish reviewed tasks.'}
    out=verify(h,ctx,payload,{})
    assert out['status']=='verified'
    assert h.task_queries.record('CHILD')['status']=='available'
    assert call(h,'verify',{'result':None,'artifacts':[]})['replayed']
    call(h,'accept',{})
    boot=start(h,{'id':'CHILD'})
    assert boot['stage']==project['process']['route']['entry']
    assert boot['task']=='CHILD'


def test_invalid_child_batch_creates_no_partial_tasks(project):
    h,ctx,child=setup(project);bad=deepcopy(child);bad['id']='BAD';bad['methods']=[]
    ctx=to_publish(h,ctx,[child,bad])
    with pytest.raises(PoiseError):verify(h,ctx,{'kind':'tasks','section':'planned_tasks','authorization':'User: publish.'},{})
    assert h.task_queries.record('CHILD') is None
    assert h.task_queries.record('BAD') is None


def test_publication_does_not_accept_unreviewed_inline_draft(project):
    h,ctx,child=setup(project);ctx=to_publish(h,ctx,[child])
    with pytest.raises(PoiseError):verify(h,ctx,{'kind':'tasks','section':'planned_tasks','authorization':'User: publish.','tasks':[child]}, {})
    assert h.task_queries.record('CHILD') is None


def test_child_rows_and_publication_receipt_roll_back_together(project):
    import sqlite3
    h,ctx,child=setup(project);other=deepcopy(child);other['id']='SECOND'
    ctx=to_publish(h,ctx,[child,other])
    # Fault injection is test setup, not a workflow method or synthetic task state.
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER reject_second_child BEFORE INSERT ON tasks WHEN NEW.id='SECOND' BEGIN SELECT RAISE(ABORT,'injected child failure'); END")
    payload={'kind':'tasks','section':'planned_tasks','authorization':'User: publish reviewed children.'}
    with pytest.raises(sqlite3.IntegrityError):verify(h,ctx,payload,{})
    assert h.task_queries.record('CHILD') is None and h.task_queries.record('SECOND') is None
    with h.store.unit_of_work() as u:
        assert u.actions.load('PLAN','publish',1) is None
    with h.store.transaction() as db:db.execute('DROP TRIGGER reject_second_child')
    out=verify(h,ctx,payload,{})
    assert out['status']=='verified'
    assert h.task_queries.record('SECOND')['status']=='available'


def test_unknown_child_goal_cannot_be_filled_from_parent(project):
    h,ctx,child=setup(project);child['goal_type']='NO_SUCH_GOAL'
    ctx=to_publish(h,ctx,[child])
    with pytest.raises(PoiseError,match='Unknown child'):
        verify(h,ctx,{'kind':'tasks','section':'planned_tasks','authorization':'User: publish.'},{})
    assert h.task_queries.record('CHILD') is None
