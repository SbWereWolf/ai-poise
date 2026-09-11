from copy import deepcopy
from pathlib import Path
import sqlite3
import pytest
from conftest import write_json
from harness.runtime import Harness
from harness.application.work import WorkTools
from harness.modules.foundation.errors import HarnessError
from batch.helpers import request
from .helpers import setup,task,draft,publish,bootstrap,verify


@pytest.fixture
def sprint(project):
    setup(project);h=Harness(project['config_path'],'planner');return project,h,WorkTools(h)


def test_draft_hidden_and_publication_all_tasks_without_worktrees(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B','documentation')])
    assert r['status']=='draft' and r['errors']==[]
    assert h.task_queries.summary()==[]
    assert not (h.state/h.paths['worktrees']).exists()
    out=publish(w,r['revision'])
    assert out['status']=='planned' and set(out['eligible'])=={'A','B'}
    assert {t['status'] for t in h.task_queries.summary()}=={'available'}
    assert not (h.state/h.paths['worktrees']).exists()
    assert h.current_task() is None


def test_incomplete_child_saved_for_feedback_not_published(sprint):
    p,h,w=sprint;t=task(p);del t['methods']
    r=draft(w,[t]);assert r['errors']
    with pytest.raises(HarnessError):publish(w,r['revision'])
    assert h.task_queries.summary()==[]
    r=draft(w,[],revision=r['revision'],request_id='draft-2',updates=[{'kind':'upsert_tasks','tasks':[task(p)]}])
    assert r['revision']==2 and not r['errors']
    assert publish(w,2)['eligible']==['A']
    history=w.invoke(request('show',{'queries':[{'id':'s','kind':'sprint','sprint_id':None,'view':'history'}]}))['results'][0]['value']
    assert len(history['layers'])>=3


def test_unknown_exact_method_prevents_publication(sprint):
    p,h,w=sprint;t=task(p);t['checks']['work']=['MISSING']
    r=draft(w,[t]);assert r['errors']
    with pytest.raises(HarnessError):publish(w,r['revision'])


def test_repeat_and_conflicting_request_id(sprint):
    p,h,w=sprint;r=draft(w,[task(p)])
    assert draft(w,[task(p)])['revision']==r['revision']
    with pytest.raises(HarnessError):draft(w,[task(p,'B')])
    a=publish(w,r['revision']);b=publish(w,r['revision']);assert a==b
    assert len(h.task_queries.summary())==1


def test_stale_revision_cannot_replace_draft(sprint):
    p,h,w=sprint;draft(w,[task(p)])
    with pytest.raises(HarnessError):draft(w,[],revision=0,request_id='bad',updates=[])


def test_publish_transaction_rolls_back_members_and_tasks(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B')])
    with h.store.transaction() as db:
        db.execute("CREATE TRIGGER fail_member BEFORE INSERT ON sprint_members WHEN NEW.task_id='B' BEGIN SELECT RAISE(ABORT,'injected publication failure'); END")
    with pytest.raises(sqlite3.IntegrityError):publish(w,r['revision'])
    assert h.task_queries.summary()==[]
    with h.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM sprint_members').fetchone()[0]==0
        db.execute('DROP TRIGGER fail_member')
    assert set(publish(w,r['revision'])['eligible'])=={'A','B'}


def test_invalid_trace_schedule_prevents_all_sprint_member_publication(project):
    setup(project)
    work=project['process']['stages'][0]
    later=deepcopy(work);later.update(id='later',transitions={'complete':None},rework_targets=['later'])
    work['transitions']={'complete':'later'}
    project['process']['stages']=[work,later]
    write_json(project['root']/'config/processes/development.json',project['process'])
    h=Harness(project['config_path'],'planner');w=WorkTools(h)

    valid=task(project,'A')
    invalid=task(project,'B')
    for candidate in (valid,invalid):
        candidate['checks']['later']=[]
        candidate['evidence_plan']['later']={'subject_methods':{},'arguments':[],'review_arguments':[]}
    invalid['content_contract']={"sections":[],"routes":[
        {"id":"delivery","requirements":invalid['requirements'],"points":[
            {"id":"method","kind":"method","fields":{},"write_stages":["work"]}]}],
        "requirements":[
            {"id":"method-too-late","kind":"trace","route":"delivery","point":"method",
             "stages":["later"],"phase":"pre","field_equals":{}}]}

    planned=draft(w,[valid,invalid])
    with pytest.raises(HarnessError) as error:
        publish(w,planned['revision'])
    assert all(value in str(error.value) for value in
               ('method-too-late','delivery','method','work','later'))
    assert h.task_queries.summary()==[]
    assert not (h.state/h.paths['worktrees']).exists()
    assert bootstrap(w,'S')['status']=='draft'


def test_bootstrap_by_sprint_id_returns_ready_set_without_claim(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B')],[{'predecessor':'A','successor':'B','kind':'completion'}]);publish(w,r['revision'])
    out=bootstrap(w,'S')
    assert out['eligible']==['A'] and h.current_task() is None
    assert not (h.state/h.paths['worktrees']).exists()
    assert bootstrap(w)['eligible']==['A']


def test_start_by_id_runs_pinned_process_and_blocks_successor(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B')],[{'predecessor':'A','successor':'B','kind':'completion'}]);publish(w,r['revision'])
    with pytest.raises(HarnessError):bootstrap(w,'B')
    ctx=bootstrap(w,'A');assert ctx['task']=='A' and Path(ctx['worktree']).exists()
    verify(w,ctx)
    assert bootstrap(w,'S')['eligible']==[]
    with pytest.raises(HarnessError):bootstrap(w,'B')
    out=w.invoke(request('accept',{}));assert out['sprint']['eligible']==['B']
    assert bootstrap(w,'B')['task']=='B'


def test_result_dependency_starts_from_predecessor_tree(sprint):
    p,h,w=sprint
    a=task(p,'A',command='from src.double import double; assert double(2)==4')
    b=task(p,'B','documentation',command='from src.double import double; assert double(2)==4')
    r=draft(w,[a,b],[{'predecessor':'A','successor':'B','kind':'result'}]);publish(w,r['revision'])
    c=bootstrap(w,'A');(Path(c['worktree'])/'src/double.py').write_text('def double(n):\n    return n*2\n')
    verify(w,c);w.invoke(request('accept',{}))
    c=bootstrap(w,'B');assert 'n*2' in (Path(c['worktree'])/'src/double.py').read_text()
    assert 'n + 1' in (p['app']/'src/double.py').read_text()
    (Path(c['worktree'])/'docs').mkdir();(Path(c['worktree'])/'docs/guide.md').write_text('Use double(2) to obtain 4.\n')
    assert verify(w,c)['status']=='verified'
    assert w.invoke(request('accept',{}))['sprint']['status']=='completed'


def test_single_cancel_does_not_cancel_siblings_and_waiver_unblocks(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B'),task(p,'C')],[{'predecessor':'A','successor':'B','kind':'completion'}]);publish(w,r['revision'])
    o=w.invoke(request('sprint',{'action':'cancel_tasks','sprint_id':None,'request_id':'cancel-A','tasks':['A'],'mode':'single','reason':'Пользователь отменяет A'}))
    assert 'C' in o['eligible'] and 'B' not in o['eligible']
    o=w.invoke(request('sprint',{'action':'waive_dependencies','sprint_id':None,'request_id':'waive-A',
        'decisions':[{'predecessor':'A','successor':'B','reason':'Продолжить без A'}]}))
    assert set(o['eligible'])=={'B','C'}
    assert h.task_queries.record('B')['status']=='available'


def test_two_sessions_different_tasks_same_database(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B')]);publish(w,r['revision'])
    a=bootstrap(w,'A');other=WorkTools(Harness(p['config_path'],'agent-B'));b=bootstrap(other,'B')
    assert a['worktree']!=b['worktree']
    assert h.current_task()['id']=='A'
    assert other.runtime.current_task()['id']=='B'
    assert bootstrap(w,'S')['active_task']=='A'


def test_forced_close_skips_unfinished_gates_and_preserves_files(sprint):
    p,h,w=sprint;r=draft(w,[task(p)]);publish(w,r['revision']);ctx=bootstrap(w,'A')
    path=Path(ctx['worktree'])/'src/double.py';path.write_text('unfinished user work\n')
    o=w.invoke(request('sprint',{'action':'force_close','sprint_id':None,'request_id':'close-S','reason':'Пользователь прекращает спринт'}))
    assert o['status']=='cancelled' and path.read_text()=='unfinished user work\n'
    assert h.task_queries.record('A')['status']=='cancelled'


def test_fk_prevents_cross_sprint_dependency(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B')]);publish(w,r['revision'])
    with pytest.raises(sqlite3.IntegrityError):
        with h.store.transaction() as db:
            db.execute("INSERT INTO sprint_dependencies(sprint_id,predecessor,successor,kind) VALUES('OTHER','A','B','completion')")


def test_published_contract_cannot_be_overridden_by_bootstrap(sprint):
    p,h,w=sprint;r=draft(w,[task(p)]);publish(w,r['revision']);t=task(p);t['goal']='Подмена'
    with pytest.raises(HarnessError):
        w.invoke(request('bootstrap',{'task':t,'decision':None,'feedback':None,'rework_stage':None}))


def test_unknown_sprint_cannot_be_assigned_by_task_input(sprint):
    p,h,w=sprint
    with pytest.raises(HarnessError):
        w.invoke(request('bootstrap',{'task':task(p),'decision':None,'feedback':None,'rework_stage':None}))
    assert h.task_queries.summary()==[]


def test_duplicate_task_id_other_sprint_cannot_partially_publish(sprint):
    p,h,w=sprint;r=draft(w,[task(p)]);publish(w,r['revision'])
    t=task(p);t['sprint_id']='OTHER'
    r=w.invoke(request('sprint',{'action':'draft','sprint_id':'OTHER','request_id':'other-draft','expected_revision':None,
      'template':{'id':'basic','version':'1'},'changes':__import__('sprints.helpers',fromlist=['changes']).changes([t])}))
    with pytest.raises(HarnessError):
        w.invoke(request('sprint',{'action':'publish','sprint_id':'OTHER','request_id':'other-pub','expected_revision':r['revision']}))
    assert len(h.task_queries.summary())==1


def test_two_different_result_revisions_require_explicit_integration(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B'),task(p,'C')],[
        {'predecessor':'A','successor':'C','kind':'result'},{'predecessor':'B','successor':'C','kind':'result'}]);publish(w,r['revision'])
    for name,value in [('A','2'),('B','3')]:
        c=bootstrap(w,name);(Path(c['worktree'])/'src/double.py').write_text(f'def double(n):\n    return n*{value}\n')
        verify(w,c);w.invoke(request('accept',{}))
    out=bootstrap(w,'S')
    assert out['eligible']==[] and out['status']=='blocked'
    assert out['blocked'][0]['reasons'][0]['reason']=='integration_required'
    with pytest.raises(HarnessError):bootstrap(w,'C')
    assert not (h.state/h.paths['worktrees']/'C').exists()


def test_sprint_artifact_creation_without_task_or_worktree(sprint):
    from batch.helpers import text_artifact
    p,h,w=sprint;r=draft(w,[task(p)]);publish(w,r['revision'])
    result=w.invoke(request('artifacts',{'items':[text_artifact('sprint','shared.md','Общие данные')]}))
    assert result['artifacts'][0]['scope']=='sprint'
    assert Path(result['artifact_paths'][0]).is_file()
    assert h.current_task() is None and not (h.state/h.paths['worktrees']).exists()


def test_request_identity_is_scoped_to_sprint_not_global(sprint):
    from .helpers import changes
    p,h,w=sprint
    for sid,tid in [('S','A'),('OTHER','B')]:
        t=task(p,tid);t['sprint_id']=sid
        out=w.invoke(request('sprint',{'action':'draft','sprint_id':sid,'request_id':'same-draft',
            'expected_revision':None,'template':{'id':'basic','version':'1'},'changes':changes([t])}))
        out=w.invoke(request('sprint',{'action':'publish','sprint_id':None,'request_id':'same-publish','expected_revision':out['revision']}))
        assert out['sprint']==sid and out['eligible']==[tid]


def test_dependency_batch_update_only_for_unstarted_successor(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B')],[{'predecessor':'A','successor':'B','kind':'completion'}]);out=publish(w,r['revision'])
    edit={'action':'dependencies','sprint_id':None,'request_id':'graph-1','expected_revision':out['revision'],
          'items':[],'reason':'Пользователь: задачи независимы'}
    o=w.invoke(request('sprint',edit));assert set(o['eligible'])=={'A','B'}
    bootstrap(w,'B')
    edit.update(request_id='graph-2',expected_revision=o['revision'],items=[{'predecessor':'A','successor':'B','kind':'completion'}])
    with pytest.raises(HarnessError):w.invoke(request('sprint',edit))


def test_published_task_uses_process_snapshot_after_file_edit(sprint):
    from conftest import write_json
    p,h,w=sprint;r=draft(w,[task(p)]);publish(w,r['revision'])
    changed=deepcopy(p['process']);changed['stages'][0]['instruction']='Новые правила другой редакции'
    write_json(p['root']/'config/processes/development.json',changed)
    new=WorkTools(Harness(p['config_path'],'worker'))
    assert bootstrap(new,'A')['instruction']!=changed['stages'][0]['instruction']


def test_force_close_draft_without_publishing_incomplete_tasks(sprint):
    p,h,w=sprint;r=draft(w,[{'id':'A','sprint_id':'S'}]);assert r['errors']
    o=w.invoke(request('sprint',{'action':'force_close','sprint_id':None,'request_id':'close-draft','reason':'Пользователь отменил планирование'}))
    assert o['status']=='cancelled' and h.task_queries.summary()==[]


def test_batch_many_tasks_published_without_per_task_tool_calls(sprint):
    p,h,w=sprint
    tasks=[task(p,f'T-{i}') for i in range(200)]
    r=draft(w,tasks);out=publish(w,r['revision'])
    assert len(out['eligible'])==200 and len(h.task_queries.summary())==200
    assert not (h.state/h.paths['worktrees']).exists()


def test_parallel_verifies_same_file_different_tasks(sprint):
    from concurrent.futures import ThreadPoolExecutor
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B')]);publish(w,r['revision'])
    a=bootstrap(w,'A');other=WorkTools(Harness(p['config_path'],'agent-B'));b=bootstrap(other,'B')
    (Path(a['worktree'])/'src/double.py').write_text('def double(n):\n    return n*2\n')
    (Path(b['worktree'])/'src/double.py').write_text('def double(n):\n    return n+n\n')
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa=pool.submit(verify,w,a);fb=pool.submit(verify,other,b)
        ra,rb=fa.result(),fb.result()
    assert ra['status']==rb['status']=='verified' and ra['commit']!=rb['commit']
    assert 'n*2' in (Path(a['worktree'])/'src/double.py').read_text()
    assert 'n+n' in (Path(b['worktree'])/'src/double.py').read_text()
    w.invoke(request('accept',{}));out=other.invoke(request('accept',{}))
    assert out['sprint']['status']=='completed'


def test_fk_existing_sprint_cannot_reference_member_of_another(sprint):
    from .helpers import changes
    p,h,w=sprint;r=draft(w,[task(p,'A')]);publish(w,r['revision'])
    t=task(p,'B');t['sprint_id']='OTHER'
    o=w.invoke(request('sprint',{'action':'draft','sprint_id':'OTHER','request_id':'draft',
         'expected_revision':None,'template':{'id':'basic','version':'1'},'changes':changes([t])}))
    w.invoke(request('sprint',{'action':'publish','sprint_id':None,'request_id':'pub','expected_revision':o['revision']}))
    with pytest.raises(sqlite3.IntegrityError):
        with h.store.transaction() as db:
            db.execute("INSERT INTO sprint_dependencies VALUES('OTHER','A','B','result')")


def test_graph_edit_cycle_rolls_back_previous_edges(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B')],[{'predecessor':'A','successor':'B','kind':'completion'}]);o=publish(w,r['revision'])
    with pytest.raises(HarnessError):
        w.invoke(request('sprint',{'action':'dependencies','sprint_id':None,'request_id':'cycle','expected_revision':o['revision'],
            'items':[{'predecessor':'A','successor':'B','kind':'completion'},{'predecessor':'B','successor':'A','kind':'result'}],
            'reason':'Ошибочный новый план'}))
    assert bootstrap(w,'S')['eligible']==['A']


def test_rework_preserves_membership_and_later_dependency_stays_blocked(sprint):
    p,h,w=sprint;r=draft(w,[task(p,'A'),task(p,'B')],[{'predecessor':'A','successor':'B','kind':'completion'}]);publish(w,r['revision'])
    ctx=bootstrap(w,'A');verify(w,ctx)
    ctx=w.invoke(request('bootstrap',{'task':None,'decision':'rework','feedback':'Уточнить отчёт','rework_stage':None}))
    assert ctx['iteration']==2
    assert bootstrap(w,'S')['eligible']==[]
    verify(w,ctx,'Уточнённый результат');o=w.invoke(request('accept',{}))
    assert o['sprint']['eligible']==['B']
    with h.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM task_results WHERE task_id='A'").fetchone()[0]==2
