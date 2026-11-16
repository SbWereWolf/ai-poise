"""Standalone Poise entry: explicit configuration, bounded packet and response."""
import json
import os
from pathlib import Path
import sys

from application.sessions import SessionOperations
from application.hooks import HookEvents
from application.interception import HookInterception
from application.capabilities import CapabilityChecks
from application.reader import ReaderOperations
from application.filesystem import configured_policy, raise_rejected, write_files
from application.state import StateOperations
from domain.generic import Problem, exact_fields, invalid
from domain.session_config import parse_settings
from domain.reader_config import parse_settings as reader_settings
from domain.state import StateSettings, invalid_config
from infrastructure.callers import CallerResolver
from infrastructure.json_io import loads
from infrastructure.sessions import SessionRepository
from infrastructure.state import StateStore
from infrastructure.hooks import HookRegistry
from infrastructure.hook_configuration import load_hook_configuration
from infrastructure.commands import CommandObserver, LocalProbeExecutor
from infrastructure.files import FileSurface, OutputStore
from infrastructure.mcp import StdioProbe
from infrastructure.reader import ReaderRepository
from infrastructure.reader_files import ReaderFiles
from infrastructure.filesystem import LocalFilesystem
from application.commands import CommandOperations
from infrastructure.command_files import CommandFiles, configured_settings as command_settings
from infrastructure.command_store import CommandStore
from infrastructure.command_execution import WorkerLauncher
from application.output import OutputOperations
from domain.output_config import parse_settings as output_settings
from infrastructure.output_files import OutputFiles


from application.observations import ObservationOperations
from application.telemetry import TelemetryOperations
from domain.observation_config import parse_settings as observation_settings
from domain.measurement_config import parse_settings as measurement_settings
from infrastructure.observation_store import ObservationStore

SCHEMA = 'stride-poise/v1'


def load_config(path):
    try:
        value = loads(path.read_bytes())
    except (OSError, ValueError, RecursionError):
        invalid_config('/config')
    return parse_settings(value), value


def read_packet(limit):
    remaining, data = limit + 1, bytearray()
    while remaining:
        chunk = sys.stdin.buffer.read(min(remaining, 65536))
        if not chunk:
            break
        data.extend(chunk)
        remaining -= len(chunk)
    if len(data) > limit:
        raise Problem('packet_limit', {'limit': 'packet_bytes'}, 'reduce:packet')
    try:
        return loads(data)
    except (ValueError, RecursionError):
        raise Problem('invalid_json', {}, 'provide:json') from None


PRIMARY = frozenset(('session.ensure','session.assign_role','session.status','hook.event','hook.intercept',
    'reader.context','reader.reset','reader.read','reader.next','paths.evaluate','files.write',
    'command.request','command.completion','command.delete','output.minimal','output.range',
    'observations.record','observations.read','content.measure'))


def session_operations(settings,path):
    state=StateStore(StateSettings(settings.session_store,1,settings.packet_bytes,settings.sqlite_timeout_seconds),path)
    return SessionOperations(StateOperations(state,1),CallerResolver(settings,path,os.environ),
                             SessionRepository(state),settings.roles)


def execute_primary(operation,value,settings,raw_config,path):
    output=None;output_limits=None;operations=None
    try:
        if operation not in PRIMARY:invalid('/operation')
        if operation in ('session.ensure','session.status'):exact_fields(value,set(),'/input')
        if operation=='content.measure':
            result=ObservationOperations(measurement=measurement_settings(raw_config)).measure(value)
        elif operation.startswith('observations.'):
            def selected_sessions():
                nonlocal operations
                if operations is None:operations=session_operations(settings,path)
                return operations
            observations=ObservationOperations(ObservationStore(observation_settings(raw_config,path),settings.installation_id),selected_sessions)
            result=getattr(observations,operation.split('.')[1])(value)
        else:
            target_files, policy = LocalFilesystem(), None
            commands_config = (command_settings(raw_config,path,settings.session_store)
                               if operation.startswith('command.') else None)
            if operation in ('paths.evaluate', 'files.write') or (
                    operation in ('reader.read', 'reader.next') and 'paths' in raw_config):
                policy = configured_policy(raw_config, target_files)
            if operation == 'paths.evaluate':
                result = policy.evaluate(value)
                raise_rejected(result)
            elif operation == 'files.write':
                result = write_files(value, policy, target_files)
            elif operation.startswith('output.'):
                output_limits = output_settings(raw_config)
                output_operations = OutputOperations(output_limits, OutputFiles())
                result = getattr(output_operations, operation.split('.')[1])(value)
            else:
                state_settings = StateSettings(settings.session_store, 1, settings.packet_bytes,
                                               settings.sqlite_timeout_seconds)
                store = StateStore(state_settings, path)
                operations = SessionOperations(StateOperations(store, 1),
                    CallerResolver(settings, path, os.environ), SessionRepository(store), settings.roles)
                if commands_config is not None:
                    commands = CommandOperations(operations,commands_config,
                        CommandStore(commands_config.store,commands_config.limits['sqlite_timeout_seconds']),
                        WorkerLauncher(),CommandFiles)
                    result = getattr(commands,operation.split('.')[1])(value)
                elif operation.startswith('reader.'):
                    limits = reader_settings(raw_config)
                    reader = ReaderOperations(operations, ReaderRepository(store, operations.repository),
                                              ReaderFiles(), limits, policy=policy)
                    result = getattr(reader, operation.split('.')[1])(value)
                elif operation.startswith('hook.'):
                    hooks = load_hook_configuration(raw_config, path, settings, operation)
                    if operation == 'hook.event':
                        result = HookEvents(settings, hooks.definition, operations,
                            HookRegistry(store, operations.repository), FileSurface(settings.packet_bytes)).handle(value)
                    else:
                        # Construction allocates no files; capture begins only after complete admission.
                        generic = hooks.generic
                        commands, capabilities, limits = None, None, None
                        files = FileSurface(settings.packet_bytes)
                        if generic is not None:
                            output = OutputStore(generic)
                            limits = generic['limits']
                            files = FileSurface(limits['input_bytes'])
                            commands = CommandObserver(output)
                            capabilities = CapabilityChecks(LocalProbeExecutor(commands, StdioProbe, output), files, limits)
                        result = HookInterception(hooks.definition, operations, commands, capabilities,
                                                 files, limits, hooks.probes).handle(value)
                elif operation == 'session.ensure':
                    result = operations.ensure()
                elif operation == 'session.assign_role':
                    result = operations.assign_role(value)
                else:
                    result = operations.status()
        response = {'schema': SCHEMA, 'status': 'ok', 'result': result, 'error': None, 'evidence': [] if output is None else output.references}
        exit_code = 0
    except Problem as problem:
        response = {'schema': SCHEMA, 'status': problem.status, 'result': problem.result,
                    'error': problem.error, 'evidence': [] if output is None else output.references}
        exit_code = 1 if problem.status == 'unavailable' else 2
    except OSError as error:
        response = {'schema': SCHEMA, 'status': 'unavailable', 'result': None,
                    'error': {'code': 'io_unavailable', 'details': {'reason': str(error)},
                              'action': 'configure:files'},
                    'evidence': [] if output is None else output.references}
        exit_code = 1
    raw = response_bytes(response)
    if output_limits is not None and response['result'] is not None and len(raw) > output_limits.max_response_bytes:
        response = {'schema': SCHEMA, 'status': 'rejected', 'result': None,
                    'error': {'code': 'output_response_limit', 'details': {'limit': 'max_response_bytes'},
                              'action': 'reduce:output'}, 'evidence': []}
        raw, exit_code = response_bytes(response), 2
    return response,exit_code,None if operations is None else operations.observed_session_id


def main(argv=None):
    arguments=sys.argv[1:] if argv is None else argv
    try:
        if len(arguments)!=2 or arguments[0]!='--config' or not arguments[1]:invalid_config('/config')
        path=Path(arguments[1]).absolute()
        settings,raw_config=load_config(path)
        packet=exact_fields(read_packet(settings.packet_bytes),{'schema','operation','input'},'/')
        if packet['schema']!=SCHEMA:
            value=packet['schema'] if isinstance(packet['schema'],str) else None
            raise Problem('unsupported_schema',{'schema':value},'select:schema')
        operation=packet['operation'];value=packet['input']
        if operation=='observations.prepare':
            result=ObservationOperations(ObservationStore(observation_settings(raw_config,path),settings.installation_id)).prepare(value)
            response={'schema':SCHEMA,'status':'ok','result':result,'error':None,'evidence':[]};code=0
        elif isinstance(operation,str) and operation.startswith('telemetry.'):
            telemetry=TelemetryOperations(raw_config,path,settings.installation_id,
                lambda name,data:execute_primary(name,data,settings,raw_config,path),PRIMARY)
            if operation=='telemetry.invoke':response,code=telemetry.invoke(value)
            elif operation in ('telemetry.status','telemetry.replay'):
                result=getattr(telemetry,operation.split('.')[1])(value)
                response={'schema':SCHEMA,'status':'ok','result':result,'error':None,'evidence':[]};code=0
            else:invalid('/operation')
        else:
            if not isinstance(operation,str):invalid('/operation')
            response,code,_=execute_primary(operation,value,settings,raw_config,path)
    except Problem as error:
        response={'schema':SCHEMA,'status':error.status,'result':error.result,'error':error.error,'evidence':[]}
        code=1 if error.status=='unavailable' else 2
    except OSError as error:
        response={'schema':SCHEMA,'status':'unavailable','result':None,'error':{
            'code':'io_unavailable','details':{'reason':str(error)},'action':'configure:files'},'evidence':[]};code=1
    sys.stdout.buffer.write(response_bytes(response))
    return code


def response_bytes(response):
    return (json.dumps(response, ensure_ascii=False, allow_nan=False,
                       separators=(',', ':')) + '\n').encode('utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
