from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
from .common import PoiseError,load_config
from .infrastructure.session_establishment import direct_caller,establish_poise


def parser():
    p=argparse.ArgumentParser(description='Декларативный work-пакет или пакет правок goal-config через stdin.')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('infra','deps','check'):
        sub.add_parser(name,help='Route to the independent environment-maintenance application')
    local_assets=sub.add_parser('local-assets',help='Restore declared local files from an explicit snapshot')
    local_assets.add_argument('--manifest',type=Path,required=True)
    project=sub.add_parser('project',help='Create a complete configured project from one explicit batch')
    project.add_argument('project_action',nargs='?',choices=('list','check'))
    project.add_argument('--settings',type=Path,required=True)
    next_command=sub.add_parser('next',help='Read all startable Tasks across configured projects; never claim')
    next_command.add_argument('--settings',type=Path,required=True)
    next_command.add_argument('--project',help='Exact configured project ID (no prefix matching)')
    project_config=sub.add_parser('project-config',help='Update one existing configured project')
    project_config.add_argument('--settings',type=Path,required=True)
    wizard=sub.add_parser('project-init',help='Interactive project questionnaire: set/keep/back/abort')
    wizard.add_argument('--settings',type=Path,required=True)
    for name in ('template','destination','request-id'):
        wizard.add_argument('--'+name,required=True)
    wizard.add_argument('--probe-repository',choices=('yes','no'),required=True)
    sub.add_parser('work',help='work, sprint, handoff, transfer: один декларативный JSON-пакет')
    runtime=sub.add_parser('runtime',help='Explicit identity/capabilities/transcript plus one work packet')
    runtime.add_argument('--settings',type=Path,required=True)
    editor=sub.add_parser('goal-config',help='Создать/изменить process одним JSON-пакетом')
    editor.add_argument('--settings',type=Path,required=True)
    catalogue=sub.add_parser('catalogue',help='Batch install independent processes or prepare typed tasks')
    catalogue.add_argument('--settings',type=Path,required=True)
    skills=sub.add_parser('skills',help='Read skill metadata or validate decomposition without a Task session')
    skills.add_argument('--catalog',type=Path,required=True)
    skills.add_argument('--root',type=Path,required=True)
    creation=sub.add_parser('runtime-setup',help='Create explicit hook settings and install with one packet')
    creation.add_argument('--settings',type=Path,required=True)
    creation.add_argument('--max-input-bytes',type=int,required=True)
    setup=sub.add_parser('runtime-config',help='Batch hook installation plus live read-only probes')
    setup.add_argument('--settings',type=Path,required=True)
    hook=sub.add_parser('hook',help='Codex native command-hook event (adapter, not a task API)')
    hook.add_argument('--settings',type=Path,required=True)
    hook.add_argument('--definition',type=Path,required=True)
    bound=sub.add_parser('hook-work',help='One work packet using a generated session binding')
    bound.add_argument('--settings',type=Path,required=True)
    bound.add_argument('--binding',type=Path,required=True)
    backup=sub.add_parser('backup',help='Task DB backup list, create, restore and help')
    actions=backup.add_subparsers(dest='backup_action',required=True)
    actions.add_parser('help',help='Show backup commands and the exclusive-operator requirement')
    for name in ('list','create'):
        command=actions.add_parser(name)
        command.add_argument('--config',type=Path,required=True)
    restore=actions.add_parser('restore')
    restore.add_argument('--config',type=Path,required=True)
    restore.add_argument('backup_name')
    migration=sub.add_parser('route-migrate',help='Remove retired route count limits from one project')
    migration.add_argument('--config',type=Path,required=True)
    task_process_migration=sub.add_parser(
        'task-process-migrate', help='Migrate one explicitly authorized Task process-snapshot batch'
    )
    task_process_migration.add_argument('--config',type=Path,required=True)
    requirements=sub.add_parser(
        'requirements', help='Apply/query one project Requirements Registry JSON packet'
    )
    requirements.add_argument('--config',type=Path,required=True)
    return p


def main():
    if len(sys.argv)>1 and sys.argv[1] in ('infra','deps','check'):
        from .interfaces.maintenance import execute
        return execute(sys.argv[1:])
    args=parser().parse_args()
    if args.command=='local-assets':
        from .interfaces.local_assets import execute
        return execute(args.manifest,sys.stdin.buffer,sys.stdout)
    if args.command=='project':
        from .interfaces.projects import execute
        return execute(args.settings,sys.stdin.buffer,sys.stdout,args.project_action)
    if args.command=='next':
        from .interfaces.projects import next_tasks
        return next_tasks(args.settings,args.project,sys.stdout)
    if args.command=='project-config':
        from .interfaces.project_config import execute
        return execute(args.settings,sys.stdin.buffer,sys.stdout)
    if args.command=='project-init':
        from .interfaces.projects import interactive
        from .infrastructure.projects import ProjectSettings
        try:
            settings=ProjectSettings(args.settings)
            if args.template not in settings.raw['templates']:
                raise PoiseError('Unknown explicitly selected project template')
            selection=settings.raw['templates'][args.template]
        except PoiseError as exc:
            print(str(exc),file=sys.stderr)
            return 2
        request={'schema':'project-setup-1','request_id':args.request_id,'destination':args.destination,
            'template':{'id':args.template,'version':selection['version'],'digest':selection['digest']},'edits':[],
            'probe_repository':args.probe_repository=='yes'}
        return interactive(args.settings,request,sys.stdin,sys.stdout,sys.stderr)
    if args.command=='catalogue':
        from .interfaces.catalogue import execute
        return execute(args.settings,sys.stdin.buffer,sys.stdout)
    if args.command=='skills':
        from .interfaces.skills import execute
        return execute(args.catalog,args.root,sys.stdin.buffer,sys.stdout)
    if args.command=='runtime-setup':
        from .interfaces.hook_transport import setup
        return setup(args,sys.stdin.buffer,sys.stdout,sys.stderr)
    if args.command in ('runtime-config','hook','hook-work'):
        from .interfaces.hook_transport import execute
        return execute(args.command,args,sys.stdin.buffer,sys.stdout,sys.stderr)
    if args.command=='goal-config':
        from .interfaces.goal_config import execute
        return execute(args.settings,sys.stdin.buffer,sys.stdout)
    if args.command=='runtime':
        from .interfaces.runtime_adapter import execute
        return execute(args.settings,sys.stdin.buffer,sys.stdout,sys.stderr)
    if args.command=='backup':
        from .interfaces.backups import execute
        return execute(
            args.backup_action,
            getattr(args,'config',None),
            getattr(args,'backup_name',None),
            sys.stdout,
        )
    if args.command=='route-migrate':
        from .interfaces.route_migration import execute
        return execute(args.config,sys.stdout)
    if args.command=='task-process-migrate':
        from .interfaces.task_process_migration import execute
        return execute(args.config,sys.stdin.buffer,sys.stdout)
    if args.command=='requirements':
        from .interfaces.requirements_registry import execute
        return execute(args.config,sys.stdin.buffer,sys.stdout)
    if not os.environ.get('POISE_CONFIG'):
        print('Не задан POISE_CONFIG: выберите конфигурацию проекта.',file=sys.stderr)
        return 2
    try:
        from .interfaces.work import execute
        from .infrastructure.clock import SystemClock
        config=Path(os.environ['POISE_CONFIG'])
        _,document,_=load_config(config)
        caller=direct_caller(document['project'],os.environ)
        h=establish_poise(config,caller,[],SystemClock()).runtime
        return execute(h,sys.stdin.buffer,sys.stdout)
    except PoiseError as exc:
        print(str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
