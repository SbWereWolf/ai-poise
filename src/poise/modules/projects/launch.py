"""Pure selection and command contracts for configured project operations."""
from __future__ import annotations

from dataclasses import dataclass

from ..foundation.errors import PoiseError
from ..foundation.validation import configuration_fields


def text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise PoiseError(f'{field}: a nonempty string without NUL is required')
    return value


def command(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PoiseError(f'{field}: a nonempty command argument list is required')
    return tuple(text(argument, field) for argument in value)


@dataclass(frozen=True)
class ProjectLaunchSettings:
    poise_root: str
    data_root: str
    project_id: str
    project_setup: str
    command: tuple[str, ...]
    overrides: dict[str, tuple[str, ...]]
    rejected_exit_code: int

    @classmethod
    def parse(cls, raw: object) -> ProjectLaunchSettings:
        values = configuration_fields(raw, {
            'schema', 'poise_root', 'data_root', 'project_id', 'project_setup',
            'command', 'overrides', 'exit_codes',
        }, 'launcher settings')
        if values['schema'] != 'stride-launcher-1':
            raise PoiseError('schema: unsupported launcher settings schema')
        roots = {name: text(values[name], name) for name in
                 ('poise_root', 'data_root', 'project_id', 'project_setup')}
        overrides = values['overrides']
        if not isinstance(overrides, dict):
            raise PoiseError('overrides: a command mapping is required')
        parsed_overrides = {
            text(name, 'overrides'): command(prefix, f'overrides.{name}')
            for name, prefix in overrides.items()
        }
        codes = configuration_fields(values['exit_codes'], {'rejected'}, 'exit_codes')
        rejected = codes['rejected']
        if type(rejected) is not int or not 1 <= rejected <= 255:
            raise PoiseError('exit_codes.rejected: an integer from 1 through 255 is required')
        return cls(**roots, command=command(values['command'], 'command'),
                   overrides=parsed_overrides, rejected_exit_code=rejected)

    def select_project(self, listing: dict) -> str:
        for error in listing['errors']:
            if error['project'] == self.project_id:
                raise PoiseError(f"project_id {self.project_id}, {error['config_path']}: {error['reason']}")
        for project in listing['projects']:
            if project['project'] == self.project_id:
                return project['config_path']
        raise PoiseError(f'project_id: unknown configured project {self.project_id!r}')

    def plan_command(self, arguments: list[str]) -> tuple[str, ...]:
        if arguments and arguments[0] in self.overrides:
            return (*self.overrides[arguments[0]], *arguments[1:])
        return (*self.command, *arguments)
