"""Full machine JSON port and common saved-view CLI adapter for named checks."""
import json
import os
from pathlib import Path
import sqlite3
import uuid

from ..common import PoiseError, load_config
from ..composition import project_checks_tools
from ..infrastructure.goal_config import strict_json
from ..infrastructure.project_binding import read_project_checkout, ProjectBindingFailure
from ..infrastructure.result_views import SavedResponse, saved_result_view
from ..modules.project_checks.contracts import CheckFailure, CheckRequest
from ..modules.project_checks.outcomes import exit_code


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'


def references(service, response_path=None):
    result = {'response_path': response_path, 'record_paths': [], 'stdout_paths': [], 'stderr_paths': []}
    if service is not None:
        for record in service.known_records:
            if Path(record['record_path']).is_file():
                result['record_paths'].append(record['record_path'])
            for stream in ('stdout', 'stderr'):
                reference = record[stream]
                if reference is not None and Path(reference['path']).is_file():
                    result[stream + '_paths'].append(reference['path'])
    return result


def execute(config_path, checkout, policy_path, stream, output, error_output, clock):
    service, bound, operation, saver = None, None, None, None
    result = {'schema': 'poise-project-checks-result-1', 'operation': None, 'status': 'invalid',
              'project': None, 'checkout': None, 'policy_sha256': None, 'items': [],
              'error': None, 'warnings': [], 'response_path': None}
    try:
        if not Path(config_path).is_absolute():
            raise CheckFailure('invalid_project', 'config', 'Требуется абсолютный путь.', 'Укажите выбранный project manifest.')
        if not Path(checkout).is_absolute():
            raise CheckFailure('project_binding', 'checkout', 'Требуется абсолютный путь.', 'Укажите зарегистрированную рабочую копию.')
        try:
            bound = read_project_checkout(config_path, checkout)
        except ProjectBindingFailure as exc:
            raise CheckFailure(exc.code, 'config' if exc.code == 'invalid_project' else 'checkout',
                               str(exc), 'Выберите действительный manifest и его зарегистрированный checkout.') from exc
        result.update(project=bound.config['project'], checkout=str(bound.checkout))
        raw = stream.read(bound.config['batch']['max_input_bytes'] + 1)
        if len(raw) > bound.config['batch']['max_input_bytes']:
            raise CheckFailure('invalid_request', 'request', 'Превышен предел входа.', 'Уменьшите пакет до настроенного max_input_bytes.')
        try:
            value = strict_json(raw.decode('utf-8'))
        except (PoiseError, UnicodeError, RecursionError) as exc:
            raise CheckFailure('invalid_request', 'request', str(exc), 'Передайте корректный JSON без повторных полей.') from exc
        if isinstance(value, dict) and isinstance(value.get('operation'), str):
            operation = value['operation']
            result['operation'] = operation if operation in ('list', 'describe', 'preview', 'run', 'result', 'calibrate', 'package_impact') else None
        request = CheckRequest.parse(value, bound.config['batch']['max_items'])
        service = project_checks_tools(bound, policy_path, dict(os.environ), clock)
        configuration = service.configuration
        result['policy_sha256'] = configuration.policy_sha256
        address = configuration.state / configuration.policy['storage']['response_directory'] / uuid.uuid4().hex / bound.config['paths']['response']
        saver = SavedResponse(address, bound.config['batch']['file_mode'])
        result['items'], result['status'] = service.execute(request)
        result['warnings'] = service.warnings
    except CheckFailure as exc:
        result.update(status=exc.status, error=exc.as_dict(references(service)))
    except (OSError, sqlite3.DatabaseError) as exc:
        known = service is not None and bool(service.known_records)
        failure = CheckFailure('result_save_failed' if known else 'unavailable_policy',
                               'run.result' if known else 'policy', str(exc),
                               'Восстановите доступ к выбранному размещению; прочитайте сохранённые материалы без повторения команды.',
                               status='unconfirmed' if known else 'unavailable')
        result.update(status=failure.status, error=failure.as_dict(references(service)))
    except PoiseError as exc:
        failure = CheckFailure('invalid_project', 'caller', str(exc),
                               'Используйте подлинную native-сессию или явный постоянный POISE_CALLER_BINDING.')
        result.update(status='invalid', error=failure.as_dict(references(service)))
    if saver is not None:
        result['response_path'] = str(saver.path)
        if result['error'] is not None:
            result['error']['references'] = references(service, str(saver.path))
        try:
            body = saver.save(result, canonical=True)
        except OSError as exc:
            result['response_path'] = None
            failure = CheckFailure('result_save_failed', 'response_path', str(exc),
                                   'Восстановите выбранное размещение полного ответа и прочитайте доступные материалы.', status='unconfirmed')
            result.update(status='unconfirmed', error=failure.as_dict(references(service)))
            body = canonical(result)
    else:
        body = canonical(result)
    try:
        output.write(body)
    except (OSError, ValueError) as exc:
        failure = CheckFailure('output_write', 'output', str(exc),
                               'Прочитайте сохранённый полный ответ и материалы по указанным адресам; команду повторять не требуется.')
        error_output.write(canonical(failure.as_dict(references(service, result['response_path']))))
        return 1
    return exit_code(result['status'])


def execute_cli(config_path, checkout, policy_path, mode, stream, output, error_output, clock):
    class ViewOutput:
        def write(self, body):
            result = json.loads(body)
            if result['response_path'] is None:
                output.write(body)
            else:
                _, config, _ = load_config(config_path)
                output.write(saved_result_view(result['response_path'], mode, config['limits']['output_chars']))
    return execute(config_path, checkout, policy_path, stream, ViewOutput(), error_output, clock)
