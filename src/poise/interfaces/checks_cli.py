"""Opt-in invocation coordination for the existing full named-check CLI."""
import json
import os

from ..common import PoiseError
from ..composition import check_invocation_tools
from ..infrastructure.goal_config import strict_json
from ..infrastructure.project_binding import read_project_checkout
from ..infrastructure.result_views import saved_result_view
from ..modules.project_checks.outcomes import exit_code
from .checks_mcp import load_policy
from .project_checks import prepare_request, execute_prepared, execute_cli


def execute(config, checkout, policy, transport_policy, identity, mode, stream, output, error_output, clock):
    try:
        transport = load_policy(transport_policy)
        bound = read_project_checkout(config, checkout)
        raw = stream.read(bound.config['batch']['max_input_bytes'] + 1)
        if len(raw) > bound.config['batch']['max_input_bytes']:
            raise PoiseError('Check request exceeds configured max_input_bytes')
        value = strict_json(raw.decode('utf-8'))
        prepared = prepare_request(config, checkout, policy, value, clock, dict(os.environ))
        owner = check_invocation_tools(prepared.service, transport, dict(os.environ))
        if prepared.request.operation == 'run':
            identity, control = owner.confirm(prepared.value, 'cli', identity)
            prepared.service.invocation = control
            prepared.service.execution.invocation = control
        else:
            identity = None
        _, result = execute_prepared(prepared, clock)
        if identity is not None:
            owner.finish(identity, result)
        output.write(json.dumps(result, ensure_ascii=False) + '\n' if result['response_path'] is None else
                     saved_result_view(result['response_path'], mode, bound.config['limits']['output_chars']))
        return exit_code(result['status'])
    except (PoiseError, OSError, ValueError, UnicodeError) as exc:
        error_output.write(str(exc) + '\n')
        return 2
