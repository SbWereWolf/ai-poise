"""Explicit read-only probe contracts. No environment, time or I/O in the model."""
from copy import deepcopy
from dataclasses import dataclass
import math
from ..artifact_factory.domain import exact
from ..foundation.errors import PoiseError


def positive(value, label, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or value <= 0:
        raise PoiseError(f'{label}: explicit positive finite number required')


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise PoiseError(f'{label}: nonempty string required')


def assertions(value):
    if not isinstance(value, list):raise PoiseError('Explicit JSON assertions required')
    for a in value:
        exact(a, {'path', 'equals'}, 'JSON assertion')
        if not isinstance(a['path'], list) or not a['path'] or any(not isinstance(k, str) or not k for k in a['path']):
            raise PoiseError('Assertion path must be a nonempty list of keys')


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
        if raw['kind'] not in ('command','mcp_stdio'):raise PoiseError('Unsupported probe transport')
        for k in ('required','project_bound'):
            if type(raw[k]) is not bool:raise PoiseError(f'{k}: explicit boolean required')
        if not isinstance(raw['argv'], list) or not raw['argv']:raise PoiseError('Exact argv required')
        for arg in raw['argv']:nonempty(arg, 'argv')
        if not isinstance(raw['environment'], dict):raise PoiseError('Explicit environment object required')
        for key, value in raw['environment'].items():
            nonempty(key, 'environment key')
            if '=' in key or not isinstance(value, str) or '\0' in value:raise PoiseError('Invalid environment entry')
        positive(raw['timeout_seconds'], 'timeout_seconds')
        positive(raw['max_output_bytes'], 'max_output_bytes', True)
        if type(raw['expected_exit_code']) is not int:raise PoiseError('Explicit exit code required')
        for key in ('stdout_contains','stderr_contains'):
            if not isinstance(raw[key], list):raise PoiseError(f'{key}: explicit list required')
            for word in raw[key]:nonempty(word,key)
        assertions(raw['json_assertions'])
        if raw['project_bound'] and not any(a['equals']=='${workspace}' for a in raw['json_assertions']):
            raise PoiseError('Project-bound probe needs an explicit exact workspace assertion')
        if raw['kind']=='command':
            if raw['mcp'] is not None:raise PoiseError('Command probe requires explicit mcp=null')
        else:
            m=raw['mcp']
            exact(m, {'protocol_version','client_info','required_tools','call','max_messages',
                      'max_pages','max_message_bytes','shutdown_seconds'},'MCP probe')
            nonempty(m['protocol_version'],'protocol_version')
            exact(m['client_info'],{'name','version'},'client_info')
            for v in m['client_info'].values():nonempty(v,'client_info')
            if not isinstance(m['required_tools'],list) or not m['required_tools']:raise PoiseError('Required tools must be explicit and nonempty')
            for tool in m['required_tools']:nonempty(tool,'required tool')
            if len(set(m['required_tools']))!=len(m['required_tools']):raise PoiseError('Duplicate required tool')
            for key in ('max_messages','max_pages','max_message_bytes'):positive(m[key],key,True)
            positive(m['shutdown_seconds'],'shutdown_seconds')
            if raw['expected_exit_code']!=0 or raw['stdout_contains'] or raw['stderr_contains']:
                raise PoiseError('MCP result is protocol data, not terminal exit/text predicates')
            if m['call'] is None:
                if raw['json_assertions'] or raw['project_bound']:raise PoiseError('Smoke assertions require an explicit tool call')
            else:
                exact(m['call'],{'name','arguments','read_only'},'MCP smoke call')
                if m['call']['name'] not in m['required_tools']:raise PoiseError('Smoke tool must be in the required inventory')
                if not isinstance(m['call']['arguments'],dict) or m['call']['read_only'] is not True:
                    raise PoiseError('Only explicitly declared read-only smoke calls are supported')
        return cls(deepcopy(raw))
