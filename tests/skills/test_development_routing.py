"""C004: single deterministic route from explicit facts, no execution/lifecycle."""
import copy
import json
from pathlib import Path
import pytest
from poise.common import PoiseError
from poise.infrastructure.development_routing import load_development_routing


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value) if not isinstance(value,str) else value)
    return path


@pytest.fixture
def configured(tmp_path):
    assets=tmp_path/'running-configuration';checkout=tmp_path/'arbitrary target';checkout.mkdir()
    write(checkout/'pyproject.toml','[project]\nname="ai-poise"\n')
    write(checkout/'AGENTS.md','# root rules\n')
    write(checkout/'src/AGENTS.md','# source rules\n')
    write(checkout/'src/example.py','VALUE = 1\n')
    skills=[]
    for name in ['poise','tdd','review']:
        path=f'.agents/skills/{name}/SKILL.md';write(checkout/path,'# '+name+'\n')
        skills.append({'id':name,'path':path,'purpose':name,'class':'common','level':None,'responsibility':None})
    catalog=write(assets/'catalog.json',{'schema':'poise-skill-catalog-1','skills':skills})
    selection=write(assets/'selection.json',{'schema':'ai-poise-skill-selection-1','subject':'ai-poise','facts':['review'],
        'rules':[{'id':'always','priority':0,'when':{'stages':[],'paths_any':[],'facts_all':[]},'skills':['poise']},
                 {'id':'code','priority':10,'when':{'stages':[],'paths_any':['src/**'],'facts_all':[]},'skills':['tdd']},
                 {'id':'review','priority':5,'when':{'stages':[],'paths_any':[],'facts_all':['review']},'skills':['review']}],
        'excluded':[]})
    policy=write(assets/'policy.json',{'schema':'ai-poise-development-routing-1','known_paths':['src/**','docs/**','AGENTS.md'],
         'handler_facts':{'produce':[],'inspect':['review']},
         'checks':[{'id':'code-checks','paths_any':['src/**'],'packages':['targeted']} ]})
    packages=write(assets/'packages.json',{'schema':'ai-poise-test-packages-1','packages':[
        {'id':'targeted','owner':'test','integration_boundaries':[],
         'members':{'source':['src/example.py'],'tests':['tests/test_example.py'],'support':[],'fixtures':[]}}]})
    paths={'catalog':str(catalog),'selection':str(selection),'policy':str(policy),'packages':str(packages)}
    request={'checkout':str(checkout),'stage':'implementation','handler':'produce','scope_paths':['src/example.py'],
             'changed_paths':['src/example.py'],'facts':[],'required_methods':['M'], 'registered_methods':['M'],
             'result_contract':{'sections':['report']}}
    return paths,request,checkout


def service(paths): return load_development_routing(**{k:Path(v) for k,v in paths.items()})


def test_deterministic_route_selects_rules_skills_and_preserves_required_checks(configured):
    paths,req,checkout=configured;router=service(paths)
    result=router.route(req)
    assert result['status']=='ready'
    assert result['skills']==['poise','tdd']
    assert [r['path'] for r in result['rules']]==['AGENTS.md','src/AGENTS.md']
    assert result['checks']=={'required_methods':['M'],'suggested_packages':['targeted'],'executed':False}
    assert result['result_contract']=={'sections':['report']}
    assert result==router.route(copy.deepcopy(req))
    assert not (checkout/'routing.json').exists()


def test_update_uses_same_owner_and_branch_policy_bytes(configured):
    paths,req,checkout=configured;router=service(paths);first=router.route(req)
    req['handler']='inspect';second=router.route(req)
    assert second['skills']==['poise','review','tdd']
    assert second['route_digest']!=first['route_digest']
    write(checkout/'src/AGENTS.md','# changed rules\n')
    assert router.route(req)['route_digest']!=second['route_digest']


def test_target_configuration_cannot_replace_running_routing_metadata(configured):
    paths,req,checkout=configured
    write(checkout/'config/development/skill-selection.json','this is not a routing config')
    assert service(paths).route(req)['skills']==['poise','tdd']


def test_unknown_paths_and_facts_are_visible_without_full_suite_fallback(configured):
    paths,req,_=configured;req['changed_paths']=['unknown/area.py'];req['facts']=['invented']
    result=service(paths).route(req)
    assert result['status']=='needs_inputs'
    assert 'unmapped_path:unknown/area.py' in result['missing_inputs']
    assert 'unknown_fact:invented' in result['missing_inputs']
    assert result['checks']['required_methods']==['M']
    assert 'full' not in str(result['checks'])


def test_unregistered_required_method_is_not_silently_dropped(configured):
    paths,req,_=configured;req['required_methods']=['lost']
    result=service(paths).route(req)
    assert 'unregistered_method:lost' in result['missing_inputs']
    assert result['checks']['required_methods']==['lost']


def test_metadata_configuration_validation_precedes_subject_read(configured):
    paths,req,_=configured
    raw=json.loads(Path(paths['policy']).read_text());raw['checks'][0]['packages']=['invented'];write(Path(paths['policy']),raw)
    with pytest.raises(PoiseError):service(paths)


@pytest.mark.parametrize('path',['../foreign/file.py','/etc/passwd'])
def test_escape_rejected(configured,path):
    paths,req,_=configured;req['scope_paths']=[path]
    with pytest.raises(PoiseError):service(paths).route(req)


def test_snapshot_replay_validates_provenance_not_only_json_shape(configured,tmp_path):
    paths,req,_=configured;router=service(paths);p=tmp_path/'session/route.json'
    result=router.route(req,snapshot=p)
    assert router.read_snapshot(p)==result
    raw=json.loads(p.read_text());raw['skills']=['forged'];write(p,raw)
    with pytest.raises(PoiseError):router.read_snapshot(p)


def test_missing_selected_skill_is_reported_not_replaced(configured):
    paths,req,checkout=configured;(checkout/'.agents/skills/tdd/SKILL.md').unlink()
    route=service(paths).route(req)
    assert route['skills']==['poise','tdd']
    assert route['status']=='needs_inputs'
    assert route['missing_inputs']==['missing_skill:tdd']


def test_scoped_rules_do_not_scan_unrelated_tree(configured):
    paths,req,checkout=configured
    write(checkout/'unrelated/AGENTS.md','# must not load\n')
    req['scope_paths']=['src/**']
    assert [r['path'] for r in service(paths).route(req)['rules']]==['AGENTS.md','src/AGENTS.md']


def test_bootstrap_and_work_refresh_share_route_owner(configured,project):
    from conftest import WorkPoise, git, write_json
    from poise.application.work import WorkTools
    paths,_,copy_path=configured
    import shutil
    for name in ('pyproject.toml','.agents'):
        source=copy_path/name;target=project['app']/name
        shutil.copytree(source,target) if source.is_dir() else shutil.copyfile(source,target)
    git(project['app'],'add','.');git(project['app'],'commit','-m','routing fixture')
    policy=json.loads(Path(paths['policy']).read_text());policy['known_paths'].append('tests/**')
    write(Path(paths['policy']),policy)
    project['cfg']['development_routing']=paths;write_json(project['config_path'],project['cfg'])
    runtime=WorkPoise(project['config_path'],'ROUTE')
    ctx=runtime.bootstrap(project['task'])
    assert ctx['development_route']['skills']==['poise']
    before=runtime.current_task()
    inspected=Path(ctx['worktree']);write(inspected/'src/new.py','VALUE=1\n')
    packet={'operation':'routing','input':{'facts':['review']},'messages':[]}
    result=WorkTools(runtime).invoke(packet)['development_route']
    assert result['skills']==['poise','review','tdd']
    assert result['checks']['required_methods']==['RED']
    assert result['input']['changed_paths']==['src/new.py']
    after=runtime.current_task()
    for field in ('version','status','stage_index','claimed_by'):
        assert before[field]==after[field]
    assert not (inspected/'development-routing.json').exists()
    assert runtime.development_routing.read_snapshot(runtime.runtime/'development-routing.json')==result


def test_without_worktree_uses_existing_configured_checkout(configured,project):
    from conftest import WorkPoise, git, write_json
    import shutil
    paths,_,copy_path=configured
    for name in ('pyproject.toml','.agents'):
        source=copy_path/name;target=project['app']/name
        shutil.copytree(source,target) if source.is_dir() else shutil.copyfile(source,target)
    git(project['app'],'add','.');git(project['app'],'commit','-m','routing fixture')
    project['process']['worktree_required']=False
    write_json(project['root']/'config/processes/development.json',project['process'])
    policy=json.loads(Path(paths['policy']).read_text());policy['known_paths'].append('tests/**');write(Path(paths['policy']),policy)
    project['cfg']['development_routing']=paths;write_json(project['config_path'],project['cfg'])
    runtime=WorkPoise(project['config_path'],'NO-WORKTREE')
    ctx=runtime.bootstrap(project['task'])
    assert ctx['worktree'] is None
    assert ctx['development_route']['input']['checkout']==str(project['app'])
    assert ctx['development_route']['rules'][0]['path']=='AGENTS.md'


def test_optional_route_does_not_change_other_projects(project):
    from conftest import WorkPoise
    h=WorkPoise(project['config_path'],'EXTERNAL')
    result=h.bootstrap(project['task'])
    assert 'development_route' not in result
    with pytest.raises(PoiseError,match='not configured'):
        h.refresh_development_route([])


def test_direct_cli_uses_same_owner_and_explicit_config(configured,tmp_path):
    import subprocess,sys,os
    paths,req,_=configured;repo=Path(__file__).resolve().parents[2]
    command=[sys.executable,'-B',str(repo/'tools/route_development.py')]
    for name,value in paths.items():command.extend(['--'+name,value])
    env={**os.environ,'PYTHONPATH':str(repo/'src')}
    result=subprocess.run(command,input=json.dumps(req),text=True,capture_output=True,cwd=tmp_path,env=env)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==service(paths).route(req)


def test_shipped_policy_accepts_catalog_and_records_intentional_exclusions():
    repo=Path(__file__).resolve().parents[2]
    router=load_development_routing(catalog=repo/'.agents/skill-catalog.json',
        selection=repo/'config/development/skill-selection.json',policy=repo/'config/development/routing.json',
        packages=repo/'config/testing/test-packages.json')
    req={'checkout':str(repo),'stage':'implementation','handler':'produce','scope_paths':['src/poise/modules/skills/**'],
         'changed_paths':[],'facts':[],'required_methods':[],'registered_methods':[],
         'result_contract':{'sections':['report']}}
    result=router.route(req)
    assert result['status']=='ready'
    assert 'ddd' in result['skills'] and 'poise-development' in result['skills']
    assert not set(result['skills']) & {'laravel','vue-best-practices','vitest'}
    assert result['checks']['suggested_packages']==['skills']


def test_domain_and_application_do_not_access_io():
    import ast
    base=Path(__file__).resolve().parents[2]/'src/poise'
    for name in ['modules/skills/routing.py','application/development_routing.py']:
        for node in ast.walk(ast.parse((base/name).read_text())):
            imports=([n.name for n in node.names] if isinstance(node,ast.Import) else
                     [node.module or ''] if isinstance(node,ast.ImportFrom) else [])
            assert not any('infrastructure' in m or m.split('.')[0] in
                {'os','pathlib','subprocess','sqlite3','time'} for m in imports)
