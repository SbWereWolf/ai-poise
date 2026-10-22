"""Explicit process files and ordinary atomic replacement, extracted from GoalConfig."""
import json
import os
import tempfile
from apps.execution.domain.process.errors import DomainError
from apps.execution.domain.process.json_input import strict_json


class ProcessFiles:
    def __init__(self, settings):
        self.settings = settings

    @staticmethod
    def read(path):
        return strict_json(path.read_text(encoding='utf-8'))

    def current(self, goal):
        if goal not in self.settings['workflows']:
            raise DomainError(f'Unknown process goal_type: {goal}')
        return self.settings['workflows'][goal]

    def read_current(self, goal):
        return self.read(self.current(goal))

    def resolve_template(self, name):
        if name not in self.settings['templates']:
            raise DomainError(f'Unknown process template: {name}')
        return self.read(self.settings['templates'][name])

    def publish_candidate(self, goal, candidate):
        path = self.current(goal)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                os.fchmod(output.fileno(), self.settings['file_mode'])
                json.dump(candidate, output, ensure_ascii=False, indent=self.settings['json_indent'], allow_nan=False)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
