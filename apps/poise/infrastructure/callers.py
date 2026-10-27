"""Native caller observation and persistent generated caller publication."""
import json
import os
from pathlib import Path
import tempfile
import uuid

from domain.generic import Problem
from domain.sessions import Caller
from infrastructure.json_io import loads


def invalid_caller(reason):
    raise Problem('invalid_caller', {'reason': reason}, 'correct:caller')


class CallerResolver:
    def __init__(self, settings, config_path, environment):
        self.settings = settings
        self.environment = environment
        self.binding = None
        if settings.binding_file is not None:
            selected = Path(settings.binding_file)
            self.binding = selected if selected.is_absolute() else config_path.parent / selected

    def validate_context(self):
        values = [self.environment[key] for key in ('CODEX_SESSION_ID', 'CODEX_THREAD_ID')
                  if key in self.environment]
        if self.settings.caller_mode == 'generated':
            if values:
                invalid_caller('native_generated_conflict')
            return None
        if not values:
            invalid_caller('native_missing')
        for value in values:
            try:
                valid = isinstance(value, str) and bool(value) and '\0' not in value
                if valid:
                    value.encode('utf-8')
            except UnicodeError:
                valid = False
            if not valid:
                invalid_caller('native_invalid')
        if len(set(values)) != 1:
            invalid_caller('native_conflict')
        return values[0]

    def read_binding(self):
        try:
            raw = self.binding.read_bytes()
        except FileNotFoundError:
            return None
        try:
            value = loads(raw)
        except (ValueError, RecursionError):
            invalid_caller('binding_invalid')
        if not isinstance(value, dict) or set(value) != {'schema', 'caller_id'}:
            invalid_caller('binding_invalid')
        token = value['caller_id']
        if (value['schema'] != 'poise-caller-binding-1' or
                not isinstance(token, str) or not token or '\0' in token):
            invalid_caller('binding_invalid')
        return token

    def publish_binding(self):
        token = uuid.uuid4().hex
        raw = (json.dumps({'schema': 'poise-caller-binding-1', 'caller_id': token},
                          ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.binding.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(raw)
            try:
                os.link(temporary, self.binding)
            except FileExistsError:
                return self.read_binding()
            return token
        finally:
            if temporary is not None:
                temporary.unlink()

    def resolve(self, native, *, create):
        if self.settings.caller_mode == 'native':
            return Caller(self.settings.installation_id, 'native', native)
        try:
            token = self.read_binding()
            if token is None and create:
                token = self.publish_binding()
        except OSError as error:
            raise Problem('caller_io', {'reason': str(error)},
                          'configure:caller', 'unavailable') from None
        return None if token is None else Caller(self.settings.installation_id, 'generated', token)
