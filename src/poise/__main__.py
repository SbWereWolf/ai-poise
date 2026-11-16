from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path
from .common import PoiseError,load_config
from .infrastructure.session_establishment import establish_direct_poise


class SingleArgument(argparse.Action):
    """Reject repeated selection and owner arguments, including equal values."""
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error(f'{option_string} may be supplied only once')
        if isinstance(values, str) and not values.strip():
            parser.error(f'{option_string} requires a nonempty value')
        setattr(namespace, self.dest, values)


def parser():
    p=argparse.ArgumentParser(description='Декларативный work-пакет или пакет правок goal-config через stdin.')
    sub=p.add_subparsers(dest='command',required=True)
    agent_handoff=sub.add_parser('handoff-proxy',help='Передать готовый результат и уведомить назначенного напарника')
    agent_handoff.add_argument('--settings',type=Path,required=True,action=SingleArgument)
    telemetry=sub.add_parser('telemetry',help='Чтение метрик агента и повторная доставка телеметрии')
    for name in ('config', 'checkout', 'policy'):
        telemetry.add_argument('--'+name,type=Path,required=True,action=SingleArgument)
    telemetry.add_argument('--audience',choices=('agent','machine'),required=True,action=SingleArgument)
    source_index=sub.add_parser('source-index',help='Use the index of one explicitly selected checkout')
    for name in ('config', 'checkout', 'policy'):
        source_index.add_argument('--'+name,type=Path,required=True,action=SingleArgument)
    source_index.add_argument('--handler',action='store_true')
    result_view=sub.add_parser('result-view',help='Read a bounded view of an existing complete result')
    result_view.add_argument('--response',type=Path,required=True)
    result_view.add_argument('--view',choices=('brief','expanded','full'),required=True)
    result_view.add_argument('--output-chars',type=int,required=True)
    for name in ('infra','deps','check'):
        sub.add_parser(name,help='Route to the independent environment-maintenance application')
    tool_result=sub.add_parser('tool-result',help='Save complete tool results and read bounded selected sources')
    for name in ('config', 'checkout', 'policy'):
        tool_result.add_argument('--'+name,type=Path,required=name == 'policy',action=SingleArgument)
    tool_result.add_argument('--audience',choices=('agent','machine'),required=True,action=SingleArgument)
    checks=sub.add_parser('checks',help='Select and run configured named project checks')
    for name in ('config', 'checkout', 'policy'):
        checks.add_argument('--'+name,type=Path,required=True,action=SingleArgument)
    checks.add_argument('--view',choices=('brief','expanded','full'),required=True,action=SingleArgument)
    checks.add_argument('--transport-policy',type=Path,action=SingleArgument)
    checks.add_argument('--invocation',action=SingleArgument)
    checks_mcp=sub.add_parser('checks-mcp',help='Serve configured checks through full stdio MCP')
    for name in ('config','checkout','policy','transport-policy'):
        checks_mcp.add_argument('--'+name,type=Path,required=True,action=SingleArgument)
    reader=sub.add_parser('content-read',help='Read selected project-file parts in the native context')
    for name in ('config','checkout','policy'):
        reader.add_argument('--'+name,type=Path,required=True,action=SingleArgument)
    reader.add_argument('--format',choices=('json','text'),required=True,action=SingleArgument)
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
    sub.add_parser('work',help='work, sprint, release: один декларативный JSON-пакет')
    runtime=sub.add_parser('runtime',help='Explicit identity/capabilities/transcript plus one work packet')
    runtime.add_argument('--settings',type=Path,required=True)
    editor=sub.add_parser('goal-config',help='Создать/изменить workflow одним JSON-пакетом')
    editor.add_argument('--settings',type=Path,required=True)
    catalogue=sub.add_parser('catalogue',help='Batch install independent workflows or prepare typed tasks')
    catalogue.add_argument('--settings',type=Path,required=True)
    skills=sub.add_parser('skills',help='Read skill metadata or validate decomposition without a Task session')
    skills.add_argument('--catalog',type=Path)
    skills.add_argument('--root',type=Path)
    placement=sub.add_parser('placement',help='Determine and check declared result locations by project policy')
    for name in ('config', 'checkout', 'policy'):
        placement.add_argument('--'+name,type=Path,required=True,action=SingleArgument)
    creation=sub.add_parser('runtime-setup',help='Create explicit hook settings and install with one packet')
    creation.add_argument('--settings',type=Path,required=True)
    creation.add_argument('--max-input-bytes',type=int,required=True)
    setup=sub.add_parser('runtime-config',help='Batch hook installation plus live read-only probes')
    setup.add_argument('--settings',type=Path,required=True)
    hook=sub.add_parser('hook',help='Codex native command-hook event (adapter, not a task API)')
    hook.add_argument('--settings',type=Path,required=True)
    hook.add_argument('--definition',type=Path,required=True)
    hook.add_argument('--retry',type=Path,help='Retry one saved committed lifecycle delivery without replaying a native event')
    proxy=sub.add_parser('hook-proxy',help='Run the original SessionStart callback projection')
    proxy.add_argument('--settings',type=Path,required=True)
    proxy.add_argument('--snapshot',type=Path,required=True)
    bound=sub.add_parser('hook-work',help='One work packet using a generated session binding')
    bound.add_argument('--settings',type=Path,required=True,action=SingleArgument)
    bound.add_argument('--binding',type=Path,required=True,action=SingleArgument)
    bound.add_argument('--project',action=SingleArgument,help='Exact selected project ID')
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
    task_workflow_migration=sub.add_parser(
        'task-workflow-migrate', help='Migrate one explicitly authorized Task workflow-snapshot batch'
    )
    task_workflow_migration.add_argument('--config',type=Path,required=True)
    task_workflow_migration.add_argument('--project-settings',type=Path)
    requirements=sub.add_parser(
        'requirements', help='Apply/query one project Requirements Registry JSON packet'
    )
    requirements.add_argument('--config',type=Path,required=True)
    return p


def main():
    started_at=time.monotonic()
    if len(sys.argv)>1 and sys.argv[1] in ('infra','deps','check'):
        from .interfaces.maintenance import execute
        return execute(sys.argv[1:])
    args=parser().parse_args()
    if args.command=='handoff-proxy':
        from .interfaces.agent_handoff import execute
        return execute(args.settings,sys.stdin.buffer,sys.stdout)
    if args.command=='telemetry':
        from .interfaces.agent_metrics import execute
        return execute(args.config,args.checkout,args.policy,args.audience,sys.stdin.buffer,sys.stdout)
    if args.command=='source-index':
        from .interfaces.source_index import execute
        return execute(args.config,args.checkout,args.policy,args.handler,sys.stdin.buffer,sys.stdout)
    if args.command=='tool-result':
        from .interfaces.tool_results import execute
        return execute(args.config,args.checkout,args.policy,args.audience,sys.stdin.buffer,sys.stdout)
    if args.command=='content-read':
        from .interfaces.content_reader import execute_cli
        from .infrastructure.clock import SystemClock
        return execute_cli(args.config,args.checkout,args.policy,args.format,sys.stdin.buffer,sys.stdout,sys.stderr,SystemClock())
    if args.command=='checks':
        from .interfaces.project_checks import execute_cli
        from .infrastructure.clock import SystemClock
        if args.transport_policy is not None:
            from .interfaces.checks_cli import execute
            return execute(args.config,args.checkout,args.policy,args.transport_policy,args.invocation,
                           args.view,sys.stdin.buffer,sys.stdout,sys.stderr,SystemClock())
        if args.invocation is not None:
            parser().error('--invocation requires --transport-policy')
        return execute_cli(args.config,args.checkout,args.policy,args.view,sys.stdin.buffer,sys.stdout,sys.stderr,SystemClock())
    if args.command=='checks-mcp':
        from .interfaces.checks_mcp import serve
        from .infrastructure.clock import SystemClock
        return serve(args.config,args.checkout,args.policy,args.transport_policy,
                     sys.stdin.buffer,sys.stdout,sys.stderr,SystemClock())
    if args.command=='result-view':
        from .interfaces.result_views import execute
        return execute(args.response,args.view,args.output_chars,sys.stdout,sys.stderr)
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
        except PoiseError as exc:
            print(str(exc),file=sys.stderr)
            return 2
        request={'schema':'project-setup-1','request_id':args.request_id,'destination':args.destination,
            'template':{'id':args.template},'edits':[],
            'probe_repository':args.probe_repository=='yes'}
        return interactive(args.settings,request,sys.stdin,sys.stdout,sys.stderr)
    if args.command=='catalogue':
        from .interfaces.catalogue import execute
        return execute(args.settings,sys.stdin.buffer,sys.stdout)
    if args.command=='skills':
        from .interfaces.skills import execute
        return execute(args.catalog,args.root,sys.stdin.buffer,sys.stdout)
    if args.command=='placement':
        from .interfaces.placement import execute
        return execute(args.config,args.checkout,args.policy,sys.stdin.buffer,sys.stdout)
    if args.command=='runtime-setup':
        from .interfaces.hook_transport import setup
        return setup(args,sys.stdin.buffer,sys.stdout,sys.stderr)
    if args.command == 'hook-proxy':
        from .interfaces.hook_transport import proxy_execute
        return proxy_execute(args,sys.stdin.buffer,sys.stdout.buffer,sys.stderr.buffer)
    if args.command in ('runtime-config','hook','hook-work'):
        from .interfaces.hook_transport import execute
        return execute(args.command,args,sys.stdin.buffer,sys.stdout,sys.stderr,started_at=started_at)
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
    if args.command=='task-workflow-migrate':
        from .interfaces.task_workflow_migration import execute
        return execute(args.config,sys.stdin.buffer,sys.stdout,args.project_settings)
    if args.command=='requirements':
        from .interfaces.requirements_registry import execute
        return execute(args.config,sys.stdin.buffer,sys.stdout)
    if not os.environ.get('POISE_CONFIG'):
        print('Не задан POISE_CONFIG: выберите конфигурацию проекта.',file=sys.stderr)
        return 2
    try:
        from .interfaces.work import execute, read_request, write_rejection
        from .modules.work.domain import is_observation
        from .infrastructure.clock import SystemClock
        config=Path(os.environ['POISE_CONFIG'])
        config_root,document,_=load_config(config)
        try:
            request=read_request(document,sys.stdin.buffer)
        except (PoiseError,UnicodeError,RecursionError) as exc:
            write_rejection(config_root,document,exc,sys.stdout)
            return 2
        h=establish_direct_poise(config,os.environ,SystemClock(),read_only=is_observation(request)).runtime
        return execute(h,sys.stdin.buffer,sys.stdout,request)
    except (PoiseError,UnicodeError,RecursionError) as exc:
        print(str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
