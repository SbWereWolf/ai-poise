"""Explicit read-only probe contracts. No environment, time or I/O in the model."""
from copy import deepcopy
from dataclasses import dataclass
import math
from .generic import (Problem, exact_fields, invalid, string_list, text,
                      validate_exact_keys)

def exact(value, fields, label):
    return validate_exact_keys(value, fields, label)

def nonempty(value, label):
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError(label + ": nonempty text required")



def positive(value, label, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{label}: explicit positive finite number required')


def assertions(value):
    if not isinstance(value, list):raise ValueError('Explicit JSON assertions required')
    for a in value:
        exact(a, {'path', 'equals'}, 'JSON assertion')
        if not isinstance(a['path'], list) or not a['path'] or any(not isinstance(k, str) or not k for k in a['path']):
            raise ValueError('Assertion path must be a nonempty list of keys')


def satisfies(value, checks):
    for check in checks:
        found=value
        for key in check['path']:
            if not isinstance(found, dict) or key not in found:return False
            found=found[key]
        if type(found) is not type(check['equals']) or found != check['equals']:return False
    return True


def bind_workspace(value, workspace):
    """Only the explicitly declared binding token is expanded, never shell input."""
    if isinstance(value, str):return value.replace('${workspace}', workspace)
    if isinstance(value, list):return [bind_workspace(v, workspace) for v in value]
    if isinstance(value, dict):return {k:bind_workspace(v, workspace) for k,v in value.items()}
    return value


@dataclass(frozen=True)
class ProbeSpec:
    data: dict

    @classmethod
    def parse(cls, raw):
        exact(raw, {'id','kind','required','argv','cwd','environment','timeout_seconds',
                    'max_output_bytes','expected_exit_code','stdout_contains','stderr_contains',
                    'json_assertions','mcp','tool_ref','project_bound','remediation'}, 'capability probe')
        for k in ('id','cwd','tool_ref','remediation'):nonempty(raw[k], k)
        if raw['kind'] not in ('command','mcp_stdio'):raise ValueError('Unsupported probe transport')
        for k in ('required','project_bound'):
            if type(raw[k]) is not bool:raise ValueError(f'{k}: explicit boolean required')
        if not isinstance(raw['argv'], list) or not raw['argv']:raise ValueError('Exact argv required')
        for arg in raw['argv']:nonempty(arg, 'argv')
        if not isinstance(raw['environment'], dict):raise ValueError('Explicit environment object required')
        for key, value in raw['environment'].items():
            nonempty(key, 'environment key')
            if '=' in key or not isinstance(value, str) or '\0' in value:raise ValueError('Invalid environment entry')
        positive(raw['timeout_seconds'], 'timeout_seconds')
        positive(raw['max_output_bytes'], 'max_output_bytes', True)
        if type(raw['expected_exit_code']) is not int:raise ValueError('Explicit exit code required')
        for key in ('stdout_contains','stderr_contains'):
            if not isinstance(raw[key], list):raise ValueError(f'{key}: explicit list required')
            for word in raw[key]:nonempty(word,key)
        assertions(raw['json_assertions'])
        if raw['project_bound'] and not any(a['equals']=='${workspace}' for a in raw['json_assertions']):
            raise ValueError('Project-bound probe needs an explicit exact workspace assertion')
        if raw['kind']=='command':
            if raw['mcp'] is not None:raise ValueError('Command probe requires explicit mcp=null')
        else:
            m=raw['mcp']
            exact(m, {'protocol_version','client_info','required_tools','call','max_messages',
                      'max_pages','max_message_bytes','shutdown_seconds'},'MCP probe')
            nonempty(m['protocol_version'],'protocol_version')
            exact(m['client_info'],{'name','version'},'client_info')
            for v in m['client_info'].values():nonempty(v,'client_info')
            if not isinstance(m['required_tools'],list) or not m['required_tools']:raise ValueError('Required tools must be explicit and nonempty')
            for tool in m['required_tools']:nonempty(tool,'required tool')
            if len(set(m['required_tools']))!=len(m['required_tools']):raise ValueError('Duplicate required tool')
            for key in ('max_messages','max_pages','max_message_bytes'):positive(m[key],key,True)
            positive(m['shutdown_seconds'],'shutdown_seconds')
            if raw['expected_exit_code']!=0 or raw['stdout_contains'] or raw['stderr_contains']:
                raise ValueError('MCP result is protocol data, not terminal exit/text predicates')
            if m['call'] is None:
                if raw['json_assertions'] or raw['project_bound']:raise ValueError('Smoke assertions require an explicit tool call')
            else:
                exact(m['call'],{'name','arguments','read_only'},'MCP smoke call')
                if m['call']['name'] not in m['required_tools']:raise ValueError('Smoke tool must be in the required inventory')
                if not isinstance(m['call']['arguments'],dict) or m['call']['read_only'] is not True:
                    raise ValueError('Only explicitly declared read-only smoke calls are supported')
        return cls(deepcopy(raw))


def validate_probe(value, path, limits):
    if not isinstance(value, dict):
        invalid(path)
    for key, maximum in [('timeout_seconds', limits['provider_timeout_seconds']),
                         ('max_output_bytes', limits['provider_output_bytes'])]:
        current = value.get(key)
        if type(current) not in (int, float) or current <= 0 or current > maximum:
            invalid(path + '/' + key)
    try:
        return ProbeSpec.parse(value)
    except (ValueError, TypeError, OverflowError):
        invalid(path)




OPERATIONS = {'symbol', 'usages', 'hierarchy', 'inspection', 'documentation', 'structural'}
AST_OPERATIONS = {'symbol', 'usages', 'hierarchy', 'structural'}


def parse_navigation(request, limits):
    exact_fields(request, {'checkout', 'operation', 'files', 'providers'}, '/input')
    text(request['checkout'], '/input/checkout')
    if not isinstance(request['operation'], str) or request['operation'] not in OPERATIONS:
        invalid('/input/operation')
    scope = string_list(request['files'], '/input/files')
    if not scope:
        invalid('/input/files')
    providers = request['providers']
    if not isinstance(providers, list):
        invalid('/input/providers')
    if len(providers) > limits['batch_count']:
        raise Problem('batch_limit', {'limit': 'batch_count'}, 'reduce:batch')
    prepared, identities = [], set()
    for index, raw in enumerate(providers):
        path = '/input/providers/' + str(index)
        exact_fields(raw, {'id', 'kind', 'operations', 'identity_path', 'probe'}, path)
        text(raw['id'], path + '/id')
        if raw['id'] in identities or raw['kind'] not in ('ide', 'ast'):
            invalid(path)
        identities.add(raw['id'])
        operations = string_list(raw['operations'], path + '/operations')
        if not operations or any(value not in OPERATIONS for value in operations):
            invalid(path + '/operations')
        pointer = string_list(raw['identity_path'], path + '/identity_path', unique=False)
        if not pointer:
            invalid(path + '/identity_path')
        # Validate all original and bound probes before selecting or executing any.
        validate_probe(raw['probe'], path + '/probe', limits)
        probe = deepcopy(raw['probe'])
        probe['project_bound'] = True
        probe['json_assertions'].append({'path': pointer, 'equals': '${workspace}'})
        prepared.append((raw, validate_probe(probe, path + '/probe', limits)))
    return {'checkout': request['checkout'], 'operation': request['operation'],
            'scope': sorted(scope), 'prepared': prepared}


def ordered_providers(values):
    operation = values['operation']
    kinds = ('ast',) if operation == 'structural' else ('ide', 'ast')
    return [(provider, probe) for kind in kinds for provider, probe in values['prepared']
            if provider['kind'] == kind and operation in provider['operations']
            and (kind != 'ast' or operation in AST_OPERATIONS)]
