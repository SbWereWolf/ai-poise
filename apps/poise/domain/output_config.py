"""Explicit limits for selected saved-output operations."""
from dataclasses import dataclass

from domain.generic import Problem, positive_count
from domain.state import invalid_config, required


@dataclass(frozen=True)
class OutputLimits:
    max_items: int
    max_total_range_bytes: int
    max_response_bytes: int


def parse_settings(raw):
    if 'output' not in raw:
        raise Problem('output_not_configured', {}, 'configure:output', 'unavailable')
    output = required(raw['output'], {'schema', 'limits'}, '/output')
    if output['schema'] != 'poise-output-config/v1':
        invalid_config('/output/schema')
    names = tuple(OutputLimits.__dataclass_fields__)
    limits = required(output['limits'], set(names), '/output/limits')
    for name in names:
        if not positive_count(limits[name]):
            invalid_config('/output/limits/' + name)
    return OutputLimits(**{name: limits[name] for name in names})
