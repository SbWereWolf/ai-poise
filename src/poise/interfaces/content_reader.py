"""Public complete JSON port and CLI text framing, with one execution core."""
import os
from pathlib import Path
import sqlite3
import uuid
from ..common import PoiseError
from ..composition import content_reader_tools
from ..infrastructure.goal_config import strict_json
from ..infrastructure.project_binding import read_project_checkout, ProjectBindingFailure
from ..infrastructure.result_views import SavedResponse
from ..infrastructure.document_intake.presentation import canonical_result, text_frames
from ..infrastructure.document_intake.configuration import ReaderConfiguration
from ..modules.document_intake.contracts import ContentReadRequest, ReadFailure, OPERATIONS
from ..modules.document_intake.command import CommandFailure


def execute(config_path, checkout, policy_path, stream, output, error_output, clock):
    return _execute(config_path, checkout, policy_path, 'json', stream, output, error_output, clock)


def execute_cli(config_path, checkout, policy_path, representation, stream, output, error_output, clock):
    return _execute(config_path, checkout, policy_path, representation, stream, output, error_output, clock)


def _write(output, body):
    remaining = body
    while remaining:
        count = output.write(remaining)
        if count is None:
            return
        if type(count) is not int or not 0 < count <= len(remaining):
            raise OSError('Output stream did not accept content')
        remaining = remaining[count:]


def _execute(config_path, checkout, policy_path, representation, stream, output, error_output, clock):
    result = dict(schema='poise-content-read-result-1', status='ready', operation=None,
                  project=None, checkout=None, policy_sha256=None, context=None, items=[], summary=None,
                  details=None, error=None, response_path=None)
    service, saved, code = None, None, 0
    try:
        if not Path(config_path).is_absolute():
            raise ReadFailure('invalid_project')
        if not Path(checkout).is_absolute():
            raise ReadFailure('project_binding')
        try:
            bound = read_project_checkout(config_path, checkout)
        except ProjectBindingFailure as exc:
            raise ReadFailure(exc.code) from exc
        result.update(project=bound.config['project'], checkout=str(bound.checkout))
        raw = stream.read(bound.config['batch']['max_input_bytes'] + 1)
        if len(raw) > bound.config['batch']['max_input_bytes']:
            raise ReadFailure('invalid_request')
        try:
            value = strict_json(raw.decode('utf-8'))
        except (PoiseError, UnicodeError, ValueError, RecursionError) as exc:
            raise ReadFailure('invalid_request') from exc
        if isinstance(value, dict) and isinstance(value.get('operation'), str) and value['operation'] in OPERATIONS:
            result['operation'] = value['operation']
        configuration = ReaderConfiguration.load(bound, policy_path)
        result['policy_sha256'] = configuration.policy_sha256
        request = ContentReadRequest.parse(value, bound.config['batch']['max_items'])
        service = content_reader_tools(configuration, dict(os.environ), clock)
        code, saved = service.execute(request, representation, result)
    except CommandFailure as exc:
        result.update(status='failed', error=exc.as_dict())
        code = 1
    except ReadFailure as exc:
        result.update(status=exc.status, error=exc.as_dict(), details=exc.details)
        if exc.status == 'rejected':
            result['items'] = []
        code = exc.exit
    except PoiseError as exc:
        failure = ReadFailure('admission_unavailable')
        result.update(status=failure.status, error=failure.as_dict())
        code = failure.exit
    except (OSError, sqlite3.Error) as exc:
        failure = ReadFailure('admission_unavailable')
        result.update(status=failure.status, error=failure.as_dict())
        code = failure.exit
    if saved is None and service is not None:
        root = Path(service.configuration.policy['storage']['control_response_root'])
        path = root / uuid.uuid4().hex / service.configuration.bound.config['paths']['response']
        result['response_path'] = str(path)
        try:
            saved = SavedResponse(path, service.configuration.bound.config['batch']['file_mode']).save(result, canonical=True)
        except OSError:
            failure = ReadFailure('result_save_failed')
            result.update(status=failure.status, error=failure.as_dict(), response_path=None)
            code = 1
    body = canonical_result(result) if representation == 'json' else text_frames(result)
    try:
        _write(output, body)
    except (OSError, ValueError):
        failure = ReadFailure('output_write')
        late = result | dict(status='failed', error=failure.as_dict())
        _write(error_output, canonical_result(late))
        return 1
    return code
