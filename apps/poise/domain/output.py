"""Saved-source intent and lossless public representations, without I/O."""
import base64
from pathlib import Path

from domain.generic import Problem, exact_fields, invalid, positive_count, text


def read_limit(name):
    raise Problem('output_read_limit', {'limit': name}, 'reduce:output')


def items_input(value, name, limits):
    items = exact_fields(value, {name}, '/input')[name]
    if not isinstance(items, list) or not items:
        invalid('/input/' + name)
    if len(items) > limits.max_items:
        read_limit('max_items')
    return items


def source_input(value, field):
    exact_fields(value, {'path', 'captured_bytes'}, field)
    path = text(value['path'], field + '/path')
    if not Path(path).is_absolute():
        invalid(field + '/path')
    captured = value['captured_bytes']
    if captured is not None and (type(captured) is not int or captured < 0):
        invalid(field + '/captured_bytes')
    return value


def minimal_input(value, limits):
    return [source_input(item, '/input/sources/' + str(index))
            for index, item in enumerate(items_input(value, 'sources', limits))]


def range_input(value, limits):
    items = items_input(value, 'reads', limits)
    for index, item in enumerate(items):
        field = '/input/reads/' + str(index)
        exact_fields(item, {'source', 'start', 'count'}, field)
        source_input(item['source'], field + '/source')
        if type(item['start']) is not int or item['start'] < 0:
            invalid(field + '/start')
        if not positive_count(item['count']):
            invalid(field + '/count')
    if sum(item['count'] for item in items) > limits.max_total_range_bytes:
        read_limit('max_total_range_bytes')
    return items


def source_view(source, size):
    captured = source['captured_bytes']
    if captured is None:
        extent = 'unknown'
    elif size == captured:
        extent = 'same_size'
    else:
        extent = 'shorter' if size < captured else 'longer'
    return {'path': source['path'], 'captured_bytes': captured, 'bytes': size, 'extent': extent}


def range_view(source, size, start, raw):
    stop = start + len(raw)
    eof = stop >= size
    return {**source_view(source, size), 'start': start, 'count': len(raw),
            'data': {'encoding': 'base64', 'value': base64.b64encode(raw).decode('ascii')},
            'next_start': None if eof else stop, 'eof': eof}


def representation(kind, items):
    field = {'minimal': 'sources', 'range': 'ranges'}[kind]
    result = {'schema': 'poise-output-' + kind + '/v1', field: items}
    changed = [index for index, item in enumerate(items) if item['extent'] in ('shorter', 'longer')]
    if changed:
        raise Problem('output_extent_differs', {'indices': changed}, 'inspect:output',
                      'unavailable', result)
    return result
