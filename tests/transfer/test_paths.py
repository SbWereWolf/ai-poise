from copy import deepcopy
from pathlib import Path
import json
import shutil
import sqlite3
import zipfile
import pytest
from conftest import add_test,git,seed_fixture_requirements
from batch.helpers import bootstrap,result,verify,request,message
from conftest import WorkPoise as Poise
from poise.application.work import WorkTools
from poise.common import PoiseError
from .helpers import enabled,destination,export,restore,pick,handoff_args


def prepared(project):
    enabled(project)
    h=Poise(project['config_path'],'source');tools=WorkTools(h)
    c=bootstrap(tools,project);add_test(c['worktree'])
    payload=result(c,'Keep authored path literal /source/unchanged in this section.')
    return h,tools,c,payload


def test_one_packet_wip_export_and_import_preserves_unrelated_task(project,tmp_path):
    h,a,c,payload=prepared(project)
    made=a.invoke(request('artifacts',{'items':[{'scope':'task','path':'note.txt',
                   'source':{'kind':'text','text':'Important task evidence'}}]}))
    payload['artifact_paths']=made['artifact_paths']
    saved=export(a,handoff=handoff_args(payload))
    assert saved['status']=='exported' and h.current_task() is None
    dst=destination(project,tmp_path/'destination')
    seed_fixture_requirements(dst['root'],dst['cfg'])
    b=WorkTools(Poise(dst['config_path'],'other'))
    other=deepcopy(project['task']);other['id']='OTHER'
    bc=b.invoke(request('bootstrap',{'task':other,'decision':None,'feedback':None,'rework_stage':None}))
    add_test(bc['worktree']);verify(b,result(bc))
    before=b.runtime.task_queries.record('OTHER')
    after=restore(b,saved['package_path'],saved['package_digest'])
    assert after['status']=='imported' and after['task_ids']==['T1']
    assert b.runtime.task_queries.record('OTHER')==before
    assert b.runtime.current_task()['id']=='OTHER'
    new=WorkTools(Poise(dst['config_path'],'receiver'));context=pick(new,'T1')
    assert context['stage']==c['stage'] and context['iteration']==c['iteration']
    assert context['worktree']!=c['worktree']
    assert context['result_template']['sections']==payload['sections']
    artifact=Path(context['result_template']['artifact_paths'][0])
    assert artifact.is_relative_to(dst['root']) and artifact.read_text()=='Important task evidence'
    assert verify(new,context['result_template'])['status']=='verified'
    assert git(dst['app'],'rev-parse','main')==git(project['app'],'rev-parse','main')


def test_verified_transfer_keeps_report_and_full_output_after_source_removed(project,tmp_path):
    h,a,c,payload=prepared(project)
    report=verify(a,payload)
    saved=export(a,handoff=handoff_args(None))
    dst=destination(project,tmp_path/'destination');b=WorkTools(Poise(dst['config_path'],'receiver'))
    restore(b,saved['package_path'],saved['package_digest'])
    shutil.rmtree(project['root'])
    context=pick(b,'T1');assert context['status']=='verified'
    receipt_id=report['checks'][0]['id']
    detail=b.invoke(request('show',{'queries':[{'id':'out','kind':'tool_result',
          'receipt_id':receipt_id,'representation':'full','range':None}]}))
    assert 'test_double' in detail['results'][0]['value']['text']
    count=b.runtime.store.counts('T1')
    repeated=verify(b,None)
    assert repeated['replayed'] and b.runtime.store.counts('T1')==count
    assert all(Path(x['path']).exists() for x in repeated['artifacts'])


def test_repeated_import_does_not_reset_progress_or_duplicate_rows(project,tmp_path):
    h,a,c,payload=prepared(project);saved=export(a,handoff=handoff_args(payload))
    dst=destination(project,tmp_path/'destination');b=WorkTools(Poise(dst['config_path'],'receiver'))
    first=restore(b,saved['package_path'],saved['package_digest'])
    current=pick(b,'T1');verify(b,current['result_template']);count=b.runtime.store.counts('T1')
    again=restore(b,saved['package_path'],saved['package_digest'])
    assert again['replayed'] and b.runtime.store.counts('T1')==count
    assert b.runtime.current_task()['status']=='verified'
    with b.runtime.store.transaction() as db:
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_existing_task_is_not_replaced_even_with_same_identifier(project,tmp_path):
    h,a,c,payload=prepared(project);saved=export(a,handoff=handoff_args(payload))
    dst=destination(project,tmp_path/'destination')
    seed_fixture_requirements(dst['root'],dst['cfg'])
    b=WorkTools(Poise(dst['config_path'],'receiver'))
    bootstrap(b,dst);prior=b.runtime.current_task()
    with pytest.raises(PoiseError,match='exist|conflict|collision'):
        restore(b,saved['package_path'],saved['package_digest'])
    assert b.runtime.current_task()==prior


def test_export_requires_explicit_handoff_before_copying_active_work(project):
    h,a,c,payload=prepared(project)
    with pytest.raises(PoiseError,match='handoff|owned|claimed'):
        export(a,ids=['T1'])
    assert h.current_task()['claimed_by']=='source'


def test_repeated_export_returns_same_package(project):
    h,a,c,payload=prepared(project);args=handoff_args(payload)
    first=export(a,handoff=args)
    second=export(a,handoff=args)
    assert second['replayed'] and second['package_digest']==first['package_digest']
    assert second['package_path']==first['package_path']


def test_export_ignores_task_config_digest(project):
    from conftest import write_json

    h,source,context,payload=prepared(project)
    source.invoke(request('handoff',handoff_args(payload)))
    saved_worktree=context['worktree']
    saved_contract=deepcopy(h.task_queries.record('T1')['contract'])

    project['cfg']['limits']['output_chars']+=1
    write_json(project['config_path'],project['cfg'])
    current=WorkTools(Poise(project['config_path'],'source'))
    package=export(current,ids=['T1'],request_id='export-after-config-change')

    assert package['status']=='exported'
    assert Path(package['package_path']).is_file()
    assert current.runtime.task_queries.record('T1')['contract']==saved_contract
    assert current.runtime.task_queries.record('T1')['worktree']==saved_worktree


def test_package_contains_only_selected_owners_and_no_active_sessions(project):
    h,a,c,payload=prepared(project);export(a,handoff=handoff_args(payload))
    other=deepcopy(project['task']);other['id']='PRIVATE'
    bootstrap(WorkTools(Poise(project['config_path'],'private')),dict(project,task=other))
    saved=export(a,ids=['T1'],request_id='subset')
    with zipfile.ZipFile(saved['package_path']) as z:
        dbpath=project['root']/'inspection.sqlite';dbpath.write_bytes(z.read('snapshot.sqlite'))
    with sqlite3.connect(dbpath) as db:
        assert db.execute('SELECT id FROM tasks').fetchall()==[('T1',)]
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM runtime_bindings').fetchone()[0]==0
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_message_history_survives_transfer_without_duplicate_charges(project,tmp_path):
    h,a,c,payload=prepared(project)
    event=message('turn-transfer')
    a.invoke({'operation':'show','input':{'queries':[{'id':'s','kind':'task'}]},'messages':[event]})
    saved=export(a,handoff=handoff_args(payload))
    dst=destination(project,tmp_path/'destination');b=WorkTools(Poise(dst['config_path'],'receiver'))
    restore(b,saved['package_path'],saved['package_digest']);pick(b,'T1')
    out=b.invoke({'operation':'show','input':{'queries':[{'id':'m','kind':'messages'}]},'messages':[event]})
    assert out['interaction']['user_messages_count']==1


def test_other_project_or_changed_execution_policy_rejected(project,tmp_path):
    h,a,c,payload=prepared(project);saved=export(a,handoff=handoff_args(payload))
    dst=destination(project,tmp_path/'destination');dst['cfg']['limits']['verify_attempts']+=1
    from conftest import write_json
    write_json(dst['config_path'],dst['cfg'])
    b=WorkTools(Poise(dst['config_path'],'receiver'))
    with pytest.raises(PoiseError,match='config|policy|project'):
        restore(b,saved['package_path'],saved['package_digest'])
    assert b.runtime.task_queries.record('T1') is None


def test_whole_sprint_import_preserves_membership_dependencies_and_unstarted_tasks(project,tmp_path):
    from sprints.helpers import setup,task,draft,publish,bootstrap as choose,verify as done
    enabled(project);setup(project)
    h=Poise(project['config_path'],'source');a=WorkTools(h)
    tasks=[task(project,'A'),task(project,'B')]
    r=draft(a,tasks,[{'predecessor':'A','successor':'B','kind':'result'}]);publish(a,r['revision'])
    c=choose(a,'A');done(a,c);a.invoke(request('accept',{}))
    saved=export(a,sprint='S')
    dst=destination(project,tmp_path/'destination');b=WorkTools(Poise(dst['config_path'],'receiver'))
    restore(b,saved['package_path'],saved['package_digest'])
    overview=choose(b,'S');assert overview['sprint']=='S'
    nxt=choose(b,'B');assert nxt['stage']=='work'
    assert b.runtime.current_task()['base']==h.task_queries.record('A')['last_report']['commit']
    assert done(b,nxt)['status']=='verified'


def test_partial_sprint_export_is_rejected_instead_of_silently_copying_siblings(project):
    from sprints.helpers import setup,task,draft,publish
    enabled(project);setup(project);a=WorkTools(Poise(project['config_path'],'source'))
    r=draft(a,[task(project,'A'),task(project,'B')]);publish(a,r['revision'])
    with pytest.raises(PoiseError,match='sprint|scope'):
        export(a,ids=['A'])


def test_completed_result_integration_is_transferable(project):
    from result_integration.helpers import integration_input,prepare_completed_task

    enabled(project)

    def source_change(worktree):
        (worktree/'src'/'portable.py').write_text('PORTABLE = True\n')

    tools,_,accepted=prepare_completed_task(project,source_change)
    integrated=tools.invoke(request('integrate',integration_input(project,accepted)))

    assert integrated['status']=='integrated'
    saved=export(tools,ids=['T1'],request_id='export-integrated-task')
    assert saved['status']=='exported'


def test_registered_method_order_is_preserved_across_store_transfer(project,tmp_path):
    h,a,c,payload=prepared(project);saved=export(a,handoff=handoff_args(payload))
    before=h.task_queries.record('T1')['contract']['methods']
    dst=destination(project,tmp_path/'destination');b=WorkTools(Poise(dst['config_path'],'receiver'))
    restore(b,saved['package_path'],saved['package_digest'])
    after=b.runtime.task_queries.record('T1')['contract']['methods']
    assert [m['id'] for m in before]==['RED','GREEN']
    assert after==before
