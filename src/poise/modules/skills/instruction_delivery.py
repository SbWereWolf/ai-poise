"""Current-context instruction delivery, separate from source observation.

A receipt records versions. Only the caller's separate confirmation that text
is retained in the same context permits omitting it from the delivery packet.
"""
from dataclasses import replace
import hashlib
import json
import re

from ..foundation.errors import DomainError
from ..source_reader.domain import select_text_range
from .instructions import (
    InstructionInputError, MaterialSpec, material_key, parse_prepare_request,
    prepare_paths, relative_file, strings, text,
)


SCHEMA = 'poise-instruction-delivery-1'
RECEIPT_SCHEMA = 'poise-instruction-delivery-receipt-1'
PORTABLE_SCHEMA = 'poise-portable-instruction-result-1'
FRAME_SCHEMA = 'poise-portable-instructions-frame-1'
IDENTITY = ('kind', 'id', 'root', 'path')
FINGERPRINT = (*IDENTITY, 'sha256')
FILE_FIELDS = ('operation', 'project_config', 'checkout', 'policy', 'paths',
               'stage', 'context', 'previous', 'retained', 'tool')


def require(condition, field, code='invalid_request'):
    if not condition:
        raise InstructionInputError(code, field, 'provide:' + field)


def fields(value, names):
    return isinstance(value, dict) and set(value) == set(names)


def stage_value(value):
    require(fields(value, ('id', 'skill_ids')) and text(value['id']), 'stage')
    require(strings(value['skill_ids']), 'stage.skill_ids')
    return {'id': value['id'], 'skill_ids': sorted(value['skill_ids'])}


def context_value(value):
    require(fields(value, ('id', 'generation', 'reason')) and text(value['id'])
            and type(value['generation']) is int and value['generation'] > 0
            and value['reason'] in ('initial', 'continue', 'resume', 'compaction'), 'context')
    return dict(value)


def budget_value(value):
    require(fields(value, ('max_bytes',)) and type(value['max_bytes']) is int
            and value['max_bytes'] > 0, 'budget')
    return dict(value)


def continuation_value(value):
    require(value is None or text(value), 'continuation')
    return value


def parse_delivery_request(raw):
    require(fields(raw, FILE_FIELDS)
            and raw['operation'] == 'deliver', 'request')
    base = parse_prepare_request({
        'operation': 'prepare', 'previous': None,
        **{key: raw[key] for key in ('project_config', 'checkout', 'policy', 'paths')},
    })
    context = context_value(raw['context'])
    require(strings(raw['retained']), 'retained')
    tool = raw['tool']
    require(tool is None or (
        fields(tool, ('id', 'root', 'path')) and text(tool['id']) and ':' not in tool['id']
        and tool['root'] in ('checkout', 'instructions') and relative_file(tool['path'])), 'tool')
    return {**raw, **{key: base[key] for key in ('project_config', 'checkout', 'policy')},
            'paths': prepare_paths(base['paths']), 'stage': stage_value(raw['stage']),
            'context': dict(context), 'retained': list(raw['retained']),
            'tool': None if tool is None else dict(tool)}


def parse_bounded_delivery_request(raw):
    require(fields(raw, (*FILE_FIELDS, 'budget', 'continuation')), 'request')
    parsed = parse_delivery_request({key: raw[key] for key in FILE_FIELDS})
    return {**parsed, 'budget': budget_value(raw['budget']),
            'continuation': continuation_value(raw['continuation'])}


def select_delivery_materials(specs, catalog, stage, paths, tool):
    selected = {tuple(getattr(spec, key) for key in IDENTITY): spec for spec in specs}
    try:
        assigned = catalog.select(stage['skill_ids'])
    except DomainError as exc:
        raise InstructionInputError('invalid_request', 'stage.skill_ids',
                                    'provide:stage.skill_ids') from exc
    for skill in assigned:
        spec = MaterialSpec('skill', skill['id'], 'instructions', skill['path'],
                            tuple(paths), True, ('stage:' + stage['id'],))
        key = tuple(getattr(spec, field) for field in IDENTITY)
        prior = selected.get(key)
        if prior is not None:
            spec = replace(spec, scopes=tuple(sorted(set(prior.scopes) | set(spec.scopes))),
                           reasons=tuple(sorted(set(prior.reasons) | set(spec.reasons))))
        selected[key] = spec
    if tool is not None:
        spec = MaterialSpec('tool', tool['id'], tool['root'], tool['path'], tuple(paths),
                            True, ('tool:' + tool['id'],))
        selected[tuple(getattr(spec, field) for field in IDENTITY)] = spec
    return [spec for _, spec in sorted(selected.items())]


def validate_delivery_receipt(previous, observation):
    if previous is None:
        return
    try:
        require(fields(previous, ('schema', 'project', 'checkout', 'instruction_root',
                                  'context', 'stage', 'paths', 'materials')), 'previous')
        require(previous['schema'] == RECEIPT_SCHEMA, 'previous')
        require(all(previous[key] == observation[key]
                    for key in ('project', 'checkout', 'instruction_root')), 'previous')
        context = previous['context']
        require(fields(context, ('id', 'generation')) and text(context['id'])
                and type(context['generation']) is int and context['generation'] > 0, 'previous')
        require(stage_value(previous['stage']) == previous['stage'], 'previous')
        require(isinstance(previous['paths'], list)
                and prepare_paths(previous['paths']) == previous['paths'], 'previous')
        require(isinstance(previous['materials'], list), 'previous')
        identities = []
        for record in previous['materials']:
            require(fields(record, FINGERPRINT), 'previous')
            require(record['kind'] in ('rule', 'profile', 'skill', 'tool')
                    and text(record['id']) and ':' not in record['id']
                    and relative_file(record['path']), 'previous')
            roots = ('checkout', 'instructions') if record['kind'] == 'tool' else (
                ('instructions',) if record['kind'] == 'skill' else ('checkout',))
            require(record['root'] in roots and isinstance(record['sha256'], str)
                    and re.fullmatch('[0-9a-f]{64}', record['sha256']) is not None, 'previous')
            identities.append(tuple(record[key] for key in IDENTITY))
        require(identities == sorted(set(identities)), 'previous')
    except InstructionInputError as exc:
        raise InstructionInputError('invalid_previous', 'previous', 'provide:previous') from exc


def delivery_receipt(observation, request):
    binding = {key: observation[key] for key in ('project', 'checkout', 'instruction_root')}
    context = {key: request['context'][key] for key in ('id', 'generation')}
    return {'schema': RECEIPT_SCHEMA, **binding, 'context': context, 'stage': request['stage'],
            'paths': observation['paths'], 'materials': [
                {key: record[key] for key in FINGERPRINT}
                for record in observation['materials'] if record['available']]}


def delivery_result(observation, request):
    previous = request['previous']
    validate_delivery_receipt(previous, observation)
    before = {} if previous is None else {
        material_key(record): record['sha256'] for record in previous['materials']}
    retained = set(request['retained'])
    require(previous is None or retained <= before.keys(), 'retained')
    context = {key: request['context'][key] for key in ('id', 'generation')}
    comparable = (previous is not None and request['context']['reason'] == 'continue'
                  and previous['context'] == context and previous['stage'] == request['stage']
                  and previous['paths'] == observation['paths'])
    materials, reused = [], []
    for record in observation['materials']:
        key = material_key(record)
        if comparable and record['available'] and key in retained and before[key] == record['sha256']:
            reused.append({field: value for field, value in record.items() if field != 'content'})
        else:
            materials.append(record)
    binding = {key: observation[key] for key in ('project', 'checkout', 'instruction_root')}
    receipt = delivery_receipt(observation, request)
    delivery = {**binding, 'context': request['context'], 'stage': request['stage'],
                'paths': observation['paths'], 'materials': materials, 'reused': reused,
                'diagnostics': observation['diagnostics']}
    return {'schema': SCHEMA, 'status': observation['status'], 'observation': observation,
            'delivery': delivery, 'receipt': receipt}


def canonical_bytes(value):
    """Instruction-frame byte contract, independent of tool display policy."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def delivery_binding(observation, request):
    value = {key: observation[key] for key in (
        'project', 'checkout', 'instruction_root', 'policy_sha256', 'paths', 'diagnostics')}
    value.update(context={key: request['context'][key] for key in ('id', 'generation')},
                 stage=request['stage'], materials=[
                     {key: item for key, item in record.items() if key != 'content'}
                     for record in observation['materials']])
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def material_address(binding, record):
    return 'material:' + hashlib.sha256(canonical_bytes([binding, material_key(record)])).hexdigest()


def continuation_token(binding, budget):
    return 'continuation:' + hashlib.sha256(canonical_bytes([binding, budget])).hexdigest()


def frame_context(observation, request):
    return {**{key: observation[key] for key in ('project', 'checkout', 'instruction_root', 'paths')},
            'context': request['context'], 'stage': request['stage']}


def portable_result(observation, request, frame, receipt=None):
    return {'schema': PORTABLE_SCHEMA, 'status': frame['status'], 'observation': observation,
            'delivery': frame,
            'receipt': delivery_receipt(observation, request) if receipt is None else receipt}


def bounded_delivery_result(observation, request):
    base = delivery_result(observation, request)
    binding = delivery_binding(observation, request)
    token = continuation_token(binding, request['budget'])
    if request['continuation'] is not None and (
            request['context']['reason'] != 'continue' or request['continuation'] != token):
        raise InstructionInputError('stale_continuation', 'continuation', 'refresh:delivery')
    pending = base['delivery']['materials']
    remaining = [{
        'key': material_key(record), 'required': record['required'], 'sha256': record['sha256'],
        'address': material_address(binding, record) if record['available'] else None,
        'reason': 'budget' if record['available'] else 'unavailable',
    } for record in pending]

    def frame(materials, left):
        has_pending = any(row['reason'] == 'budget' for row in left)
        status = ('needs_inputs' if observation['status'] == 'needs_inputs' else
                  'partial' if has_pending else 'complete')
        return {'schema': FRAME_SCHEMA, 'mode': 'deliver', 'status': status,
                **frame_context(observation, request), 'materials': materials,
                'reused': base['delivery']['reused'], 'remaining': left,
                'continuation': token if has_pending else None,
                'diagnostics': observation['diagnostics']}

    limit = request['budget']['max_bytes']
    current = frame([], remaining)
    if len(canonical_bytes(current)) > limit:
        raise InstructionInputError('budget_too_small', 'budget', 'increase:budget')
    for record in pending:
        if not record['available']:
            continue
        left = [row for row in current['remaining'] if row['key'] != material_key(record)]
        candidate = frame([*current['materials'], record], left)
        if len(canonical_bytes(candidate)) <= limit:
            current = candidate
    return portable_result(observation, request, current, base['receipt'])


def addressed_delivery_result(observation, request):
    binding = delivery_binding(observation, request)
    record = next((record for record in observation['materials'] if record['available']
                   and material_address(binding, record) == request['address']), None)
    if record is None:
        raise InstructionInputError('stale_address', 'address', 'refresh:delivery')
    selection = request['range']
    if selection is None:
        content, complete = record['content'], True
    else:
        try:
            if not fields(selection, ('unit', 'start', 'end')):
                raise ValueError('Invalid range shape')
            content, complete = select_text_range(
                record['content'], selection['unit'], selection['start'], selection['end'])
        except (ValueError, UnicodeError) as exc:
            raise InstructionInputError('invalid_range', 'range', 'choose:range') from exc
    frame = {'schema': FRAME_SCHEMA, 'mode': 'read', 'status': 'read',
             **frame_context(observation, request), 'material': material_key(record),
             'sha256': record['sha256'], 'range': selection,
             'content': content, 'complete_material': complete}
    if len(canonical_bytes(frame)) > request['budget']['max_bytes']:
        raise InstructionInputError('budget_too_small', 'range', 'choose:range')
    return portable_result(observation, request, frame)
