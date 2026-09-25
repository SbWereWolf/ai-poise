import copy
import json
from pathlib import Path
from conftest import Poise
from poise.composition import goal_config_tools
from poise.common import digest
from tests.conftest import write_json, fill, add_test
from tests.goal_config.helpers import request, settings


def test_existing_task_keeps_snapshot_and_new_task_gets_new_pack(project):
    old=Poise(project['config_path'],'old-session').bootstrap(project['task_path'])
    path,_=settings(project['root'],{'development':'config/processes/development.json'})
    before=project['process']; changed_instruction='New explicit instruction for NEW tasks only.'
    goal_config_tools(path).apply_batch(request('update','development',digest(before),[
      {'op':'patch_stage','id':'tests','set':{'instruction':changed_instruction}}]))
    restored=Poise(project['config_path'],'old-session').bootstrap()
    assert restored['instruction']==old['instruction']
    fresh=copy.deepcopy(project['task']); fresh['id']='T2'
    new_path=write_json(project['root']/'task-2.json',fresh)
    new=Poise(project['config_path'],'new-session').bootstrap(new_path)
    assert new['instruction']==changed_instruction
    # Existing execution can still run RED using its own captured stage.
    add_test(restored['worktree']); fill(restored)
    assert Poise(project['config_path'],'old-session').verify()['status']=='verified'


def test_generated_changed_pack_runs_with_new_required_section(project):
    path,_=settings(project['root'],{'development':'config/processes/development.json'})
    goal_config_tools(path).apply_batch(request('update','development',digest(project['process']),[
      {'op':'put_requirement','value':{'id':'new-required','kind':'section','stages':['tests'],'phase':'post','section':'rollback','states':['populated']}},
      {'op':'put_section','value':{'id':'rollback','template':'Describe rollback.','normalization':'strip','write_stages':['tests']}}
    ]))
    project['task']['stage_contracts'][0]['exit_requirements'] = ['new-required']
    write_json(project['task_path'],project['task'])
    h=Poise(project['config_path'],'session'); context=h.bootstrap(project['task_path'])
    add_test(context['worktree']); payload_path=fill(context)
    rejected=h.verify(); assert rejected['status']=='content_requirements_failed'
    assert rejected['content_gate']['phase'] == 'post'
    assert rejected['checks'][0]['actual_exit_code'] == 1
    assert h.show()['status'] == 'active'
    data=payload_path; data['sections']['rollback']='Keep previous task commit; test-only files can be restored.'

    good=h.verify(); assert good['status']=='verified'
