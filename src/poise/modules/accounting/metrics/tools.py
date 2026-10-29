"""Historical tool measurements; never execute commands or retain their bodies."""
from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import PurePosixPath
import re

from .domain import ObservationFacts, identity, instant


def text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ''
    if isinstance(value, list):
        return '\n'.join((item['text'] if isinstance(item.get('text'), str) else
                          json.dumps(item, ensure_ascii=False, separators=(',', ':')))
                         if isinstance(item, dict) else str(item) for item in value)
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')) if isinstance(value, dict) else str(value)


def classification(name, body):
    name, lowered = name.lower(), body.lower()
    if name in ('wait', 'write_stdin'):
        return 'polling'
    if 'tools.wait(' in lowered or 'tools.write_stdin(' in lowered:
        return 'batched-polling'
    if 'mcp__repo_checks__run' in lowered or 'run_check.py' in lowered:
        return 'verification'
    if 'content-read.py' in lowered or re.search(r'(^|[;&|]\s*)(cat|head|tail|sed)\s', lowered):
        return 'file-read'
    if 'rg ' in lowered or 'ripgrep' in lowered or 'search_symbol' in lowered:
        return 'search'
    if 'apply_patch' in lowered or 'write_file' in lowered:
        return 'file-write'
    return 'other'


def relative_path(path, checkout):
    path = path.strip('"\'').replace('\\', '/')
    root = str(checkout).replace('\\', '/').rstrip('/')
    if path.startswith(root + '/'):
        path = path[len(root) + 1:]
    path = path.removeprefix('./')
    if PurePosixPath(path).is_absolute() or '..' in path.split('/') or re.match(r'^[A-Za-z]:/', path):
        return None
    return path or None


def file_reads(body, checkout):
    result = []
    patterns = (
        (r'(?:^|[;&|]\s*)cat\s+(?P<path>"[^"]+"|\'[^\']+\'|[^\s;&|]+)', 'cat'),
        (r'(?:^|[;&|]\s*)head\s+(?:-n\s+\d+\s+)?(?P<path>"[^"]+"|\'[^\']+\'|[^\s;&|]+)', 'head'),
        (r'(?:^|[;&|]\s*)tail\s+(?:-n\s+\d+\s+)?(?P<path>"[^"]+"|\'[^\']+\'|[^\s;&|]+)', 'tail'),
        (r'sed\s+-n\s+["\']?(?P<range>\d+(?:,\d+)?p)["\']?\s+(?P<path>"[^"]+"|\'[^\']+\'|[^\s;&|]+)', 'sed'),
    )
    for pattern, reader in patterns:
        for match in re.finditer(pattern, body, flags=re.MULTILINE):
            path = relative_path(match['path'], checkout)
            if path is None:
                continue
            row = {'path': path, 'reader': reader}
            interval = match.groupdict().get('range')
            if interval:
                numbers = [int(n) for n in interval[:-1].split(',')]
                row.update(range_start=numbers[0], range_end=numbers[-1])
            if row not in result:
                result.append(row)
    for match in re.finditer(r'content-read\.py\b(?P<args>[^\n;]*)', body):
        args = match['args']
        selected = re.search(r'--path(?:=|\s+)("[^"]+"|\'[^\']+\'|[^\s]+)', args)
        if selected is None:
            continue
        path = relative_path(selected[1], checkout)
        if path is None:
            continue
        row = {'path': path, 'reader': 'content-read'}
        interval = re.search(r'--lines(?:=|\s+)(\d+)(?:[-:,](\d+))?', args)
        if interval:
            row.update(range_start=int(interval[1]), range_end=int(interval[2] or interval[1]))
        if row not in result:
            result.append(row)
    return result


def check_fields(body, output):
    result = {}
    if 'repo_checks' not in body and 'run_check.py' not in body and 'check_id' not in output:
        return result
    for name, option in (('command_id', 'command-id'), ('workload_class', 'workload-class')):
        match = re.search(r'"' + name + r'"\s*:\s*"([^"]+)"', body) or re.search(
            r'--' + option + r'(?:=|\s+)([^\s"\']+)', body)
        if match:
            result[name] = match[1]
    for candidate in [output.strip(), *re.findall(r'\{[^\n]{20,}\}', output)]:
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        for name in ('check_id', 'command_id', 'workload_class', 'status'):
            if isinstance(value.get(name), str):
                result.setdefault(name, value[name])
        cache = value.get('cache')
        if isinstance(cache, dict) and type(cache.get('hit')) is bool:
            result['cache_hit'] = cache['hit']
        if result:
            break
    return result


def tool_observations(records, scope, source, checkout, categories):
    inputs, outputs, patches = {}, {}, []
    current = None
    for record in records:
        event, payload = record.event, record.event['payload']
        if payload.get('type') == 'task_started':
            current = payload.get('turn_id')
        kind, call = payload.get('type'), payload.get('call_id')
        if event.get('type') == 'response_item' and call:
            if kind in ('custom_tool_call', 'function_call'):
                passthrough = payload.get('internal_chat_message_metadata_passthrough')
                meta = passthrough if isinstance(passthrough, dict) else {}
                inputs.setdefault(call, (record, payload.get('turn_id') or meta.get('turn_id') or current))
            elif kind in ('custom_tool_call_output', 'function_call_output'):
                outputs.setdefault(call, record)
        if kind == 'patch_apply_end' and payload.get('success') is True:
            patches.append((record, payload.get('turn_id') or current))
    result, turns, seen_reads = {'facts': [], 'metadata': []}, {}, set()
    def counts(turn):
        return turns.setdefault(turn, dict.fromkeys(('tool_calls', 'standalone_poll_calls', 'batched_poll_operations',
            'file_reads', 'file_writes', 'tool_output_bytes', 'file_read_bytes', 'lines_added', 'lines_deleted'), 0))
    for call, (record, turn) in inputs.items():
        payload = record.event['payload']
        body = text(payload.get('input') if payload['type'] == 'custom_tool_call' else payload.get('arguments'))
        completed = outputs.get(call)
        output = text(completed.event['payload'].get('output')) if completed else None
        name = payload.get('name') if isinstance(payload.get('name'), str) else 'unknown'
        context = {'session_id': scope['session'], 'turn_id': turn, 'call_id': call, 'tool_name': name,
                   'classification': classification(name, body), 'input_hash': hashlib.sha256(body.encode()).hexdigest(),
                   'input_offset': record.offset, 'output_offset': None if completed is None else completed.offset,
                   'started_at': record.event.get('timestamp'),
                   'completed_at': None if completed is None else completed.event.get('timestamp'),
                   'status': payload.get('status')}
        entity = identity([scope['session'], call])
        facts = ObservationFacts(scope, source, record.event.get('timestamp'))
        for metric, value in (('input_bytes', len(body.encode())),
                              ('output_bytes', None if output is None else len(output.encode()))):
            facts.add('tools', entity, metric, value, 'bytes', kind='observed',
                      state='known' if value is not None else 'unknown', context=context,
                      reason=None if value is not None else 'Matching tool output is absent')
        reported = re.search(r'^Wall time:?\s+([0-9]+(?:\.[0-9]+)?)\s+seconds\b', output or '', re.M)
        duration = float(reported[1]) * 1000 if reported else None
        if duration is None and context['started_at'] and context['completed_at']:
            measured = (instant(context['completed_at']) - instant(context['started_at'])).total_seconds() * 1000
            duration = measured if measured >= 0 else None
        if duration is not None:
            facts.add('tools', entity, 'duration_ms', duration, 'milliseconds',
                      kind='observed', state='known', context=context)
        summary = counts(turn)
        summary['tool_calls'] += 1
        summary['tool_output_bytes'] += 0 if output is None else len(output.encode())
        summary['standalone_poll_calls'] += context['classification'] == 'polling'
        if context['classification'] == 'batched-polling':
            summary['batched_poll_operations'] += len(re.findall(r'tools\.(?:wait|write_stdin)\(', body))
        for read in file_reads(body, checkout):
            category = next((name for name, patterns in categories.items()
                             if any(fnmatchcase(read['path'], pattern) for pattern in patterns)), None)
            read_context = {**context, **read, 'category': category}
            skill = re.match(r'^\.agents/skills/([^/]+)/(.+)$', read['path'])
            if skill:
                read_context.update(skill=skill[1], kind='core' if skill[2] == 'SKILL.md' else 'reference',
                                    repeated=(scope['session'], read['path']) in seen_reads)
            seen_reads.add((scope['session'], read['path']))
            facts.add('files-skills-checks', identity([entity, read]), 'output_bytes',
                      None if output is None else len(output.encode()), 'UTF-8 bytes', kind='observed',
                      state='known' if output is not None else 'unknown', context=read_context,
                      reason=None if output is not None else 'Matching read output is absent')
            summary['file_reads'] += 1
            summary['file_read_bytes'] += 0 if output is None else len(output.encode())
        checked = check_fields(body, output or '')
        if checked:
            facts.describe('files-skills-checks', entity, {**context, **checked})
        result['facts'].extend(facts.facts)
        result['metadata'].extend(facts.metadata)
    for record, turn in patches:
        changes = record.event['payload'].get('changes')
        if not isinstance(changes, dict):
            continue
        summary = counts(turn)
        for path, change in changes.items():
            if not isinstance(change, dict) or not isinstance(change.get('unified_diff'), str):
                continue
            lines = change['unified_diff'].splitlines()
            added = sum(line.startswith('+') and not line.startswith('+++') for line in lines)
            deleted = sum(line.startswith('-') and not line.startswith('---') for line in lines)
            context = {'session_id': scope['session'], 'turn_id': turn, 'path': path,
                       'call_id': record.event['payload'].get('call_id')}
            facts = ObservationFacts(scope, source, record.event.get('timestamp'))
            for name, value in (('lines_added', added), ('lines_deleted', deleted)):
                facts.add('files-skills-checks', identity([scope['session'], record.event_hash, path]),
                          name, value, 'lines', kind='observed', state='known', context=context)
                summary[name] += value
            summary['file_writes'] += 1
            result['facts'].extend(facts.facts)
    for turn, values in turns.items():
        facts = ObservationFacts(scope, source, next((r.event.get('timestamp') for r in records
                                                     if r.event['payload'].get('turn_id') == turn), None))
        for name, value in values.items():
            unit = 'lines' if name.startswith('lines_') else 'UTF-8 bytes' if name.endswith('_bytes') else 'count'
            facts.add('turn-counts', identity([scope['session'], turn]), name, value, unit,
                      kind='observed', state='known', context={'session_id': scope['session'], 'turn_id': turn})
        result['facts'].extend(facts.facts)
    return result
