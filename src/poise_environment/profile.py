"""Materialize a versioned deployment catalogue; never create application state."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from environment_maintenance.shared.filesystem import lexical_path
from poise.common import digest


def generate(args):
    source=lexical_path(args.source)
    output=lexical_path(args.output)
    if output.exists():
        raise ValueError('Profile destination already exists; existing configuration is never replaced')
    settings=json.loads((source/'config/project-setup.json').read_text())
    selection=settings['templates'][args.template]
    blueprint=json.loads((source/selection['path']).read_text())
    if digest(blueprint)!=selection['digest']:
        raise ValueError('Source project template digest mismatch')
    state=str(lexical_path(args.state)); repository=str(lexical_path(args.repository))
    if not output.parent.is_dir():raise ValueError('Profile parent directory must exist')
    # The source locator is an explicit development option, empty in installed delivery.
    module_paths=args.module_path or ''
    params={name:{'type':'string','default':value} for name,value in {
        'python':sys.executable,'module_path':module_paths,'profile_root':str(output),
        'state_root':state,'repository':repository,'project_id':args.project,
        'base_ref':args.base_ref,'author_name':args.author_name,'author_email':args.author_email,
        'git':'git','directory_mode':'0750','python_minimum':'3.13','python_maximum':'3.14',
        'template_id':args.template,'remote':'disabled'}.items()}
    requirements=[];routes={}
    def repair(identity,kind,module,argv):
        requirements.append({'id':identity,'kind':kind,'check':{'repair':identity},'apply':{'repair':identity},'recommendation':{'repair':identity}})
        routes[identity]={'argv':['${python}','-B','-m',module,*argv],'timeout':25,
                          'env':{'PYTHONPATH':'${module_path}','PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8'}}
    repair('python-runtime','deps','environment_maintenance.repairs.python_runtime',
           ['--minimum','${python_minimum}','--maximum','${python_maximum}'])
    requirements.append({'id':'git-executable','kind':'deps',
        'check':{'argv':['${git}','--version'],'timeout':10},
        'apply':{'argv':['apt-get','install','--yes','git'],'timeout':120},
        'recommendation':'Git недоступен. Укажите установленный Git через --set git=/path/git либо установите пакет git средствами Ubuntu. Команда deps не повышает привилегии.'})
    repair('target-repository','infra','poise_environment.repairs.repository',[
        '--repository','${repository}','--base-ref','${base_ref}','--git','${git}'])
    for identity,suffix in [('state-root',''),('runtime-directory','/runtime'),('task-directory','/standalone'),('sprint-directory','/sprints'),('worktree-directory','/worktrees'),('requirements-directory','/database'),('telemetry-directory','/telemetry')]:
        repair(identity,'infra','environment_maintenance.repairs.directory',['--path','${state_root}'+suffix,'--permissions','${directory_mode}'])
    repair('poise-project-config','infra','poise_environment.repairs.project_config',[
        '--settings','${profile_root}/setup.json','--template','${template_id}',
        '--project','${project_id}','--repository','${repository}','--state','${state_root}',
        '--base-ref','${base_ref}','--author-name','${author_name}','--author-email','${author_email}',
        '--remote','${remote}'])
    for identity,module in [('poise-task-database','task_database'),('poise-requirements-database','requirements_database'),('poise-telemetry-database','telemetry_database')]:
        repair(identity,'infra','poise_environment.repairs.'+module,['--config','${profile_root}/project/project.json','--state-root','${state_root}'])
    with tempfile.TemporaryDirectory(prefix='.profile-',dir=output.parent) as tmp:
        stage=Path(tmp)/'profile';stage.mkdir(mode=0o750)
        def write(path,value):
            p=stage/path;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');p.chmod(0o600)
        write(selection['path'],blueprint)
        for ref in blueprint['process_sources'].values():
            doc=json.loads((source/ref['path']).read_text())
            if digest(doc)!=ref['digest']:raise ValueError('Process template digest mismatch')
            write(ref['path'],doc)
        # New setup registry, not the historical source registry or development DB.
        settings.update(root='.',templates={args.template:selection},registry='configured-projects.json',lock='setup.lock')
        write('setup.json',settings)
        write('configured-projects.json',{'schema':'configured-project-registry-1','projects':{}})
        write('requirements.json',{'schema':'environment-maintenance/requirements/v1',
              'parameters':params,'inherit_env':['PATH','HOME','LANG'],'requirements':requirements})
        write('repairs.json',{'schema':'environment-maintenance/repairs/v1','repairs':routes})
        write('values.example.json',{'state_root':state,'repository':repository,'project_id':args.project})
        if output.exists():raise ValueError('Profile destination appeared during creation')
        os.rename(stage,output)
    return {'status':'created','profile':str(output),'requirements':len(requirements),'state_created':False}


def main(argv=None):
    p=argparse.ArgumentParser(description='Создать явный профиль обслуживания Poise из шаблонов поставки')
    for name in ('source','output','state','repository','project','base-ref','author-name','author-email'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--template',default='linux-reference')
    p.add_argument('--module-path',default='')
    args=p.parse_args(argv)
    try:
        print(json.dumps(generate(args),ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError) as e:
        print(json.dumps({'status':'invalid','error':str(e)},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
