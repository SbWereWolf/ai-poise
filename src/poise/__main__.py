from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
from .runtime import Poise
from .common import PoiseError


def parser():
    p=argparse.ArgumentParser(description='Декларативный work-пакет или пакет правок goal-config через stdin.')
    sub=p.add_subparsers(dest='command',required=True)
    project=sub.add_parser('project',help='Create a complete configured project from one explicit batch')
    project.add_argument('--settings',type=Path,required=True)
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
    return p


def main():
    args=parser().parse_args()
    if args.command=='project':
        from .interfaces.projects import execute
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
    for name in ('POISE_CONFIG','POISE_SESSION'):
        if name not in os.environ or not os.environ[name]:
            print(f'Не задан {name}: выберите конфигурацию проекта и сессию.',file=sys.stderr)
            return 2
    try:
        from .interfaces.work import execute
        h=Poise(Path(os.environ['POISE_CONFIG']),os.environ['POISE_SESSION'])
        return execute(h,sys.stdin.buffer,sys.stdout)
    except PoiseError as exc:
        print(str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
