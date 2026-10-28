"""Reader values, admission and per-Session acknowledgement semantics; no I/O."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePath

from domain.generic import Problem


RESET_REASONS = ('startup', 'resume', 'compact', 'clear', 'fork', 'manual')


def reader_invalid(field):
    raise Problem('reader_invalid_input', {'field': field}, 'provide:reader-input')


def reader_fields(value, fields, field):
    if not isinstance(value, dict):
        reader_invalid(field)
    for name in sorted(fields - value.keys()):
        reader_invalid(field + '/' + name.replace('~', '~0').replace('/', '~1'))
    for name in sorted(value.keys() - fields):
        reader_invalid(field + '/' + name.replace('~', '~0').replace('/', '~1'))
    return value


def reader_text(value, field):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        reader_invalid(field)
    return value


def reader_integer(value, field, minimum):
    if type(value) is not int or value < minimum:
        reader_invalid(field)
    return value


def parse_context(value):
    return reader_fields(value, set(), '/input')


def parse_reset(value):
    reader_fields(value, {'reason', 'event_id'}, '/input')
    if value['reason'] not in RESET_REASONS:
        reader_invalid('/input/reason')
    reader_text(value['event_id'], '/input/event_id')
    return value


def parse_cursor(value, field):
    reader_fields(value, {'path', 'unit', 'next', 'version', 'generation'}, field)
    reader_text(value['path'], field + '/path')
    if not PurePath(value['path']).is_absolute():
        reader_invalid(field + '/path')
    if value['unit'] not in ('lines', 'bytes'):
        reader_invalid(field + '/unit')
    reader_integer(value['next'], field + '/next', 1 if value['unit'] == 'lines' else 0)
    reader_integer(value['generation'], field + '/generation', 1)
    version = reader_fields(value['version'], {'size_bytes', 'modified_ns'}, field + '/version')
    reader_integer(version['size_bytes'], field + '/version/size_bytes', 0)
    if type(version['modified_ns']) is not int:
        reader_invalid(field + '/version/modified_ns')
    return value


def parse_batch(value, limits, *, following):
    fields = {'generation', 'acknowledged', 'reads'} | (set() if following else {'cwd'})
    reader_fields(value, fields, '/input')
    reader_integer(value['generation'], '/input/generation', 1)
    if not following:
        reader_text(value['cwd'], '/input/cwd')
        if not PurePath(value['cwd']).is_absolute():
            reader_invalid('/input/cwd')
    acknowledged = value['acknowledged']
    if not isinstance(acknowledged, list) or len(acknowledged) > limits.max_receipts:
        reader_invalid('/input/acknowledged')
    for identity in acknowledged:
        reader_text(identity, '/input/acknowledged')
    if len(set(acknowledged)) != len(acknowledged):
        reader_invalid('/input/acknowledged')
    if not isinstance(value['reads'], list) or not 1 <= len(value['reads']) <= limits.max_items:
        reader_invalid('/input/reads')
    for index, item in enumerate(value['reads']):
        field = '/input/reads/' + str(index)
        own = {'reread', 'reason'} | ({'cursor', 'count'} if following else {'path', 'range'})
        reader_fields(item, own, field)
        if type(item['reread']) is not bool:
            reader_invalid(field + '/reread')
        if not isinstance(item['reason'], str) or '\0' in item['reason']:
            reader_invalid(field + '/reason')
        if item['reread']:
            reader_text(item['reason'], field + '/reason')
        if following:
            cursor = parse_cursor(item['cursor'], field + '/cursor')
            unit, count, count_field = cursor['unit'], item['count'], field + '/count'
        else:
            reader_text(item['path'], field + '/path')
            selection = reader_fields(item['range'], {'unit', 'start', 'count'}, field + '/range')
            unit, count, count_field = selection['unit'], selection['count'], field + '/range/count'
            if unit not in ('lines', 'bytes'):
                reader_invalid(field + '/range/unit')
            reader_integer(selection['start'], field + '/range/start', 1 if unit == 'lines' else 0)
        reader_integer(count, count_field, 1)
        if count > (limits.max_lines if unit == 'lines' else limits.max_range_bytes):
            reader_invalid(count_field)
    return value


def parse_read(value, limits):
    return parse_batch(value, limits, following=False)


def parse_next(value, limits):
    return parse_batch(value, limits, following=True)


@dataclass(frozen=True)
class ReaderVersion:
    size_bytes: int
    modified_ns: int

    def view(self):
        return {'size_bytes': self.size_bytes, 'modified_ns': self.modified_ns}


@dataclass(frozen=True)
class ReaderPortion:
    path: str
    version: ReaderVersion
    unit: str
    start: int
    count: int
    text: str
    eof: bool
    next_start: int | None

    def coordinates(self):
        return {'path': self.path, 'version': self.version.view(),
                'range': {'unit': self.unit, 'start': self.start, 'count': self.count}}

    def cursor(self, generation):
        if self.next_start is None:
            return None
        return {'path': self.path, 'unit': self.unit, 'next': self.next_start,
                'version': self.version.view(), 'generation': generation}


@dataclass(frozen=True)
class ReaderDocument:
    path: str
    version: ReaderVersion
    raw: bytes
    text: str

    def select(self, unit, start, count, field):
        if unit == 'lines':
            lines = self.text.splitlines(keepends=True)
            maximum = len(lines) + 1
            if start > maximum:
                self.bounds(unit, start, maximum)
            selected = lines[start - 1:start - 1 + count]
            text, actual = ''.join(selected), len(selected)
            position, eof = start + actual, start + actual == maximum
        else:
            maximum = len(self.raw)
            if start > maximum:
                self.bounds(unit, start, maximum)
            try:
                self.raw[:start].decode('utf-8')
            except UnicodeDecodeError:
                reader_invalid(field)
            position = min(start + count, maximum)
            while position > start:
                try:
                    text = self.raw[start:position].decode('utf-8')
                    break
                except UnicodeDecodeError:
                    position -= 1
            else:
                text = ''
            if position == start and start != maximum:
                minimum = len(self.raw[start:].decode('utf-8')[0].encode('utf-8'))
                raise Problem('reader_count_too_small', {'path': self.path, 'start': start,
                              'minimum_count': minimum}, 'reader:read')
            actual, eof = position - start, position == maximum
        return ReaderPortion(self.path, self.version, unit, start, actual, text,
                             eof, None if eof else position)

    def bounds(self, unit, start, maximum):
        raise Problem('reader_bounds', {'path': self.path, 'unit': unit, 'start': start,
                      'maximum_start': maximum}, 'reader:read')


class ReaderState:
    def __init__(self, data):
        self.data = data

    @classmethod
    def initial(cls):
        return cls({'generation': 1, 'reason': 'startup', 'event_id': None, 'receipts': []})

    @classmethod
    def restore(cls, data):
        # Required stored values are used explicitly; absence is never a reset.
        if (not isinstance(data, dict) or set(data) != {'generation', 'reason', 'event_id', 'receipts'}
                or type(data['generation']) is not int or data['generation'] < 1
                or data['reason'] not in RESET_REASONS or not isinstance(data['receipts'], list)):
            raise ValueError('Invalid reader state')
        if data['event_id'] is not None and (not isinstance(data['event_id'], str)
                or not data['event_id'].strip() or '\0' in data['event_id']):
            raise ValueError('Invalid reader event')
        for receipt in data['receipts']:
            if (not isinstance(receipt, dict)
                    or set(receipt) != {'path', 'version', 'range', 'receipt_id', 'acknowledged'}
                    or not isinstance(receipt['receipt_id'], str) or not receipt['receipt_id'].strip()
                    or '\0' in receipt['receipt_id'] or not isinstance(receipt['path'], str)
                    or not isinstance(receipt['version'], dict) or not isinstance(receipt['range'], dict)
                    or type(receipt['acknowledged']) is not bool):
                raise ValueError('Invalid reader receipt')
        return cls(data)

    def context(self):
        return {'schema': 'poise-reader/2', **{key: self.data[key]
                for key in ('generation', 'reason', 'event_id')}}

    def reset(self, reason, event_id):
        candidate = deepcopy(self.data)
        if candidate['event_id'] == event_id:
            if candidate['reason'] != reason:
                raise Problem('reader_event_conflict', {'event_id': event_id}, 'reader:reset')
        else:
            candidate.update(generation=candidate['generation'] + 1, reason=reason,
                             event_id=event_id, receipts=[])
        return ReaderState(candidate)

    def check_generation(self, generation, cursors=()):
        if generation != self.data['generation'] or any(
                cursor['generation'] != self.data['generation'] for cursor in cursors):
            raise Problem('reader_context_changed', {'generation': self.data['generation']}, 'reader:context')

    def check_acknowledged(self, acknowledged):
        known = {receipt['receipt_id'] for receipt in self.data['receipts']}
        for identity in acknowledged:
            if identity not in known:
                raise Problem('reader_receipt_unknown', {'receipt_id': identity}, 'reader:read')

    def complete(self, portions, selections, acknowledged, limits, receipt_factory):
        self.check_acknowledged(acknowledged)
        candidate = deepcopy(self.data)
        ledger = candidate['receipts']
        for receipt in ledger:
            if receipt['receipt_id'] in acknowledged:
                receipt['acknowledged'] = True
        items, size = [], 0
        for portion, selection in zip(portions, selections, strict=True):
            coordinates = portion.coordinates()
            known = next((entry for entry in ledger if all(
                entry[key] == coordinates[key] for key in ('path', 'version', 'range'))), None)
            if portion.count == 0:
                identity, status, skip = None, 'eof', False
            else:
                skip = bool(known and known['acknowledged'] and not selection['reread'])
                status = 'unchanged' if skip else 'reread' if selection['reread'] else 'read'
                identity = known['receipt_id'] if known else receipt_factory()
                if known is not None:
                    ledger.remove(known)
                ledger.append({**coordinates, 'receipt_id': identity,
                               'acknowledged': bool(known and known['acknowledged'])})
            item = {**coordinates, 'status': status, 'receipt_id': identity,
                    'eof': portion.eof, 'next': portion.cursor(candidate['generation'])}
            if not skip:
                size += len(portion.text.encode('utf-8'))
                if size > limits.max_output_bytes:
                    raise Problem('reader_output_limit', {'limit': limits.max_output_bytes}, 'reader:read')
                item['text'] = portion.text
            items.append(item)
        candidate['receipts'] = ledger[-limits.max_receipts:]
        response = {'schema': 'poise-reader/2', 'generation': candidate['generation'],
                    'items': items, 'text_bytes': size}
        return ReaderState(candidate), response
