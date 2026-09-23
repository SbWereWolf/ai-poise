"""Poise project configuration: reuse its existing publication/validation owner."""
import argparse
import os
from pathlib import Path
from environment_maintenance.shared.filesystem import lexical_path,regular_or_missing
from environment_maintenance.shared.protocol import serve,result,recommend
from poise.application.projects import ProjectCommands
from poise.common import PoiseError,load_config,configured_root,digest
from poise.infrastructure.projects import ProjectSettings,FileProjectSetup

TEMPLATES={
 'missing':'Конфигурация {path} отсутствует. Выполните infra с тем же профилем: проект создаётся штатным владельцем конфигурации Poise.',
 'invalid_config':'Конфигурация {path} невалидна. Сохраните её и исправьте через владельца конфигурации Poise; автоматической замены нет.',
 'configuration_drift':'Настройки {path} расходятся с эффективными параметрами. Уточните --set/--values либо выполните явную управляемую ревизию конфигурации.',
 'symlink':'Путь {path} содержит символическую ссылку. Ремонт не выполняется; укажите настоящий каталог.',
 'unknown':'Проверьте {path}, доступность локального Git-репозитория и исходную ошибку. Пользовательские файлы не перезаписываются.'}


def handle(args,request):
    path=Path(args.settings).absolute().parent/'project/project.json'
    if request['mode']=='recommend':return recommend(request,TEMPLATES,{'path':path})
    path=lexical_path(path);settings=ProjectSettings(lexical_path(args.settings))
    port=FileProjectSetup(settings)
    if regular_or_missing(path):
        try:
            root,cfg,_=load_config(path)
        except PoiseError as e:return result('action_required','invalid_config',detail=str(e))
        expected={'repository':args.repository,'base_ref':args.base_ref,'author_name':args.author_name,'author_email':args.author_email,'remote':args.remote,'push_required':False}
        if cfg['project']!=args.project or str(configured_root(root,cfg['paths']['state']))!=str(lexical_path(args.state)) or any(cfg['git'][k]!=v for k,v in expected.items()):
            return result('action_required','configuration_drift')
        return result('satisfied','ok')
    if request['mode']=='check':return result('action_required','missing')
    entry=settings.raw['templates'][args.template]
    changes=[(['project'],args.project),(['git','repository'],args.repository),(['git','base_ref'],args.base_ref),(['git','remote'],args.remote),(['git','author_name'],args.author_name),(['git','author_email'],args.author_email),(['git','push_required'],False),(['paths','state'],str(lexical_path(args.state))),(['environment_names'],['PATH','HOME','LANG'])]
    packet={'schema':'project-setup-1','request_id':'maintenance-'+digest(changes),'destination':'project','template':{'id':args.template,'version':entry['version'],'digest':entry['digest']},'edits':[{'path':p,'value':v} for p,v in changes],'probe_repository':False}
    try:
        receipt=ProjectCommands(port).apply(packet)
    except PoiseError as e:return result('action_required','invalid_config',detail=str(e))
    return result('repaired','created',project=receipt['project'])


def main():
    p=argparse.ArgumentParser(description='Poise project configuration repair')
    for n in ('settings','template','project','repository','state','base-ref','author-name','author-email','remote'):p.add_argument('--'+n,required=True)
    return serve(p,handle)
if __name__=='__main__':raise SystemExit(main())
