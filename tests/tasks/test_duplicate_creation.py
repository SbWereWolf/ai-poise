"""Native Task duplicate creation keeps lineage and never copies execution state."""
from copy import deepcopy
import pytest
from conftest import WorkPoise
from poise.application.work import WorkTools
from poise.modules.foundation.errors import PoiseError
from sprints.helpers import setup, task, draft, publish
from batch.helpers import request


def prepared(project, *, command='print("checked")'):
    setup(project)
    runtime = WorkPoise(project['config_path'], 'planner')
    contract = task(project, 'P', command=command);contract['sprint_id'] = None
    runtime.task_commands.create(contract,runtime.session,runtime.processes['development'],[],
        {'config_hash':runtime.config_hash},None,runtime.cfg.get('task_ids'),runtime._creation_base(),
        runtime.cfg['task_decomposition'])
    client = WorkTools(runtime)
    draft(client, [])
    return runtime, client


def duplicate(client, tid='D', parent='P', sprint='S'):
    return client.invoke(request('task',{'action':'duplicate','task_id':tid,'parent_id':parent,
        'sprint_id':sprint,'request_id':f'copy-{tid}'}))


def test_native_duplicate_and_reverse_projection_survive_ready_publish(project):
    h,c = prepared(project)
    before = deepcopy(h.task_queries.record('P'))
    born = duplicate(c)
    assert (born['status'],born['claimed_by'],born['duplicate']['parent_id'])==('newborn',None,'P')
    d = h.task_queries.record('D')
    assert d['draft']['requirements']==before['contract']['requirements']
    assert d['process']==before['process'] and d['worktree'] is None
    assert h.current_task() is None
    assert h.task_queries.record('P')['duplicate']=={'kind':'duplicate','role':'parent','parent_id':'P','children':['D']}
    assert h.task_queries.record('P')['history']==before['history']
    h.bootstrap({'id':'D'})
    d=h.task_queries.record('D')
    changed=h.task_action({'action':'edit','task_id':'D','expected_revision':d['revision'],
        'request_id':'edit-D','patch':{'goal':d['draft']['goal']},'remove':[]})
    ready=h.task_action({'action':'ready','task_id':'D','expected_revision':changed['revision'],'request_id':'ready-D'})
    rev=h.sprint_tools.overview('S')['revision']
    publish(c,rev)
    record=h.task_queries.record('D')
    assert record['status']=='available' and record['duplicate_parent_id']=='P'
    assert h.task_commands.read_duplicate_family('P')==h.task_commands.read_duplicate_family('D')


def test_copy_of_child_normalizes_root_and_replay_allocates_nothing(project):
    h,c=prepared(project)
    first=duplicate(c)
    replay=duplicate(c)
    assert replay['duplicate']==first['duplicate']
    sibling=duplicate(c,'E','D')
    assert sibling['duplicate']['parent_id']=='P'
    assert h.task_queries.record('P')['duplicate']['children']==['D','E']
    with h.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM tasks').fetchone()[0]==3


@pytest.mark.parametrize('parent,sprint',[('missing','S'),('P','missing'),('P',None)])
def test_failed_duplicate_creation_has_no_partial_lineage_or_task(project,parent,sprint):
    h,c=prepared(project)
    with pytest.raises(PoiseError):duplicate(c,parent=parent,sprint=sprint)
    assert h.task_queries.record('D') is None
    assert 'duplicate' not in h.task_queries.record('P')


def test_cannot_reparent_existing_task_or_mutate_parent_history(project):
    h,c=prepared(project);duplicate(c)
    before=deepcopy(h.task_queries.record('D'))
    with pytest.raises(PoiseError):duplicate(c,parent='D')
    assert h.task_queries.record('D')==before


def test_created_duplicate_uses_native_start_and_tie_gate(project):
    h,c = prepared(project)
    duplicate(c)
    h.bootstrap({'id':'D'})
    d=h.task_queries.record('D')
    h.task_action({'action':'ready','task_id':'D','expected_revision':d['revision'],'request_id':'ready-D'})
    publish(c,h.sprint_tools.overview('S')['revision'])
    first=WorkPoise(project['config_path'],'parent-worker')
    second=WorkPoise(project['config_path'],'duplicate-worker')
    assert first.bootstrap({'id':'P'})['status']=='active'
    blocked=second.bootstrap({'id':'D'})
    assert blocked['status']=='duplicate_start_blocked'
    assert blocked['duplicate_gate']['relatives'][0]['task_id']=='P'
    assert second.current_task() is None
    approved=second.bootstrap({'id':'D'},force_duplicate_start=True)
    assert approved['status']=='active'
    assert approved['duplicate_gate']['role']=='child'
    assert approved['duplicate_gate']['allowed'] is True


def test_duplicate_lineage_survives_native_restart_and_ready(project):
    h,c=prepared(project);duplicate(c)
    h.bootstrap({'id':'D'})
    d=h.task_queries.record('D')
    h.task_action({'action':'ready','task_id':'D','expected_revision':d['revision'],
        'request_id':'initial-ready-D'})
    publish(c,h.sprint_tools.overview('S')['revision'])
    assert h.bootstrap({'id':'D'})['status']=='active'
    before=h.task_queries.record('D')
    root_history=deepcopy(h.task_queries.record('P')['history'])
    restarted=c.invoke(request('task',{'action':'restart','task_id':'D',
        'expected_version':before['version'],'request_id':'restart-D',
        'reason':'Repair the explicitly selected unfinished duplicate contract.',
        'authorization':'Test operator authorized restart of D.'}))
    assert restarted['status']=='newborn'
    newborn=h.task_queries.record('D')
    assert newborn['duplicate']['parent_id']=='P'
    h.task_action({'action':'ready','task_id':'D','expected_revision':newborn['revision'],
        'request_id':'after-restart-ready-D'})
    assert h.task_queries.record('D')['duplicate']['parent_id']=='P'
    assert h.task_queries.record('P')['history']==root_history
