"""Pure range/limit contract; receipts never assert that a model retained text."""
from copy import deepcopy
from dataclasses import dataclass
import math

from ..foundation.errors import PoiseError
from ..foundation.validation import configuration_fields

CONTEXT_RESET_REASONS=('startup','resume','compact','clear','fork','manual')


def select_text_range(content, unit, start, end):
    """Select exact UTF-8 text; admission and receipts remain caller-owned."""
    if type(start) is not int or type(end) is not int or start > end:
        raise ValueError('Invalid range boundaries')
    if unit == 'lines':
        lines = content.splitlines(keepends=True)
        if start < 1 or end > len(lines):
            raise ValueError('Requested range lies beyond end of file')
        return ''.join(lines[start - 1:end]), start == 1 and end == len(lines)
    if unit == 'bytes':
        raw = content.encode('utf-8')
        if start < 0 or end > len(raw):
            raise ValueError('Requested range lies beyond end of file')
        for offset in (start, end):
            if offset not in (0, len(raw)) and raw[offset] & 0xC0 == 0x80:
                raise ValueError('Range splits a UTF-8 codepoint')
        return raw[start:end].decode('utf-8'), start == 0 and end == len(raw)
    raise ValueError('Unsupported range unit')


def exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise PoiseError(f'{label}: expected exactly {sorted(fields)}')


def positive(value, label):
    if type(value) is not int or value < 1:
        raise PoiseError(f'{label}: positive integer required')
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise PoiseError(f'{label}: nonempty text required')
    return value


@dataclass(frozen=True)
class ReadLimits:
    max_items: int
    max_lines: int
    max_file_bytes: int
    max_output_bytes: int
    max_receipts: int
    lock_wait_seconds: float
    lock_poll_seconds: float

    @classmethod
    def parse(cls, raw):
        fields = set(cls.__dataclass_fields__)
        raw = configuration_fields(raw, fields | {'schema'}, 'source reader policy')
        if raw['schema'] != 'source-reader-1':
            raise PoiseError('Unsupported source reader schema')
        for name in fields - {'lock_wait_seconds', 'lock_poll_seconds'}:
            positive(raw[name], name)
        for name in ('lock_wait_seconds', 'lock_poll_seconds'):
            value = raw[name]
            if (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                raise PoiseError(f'{name}: positive finite number required')
        return cls(**{key: raw[key] for key in fields})


def parse_read(raw, limits):
    exact(raw, {'cwd', 'generation', 'acknowledged', 'reads'}, 'range read')
    text(raw['cwd'], 'cwd'); positive(raw['generation'], 'generation')
    ack = raw['acknowledged']
    if (not isinstance(ack, list) or len(ack) > limits.max_receipts
            or any(not isinstance(a, str) or not a for a in ack) or len(set(ack)) != len(ack)):
        raise PoiseError('acknowledged: bounded unique receipt IDs required')
    reads = raw['reads']
    if not isinstance(reads, list) or not 1 <= len(reads) <= limits.max_items:
        raise PoiseError('reads: explicit bounded batch required')
    for item in reads:
        exact(item, {'path', 'start', 'end', 'reread', 'reason'}, 'range')
        text(item['path'], 'path')
        positive(item['start'], 'start'); positive(item['end'], 'end')
        if not item['start'] <= item['end'] or item['end']-item['start']+1 > limits.max_lines:
            raise PoiseError('range exceeds configured line bound')
        if type(item['reread']) is not bool or not isinstance(item['reason'], str):
            raise PoiseError('reread and reason must be explicit')
        if item['reread']:
            text(item['reason'], 'reread reason')
    return deepcopy(raw)
