"""Explicit reader limits, parsed only for the selected reader operation."""
from dataclasses import dataclass

from domain.generic import Problem, positive_count
from domain.state import invalid_config, required


@dataclass(frozen=True)
class ReaderLimits:
    max_items: int
    max_lines: int
    max_range_bytes: int
    max_file_bytes: int
    max_output_bytes: int
    max_receipts: int


def parse_settings(raw):
    if 'reader' not in raw:
        raise Problem('reader_not_configured', {}, 'configure:reader', 'unavailable')
    reader = required(raw['reader'], {'schema', 'limits'}, '/reader')
    if reader['schema'] != 'poise-reader/2':
        invalid_config('/reader/schema')
    names = tuple(ReaderLimits.__dataclass_fields__)
    limits = required(reader['limits'], set(names), '/reader/limits')
    for name in names:
        if not positive_count(limits[name]):
            invalid_config('/reader/limits/' + name)
    return ReaderLimits(**{name: limits[name] for name in names})
