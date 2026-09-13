import json
from copy import deepcopy
from pathlib import Path
import pytest
from poise.composition import project_tools
from poise.common import PoiseError,load_config,digest
from conftest import WorkPoise as Poise
from poise.application.work import WorkTools
from tests.conftest import git,write_json
from .helpers import setup_case


def test_setup_creates_independent_runtime_config_without_task_or_git_mutation(project):
    settings,_,req=setup_case(project)
    before=git(project['app'],'status','--porcelain')
    result=project_tools(settings).apply(req)
    root,cfg,processes=load_config(Path(result['config_path']))
    assert result['status']=='created' and result['readiness']['repository']=='verified'
    assert cfg['project']=='pilot' and set(processes)=={'development'}
    assert not (root/cfg['paths']['state']).exists()
    assert git(project['app'],'status','--porcelain')==before
    source=project['root']/req['template']['id'] # actual copy, not symlink
    assert (root/cfg['processes']['development']).is_file()
    assert not (root/cfg['processes']['development']).is_symlink()
    response=WorkTools(Poise(result['config_path'],'pilot')).invoke({'operation':'bootstrap','input':{'task':project['task'],'decision':None,'feedback':None,'rework_stage':None},'messages':[]})
    assert response['task']=='T1' and response['stage']=='tests'
    assert Path(response['worktree']).is_dir()


def test_repeat_is_exact_and_never_overwrites_later_configuration(project):
    settings,_,req=setup_case(project);tool=project_tools(settings)
    first=tool.apply(req);second=tool.apply(req)
    assert second['replayed'] is True and first['revision']==second['revision']
    p=Path(first['config_path']);c=json.loads(p.read_text());c['project']='later';write_json(p,c)
    with pytest.raises(PoiseError):tool.apply(req)
    assert json.loads(p.read_text())['project']=='later'


def test_replay_does_not_require_source_template_to_remain_unchanged(project):
    settings,raw,req=setup_case(project);tool=project_tools(settings);r=tool.apply(req)
    raw['version']='2';write_json(project['root']/'config/project-blueprint.json',raw)
    assert tool.apply(req)['revision']==r['revision']


def test_conflicting_request_does_not_replace_existing_project(project):
    settings,_,req=setup_case(project);tool=project_tools(settings);r=tool.apply(req)
    before=Path(r['config_path']).read_bytes();req['edits'][0]['value']='wrong'
    with pytest.raises(PoiseError):tool.apply(req)
    assert Path(r['config_path']).read_bytes()==before


@pytest.mark.parametrize('failure',['schema','process','revision','repository','branch','escape','symlink','missing_policy'])
def test_failed_preflight_publishes_no_project(project,failure):
    settings,raw,req=setup_case(project)
    if failure=='schema':req['schema']='old-format'
    if failure=='process':write_json(project['root']/'config/processes/development.json',{'goal_type':'bad'})
    if failure=='revision':req['template']['digest']='0'*64
    if failure=='repository':req['edits'][1]['value']=str(project['root']/'missing')
    if failure=='branch':req['edits'].append({'path':['git','base_ref'],'value':'absent'})
    if failure=='escape':req['destination']='../outside'
    if failure=='symlink':
        (project['root']/'configured').symlink_to(project['app'],target_is_directory=True)
    if failure=='missing_policy':
        c=json.loads(settings.read_text());del c['file_mode'];write_json(settings,c)
    with pytest.raises(PoiseError):project_tools(settings).apply(req)
    assert not (project['root']/'configured/pilot').exists()
    assert not (project['app']/'pilot').exists()


def test_push_disabled_setup_does_not_require_configured_remote(project):
    settings,_,req=setup_case(project)
    req['edits'].append({'path':['git','remote'],'value':'missing'})

    result=project_tools(settings).apply(req)

    assert result['readiness']['remote']=='not_required'
    assert json.loads(Path(result['config_path']).read_text())['git']['push_required'] is False


def test_repo_probe_can_be_skipped_only_explicitly_and_is_reported(project):
    settings,_,req=setup_case(project);req['probe_repository']=False
    req['edits'][1]['value']=str(project['root']/'not-yet-mounted')
    r=project_tools(settings).apply(req)
    assert r['readiness']['repository']=='not_checked'


def test_failure_before_atomic_publication_leaves_no_partial_project(project,monkeypatch):
    import poise.infrastructure.projects as module
    settings,_,req=setup_case(project);tool=project_tools(settings)
    original=module.publish_directory
    def fail(*args):raise OSError('simulated storage failure')
    monkeypatch.setattr(module,'publish_directory',fail)
    with pytest.raises(PoiseError):tool.apply(req)
    assert not (project['root']/'configured/pilot').exists()
    monkeypatch.setattr(module,'publish_directory',original)
    assert tool.apply(req)['status']=='created'


def test_process_and_manifest_destination_collision_is_rejected(project):
    settings,raw,req=setup_case(project)
    raw['config']['processes']['development']='project.json'
    write_json(project['root']/'config/project-blueprint.json',raw)
    cfg=json.loads(settings.read_text());cfg['templates']['selected']['digest']=digest(raw);write_json(settings,cfg)
    req['template']['digest']=digest(raw)
    with pytest.raises(PoiseError):project_tools(settings).apply(req)
    assert not (project['root']/'configured/pilot').exists()


def test_invalid_project_configuration_is_rejected_before_git_probe(project,monkeypatch):
    from poise.infrastructure.projects import FileProjectSetup
    settings,_,req=setup_case(project)
    req['edits'].append({'path':['limits'],'value':{}})
    def forbidden(*args):raise AssertionError('must validate complete candidate before Git')
    monkeypatch.setattr(FileProjectSetup,'_probe',forbidden)
    with pytest.raises(PoiseError):project_tools(settings).apply(req)
    assert not (project['root']/'configured/pilot').exists()


@pytest.mark.parametrize('field,value',[('base_ref',['main']),('remote',7),('repository',None)])
def test_wrong_git_value_is_contract_rejection_not_uncaught_python_exception(project,field,value):
    settings,_,req=setup_case(project)
    if field=='repository':req['edits'][1]['value']=value
    else:req['edits'].append({'path':['git',field],'value':value})
    with pytest.raises(PoiseError):project_tools(settings).apply(req)
    assert not (project['root']/'configured/pilot').exists()


def test_generated_manifest_and_mutable_state_must_not_overlap(project):
    settings,_,req=setup_case(project)
    req['edits'].append({'path':['paths','state'],'value':'project.json'})
    with pytest.raises(PoiseError):project_tools(settings).apply(req)
    assert not (project['root']/'configured/pilot').exists()

def test_project_manifest_can_point_to_absolute_mutable_state_outside_poise_root(project):
    settings,_,req=setup_case(project)
    external=(project['root'].parent/'external-system-state').resolve()
    req['edits'].append({'path':['paths','state'],'value':str(external)})
    result=project_tools(settings).apply(req)
    root,cfg,_=load_config(Path(result['config_path']))
    assert Path(cfg['paths']['state']).is_absolute()
    assert not external.exists()
    h=Poise(result['config_path'],'absolute-state')
    response=WorkTools(h).invoke({'operation':'bootstrap','input':{'task':None,'decision':None,'feedback':None,'rework_stage':None},'messages':[]})
    assert response['status']=='read_only'
    assert Path(response['runtime_root']).is_relative_to(external)
    assert external.exists()


def test_project_rejects_filesystem_root_as_mutable_state(project):
    settings,_,req=setup_case(project)
    req['edits'].append({'path':['paths','state'],'value':Path(project['root'].anchor).as_posix()})
    with pytest.raises(PoiseError,match='корень файловой системы'):
        project_tools(settings).apply(req)
