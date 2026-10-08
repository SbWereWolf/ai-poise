"""Explicit project diagnosis contracts and verdicts, without external effects."""
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..foundation.errors import DomainError
from ..foundation.validation import validate_exact_keys


def explicit_text(value):
    return isinstance(value, str) and bool(value.strip()) and '\0' not in value


def explicit_config_path(value):
    return explicit_text(value) and PurePosixPath(value).is_absolute()


@dataclass(frozen=True)
class ProjectPreflightRequest:
    config_path: str
    project_id: str
    task_id: str | None
    probe_repository: bool
    commit_message: str | None

    @classmethod
    def parse(cls, raw):
        validate_exact_keys(raw, {'schema', 'config_path', 'project_id', 'task_id',
                                 'probe_repository', 'commit_message'},
                            'project preflight request', DomainError)
        if raw['schema'] != 'project-preflight-1':
            raise DomainError('Unsupported project preflight schema')
        if not explicit_config_path(raw['config_path']):
            raise DomainError('config_path: explicit absolute file path required')
        if not explicit_text(raw['project_id']):
            raise DomainError('project_id: explicit nonempty string required')
        if raw['task_id'] is not None and not explicit_text(raw['task_id']):
            raise DomainError('task_id: nonempty string or explicit null required')
        if type(raw['probe_repository']) is not bool:
            raise DomainError('probe_repository: explicit boolean required')
        if raw['commit_message'] is not None and not isinstance(raw['commit_message'], str):
            raise DomainError('commit_message: string or explicit null required')
        return cls(*(raw[key] for key in ('config_path', 'project_id', 'task_id',
                                         'probe_repository', 'commit_message')))


@dataclass(frozen=True)
class ConfigurationObservation:
    context: dict
    configuration: dict | None
    status: str
    reason: str | None


class ProjectPreflightReport:
    """Version-one observation semantics; readiness never grants Task ownership."""

    def __init__(self, settings_path, context):
        self.settings_path = settings_path
        self.context = context
        self.checks = [
            {'name': name, 'status': 'not_checked', 'reason': None}
            for name in ('configuration', 'git', 'commit_policy', 'task_lookup')
        ]

    def record(self, name, status, reason=None):
        for check in self.checks:
            if check['name'] == name:
                check.update(status=status, reason=reason)
                return
        raise DomainError('Unknown project preflight component')

    def input_failure(self, reason):
        return self._result('rejected', False, reason)

    def finish(self):
        for check in self.checks:
            if check['status'] in ('rejected', 'unknown'):
                status = 'rejected' if check['status'] == 'rejected' else 'checked'
                return self._result(status, False, check['reason'])
        skipped = [check['name'] for check in self.checks[:3]
                   if check['status'] != 'passed']
        if skipped:
            return self._result('checked', False,
                                'Обязательные проверки не выполнены: ' + ', '.join(skipped) + '.')
        return self._result('checked', True, None)

    def _result(self, status, ready, reason):
        return {'schema': 'project-preflight-result-1', 'status': status,
                'ready': ready, 'reason': reason, 'context': self.context,
                'checks': self.checks, 'recovery': [] if ready else self._recovery()}

    def _recovery(self):
        config_path = self.context['config_path']
        retry_path = config_path if explicit_config_path(config_path) else None
        routes = [
            ('retry_check', 'poise project check', retry_path, [],
             'Укажите обязательные параметры явно; проверка не изменяет конфигурацию и данные.'),
            ('list_projects', 'poise project list', None, [],
             'Список проектов не выбирает проект и не изменяет его состояние.'),
            ('select_work_config', 'poise work', None, [],
             'Выбор конфигурации не переносит данные и не заменяет владение задачей.'),
            ('select_requirements_config', 'poise requirements', None, [],
             'Смена конфигурации не перемещает базу Requirements и её lock-файл.'),
        ]
        if self.checks[0]['status'] == 'passed' and self.checks[1]['status'] == 'rejected':
            routes.append((
                'supported_config_update', 'poise project-config', config_path,
                ['git.repository', 'git.base_ref'],
                'Изменение требует актуальной ревизии и покоя проекта; поля paths имеют отдельного '
                'владельца, данные автоматически не перемещаются.'))
        return [{'action': action, 'interface': interface,
                 'settings_path': self.settings_path, 'config_path': path,
                 'supported_fields': fields, 'limitations': [limitation]}
                for action, interface, path, fields, limitation in routes]
