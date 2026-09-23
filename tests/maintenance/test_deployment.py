"""Public deployment boundary checks. Every installation is disposable."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
CORE = REPO.parent / 'environment-maintenance' / 'src'
ENV = dict(os.environ, PYTHONPATH=os.pathsep.join([str(REPO/'src'), str(CORE)]), PYTHONDONTWRITEBYTECODE='1', LANG='C.UTF-8')

def invoke(*args, stdin=None, env=None):
    return subprocess.run([sys.executable,'-B',*map(str,args)],input=stdin,capture_output=True,text=True,env=env or ENV,timeout=120)

def snapshot(root):
    return {str(p.relative_to(root)):(p.stat().st_mode & 0o777,hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_file()}

@pytest.fixture
def installation(tmp_path):
    repo=tmp_path/'target-repository';repo.mkdir()
    for cmd in [['init','-b','main'],['-c','user.name=Acceptance','-c','user.email=acceptance@example.invalid','commit','--allow-empty','-m','Initial target']]:
        subprocess.run(['git','-C',str(repo),*cmd],check=True,capture_output=True)
    profile=tmp_path/'profile'
    r=invoke('-m','poise_environment.profile','--source',REPO,'--output',profile,
             '--state',tmp_path/'state','--repository',repo,'--project','acceptance',
             '--module-path',ENV['PYTHONPATH'],'--base-ref','main','--author-name','Acceptance','--author-email','acceptance@example.invalid')
    assert r.returncode==0,(r.stdout,r.stderr)
    return tmp_path,profile

def command(profile,verb,*extra):
    r=invoke('-m','poise',verb,'--catalog',profile/'requirements.json','--repairs',profile/'repairs.json',*extra)
    try: result=json.loads(r.stdout)
    except ValueError: pytest.fail(f'Expected structured maintenance report: {r.stdout} {r.stderr}')
    return r,result

@pytest.mark.parametrize('verb',['infra','deps','check'])
def test_router_does_not_require_harness_configuration(tmp_path,verb):
    cat=tmp_path/'requirements.json';cat.write_text(json.dumps({'schema':'environment-maintenance/requirements/v1','parameters':{},'requirements':[]}))
    env={k:v for k,v in ENV.items() if not k.startswith('POISE_')}
    r=invoke('-m','poise',verb,'--catalog',cat,env=env)
    assert r.returncode==0,(r.stdout,r.stderr)
    assert json.loads(r.stdout)['status']=='not_required'

def test_harness_help_preserved():
    r=invoke('-m','poise','work','--help');assert r.returncode==0
    assert 'work' in r.stdout

def test_profile_catalogue_is_complete_and_core_independent(installation):
    root,p=installation; c=json.loads((p/'requirements.json').read_text())
    assert len(c['requirements'])>=10
    assert all({'id','check','apply','kind','recommendation'}<=r.keys() for r in c['requirements'])
    assert {r['kind'] for r in c['requirements']}=={'infra','deps'}
    assert not (root/'state').exists()
    r=invoke('-c','import environment_maintenance,sys;assert "poise" not in sys.modules');assert r.returncode==0

def test_missing_and_dry_run_leave_installation_unchanged(installation):
    root,p=installation; before=snapshot(root)
    for op,flags in [('check',[]),('infra',['--dry-run']),('deps',['--dry-run'])]:
        r,result=command(p,op,*flags);assert r.returncode in (0,3)
        assert all('apply' not in x for x in result['requirements'])
    assert before==snapshot(root)
    assert not (root/'state').exists()

def test_real_poise_databases_and_idempotent_repair(installation):
    root,p=installation
    for op in ['deps','infra','check']:
        r,result=command(p,op);assert r.returncode==0,(op,result)
    for name,version in [('state.sqlite',13),('database/requirements.sqlite',1),('telemetry/events.sqlite',13)]:
        db=root/'state'/name
        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as c:
            assert c.execute('PRAGMA user_version').fetchone()[0]==version
            assert c.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
            assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    with sqlite3.connect(root/'state/state.sqlite') as c:assert c.execute('SELECT count(*) FROM tasks').fetchone()[0]==0
    before=snapshot(root)
    for op in ['infra','deps','check']:
        r,result=command(p,op);assert r.returncode==0,result
    assert before==snapshot(root)
    config=p/'project/project.json'
    r=invoke('-c','from poise.common import load_config;from pathlib import Path;import sys;load_config(Path(sys.argv[1]))',config)
    assert r.returncode==0,r.stderr

@pytest.mark.parametrize('damage',['corrupt','version','schema'])
def test_existing_state_damage_is_not_rebuilt(installation,damage):
    root,p=installation;assert command(p,'infra')[0].returncode==0
    db=root/'state/state.sqlite'
    if damage=='corrupt':db.write_bytes(b'NOT A SQLITE DATABASE')
    else:
        with sqlite3.connect(db) as c:
            c.execute('PRAGMA user_version=999' if damage=='version' else 'CREATE TABLE rogue(id)')
    before=snapshot(root)
    for op in ['check','infra']:
        r,report=command(p,op);assert r.returncode!=0
        item=next(r for r in report['requirements'] if r['id']=='poise-task-database')
        assert damage in item['initial_check']['response']['code']
        assert item['recommendation']
    assert before==snapshot(root)

def test_invalid_project_config_preserved(installation):
    root,p=installation;assert command(p,'infra')[0].returncode==0
    (p/'project/project.json').write_text('{bad json');before=snapshot(root)
    assert command(p,'infra')[0].returncode!=0
    assert before==snapshot(root)

def test_cli_parameter_override_is_used_by_all_modules(installation):
    root,p=installation;state=root/'override'
    r,result=command(p,'infra','--set','state_root='+str(state));assert r.returncode==0,result
    assert (state/'state.sqlite').exists() and not (root/'state').exists()
    r,result=command(p,'check','--set','state_root='+str(state));assert r.returncode==0,result
    assert command(p,'check')[0].returncode!=0

@pytest.mark.parametrize('location',['state','project'])
def test_symlink_installation_is_rejected_without_writing_target(installation,location):
    root,p=installation;external=root/'unrelated';external.mkdir();(external/'keep').write_text('preserve')
    (root/'state' if location=='state' else p/'project').symlink_to(external,target_is_directory=True)
    before=snapshot(external)
    r,result=command(p,'infra');assert r.returncode!=0
    assert before==snapshot(external)

def test_existing_profile_not_overwritten(installation):
    root,p=installation;before=snapshot(root)
    r=invoke('-m','poise_environment.profile','--source',REPO,'--output',p,'--state',root/'state','--repository',root/'target-repository','--project','other','--base-ref','main','--author-name','Other','--author-email','other@example.invalid')
    assert r.returncode!=0
    assert before==snapshot(root)


def test_config_drift_cannot_redirect_database_creation(installation):
    root,p=installation
    assert command(p,'infra')[0].returncode==0
    original=root/'state'
    (original/'state.sqlite').unlink()
    before=snapshot(original)
    r,report=command(p,'infra','--set','state_root='+str(root/'new-state'))
    assert r.returncode!=0
    assert snapshot(original)==before
    assert not (original/'state.sqlite').exists()


def test_repository_probe_detects_missing_base_and_does_not_repair(installation):
    root,p=installation
    before=snapshot(root)
    r,report=command(p,'infra','--dry-run','--set','base_ref=missing-branch')
    assert r.returncode != 0
    item=next(x for x in report['requirements'] if x['id']=='target-repository')
    assert item['initial_check']['response']['code']=='base'
    assert snapshot(root)==before
