"""Full machine source-index JSON and the existing command-handler envelope."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import sys

from ..common import PoiseError
from ..composition import source_index_tools
from ..infrastructure.goal_config import strict_json
from ..infrastructure.hook_handlers import append_context
from ..infrastructure.project_binding import ProjectBindingFailure, read_project_checkout
from ..modules.foundation.validation import validate_exact_keys
from ..modules.source_index.domain import IndexFailure, IndexRequest, exit_code


def execute(config_path, checkout, policy_path, handler, stream, output):
    packet, service = None, None
    result = {'schema': 'poise-source-index-result-1', 'operation': None, 'status': 'rejected',
              'checkout': None, 'database': None, 'reason': None, 'recovery': None, 'observations': []}
    try:
        if not Path(config_path).is_absolute() or not Path(checkout).is_absolute():
            raise IndexFailure('rejected', 'checkout/config: требуются абсолютные пути',
                               'Выберите general profile и зарегистрированный checkout.')
        try:
            bound = read_project_checkout(config_path, checkout)
        except ProjectBindingFailure as exc:
            raise IndexFailure('rejected', f'checkout/config: {exc}',
                               'Выберите действительный general profile и его зарегистрированный checkout.') from exc
        result['checkout'] = str(bound.checkout)
        limit = bound.config['batch']['max_input_bytes']
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise IndexFailure('rejected', 'request: превышен max_input_bytes', 'Уменьшите входной JSON.')
        value = strict_json(raw.decode('utf-8'))
        if handler:
            validate_exact_keys(value, {'schema', 'invocation_id', 'mode', 'event', 'event_receipt',
                                       'configuration', 'response'}, 'handler input', PoiseError)
            if (value['schema'] != 'poise-hook-handler-input-1' or value['mode'] != 'sync'
                    or not isinstance(value['invocation_id'], str) or not value['invocation_id']
                    or not isinstance(value['response'], dict) or not isinstance(value['event'], dict)
                    or not isinstance(value['event'].get('hook_event_name'), str)):
                raise PoiseError('Неверный command-handler input')
            packet = value
            value = {'operation': 'status', 'arguments': []}
        request = IndexRequest.parse(value)
        result['operation'] = request.operation
        service = source_index_tools(bound, policy_path, dict(os.environ))
        result['database'] = service.workspace.database
        result = service.execute(request)
    except IndexFailure as exc:
        result.update(status=exc.status, reason=exc.reason, recovery=exc.recovery)
    except (PoiseError, OSError, ValueError, UnicodeError, RecursionError) as exc:
        result.update(status='rejected', reason=f'request/config: {exc}',
                      recovery='Исправьте явный вход и выбранные настройки.')
    code = exit_code(result['status'])
    if handler and packet is not None:
        response = deepcopy(packet['response'])
        if result['status'] != 'disabled':
            command = shlex.join([sys.executable, '-B', '-m', 'poise', 'source-index',
                                  '--config', str(config_path), '--checkout', str(checkout),
                                  '--policy', str(policy_path)])
            context = f"source-index: {result['status']}; checkout={result['checkout']}; {command}"
            if result['reason'] is not None:
                context += '\n' + result['reason'] + '\n' + result['recovery']
            response = append_context(response, packet['event']['hook_event_name'], context)
        result = {'schema': 'poise-hook-handler-result-1', 'invocation_id': packet['invocation_id'],
                  'response': response, 'control': {'action': 'continue', 'reason': None}}
    output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + '\n')
    return code
