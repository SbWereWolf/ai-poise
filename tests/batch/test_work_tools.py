from copy import deepcopy
from pathlib import Path
import json
import pytest
from conftest import add_test,write_json
from conftest import WorkPoise as Poise
from poise.application.work import WorkTools
from poise.common import PoiseError
from .helpers import configure,bootstrap,result,verify,request,message,text_artifact


def tools_for(project):
    configure(project)
    return WorkTools(Poise(project['config_path'],'SESSION-BATCH'))


def test_single_packet_result_artifacts_messages_and_replay(project):
    from sprints.helpers import publish_existing_contract
    tools=tools_for(project);project['task']=publish_existing_contract(tools.runtime,project['task'],'S1')
    boot=bootstrap(tools,project,[message()]);add_test(boot['worktree'])
    assert 'result_path' not in boot and not list(Path(boot['runtime_root']).glob('*result*'))
    generated=[text_artifact('task','report.md'),text_artifact('sprint','shared.md'),text_artifact('runtime','scratch.md')]
    value=result(boot)
    first=verify(tools,value,generated,[message()])
    assert first['status']=='verified' and len(first['artifacts'])==2
    assert all(Path(x['path']).is_file() for x in first['artifacts'])
    assert not Path(boot['runtime_root']).exists()
    replay=verify(tools,None,[],[message()])
    assert replay['replayed'] and replay['checks']==first['checks']
    assert replay['interaction']['user_messages_count']==1
    assert replay['interaction']['delivered_stages_count']==1
    assert replay['interaction']['delivered_iterations_count']==1
    assert replay['interaction']['coverage']=='partial'


def test_missing_result_no_legacy_file_read(project):
    tools=tools_for(project);b=bootstrap(tools,project)
    Path(b['runtime_root'],'stage-result.json').write_text('{}')
    with pytest.raises(PoiseError):verify(tools,None)
    assert tools.runtime.show()['submission_count']==0


def test_bad_semantic_payload_has_no_artifact_side_effect(project):
    tools=tools_for(project);b=bootstrap(tools,project)
    value=result(b); value['stage_work']={'unknown':'bad'}
    with pytest.raises(PoiseError):verify(tools,value,[text_artifact()])
    assert not (Path(b['task_root'])/'artifacts/report.md').exists()
    assert tools.runtime.show()['submission_count']==0


def test_count_gate_created_artifacts_are_inputs_same_verify(project):
    configure(project)
    project['process']['stages'][0]['artifact_requirements']=[{'scope':'task','pattern':'artifacts/*.md','minimum':2,'maximum':2}]
    write_json(project['root']/'config/processes/development.json',project['process'])
    tools=WorkTools(Poise(project['config_path'],'S'))
    b=bootstrap(tools,project);add_test(b['worktree'])
    v=verify(tools,result(b),[text_artifact(path='a.md'),text_artifact(path='b.md')])
    assert v['status']=='verified' and len(v['artifacts'])==2


def tools_with_exit_section(project, section, requirement):
    # A produced section is a postcondition, frozen before stage execution.
    # Dynamic content additions do not rewrite an entered StageContract.
    project['task']['content_contract']={
        'sections':[{'id':section,'template':'Fill','write_stages':['tests'],'normalization':'strip'}],
        'routes':[],
        'requirements':[{'id':requirement,'kind':'section','section':section,
                         'stages':['tests'],'phase':'post','states':['populated']}],
    }
    project['task']['stage_contracts'][0]['exit_requirements']=[requirement]
    return tools_for(project)


def test_blocked_content_can_retry_same_created_artifact_without_duplicate(project):
    tools=tools_with_exit_section(project,'reason','need')
    b=bootstrap(tools,project);add_test(b['worktree']);value=result(b)
    blocked=verify(tools,value,[text_artifact()])
    assert blocked['status']=='content_requirements_failed'
    assert blocked['content_gate']['phase']=='post'
    assert tools.runtime.show()['attempts']==1
    artifact=Path(b['task_root'])/'artifacts/report.md'
    original=artifact.read_bytes()
    assert tools.runtime.task_queries.record('T1')['status']=='active'
    with pytest.raises(PoiseError):
        tools.invoke(request('accept',{}))
    value['sections']['reason']='The evidence is sufficient.'
    done=verify(tools,value,[text_artifact()])
    assert done['status']=='verified' and len(done['artifacts'])==1
    assert [c['id'] for c in done['checks']]==[c['id'] for c in blocked['checks']]
    assert tools.runtime.show()['attempts']==1
    assert artifact.read_bytes()==original
    assert Path(done['artifacts'][0]['path'])==artifact


def test_show_batch_sections_with_range_and_metrics(project):
    tools=tools_for(project);b=bootstrap(tools,project);add_test(b['worktree']);verify(tools,result(b,'первая\nвторая\nтретья\n'))
    q=[{'id':'text','kind':'section','name':'report','stage':None,'submission':None,'range':{'unit':'lines','start':2,'end':2}},
       {'id':'metrics','kind':'messages'}, {'id':'state','kind':'task'}]
    r=tools.invoke(request('show',{'queries':q}))
    assert len(r['results'])==3
    text=r['results'][0]['value']
    assert text['text']=='вторая\n' and text['total_lines']==3
    assert text['total_bytes']==len('первая\nвторая\nтретья\n'.encode())
    assert r['results'][1]['value']['coverage']=='unavailable'


def test_messages_batch_exact_once_without_task_id(project):
    tools=tools_for(project);b=bootstrap(tools,project,[message()]);add_test(b['worktree'])
    r=verify(tools,result(b),messages=[message(),message('u2',None)])
    metrics=r['interaction']
    assert metrics['user_messages_count']==2 and metrics['unclassified_messages']==1
    assert metrics['goal_type']=='development' and metrics['planned_content_stages_count']==4
    again=tools.invoke(request('show',{'queries':[{'id':'m','kind':'messages'}]},[message()]))
    assert again['results'][0]['value']['user_messages_count']==2


def test_message_same_identity_different_body_rejected(project):
    tools=tools_for(project);bootstrap(tools,project,[message()])
    with pytest.raises(PoiseError):tools.invoke(request('show',{'queries':[{'id':'m','kind':'messages'}]},[message(reason='feedback')]))


def test_message_batch_is_atomic_on_conflict(project):
    tools=tools_for(project);bootstrap(tools,project,[message()])
    with pytest.raises(PoiseError):tools.invoke(request('show',{'queries':[{'id':'m','kind':'messages'}]},[message('new'),message(reason='feedback')]))
    metrics=tools.invoke(request('show',{'queries':[{'id':'m','kind':'messages'}]}))['results'][0]['value']
    assert metrics['user_messages_count']==1


def test_message_after_cancel_retained(project):
    tools=tools_for(project);bootstrap(tools,project,[message()])
    r=tools.invoke(request('cancel',{'reason':'Пользователь отменил'},[message('u2','cancel')]))
    assert r['status']=='cancelled' and r['interaction']['user_messages_count']==2


def test_bootstrap_task_direct_not_temp_json(project):
    tools=tools_for(project);project['task_path'].unlink()
    b=bootstrap(tools,project);assert b['task']=='T1'
    assert not project['task_path'].exists()


@pytest.mark.parametrize('bad',[{}, {'operation':'bogus','input':{},'messages':[]},
    {'operation':'show','input':{'queries':[]},'messages':[]}])
def test_invalid_request_has_no_task_side_effect(project,bad):
    tools=tools_for(project)
    with pytest.raises(PoiseError):tools.invoke(bad)
    assert tools.runtime.store.current(tools.runtime.session) is None


def test_new_runtime_same_message_no_recount(project):
    tools=tools_for(project);bootstrap(tools,project,[message()])
    new=WorkTools(Poise(project['config_path'],'SESSION-BATCH'))
    b=new.invoke(request('bootstrap',{'task':None,'decision':None,'feedback':None,'rework_stage':None},[message()]))
    assert b['interaction']['user_messages_count']==1


def test_resume_restores_complete_candidate_without_result_file(project):
    tools=tools_with_exit_section(project,'extra','need-extra')
    b=bootstrap(tools,project);add_test(b['worktree']);value=result(b)
    value['content_additions']['sections']=[{'id':'note','template':'Note',
        'write_stages':['tests'],'normalization':'strip'}]
    value['sections']['note']='The complete candidate includes this dynamic addition.'
    blocked=verify(tools,value)
    assert blocked['status']=='content_requirements_failed'
    assert not list(Path(b['runtime_root']).glob('*result*'))
    resumed_tools=WorkTools(Poise(project['config_path'],'SESSION-BATCH'))
    resumed=resumed_tools.invoke(request('bootstrap',{'task':None,'decision':None,'feedback':None,'rework_stage':None}))
    assert resumed['result_template']['sections']==value['sections']
    payload=resumed['result_template'];payload['sections']['extra']='Done'
    done=verify(resumed_tools,payload)
    assert done['status']=='verified'
    assert [c['id'] for c in done['checks']]==[c['id'] for c in blocked['checks']]


def test_failed_code_then_fixed_code_same_direct_payload(project):
    tools=tools_for(project);b=bootstrap(tools,project);add_test(b['worktree'])
    p=result(b)
    Path(b['worktree'],'tests/test_double.py').write_text('import missing_package\n')
    failed=verify(tools,p)
    assert failed['status']=='checks_failed'
    add_test(b['worktree']);ok=verify(tools,p)
    assert ok['status']=='verified' and ok['attempt']==2
    assert tools.runtime.show()['submission_count']==1


def test_repeated_original_packet_does_not_recreate_runtime_artifact(project):
    tools=tools_for(project);b=bootstrap(tools,project);add_test(b['worktree'])
    p=result(b);items=[text_artifact('runtime'),text_artifact()]
    first=verify(tools,p,items);assert first['status']=='verified'
    replay=verify(tools,p,items);assert replay['replayed']
    assert not Path(b['runtime_root']).exists()
    with pytest.raises(PoiseError):verify(tools,p,[text_artifact(text='other')])


def test_artifact_batch_is_available_without_verify_and_path_only_registration(project):
    tools=tools_for(project);b=bootstrap(tools,project);add_test(b['worktree'])
    made=tools.invoke(request('artifacts',{'items':[text_artifact(path=f'{i}.md') for i in range(30)]}))
    assert len(made['artifact_paths'])==30
    assert len(made['artifacts'])==30 and all(a['id'] for a in made['artifacts'])
    p=result(b)  # creation already registered the files; no separate register call
    report=verify(tools,p)
    assert report['status']=='verified' and len(report['artifacts'])==30


def test_one_turn_promotes_from_ad_hoc_to_task_without_double_count(project):
    tools=tools_for(project)
    b=tools.invoke(request('bootstrap',{'task':None,'decision':None,'feedback':None,'rework_stage':None},[message()]))
    assert b['interaction']['observed_messages_count']==1
    bound=bootstrap(tools,project,[message()])
    assert bound['interaction']['observed_messages_count']==1
    with tools.runtime.store.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM interaction_events').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM interaction_bindings').fetchone()[0]==1


def test_message_context_stage_and_iterations_not_verify_attempts(project):
    tools=tools_for(project);b=bootstrap(tools,project,[message()]);add_test(b['worktree'])
    v=verify(tools,result(b),messages=[message()])
    again=verify(tools,None,messages=[message()])
    assert again['interaction']['delivered_iterations_count']==1
    b=tools.invoke(request('bootstrap',{'task':None,'decision':'rework','feedback':'Clarify report','rework_stage':None},[message('u2','feedback')]))
    v=verify(tools,result(b,'Clarified'),messages=[message('u2','feedback')])
    m=v['interaction']
    assert m['user_messages_count']==2 and m['delivered_stages_count']==1 and m['delivered_iterations_count']==2
    assert m['reason_counts']['feedback']==1
    b=tools.invoke(request('bootstrap',{'task':None,'decision':'continue','feedback':None,'rework_stage':None},[message('u3','continue')]))
    v=verify(tools,result(b),messages=[message('u3','continue')])
    assert v['interaction']['delivered_stages_count']==2 and v['interaction']['delivered_iterations_count']==3
    assert v['interaction']['reason_counts']['feedback']==1  # normal continue is not a defect


def test_secondary_task_cannot_steal_primary_message_binding(project):
    tools=tools_for(project);bootstrap(tools,project,[message()])
    tools.invoke(request('cancel',{'reason':'cancelled'}))
    project['task']['id']='T2'
    b=bootstrap(tools,project,[message()])
    assert b['interaction']['coverage']=='unavailable'
    with tools.runtime.store.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM interaction_events').fetchone()[0]==1
        assert db.execute('SELECT task_id FROM interaction_bindings').fetchone()[0]=='T1'


def test_bad_message_time_and_unknown_reason_rejected_without_work(project):
    tools=tools_for(project)
    for m in ({**message(),'occurred_at':'2026-09-06T12:00:00'},message(reason='guess-from-wording')):
        with pytest.raises(PoiseError):bootstrap(tools,project,[m])
    assert tools.runtime.store.current(tools.runtime.session) is None


def test_mixed_evidence_continuation_direct_batch_no_rerun(project):
    from evidence.test_paths import setup,arg
    configure(project);unused,counter=setup(project)
    process=json.loads((project['root']/'config/processes/verification_demo.json').read_text())
    # The observation process is measure/audit, not the development route.
    project['task']['decomposition']['phases']=[
        {'stage':stage['id'],'skills':['task-domain'],'areas':[]}
        for stage in process['stages']]
    project['task']['stage_contracts']=[
        {'stage_id':stage['id'],'allowed_paths':list(stage['allowed_paths']),
         'entry_requirements':[],'exit_requirements':[]}
        for stage in process['stages']]
    tools=WorkTools(Poise(project['config_path'],'DIRECT-MIXED'))
    b=bootstrap(tools,project,[message()]);p=result(b)
    first=verify(tools,p,messages=[message()]);assert first['status']=='awaiting_continuation'
    p=first['context']['result_template'];p['evidence_work']['arguments']=[arg([first['checks'][0]['id']])]
    assert p['evidence_work']['phase']=='continue'
    final=verify(tools,p,messages=[message()]);assert final['status']=='verified'
    assert counter.read_text()=='x' and final['interaction']['user_messages_count']==1


def test_bad_existing_artifact_path_prevents_new_file_publication(project):
    tools=tools_for(project);b=bootstrap(tools,project)
    p=result(b);p['artifact_paths']=[str(project['app']/'src/double.py')]
    with pytest.raises(PoiseError):verify(tools,p,[text_artifact()])
    assert not (Path(b['task_root'])/'artifacts/report.md').exists()


def test_batch_show_no_repeat_task_id_and_bad_query_rejected(project):
    tools=tools_for(project);b=bootstrap(tools,project);add_test(b['worktree']);verify(tools,result(b))
    with pytest.raises(PoiseError):tools.invoke(request('show',{'queries':[{'id':'x','kind':'task'},{'id':'x','kind':'messages'}]}))
    q={'id':'text','kind':'section','name':'report','stage':None,'submission':None,'range':{'unit':'bytes','start':1,'end':2}}
    with pytest.raises(PoiseError):tools.invoke(request('show',{'queries':[q]}))


def test_content_query_in_batch_retains_current_process_position(project):
    tools=tools_for(project);b=bootstrap(tools,project)
    response=tools.invoke(request('show',{'queries':[{'id':'content','kind':'content'}]}))
    content=response['results'][0]['value']
    assert content['stage']==b['stage'] and content['iteration']==b['iteration']
    assert content['task']==b['task']
