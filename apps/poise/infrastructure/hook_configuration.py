"""Selected hook configuration and complete pre-dispatch dependency validation."""
from dataclasses import dataclass
from pathlib import Path

from domain.generic import Problem
from domain.hooks import HookDefinition
from domain.probes import validate_probe
from domain.state import invalid_config
from infrastructure.files import FileSurface
from infrastructure.generic_config import load_config


@dataclass(frozen=True)
class HookConfiguration:
    definition: HookDefinition
    generic: dict | None
    probes: tuple


def load_hook_configuration(raw, path, settings, operation):
    definition = HookDefinition.parse(raw.get('hooks'))
    if operation == 'hook.event' and settings.caller_mode != 'native':
        invalid_config('/caller/mode')
    generic, specs = None, []
    if definition.generic_config is not None:
        location = Path(definition.generic_config)
        location = location if location.is_absolute() else path.parent / location
        try:
            generic = load_config(location)
        except (Problem, OSError, ValueError, RecursionError):
            invalid_config('/hooks/generic_config')
    if definition.external is not None:
        try:
            FileSurface(generic['limits']['input_bytes']).checkout(
                definition.external['cwd'], '/hooks/external')
        except Problem:
            invalid_config('/hooks/external')
    if definition.probes:
        if len(definition.probes) > generic['limits']['batch_count']:
            invalid_config('/hooks/probes')
        for index, value in enumerate(definition.probes):
            pointer = '/hooks/probes/' + str(index)
            try:
                specs.append(validate_probe(value, pointer, generic['limits']))
            except (Problem, ValueError):
                invalid_config(pointer)
        if len({spec.data['id'] for spec in specs}) != len(specs):
            invalid_config('/hooks/probes')
    return HookConfiguration(definition, generic, tuple(specs))
