"""Normalized instruction protocol; no filesystem, session or Task dependencies."""
from copy import deepcopy
import re

from .instructions import (
    InstructionInputError, RESPONSE_FIELDS, SCHEMA, diagnostics_for, prepare_paths,
    status_for, strings, text, validate_material_records,
)
from .instruction_delivery import (
    PORTABLE_SCHEMA, addressed_delivery_result, bounded_delivery_result,
    budget_value, context_value, continuation_value, fields, require, stage_value,
)


REQUEST_SCHEMA = 'poise-portable-instruction-request-1'


def validate_selection(selection):
    try:
        if not fields(selection, RESPONSE_FIELDS):
            raise ValueError('Invalid observation shape')
        if selection['schema'] != SCHEMA or selection['delta'] is not None:
            raise ValueError('Expected a complete current observation')
        if not text(selection['project']):
            raise ValueError('Invalid project identity')
        for key in ('checkout', 'instruction_root'):
            if not text(selection[key]) or not selection[key].startswith('/'):
                raise ValueError('Explicit absolute source roots required')
        if (not isinstance(selection['policy_sha256'], str)
                or re.fullmatch('[0-9a-f]{64}', selection['policy_sha256']) is None):
            raise ValueError('Invalid policy version')
        paths = prepare_paths(selection['paths'])
        if paths != selection['paths']:
            raise ValueError('Expected canonical paths')
        validate_material_records(selection['materials'], paths, allow_tools=True)
        if (selection['diagnostics'] != diagnostics_for(selection['materials'])
                or selection['status'] != status_for(selection['materials'])):
            raise ValueError('Observation disagrees with source availability')
    except (KeyError, TypeError, ValueError, UnicodeError, InstructionInputError) as exc:
        raise InstructionInputError('invalid_selection', 'selection', 'provide:selection') from exc


def parse_portable_request(raw):
    require(isinstance(raw, dict), 'request')
    operation = raw.get('operation')
    require(operation in ('deliver_selected', 'read_selected'), 'operation')
    common = ('schema', 'operation', 'selection', 'context', 'stage', 'budget')
    specific = (('previous', 'retained', 'continuation') if operation == 'deliver_selected'
                else ('address', 'range'))
    require(fields(raw, (*common, *specific)), 'request')
    require(raw['schema'] == REQUEST_SCHEMA, 'schema')
    context = context_value(raw['context'])
    try:
        stage = stage_value(raw['stage'])
    except InstructionInputError as exc:
        raise InstructionInputError('invalid_request', 'stage', 'provide:stage') from exc
    budget = budget_value(raw['budget'])
    validate_selection(raw['selection'])
    if operation == 'deliver_selected':
        require(strings(raw['retained']), 'retained')
        continuation_value(raw['continuation'])
    else:
        require(text(raw['address']), 'address')
    return {**deepcopy(raw), 'context': context, 'stage': stage, 'budget': budget}


def execute(request):
    """Return the agreed value or a specific protocol refusal, without effects."""
    try:
        parsed = parse_portable_request(request)
        if parsed['operation'] == 'deliver_selected':
            return bounded_delivery_result(parsed['selection'], parsed)
        return addressed_delivery_result(parsed['selection'], parsed)
    except InstructionInputError as exc:
        return {**exc.response(), 'schema': PORTABLE_SCHEMA}
