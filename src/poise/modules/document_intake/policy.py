"""Required policy fields are strict; unknown raw options remain untouched."""
import math
from pathlib import Path
from .contracts import ReadFailure


class ContentReaderPolicy:
    @staticmethod
    def from_configuration(raw):
        def require(path, predicate):
            value = raw
            for part in path.split('.'):
                if not isinstance(value, dict) or part not in value:
                    raise ReadFailure('invalid_policy', field='policy:' + path)
                value = value[part]
            if not predicate(value):
                raise ReadFailure('invalid_policy', field='policy:' + path)
        text = lambda v: isinstance(v, str) and bool(v.strip())
        integer = lambda v: type(v) is int and v > 0
        duration = lambda v: type(v) in (float, int) and math.isfinite(v) and v > 0
        absolute = lambda v: text(v) and Path(v).is_absolute()
        component = lambda v: text(v) and v not in ('.', '..') and '/' not in v and '\\' not in v and '\x00' not in v
        require('schema', lambda v: v == 'poise-content-reader-policy-1')
        require('version', text)
        for group in ('complete', 'bounded', 'output'):
            for field in ('max_bytes', 'max_lines'):
                require(group + '.' + field, integer)
        require('registry.schema_version', lambda v: type(v) is int and v == 2)
        require('registry.busy_timeout_ms', integer)
        require('registry.filename', component)
        for field in ('temporary_root', 'coordination_root', 'diagnostic_file', 'registry_template'):
            require('session.' + field, absolute)
        for field in ('registry_directory', 'response_directory', 'ownership_filename'):
            require('session.' + field, component)
        for field in ('hook_settings', 'definition_path'):
            require('admission.' + field, absolute)
        for field in ('wait_seconds', 'poll_seconds', 'recovery_budget_seconds',
                      'host_event_budget_seconds', 'end_observation_poll_seconds'):
            require('coordination.' + field, duration)
        require('storage.control_response_root', absolute)
        return raw
