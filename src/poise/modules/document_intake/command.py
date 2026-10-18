"""Tool-call values and completion normalization, independent of transport/storage."""
from dataclasses import dataclass
from .domain import identity

CALL_IMMUTABLE = ('schema', 'scope', 'episode', 'pre_turn_id', 'tool_name',
                  'input_digest', 'generation', 'created_clock', 'policy_snapshot',
                  'policy_sha256', 'plans')
CALL_MUTABLE = ('state', 'pre_result', 'post_fingerprint', 'post_result',
                'post_turn_id', 'publication_state')
PLAN_FIELDS = {'schema', 'origin', 'reader_kind', 'coverage_kind', 'line_ranges',
               'version', 'operation_digest', 'generation', 'path', 'version_key'}


class CommandFailure(Exception):
    def __init__(self, code, *, field=None):
        self.code, self.field = code, field
        super().__init__(code)

    def as_dict(self):
        return dict(code=self.code, field=self.field,
                    message='Учёт чтения не подтверждён: ' + self.code,
                    action='Проверьте указанные настройки и состояние через coverage; '
                           'повторите доставку того же события после устранения причины.')


@dataclass(frozen=True)
class Completion:
    reason: str
    usable: bool
    exit_code: int | None
    text_digest: str
    text_bytes: int

    @property
    def fingerprint(self):
        return identity([self.reason, self.exit_code, self.text_digest, self.text_bytes])


def normalize_completion(value, policy):
    """Interpret configured fields; never stringify a non-text response as content."""
    invalid, text, exit_code = False, None, None
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        exits = [value[k] for k in policy['exit_fields'] if k in value]
        if any(type(v) is not int for v in exits) or len(set(map(repr, exits))) > 1:
            invalid = True
        elif exits:
            exit_code = exits[0]
        output = policy['output_field']
        streams = [value[k] for k in policy['stream_fields'] if k in value]
        if output in value:
            if isinstance(value[output], str):
                text = value[output]
            else:
                invalid = True
        elif streams:
            if all(isinstance(v, str) for v in streams):
                text = ''.join(streams)
            else:
                invalid = True
        if any(not isinstance(v, str) for v in streams):
            invalid = True
    else:
        invalid = True
    encoded = text.encode('utf-8') if text is not None else b''
    import hashlib
    digest = hashlib.sha256(encoded).hexdigest()
    if invalid:
        reason = 'invalid_response'
    elif exit_code is not None and exit_code != 0:
        reason = 'known_failure'
    elif len(encoded) < policy['minimum_response_bytes']:
        reason = 'insufficient_response'
    elif exit_code is None and not policy['unknown_exit_code_is_success']:
        reason = 'unknown_exit_code'
    else:
        reason = 'usable_result'
    return Completion(reason, reason == 'usable_result', exit_code, digest, len(encoded))


def decision(reason, action):
    return dict(action=action, reason_code=reason,
                message='Решение учёта чтений: ' + reason,
                action_hint='Запросите coverage и выберите необходимые ещё не учтённые '
                            'диапазоны либо проверьте явную политику чтения.')


def parse_tool_event(value):
    if not isinstance(value, dict) or 'agent_id' in value:
        raise CommandFailure('invalid_event', field='agent_id')
    for field in ('session_id', 'cwd', 'turn_id', 'tool_use_id', 'tool_name'):
        if not isinstance(value.get(field), str) or not value[field].strip() or '\x00' in value[field]:
            raise CommandFailure('invalid_event', field=field)
    if value.get('hook_event_name') not in ('PreToolUse', 'PostToolUse'):
        raise CommandFailure('invalid_event', field='hook_event_name')
    if (not isinstance(value.get('tool_input'), dict)
            or not isinstance(value['tool_input'].get('command'), str)
            or not value['tool_input']['command'].strip()):
        raise CommandFailure('invalid_event', field='tool_input.command')
    return value


def validate_plan(plan):
    """One codec owns both stored and in-flight read metadata."""
    from .coverage import union_ranges
    def invalid():
        raise CommandFailure('registry_invalid')
    if not isinstance(plan, dict) or set(plan) != PLAN_FIELDS:
        invalid()
    if plan['schema'] != 'poise-read-plan-1' or plan['origin'] not in ('native', 'direct'):
        invalid()
    if plan['coverage_kind'] not in ('complete', 'lines', 'operation'):
        invalid()
    if type(plan['generation']) is not int or plan['generation'] < 0:
        invalid()
    for field in ('reader_kind', 'path', 'version_key', 'operation_digest'):
        if not isinstance(plan[field], str) or not plan[field]:
            invalid()
    if not isinstance(plan['line_ranges'], list):
        invalid()
    try:
        if union_ranges(plan['line_ranges']) != plan['line_ranges']:
            invalid()
    except (ValueError, TypeError):
        invalid()
    if plan['coverage_kind'] == 'operation' and plan['line_ranges']:
        invalid()
    version = plan['version']
    if not isinstance(version, dict) or set(version) not in ({'size', 'sha256'}, {'size', 'mtime_ns'}):
        invalid()
    if type(version['size']) is not int or version['size'] < 0:
        invalid()
    if 'mtime_ns' in version and type(version['mtime_ns']) is not int:
        invalid()
    if 'sha256' in version and (not isinstance(version['sha256'], str)
            or len(version['sha256']) != 64 or any(c not in '0123456789abcdef' for c in version['sha256'])):
        invalid()
    if identity(version) != plan['version_key']:
        invalid()
    return plan


def call_hash(record):
    return identity({key: record[key] for key in CALL_IMMUTABLE})


def validate_call(record):
    from .command_policy import CommandPolicy
    if (not isinstance(record, dict) or set(record) != set(CALL_IMMUTABLE + CALL_MUTABLE)
            or record['schema'] != 'poise-command-call-1'):
        raise CommandFailure('registry_invalid')
    if (record['state'] not in ('pending', 'settled', 'discarded', 'expired')
            or record['publication_state'] not in ('confirmed', 'unconfirmed')
            or type(record['generation']) is not int or record['generation'] < 0):
        raise CommandFailure('registry_invalid')
    scope = record['scope']
    if (not isinstance(scope, dict) or set(scope) != {'namespace', 'caller_key', 'checkout_key', 'tool_use_id'}
            or any(not isinstance(v, str) or not v for v in scope.values())):
        raise CommandFailure('registry_invalid')
    for key in ('pre_turn_id', 'tool_name', 'input_digest', 'policy_sha256'):
        if not isinstance(record[key], str) or not record[key]:
            raise CommandFailure('registry_invalid')
    clock = record['created_clock']
    if (not isinstance(clock, dict) or set(clock) != {'audit_utc', 'monotonic_ns', 'comparison_domain'}
            or type(clock['monotonic_ns']) is not int or clock['monotonic_ns'] < 0
            or not isinstance(clock['audit_utc'], str) or not clock['audit_utc']
            or not isinstance(clock['comparison_domain'], str) or not clock['comparison_domain']):
        raise CommandFailure('registry_invalid')
    try:
        CommandPolicy.parse(record['policy_snapshot'])
    except CommandFailure as exc:
        raise CommandFailure('registry_invalid') from exc
    if not isinstance(record['plans'], list):
        raise CommandFailure('registry_invalid')
    for plan in record['plans']:
        validate_plan(plan)
        if plan['generation'] != record['generation']:
            raise CommandFailure('registry_invalid')
    if (not isinstance(record['episode'], dict) or not record['episode']
            or not isinstance(record['pre_result'], dict)
            or (record['post_turn_id'] is not None and not isinstance(record['post_turn_id'], str))
            or (record['post_fingerprint'] is not None and not isinstance(record['post_fingerprint'], str))
            or (record['post_result'] is not None and not isinstance(record['post_result'], dict))):
        raise CommandFailure('registry_invalid')
    if record['state'] == 'pending' and (record['post_fingerprint'] is not None or record['post_result'] is not None):
        raise CommandFailure('registry_invalid')
    if record['state'] != 'pending' and (record['post_fingerprint'] is None or record['post_result'] is None):
        raise CommandFailure('registry_invalid')
    return record
