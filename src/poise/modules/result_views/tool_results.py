"""Complete tool-result contracts and pure model-facing selections."""
from copy import deepcopy
from dataclasses import dataclass
import re
from string import Formatter

from ..foundation.errors import DomainError
from ..foundation.validation import configuration_fields
from .domain import utf8_json_text


class ToolResultFailure(DomainError):
    def __init__(self, code, field, action):
        self.code, self.field, self.action = code, field, action
        super().__init__(f'{code}: {field}; {action}')

    def result(self, exit_code=None):
        return {'status': 'rejected', 'error': {
            'code': self.code, 'field': self.field, 'action': self.action}, 'exit_code': exit_code}


def reject(code='invalid_request', field='request', action='provide:request'):
    raise ToolResultFailure(code, field, action)


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        reject()


def integer(value, lower, upper=None):
    return type(value) is int and value >= lower and (upper is None or value <= upper)


def text(value):
    return isinstance(value, str) and bool(value)


def capture_facts(capture, exit_code):
    exact(capture, {'state', 'reason'})
    if capture['state'] not in ('complete', 'partial', 'unknown'): reject()
    if capture['reason'] is not None and not text(capture['reason']): reject()
    if exit_code is not None and type(exit_code) is not int: reject()


def tool_identity(tool):
    exact(tool, {'kind', 'name'})
    if tool['kind'] not in ('bash', 'mcp', 'generic'):
        reject('unmapped_input', 'tool.kind', 'configure:input')
    if not text(tool['name']): reject()


def saved_result(value, address, scope, *, prepared=False):
    """Validate the complete stored wire shape before exposing source pointers."""
    try:
        fields = {'schema', 'status', 'response_path', 'tool', 'response', 'capture',
                  'exit_code', 'streams', '_scope', '_fingerprint', '_response_source'}
        if prepared:
            fields |= {'run_id', 'full_path', 'expanded_path', 'last_path', '_prepared', '_published'}
        exact(value, fields)
        if (value['schema'] != 'poise-tool-result-1' or value['status'] != 'stored'
                or value['response_path'] != address or value['_scope'] != scope): reject()
        if not isinstance(value['_fingerprint'], str) or not re.fullmatch('[0-9a-f]{64}', value['_fingerprint']): reject()
        capture_facts(value['capture'], value['exit_code']); tool_identity(value['tool'])
        exact(value['streams'], {'stdout', 'stderr'})
        items = [value['_response_source'], *[v for v in value['streams'].values() if v is not None]]
        for item in items:
            exact(item, {'path', 'bytes', 'lines', 'sha256', 'binary'})
            if (not text(item['path']) or not item['path'].startswith('/')
                    or not integer(item['bytes'], 0) or not integer(item['lines'], 0)
                    or type(item['binary']) is not bool or not isinstance(item['sha256'], str)
                    or not re.fullmatch('[0-9a-f]{64}', item['sha256'])): reject()
        if prepared:
            from uuid import UUID
            if str(UUID(value['run_id'])) != value['run_id']: reject()
            exact(value['_prepared'], {'brief', 'expanded'})
            if any(not isinstance(v, str) for v in value['_prepared'].values()): reject()
            if type(value['_published']) is not bool: reject()
            if any(not text(value[k]) or not value[k].startswith('/')
                   for k in ('full_path', 'expanded_path', 'last_path')): reject()
        return value
    except (DomainError, KeyError, TypeError, ValueError) as exc:
        raise ToolResultFailure('source_invalid', 'source', 'restore:source') from exc


def encoded(value):
    return utf8_json_text(value, separators=(',', ':')) + '\n'


def response_text(value):
    return utf8_json_text(value, indent=2) + '\n'


def prefix(value, limit):
    return value.encode('utf-8')[:limit].decode('utf-8', 'ignore')


@dataclass(frozen=True)
class ToolResultPolicy:
    data: dict

    @classmethod
    def parse(cls, raw):
        try:
            def config(value, fields):
                return configuration_fields(value, set(fields.split()), 'tool result policy', DomainError)
            p = config(raw, 'schema version storage placement input display inspection hook')
            if p['schema'] != 'poise-tool-result-policy-1' or not text(p['version']):
                raise ValueError('Unsupported policy')
            p['storage'] = config(p['storage'], 'root response stdout stderr text file_mode chunk_bytes retention')
            s = p['storage']
            if not text(s['root']) or not s['root'].startswith('/') or s['retention'] != 'operator-managed':
                raise ValueError('Explicit result root and lifetime required')
            names = [s[k] for k in ('response', 'stdout', 'stderr', 'text')]
            if any(not text(n) or '/' in n or n in ('.', '..') for n in names) or len(set(names)) != 4:
                raise ValueError('Distinct file basenames required')
            if not integer(s['file_mode'], 0, 0o777) or not integer(s['chunk_bytes'], 1):
                raise ValueError('Invalid storage settings')
            p['placement'] = config(p['placement'], 'policy purpose recipient lifetime')
            if any(not text(v) for v in p['placement'].values()) or not p['placement']['policy'].startswith('/'):
                raise ValueError('Explicit placement required')
            p['input'] = config(p['input'], 'max_bytes')
            if not integer(p['input']['max_bytes'], 1):
                raise ValueError('Invalid input limit')
            d = p['display'] = config(p['display'], 'output_chars output_bytes passthrough success failure max_line_bytes binary')
            for k in ('output_chars', 'output_bytes', 'max_line_bytes'):
                if not integer(d[k], 1): raise ValueError(k)
            d['passthrough'] = config(d['passthrough'], 'max_bytes max_lines')
            d['success'] = config(d['success'], 'tail_lines max_excerpt_bytes')
            d['failure'] = config(d['failure'], 'match_patterns match_limit context_lines tail_lines max_excerpt_bytes')
            d['binary'] = config(d['binary'], 'nul_byte_is_binary')
            if type(d['binary']['nul_byte_is_binary']) is not bool: raise ValueError('Invalid binary policy')
            for section in ('passthrough', 'success', 'failure'):
                for k, v in d[section].items():
                    if k != 'match_patterns' and not integer(v, 0 if k == 'context_lines' else 1):
                        raise ValueError(k)
            patterns = d['failure']['match_patterns']
            if not isinstance(patterns, list) or any(not text(v) for v in patterns):
                raise ValueError('Explicit failure patterns required')
            for pattern in patterns: re.compile(pattern)
            p['inspection'] = config(p['inspection'], 'max_lines max_matches default_context_lines max_context_lines max_excerpt_bytes')
            for k, v in p['inspection'].items():
                if not integer(v, 0 if 'context' in k else 1): raise ValueError(k)
            if p['inspection']['default_context_lines'] > p['inspection']['max_context_lines']:
                raise ValueError('Invalid default context')
            p['hook'] = config(p['hook'], 'mode max_response_bytes max_response_lines')
            if p['hook']['mode'] not in ('audit', 'enforce'): raise ValueError('Invalid hook mode')
            for k in ('max_response_bytes', 'max_response_lines'):
                if not integer(p['hook'][k], 1): raise ValueError(k)
            if 'session' in raw or 'parsers' in raw:
                session = config(raw['session'], 'root runs last expanded full')
                if not text(session['root']) or not session['root'].startswith('/'):
                    raise ValueError('Explicit session root required')
                names = [session[k] for k in ('runs', 'last', 'expanded', 'full')]
                if any(not text(n) or '/' in n or n in ('.', '..') for n in names) or len(set(names)) != 4:
                    raise ValueError('Distinct session names required')
                if not isinstance(raw['parsers'], dict) or not raw['parsers']:
                    raise ValueError('Explicit tool parsers required')
                for name, parser in raw['parsers'].items():
                    if not text(name) or not isinstance(parser, dict): raise ValueError('Invalid parser')
                    for field in ('parser', 'format', 'brief_template', 'expanded_template', 'overflow'):
                        if not text(parser[field]): raise ValueError(field)
                    if parser['overflow'] != 'reject': raise ValueError('Unsupported overflow')
                    for view in ('brief', 'expanded'):
                        limits = config(parser[view], 'chars bytes')
                        if any(not integer(n, 1) for n in limits.values()): raise ValueError(view)
                    if 'expanded_fields' in parser or 'expanded_separator' in parser:
                        fields = parser['expanded_fields']
                        if not isinstance(fields, list) or not isinstance(parser['expanded_separator'], str):
                            raise ValueError('Invalid expanded projection')
                        for field in fields:
                            item = config(field, 'path required format template')
                            if (not isinstance(item['path'], list) or not item['path']
                                    or any(not text(k) for k in item['path'])
                                    or type(item['required']) is not bool
                                    or item['format'] not in ('json', 'count')
                                    or not isinstance(item['template'], str)):
                                raise ValueError('Invalid expanded field')
                            template_fields(item['template'], {'value'})
                p.update(session=deepcopy(session), parsers=deepcopy(raw['parsers']))
            if 'selection' in raw:
                rules = raw['selection']
                if not isinstance(rules, list) or not rules:
                    raise ValueError('Explicit parser selection required')
                ids = set()
                for rule in rules:
                    item = config(rule, 'id tool_name profile priority command_pattern')
                    if (not text(item['id']) or item['id'] in ids or not text(item['tool_name'])
                            or not text(item['profile']) or item['profile'] not in raw['parsers']
                            or not integer(item['priority'], 0)):
                        raise ValueError('Invalid parser selection')
                    ids.add(item['id'])
                    if item['command_pattern'] is not None:
                        if not isinstance(item['command_pattern'], str):
                            raise ValueError('Invalid command pattern')
                        re.compile(item['command_pattern'])
            return cls(deepcopy(raw))
        except (DomainError, KeyError, TypeError, ValueError, re.error) as exc:
            raise ToolResultFailure('invalid_policy', 'policy', 'correct:policy') from exc


@dataclass(frozen=True)
class ToolResultRequest:
    data: dict

    @classmethod
    def parse(cls, value, policy):
        if not isinstance(value, dict) or value.get('schema') != 'poise-tool-result-request-1': reject()
        operation = value.get('operation')
        base = {'schema', 'operation'}
        fields = {
            'capture': {'request_id', 'tool', 'response', 'streams', 'capture', 'exit_code'},
            'hook': {'request_id', 'event', 'streams', 'capture', 'exit_code'},
            'expanded': set(), 'full': set(),
            'view': {'response_path', 'view'}, 'summary': {'response_path', 'channel'},
            'lines': {'response_path', 'channel', 'from_line', 'to_line'},
            'bytes': {'response_path', 'channel', 'start', 'end'},
            'search': {'response_path', 'channel', 'pattern', 'context', 'max_matches'}}
        if not text(operation) or operation not in fields: reject()
        exact(value, base | fields[operation])
        if operation in ('expanded', 'full'):
            if 'session' not in policy: reject('invalid_policy', 'session', 'correct:policy')
            return cls(deepcopy(value))
        if operation in ('capture', 'hook'):
            if not text(value['request_id']): reject()
            exact(value['streams'], {'stdout', 'stderr'})
            if any(v is not None and (not text(v) or not v.startswith('/')) for v in value['streams'].values()): reject()
            capture_facts(value['capture'], value['exit_code'])
            if operation == 'capture':
                tool_identity(value['tool'])
            else:
                event = value['event']
                required = {'session_id', 'turn_id', 'tool_use_id', 'hook_event_name', 'tool_name',
                            'tool_input', 'cwd'}
                if not isinstance(event, dict) or not required.issubset(event): reject()
                if 'tool_response' not in event:
                    reject('source_missing', 'tool_response', 'provide:source')
                if event['hook_event_name'] != 'PostToolUse' or (event['tool_name'] != 'Bash'
                        and 'session' not in policy):
                    reject('unmapped_input', 'event', 'configure:input')
                if event['tool_use_id'] != value['request_id']: reject()
        else:
            if not text(value['response_path']) or not value['response_path'].startswith('/'): reject()
            if operation == 'view':
                if value['view'] not in ('brief', 'expanded', 'full'): reject()
            elif value['channel'] not in ('stdout', 'stderr', 'response'): reject()
            if operation in ('lines', 'bytes'):
                start, end = (value['from_line'], value['to_line']) if operation == 'lines' else (value['start'], value['end'])
                if not integer(start, 1 if operation == 'lines' else 0) or not integer(end, start):
                    reject('invalid_range', 'range', 'choose:range')
            if operation == 'search':
                i = policy['inspection']
                if not integer(value['context'], 0, i['max_context_lines']) or not integer(value['max_matches'], 1, i['max_matches']): reject()
                if not isinstance(value['pattern'], str): reject()
                try: re.compile(value['pattern'])
                except re.error: reject('invalid_pattern', 'pattern', 'correct:pattern')
        return cls(deepcopy(value))


def select_parser(event, policy):
    """Choose one declared native profile without decoding or authorizing input."""
    name = event['tool_name']
    if 'selection' not in policy:
        if name not in policy['parsers']:
            reject('unmapped_input', 'tool_name', 'configure:input')
        return name, policy['parsers'][name]
    rules = [r for r in policy['selection'] if r['tool_name'] == name]
    command = event.get('tool_input', {}).get('command') if isinstance(event.get('tool_input'), dict) else None
    if any(r['command_pattern'] is not None for r in rules) and not isinstance(command, str):
        reject('unmapped_input', 'tool_input.command', 'configure:input')
    matches = [r for r in rules if r['command_pattern'] is None
               or re.fullmatch(r['command_pattern'], command) is not None]
    if not matches:
        reject('unmapped_input', 'tool_name', 'configure:input')
    priority = max(r['priority'] for r in matches)
    winners = [r for r in matches if r['priority'] == priority]
    if len(winners) != 1:
        reject('ambiguous_parser', 'selection', 'configure:input')
    profile = winners[0]['profile']
    return profile, policy['parsers'][profile]


def native_snapshot(event, policy, *, selected=None):
    """Classify the declared whole wire value without reading or running a source."""
    name = event['tool_name']
    _, parser = select_parser(event, policy) if selected is None else selected
    response = deepcopy(event['tool_response'])
    capture = {'state': 'complete', 'reason': None}
    exit_code = None
    if parser['format'] in ('native-exec-json', 'saved-work-json'):
        if not isinstance(response, dict) or not isinstance(response.get('output'), str):
            reject('unmapped_input', 'tool_response', 'configure:input')
        if 'session_id' in response and type(response.get('exit_code')) is not int:
            return {'pending': True, 'capture': {'state': 'unknown', 'reason': 'native_process_pending'}}
        if type(response.get('exit_code')) is not int:
            reject('unmapped_input', 'exit_code', 'configure:input')
        exit_code = response['exit_code']
        output = response['output']
        if (re.match(r'Warning: truncated output \(original token count: \d+\)', output)
                or re.search(r'…\d+ tokens truncated…', output)):
            capture = {'state': 'partial', 'reason': 'native_output_truncated'}
        elif 'tokens truncated' in output or output.startswith('Warning: truncated output'):
            capture = {'state': 'unknown', 'reason': 'native_output_ambiguous'}
    elif parser['format'] == 'records-json':
        if not isinstance(response, dict) or not isinstance(response.get('content'), list):
            reject('unmapped_input', 'tool_response', 'configure:input')
    elif parser['format'] == 'work-json':
        if not isinstance(response, dict) or not isinstance(response.get('status'), str):
            reject('unmapped_input', 'tool_response', 'configure:input')
    else:
        reject('unmapped_input', 'format', 'configure:input')
    return {'pending': False, 'tool': {'name': name,
            'kind': 'mcp' if parser['parser'] == 'mcp' else 'bash' if name == 'Bash' else 'generic'},
            'response': response, 'capture': capture, 'exit_code': exit_code}


def template_fields(template, allowed=None):
    """Admit plain named slots only, without attribute/index traversal or coercion."""
    fields = set()
    for _, field, spec, conversion in Formatter().parse(template):
        if field is None:
            continue
        if (not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', field) or spec or conversion
                or (allowed is not None and field not in allowed)):
            raise ValueError('Invalid template field')
        fields.add(field)
    return fields


def work_template(template, raw, *, brief=None):
    values = {}
    for field in template_fields(template):
        value = brief if field == 'brief' else raw[field]
        if not isinstance(value, str):
            raise ValueError('Invalid work field')
        values[field] = value
    return template.format(**values)


def work_projection(raw, parser):
    lines = []
    for field in parser['expanded_fields']:
        value, found = raw, True
        for key in field['path']:
            if not isinstance(value, dict):
                raise ValueError('Invalid work projection path')
            if key not in value:
                found = False
                break
            value = value[key]
        if not found:
            if field['required']:
                raise ValueError('Missing required work field')
            continue
        if field['format'] == 'count':
            if not isinstance(value, list):
                raise ValueError('Invalid work collection')
            rendered = str(len(value))
        else:
            rendered = utf8_json_text(value)
        template_fields(field['template'], {'value'})
        lines.append(field['template'].format(value=rendered))
    return lines


def prepare_views(record, sources, policy, *, selected_parser=None):
    """Select both forms once from the complete source at its canonical owner."""
    parser = (policy.get('parsers', {}).get(record['tool']['name'])
              if selected_parser is None else selected_parser)
    if parser is None or parser.get('parser') not in ('bash', 'mcp', 'poise'):
        reject('unmapped_input', 'parser', 'configure:input')
    try:
        raw = record['response']
        kind = parser['parser']
        if kind == 'bash':
            actual = []
            for channel in ('stdout', 'stderr'):
                if sources[channel] is not None:
                    actual.extend(''.join(sources[channel].lines()).splitlines())
            lines = actual if actual else raw['output'].splitlines()
            summaries = [line[len(parser['summary_prefix']):] for line in lines
                         if line.startswith(parser['summary_prefix'])]
            details = '\n'.join(line for line in lines if line.startswith(parser['detail_prefix']))
            if summaries:
                brief = parser['brief_template'].format(exit_code=record['exit_code'], summary=summaries[0])
            elif parser['summary_missing'] == 'exit-only':
                brief = parser['brief_without_summary'].format(exit_code=record['exit_code'])
            else:
                raise ValueError('Missing summary')
            expanded = parser['expanded_template'].format(brief=brief, details=details)
        elif kind == 'mcp':
            content = raw['structuredContent']
            if type(content['count']) is not int or not isinstance(content['names'], list):
                raise ValueError('Invalid records')
            if any(not isinstance(n, str) for n in content['names']): raise ValueError('Invalid names')
            brief = parser['brief_template'].format(count=content['count'])
            expanded = parser['expanded_template'].format(brief=brief, names=','.join(content['names']))
        else:
            if not isinstance(raw['status'], str):
                raise ValueError('Invalid work result')
            brief = work_template(parser['brief_template'], raw)
            expanded = work_template(parser['expanded_template'], raw, brief=brief)
            if 'expanded_fields' in parser:
                expanded = parser['expanded_separator'].join([expanded, *work_projection(raw, parser)])
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError) as exc:
        raise ToolResultFailure('parser_failed', 'source', 'inspect:source') from exc
    pair = {'brief': brief, 'expanded': expanded}
    for name, value in pair.items():
        if len(value) > parser[name]['chars'] or len(value.encode()) > parser[name]['bytes']:
            reject('budget_too_small', name, 'increase:budget')
    return pair


def navigation(record, mode=None):
    result = {'status': 'stored', 'response_path': record['response_path']}
    if mode is not None: result['view'] = mode
    result.update(capture=record['capture'], exit_code=record['exit_code'])
    return result


def fits(value, policy):
    body = encoded(value)
    return len(body) <= policy['display']['output_chars'] and len(body.encode()) <= policy['display']['output_bytes']


def require_budget(value, policy):
    if not fits(value, policy): reject('budget_too_small', 'display', 'increase:budget')


def head_tail(value, limit):
    """Format one candidate from the original text, with UTF-8-safe ends."""
    raw = value.encode()
    if len(raw) <= limit: return value
    marker = '\n[tool-result] excerpt truncated by output policy\n'
    available = limit-len(marker.encode())
    if available <= 0: return prefix(marker, limit)
    head = available//2; tail = available-head
    return raw[:head].decode('utf-8', 'ignore') + marker + raw[-tail:].decode('utf-8', 'ignore')


def bounded_text(original, policy, limit, render, *, contiguous):
    """One decision for excerpt and complete serialized-envelope limits."""
    for maximum in range(min(len(original.encode()), limit), -1, -1):
        text = prefix(original, maximum) if contiguous else head_tail(original, maximum)
        value = render(text, text != original)
        if fits(value, policy): return value
    reject('budget_too_small', 'display', 'increase:budget')


def selected_excerpt(record, sources, policy):
    from collections import deque
    display = policy['display']
    primary = sources['stdout'] if sources['stdout'] is not None else sources['response']
    selected_sources = [source for source in (primary, sources['stderr']) if source is not None]
    if any(source.metadata['binary'] for source in selected_sources):
        return 'binary', '', 0, False
    else:
        shortened = False
        size = sum(source.metadata['bytes'] for source in selected_sources)
        count = sum(source.metadata['lines'] for source in selected_sources)
        def lines():
            for source in selected_sources:
                yield from source.lines()
        if size <= display['passthrough']['max_bytes'] and count <= display['passthrough']['max_lines']:
            selection, excerpt, limit = 'passthrough', ''.join(lines()), display['passthrough']['max_bytes']
        elif record['exit_code'] == 0:
            tail = deque(maxlen=display['success']['tail_lines'])
            for line in lines():
                short = prefix(line, display['max_line_bytes']); tail.append((short, short != line))
            shortened = any(clipped for _, clipped in tail)
            selection, excerpt, limit = 'success-tail', ''.join(text for text, _ in tail), display['success']['max_excerpt_bytes']
        else:
            failure = display['failure']; selected = {}; hits = 0; context_end = -1
            prior = deque(maxlen=failure['context_lines']); tail = deque(maxlen=failure['tail_lines'])
            for index, line in enumerate(lines()):
                short = prefix(line, display['max_line_bytes']); item = (short, short != line)
                tail.append((index, item))
                if hits < failure['match_limit'] and any(re.search(p, line) for p in failure['match_patterns']):
                    selected.update(prior); context_end = index+failure['context_lines']; hits += 1
                if index <= context_end: selected[index] = item
                prior.append((index, item))
            selected.update(tail)
            excerpt = ''.join(selected[i][0] for i in sorted(selected))
            shortened = any(clipped for _, clipped in selected.values())
            if not hits: excerpt = 'no configured failure pattern matched\n' + excerpt
            selection, limit = 'failure-context', failure['max_excerpt_bytes']
        return selection, excerpt, limit, shortened


def presentation(record, sources, policy, mode=None):
    result = navigation(record, mode)
    require_budget(result, policy)
    if mode == 'full': return result
    selection, excerpt, limit, shortened = selected_excerpt(record, sources, policy)
    def render(text, clipped):
        value = {**result, 'selection': selection, 'preview': text}
        if shortened or clipped: value['truncated'] = True
        return value
    result = bounded_text(excerpt, policy, limit, render, contiguous=False)
    if mode == 'expanded':
        details = {name: {key: source.metadata[key] for key in ('bytes', 'lines', 'sha256', 'binary')}
                   for name, source in sources.items() if source is not None}
        if fits({**result, 'details': details}, policy): result['details'] = details
    return result


def hook_navigation(record, policy):
    result = navigation(record)
    if policy['hook']['mode'] == 'audit':
        return {**result, 'action': 'would_replace'}
    return {**result, 'action': 'enforced', 'protocol': {
        'continue': False, 'stopReason': 'Полный результат сохранён по указанному адресу.',
        'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': record['response_path']}}}


def hook_presentation(record, sources, policy):
    result = hook_navigation(record, policy)
    require_budget(result, policy)
    selection, excerpt, limit, shortened = selected_excerpt(record, sources, policy)
    if policy['hook']['mode'] == 'audit':
        def render(text, clipped):
            value = {**result, 'selection': selection, 'preview': text}
            if shortened or clipped: value['truncated'] = True
            return value
        return bounded_text(excerpt, policy, limit, render, contiguous=False)
    raw = response_text(record['response'])
    hook = policy['hook']
    if len(raw.encode()) <= hook['max_response_bytes'] and len(raw.splitlines()) <= hook['max_response_lines']:
        excerpt, limit, shortened = raw, hook['max_response_bytes'], False
    def render(text, clipped):
        value = hook_navigation(record, policy)
        if shortened or clipped: value['truncated'] = True
        value['protocol']['hookSpecificOutput']['additionalContext'] = record['response_path'] + ('\n'+text if text else '')
        return value
    return bounded_text(excerpt, policy, limit, render, contiguous=False)


def inspection_view(value, policy, audience, field=None):
    """Selection is shared; only model delivery applies display envelopes."""
    if audience == 'machine': return value
    if audience != 'agent': reject(field='audience')
    if field is not None:
        start = value['returned_bytes'][0]; lines = value['returned_lines']
        def render(text, clipped):
            result = {**value, field: text, 'returned_bytes': [start, start+len(text.encode())],
                      'truncated': value['truncated'] or clipped}
            if lines is not None:
                count = text.count('\n') + int(bool(text) and not text.endswith('\n'))
                result['returned_lines'] = [lines[0], lines[0]+count-1] if count else [0, 0]
            return result
        return bounded_text(value[field], policy, len(value[field].encode()), render, contiguous=True)
    require_budget(value, policy)
    return value


def inspect_source(record, source, request, policy, *, audience):
    from collections import deque
    operation = request['operation']; metadata = source.metadata
    result = {'status': 'stored', 'response_path': record['response_path'], 'channel': request['channel'],
              'binary': metadata['binary']}
    if operation == 'summary':
        result.update({k: metadata[k] for k in ('bytes', 'lines', 'sha256')})
        return inspection_view(result, policy, audience)
    if metadata['binary']:
        result.update(text='', matches=[], lines=[], truncated=False)
        return inspection_view(result, policy, audience)
    limit = policy['inspection']['max_excerpt_bytes']
    if operation == 'search':
        matches = []; selected = {}; prior = deque(maxlen=request['context']); context_end = -1; more = False
        for i, line in enumerate(source.lines()):
            original = line.rstrip('\r\n'); short = prefix(original, limit)
            if len(matches) <= request['max_matches'] and re.search(request['pattern'], original):
                if len(matches) == request['max_matches']: more = True
                else:
                    matches.append(i); selected.update(prior); context_end = i+request['context']
            if i <= context_end: selected[i] = (short, short != original)
            prior.append((i, (short, short != original)))
        rows = []; remaining = limit; shortened = more
        for i in sorted(selected):
            original, clipped = selected[i]; part = prefix(original, remaining)
            shortened |= clipped or part != original; remaining -= len(part.encode())
            if part or not original: rows.append({'line': i+1, 'text': part})
        result.update(lines=rows, matches=[row for row in rows if row['line']-1 in matches], truncated=shortened)
        return inspection_view(result, policy, audience)
    if operation == 'bytes':
        start, requested_end = request['start'], min(request['end'], metadata['bytes'])
        if start > metadata['bytes'] or not source.boundary(start) or not source.boundary(requested_end):
            reject('invalid_range', 'range', 'choose:range')
        raw = source.bytes(start, min(requested_end, start+limit))
        part = raw.decode('utf-8', 'ignore'); returned_lines = None
        truncated = start+len(part.encode()) < requested_end
    else:
        first, requested_last = request['from_line'], request['to_line']
        if not metadata['lines'] and first == 1:
            start = 0; part = ''; returned_lines = [0, 0]; truncated = False
        else:
            if first > metadata['lines']: reject('invalid_range', 'range', 'choose:range')
            last = min(requested_last, metadata['lines'], first+policy['inspection']['max_lines']-1)
            chunks = []; offset = 0; remaining = limit; start = None; shortened = False
            for number, line in enumerate(source.lines(), 1):
                if number == first: start = offset
                if first <= number <= last:
                    if not remaining:
                        shortened = True; last = number-1; break
                    chunk = prefix(line, remaining); chunks.append(chunk); remaining -= len(chunk.encode())
                    shortened |= chunk != line
                    if chunk != line:
                        last = number; break
                offset += len(line.encode())
                if number == last: break
            part = ''.join(chunks); returned_lines = [first, last]
            truncated = shortened or last < min(requested_last, metadata['lines'])
    result.update(text=part, returned_bytes=[start, start+len(part.encode())], returned_lines=returned_lines,
                  total_lines=metadata['lines'], total_bytes=metadata['bytes'], truncated=truncated)
    return inspection_view(result, policy, audience, 'text')
