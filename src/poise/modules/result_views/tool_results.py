"""Complete tool-result contracts and pure model-facing selections."""
from copy import deepcopy
from dataclasses import dataclass
import re

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


def saved_result(value, address, scope):
    """Validate the complete stored wire shape before exposing source pointers."""
    try:
        exact(value, {'schema', 'status', 'response_path', 'tool', 'response', 'capture',
                      'exit_code', 'streams', '_scope', '_fingerprint', '_response_source'})
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
            return cls(deepcopy(p))
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
            'view': {'response_path', 'view'}, 'summary': {'response_path', 'channel'},
            'lines': {'response_path', 'channel', 'from_line', 'to_line'},
            'bytes': {'response_path', 'channel', 'start', 'end'},
            'search': {'response_path', 'channel', 'pattern', 'context', 'max_matches'}}
        if not text(operation) or operation not in fields: reject()
        exact(value, base | fields[operation])
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
                            'tool_input', 'tool_response', 'cwd'}
                if not isinstance(event, dict) or not required.issubset(event): reject()
                if event['hook_event_name'] != 'PostToolUse' or event['tool_name'] != 'Bash':
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


def bounded(value, policy, field):
    require_budget({k: v for k, v in value.items() if k != field}, policy)
    while not fits(value, policy):
        current = value[field]
        if not current: reject('budget_too_small', 'display', 'increase:budget')
        value[field] = current[:-1]
        value['truncated'] = True
    return value


def presentation(record, sources, policy, mode=None):
    from collections import deque
    result = navigation(record, mode)
    require_budget(result, policy)
    if mode == 'full': return result
    display = policy['display']
    primary = sources['stdout'] if sources['stdout'] is not None else sources['response']
    selected_sources = [source for source in (primary, sources['stderr']) if source is not None]
    if any(source.metadata['binary'] for source in selected_sources):
        result.update(selection='binary', preview='')
    else:
        size = sum(source.metadata['bytes'] for source in selected_sources)
        count = sum(source.metadata['lines'] for source in selected_sources)
        def lines():
            for source in selected_sources:
                yield from source.lines()
        if size <= display['passthrough']['max_bytes'] and count <= display['passthrough']['max_lines']:
            selection, excerpt, limit = 'passthrough', ''.join(lines()), display['passthrough']['max_bytes']
        elif record['exit_code'] == 0:
            tail = deque(maxlen=display['success']['tail_lines'])
            for line in lines(): tail.append(prefix(line, display['max_line_bytes']))
            selection, excerpt, limit = 'success-tail', ''.join(tail), display['success']['max_excerpt_bytes']
        else:
            failure = display['failure']; selected = {}; hits = 0; context_end = -1
            prior = deque(maxlen=failure['context_lines']); tail = deque(maxlen=failure['tail_lines'])
            for index, line in enumerate(lines()):
                short = prefix(line, display['max_line_bytes']); tail.append((index, short))
                if hits < failure['match_limit'] and any(re.search(p, line) for p in failure['match_patterns']):
                    selected.update(prior); context_end = index+failure['context_lines']; hits += 1
                if index <= context_end: selected[index] = short
                prior.append((index, short))
            selected.update(tail)
            excerpt = ''.join(selected[i] for i in sorted(selected))
            if not hits: excerpt = 'no configured failure pattern matched\n' + excerpt
            selection, limit = 'failure-context', failure['max_excerpt_bytes']
        result.update(selection=selection, preview=prefix(excerpt, limit))
    if mode == 'expanded':
        details = {name: {key: source.metadata[key] for key in ('bytes', 'lines', 'sha256', 'binary')}
                   for name, source in sources.items() if source is not None}
        if fits({**result, 'details': details}, policy): result['details'] = details
    if not fits(result, policy) and 'details' in result: del result['details']
    return bounded(result, policy, 'preview')


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
    view = presentation(record, sources, policy)
    if policy['hook']['mode'] == 'audit':
        result.update(selection=view['selection'], preview=view['preview'])
        return bounded(result, policy, 'preview')
    raw = response_text(record['response'])
    hook = policy['hook']
    optional = raw if (len(raw.encode()) <= hook['max_response_bytes'] and
                       len(raw.splitlines()) <= hook['max_response_lines']) else view['preview']
    context = result['protocol']['hookSpecificOutput']
    while optional:
        context['additionalContext'] = record['response_path'] + '\n' + optional
        if fits(result, policy): return result
        optional = optional[:-1]
    context['additionalContext'] = record['response_path']
    return result


def inspection_view(value, policy, audience, field=None):
    """Selection is shared; only model delivery applies display envelopes."""
    if audience == 'machine': return value
    if audience != 'agent': reject(field='audience')
    if field is not None: return bounded(value, policy, field)
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
    result = inspection_view(result, policy, audience, 'text')
    result['returned_bytes'][1] = start+len(result['text'].encode())
    return result
