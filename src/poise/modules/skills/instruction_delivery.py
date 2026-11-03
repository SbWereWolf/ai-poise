"""Current-context instruction delivery, separate from source observation.

A receipt records versions. Only the caller's separate confirmation that text
is retained in the same context permits omitting it from the delivery packet.
"""
from dataclasses import replace
import re

from ..foundation.errors import DomainError
from .instructions import (
    InstructionInputError, MaterialSpec, material_key, parse_prepare_request,
    prepare_paths, relative_file, strings, text,
)


SCHEMA = 'poise-instruction-delivery-1'
RECEIPT_SCHEMA = 'poise-instruction-delivery-receipt-1'
IDENTITY = ('kind', 'id', 'root', 'path')
FINGERPRINT = (*IDENTITY, 'sha256')


def require(condition, field, code='invalid_request'):
    if not condition:
        raise InstructionInputError(code, field, 'provide:' + field)


def fields(value, names):
    return isinstance(value, dict) and set(value) == set(names)


def stage_value(value):
    require(fields(value, ('id', 'skill_ids')) and text(value['id']), 'stage')
    require(strings(value['skill_ids']), 'stage.skill_ids')
    return {'id': value['id'], 'skill_ids': sorted(value['skill_ids'])}


def parse_delivery_request(raw):
    require(fields(raw, ('operation', 'project_config', 'checkout', 'policy', 'paths',
                         'stage', 'context', 'previous', 'retained', 'tool'))
            and raw['operation'] == 'deliver', 'request')
    base = parse_prepare_request({
        'operation': 'prepare', 'previous': None,
        **{key: raw[key] for key in ('project_config', 'checkout', 'policy', 'paths')},
    })
    context = raw['context']
    require(fields(context, ('id', 'generation', 'reason')) and text(context['id'])
            and type(context['generation']) is int and context['generation'] > 0
            and context['reason'] in ('initial', 'continue', 'resume', 'compaction'), 'context')
    require(strings(raw['retained']), 'retained')
    tool = raw['tool']
    require(tool is None or (
        fields(tool, ('id', 'root', 'path')) and text(tool['id']) and ':' not in tool['id']
        and tool['root'] in ('checkout', 'instructions') and relative_file(tool['path'])), 'tool')
    return {**raw, **{key: base[key] for key in ('project_config', 'checkout', 'policy')},
            'paths': prepare_paths(base['paths']), 'stage': stage_value(raw['stage']),
            'context': dict(context), 'retained': list(raw['retained']),
            'tool': None if tool is None else dict(tool)}


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
    receipt = {'schema': RECEIPT_SCHEMA, **binding, 'context': context, 'stage': request['stage'],
               'paths': observation['paths'], 'materials': [
                   {key: record[key] for key in FINGERPRINT}
                   for record in observation['materials'] if record['available']]}
    delivery = {**binding, 'context': request['context'], 'stage': request['stage'],
                'paths': observation['paths'], 'materials': materials, 'reused': reused,
                'diagnostics': observation['diagnostics']}
    return {'schema': SCHEMA, 'status': observation['status'], 'observation': observation,
            'delivery': delivery, 'receipt': receipt}
