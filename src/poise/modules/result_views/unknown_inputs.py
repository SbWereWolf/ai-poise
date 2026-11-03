"""Portable identities and bounded presentations of unsupported tool inputs."""
from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath

from ..foundation.errors import DomainError


class UnknownObservation:
    fields = {'schema', 'tool_name', 'format', 'reason', 'policy_version',
              'session_id', 'turn_id', 'tool_use_id', 'request_id', 'full_source'}

    def __init__(self, value):
        if not isinstance(value, dict) or set(value) != self.fields:
            raise DomainError('Invalid unknown observation fields')
        if value['schema'] != 'poise-tool-result-unknown-1':
            raise DomainError('Unsupported unknown observation')
        for key in ('tool_name', 'format', 'policy_version', 'request_id'):
            if not isinstance(value[key], str) or not value[key]:
                raise DomainError('Missing unknown observation identity')
        for key in ('session_id', 'turn_id', 'tool_use_id'):
            if value[key] is not None and (not isinstance(value[key], str) or not value[key]):
                raise DomainError('Invalid native observation identity')
        if value['reason'] not in ('unknown_tool', 'unknown_format'):
            raise DomainError('Invalid unknown observation reason')
        source = value['full_source']
        if not isinstance(source, dict) or set(source) != {'path', 'sha256'}:
            raise DomainError('Invalid observation source')
        checksum = source['sha256']
        if (not isinstance(source['path'], str) or not PurePosixPath(source['path']).is_absolute()
                or not isinstance(checksum, str) or len(checksum) != 64
                or any(c not in '0123456789abcdef' for c in checksum)):
            raise DomainError('Invalid observation source identity')
        self.data = deepcopy(value)

    def encoded(self):
        return (json.dumps(self.data, sort_keys=True, separators=(',', ':')) + '\n').encode()

    def key(self):
        return hashlib.sha256(self.encoded()).hexdigest()

    @classmethod
    def build(cls, record, source, request, reason, policy):
        event = request.get('event', {})
        return cls({'schema': 'poise-tool-result-unknown-1', 'tool_name': record['tool']['name'],
                    'format': wire_format(record['response']), 'reason': reason,
                    'policy_version': policy['version'], 'session_id': event.get('session_id'),
                    'turn_id': event.get('turn_id'), 'tool_use_id': event.get('tool_use_id'),
                    'request_id': request['request_id'],
                    'full_source': {'path': record['full_path'], 'sha256': source.metadata['sha256']}})


def wire_format(value):
    if isinstance(value, dict): return 'json-object'
    if isinstance(value, list): return 'json-array'
    if isinstance(value, str): return 'native-text'
    if value is None: return 'json-null'
    if type(value) is bool: return 'json-boolean'
    return 'json-number'


def generic_profile(reason):
    return {'parser': 'generic', 'format': 'generic', 'reason': reason}


def generic_views(record, reason, policy):
    outcome = 'exit unavailable' if record['exit_code'] is None else f"exit {record['exit_code']}"
    label = {'unknown_tool': 'unknown tool', 'unknown_format': 'unknown format', 'pending': 'pending'}[reason]
    brief = f"{record['tool']['name']}: {label}; {outcome}"
    from .tool_results import prefix, response_text
    excerpt = prefix(response_text(record['response']), policy['inspection']['max_excerpt_bytes'])
    return {'brief': brief, 'expanded': brief + '\n' + excerpt}
