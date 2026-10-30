"""Configured handler contracts; no process, filesystem or Task dependencies."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePath
import string

from ..capabilities.domain import ProbeSpec, positive
from ..foundation.errors import PoiseError
from ..foundation.validation import configuration_fields, nonempty, validate_exact_keys


# Protocol vocabulary: each builtin selects an existing capability owner.
BUILTINS = {
    'capability-summary': {'SessionStart'},
    'general-context': {'SessionStart', 'PreCompact', 'PostCompact', 'SessionEnd'},
    'command-intake': {'PreToolUse', 'PostToolUse'},
    'lifecycle-consumer': {'SessionStart', 'SessionEnd'},
    'task-context': {'SessionStart', 'UserPromptSubmit', 'Stop'},
    'task-runtime-cleanup': {'SessionEnd'},
    'tool-result': {'PostToolUse'},
    'check-invocation': {'PreToolUse', 'PostToolUse'},
    'agent-metrics': {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse',
                      'Stop', 'PreCompact', 'PostCompact', 'SessionEnd'},
}


def validate_builtin_options(name, options):
    if name == 'check-invocation':
        configuration_fields(options, {'policy_path', 'checks_policy_path', 'tool_selectors', 'cli_argv'}, name)
        for field in ('policy_path', 'checks_policy_path'):
            nonempty(options[field], name + '.' + field)
            if not PurePath(options[field]).is_absolute():
                raise PoiseError('Check capability policy requires an absolute path')
        selectors = configuration_fields(options['tool_selectors'], {'run', 'cancel'}, name)
        for selector in selectors.values():
            nonempty(selector, 'check tool selector')
        if (not isinstance(options['cli_argv'], list) or not options['cli_argv']
                or any(not isinstance(v, str) or not v for v in options['cli_argv'])):
            raise PoiseError('Explicit check CLI argv required')
    elif name in ('general-context', 'command-intake', 'tool-result', 'agent-metrics'):
        values = configuration_fields(options, {'policy_path'}, name)
        nonempty(values['policy_path'], name + '.policy_path')
        if not PurePath(values['policy_path']).is_absolute():
            raise PoiseError(f'{name}.policy_path must be absolute')
    elif name == 'lifecycle-consumer':
        configuration_fields(options, {'consumer'}, name)
        if not isinstance(options['consumer'], dict):
            raise PoiseError('lifecycle-consumer.consumer must be an explicit configuration')
    elif name == 'capability-summary':
        configuration_fields(options, {'probes', 'summary'}, name)
        if not isinstance(options['probes'], list):
            raise PoiseError('capability-summary requires an explicit probes list')
        probes = [ProbeSpec.parse(p) for p in options['probes']]
        if len({p.data['id'] for p in probes}) != len(probes):
            raise PoiseError('Duplicate capability probe')
        summary = configuration_fields(options['summary'], {'file', 'header_template', 'item_template'}, name)
        for key, value in summary.items():
            nonempty(value, name + '.summary.' + key)
    elif name == 'task-context':
        configuration_fields(options, {'context_template', 'stop_template', 'gate_operations'}, name)
        for key, required in [('context_template', {'launcher', 'state'}), ('stop_template', {'state'})]:
            nonempty(options[key], key)
            try:
                fields = {field for _, field, _, _ in string.Formatter().parse(options[key]) if field is not None}
                if fields != required:
                    raise ValueError('Incorrect template fields')
                options[key].format(**{k: 'sample' for k in required})
            except (KeyError, ValueError, IndexError) as exc:
                raise PoiseError(f'Invalid task-context.{key}') from exc
        operations = options['gate_operations']
        if (not isinstance(operations, list) or len(set(operations)) != len(operations)
                or any(op not in ('bootstrap', 'verify', 'artifacts') for op in operations)):
            raise PoiseError('Invalid task-context.gate_operations')


@dataclass(frozen=True)
class HandlerConfiguration:
    raw: dict

    @classmethod
    def parse(cls, raw):
        configuration_fields(raw, {'schema', 'id', 'handlers'}, 'handler configuration')
        if raw['schema'] != 'hook-handler-configuration-1':
            raise PoiseError('Unsupported handler configuration schema')
        nonempty(raw['id'], 'handler configuration.id')
        if not isinstance(raw['handlers'], list):
            raise PoiseError('Explicit handlers list required')
        identifiers = set()
        for handler in raw['handlers']:
            configuration_fields(handler, {'id', 'implementation', 'mode', 'options',
                                           'execution', 'on_error'}, 'handler')
            nonempty(handler['id'], 'handler.id')
            if handler['id'] in identifiers:
                raise PoiseError('Duplicate handler id in one configuration')
            identifiers.add(handler['id'])
            if handler['mode'] not in ('sync', 'async'):
                raise PoiseError('Unsupported handler mode; select sync or async')
            if handler['on_error'] != 'stop':
                raise PoiseError('Unsupported handler on_error; select stop')
            if not isinstance(handler['options'], dict):
                raise PoiseError('Handler options object required')
            implementation = configuration_fields(handler['implementation'], {'kind'}, 'handler implementation')
            if implementation['kind'] == 'command':
                configuration_fields(handler['implementation'], {'kind', 'argv'}, 'command')
                argv = handler['implementation']['argv']
                if not isinstance(argv, list) or not argv:
                    raise PoiseError('Explicit nonempty command argv required')
                nonempty(argv[0], 'command executable')
                for arg in argv:
                    if not isinstance(arg, str) or '\0' in arg:
                        raise PoiseError('Command argv requires NUL-free strings')
            elif implementation['kind'] == 'builtin':
                configuration_fields(handler['implementation'], {'kind', 'name'}, 'builtin')
                name = handler['implementation']['name']
                nonempty(name, 'builtin name')
                if name not in BUILTINS:
                    raise PoiseError('Unregistered builtin handler; select a registered implementation')
                if handler['mode'] != 'sync':
                    raise PoiseError('Selected builtin requires sync mode')
                validate_builtin_options(name, handler['options'])
            else:
                raise PoiseError('Unsupported handler implementation kind')
            fields = {'run_seconds', 'cleanup_seconds'}
            if handler['mode'] == 'async':
                fields.add('launch_seconds')
            execution = configuration_fields(handler['execution'], fields, 'handler execution')
            for key, value in execution.items():
                positive(value, 'handler.execution.' + key)
        return cls(deepcopy(raw))


@dataclass(frozen=True)
class HandlerInvocation:
    configuration: dict
    handler: dict
    resources: dict
    configuration_source: dict


@dataclass(frozen=True)
class DispatchPlan:
    invocations: tuple[HandlerInvocation, ...]
    revisions: tuple[dict, ...]


class HandlerResult:
    @staticmethod
    def parse(raw, invocation_id):
        validate_exact_keys(raw, {'schema', 'invocation_id', 'response', 'control'}, 'handler result', PoiseError)
        if raw['schema'] != 'poise-hook-handler-result-1' or raw['invocation_id'] != invocation_id:
            raise PoiseError('Handler result schema/invocation identity mismatch')
        if not isinstance(raw['response'], dict):
            raise PoiseError('Handler response object required')
        validate_exact_keys(raw['control'], {'action', 'reason'}, 'handler control', PoiseError)
        action, reason = raw['control']['action'], raw['control']['reason']
        if action == 'continue' and reason is None:
            return deepcopy(raw)
        if action == 'stop':
            nonempty(reason, 'handler stop reason')
            return deepcopy(raw)
        raise PoiseError('Handler control requires continue/null or stop/nonblank reason')


class AsyncResult:
    @staticmethod
    def parse(raw, invocation_id):
        validate_exact_keys(raw, {'schema', 'invocation_id', 'effects'}, 'async result', PoiseError)
        if raw['schema'] != 'poise-hook-async-result-1' or raw['invocation_id'] != invocation_id:
            raise PoiseError('Async result schema/invocation identity mismatch')
        if not isinstance(raw['effects'], dict):
            raise PoiseError('Async handler effects object required')
        return deepcopy(raw)
