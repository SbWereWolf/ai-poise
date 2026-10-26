"""Explicit tool-result JSON transport, without Task composition or execution."""
from pathlib import Path
import os

from ..common import PoiseError
from ..composition import tool_result_tools
from ..infrastructure.goal_config import strict_json
from ..infrastructure.project_binding import read_project_checkout
from ..modules.result_views.tool_results import ToolResultFailure, ToolResultPolicy, encoded


def execute(config_path, checkout, policy_path, audience, stream, output):
    original_exit = None
    try:
        if (config_path is None) != (checkout is None):
            raise ToolResultFailure('invalid_request', 'selection', 'provide:request')
        if not all(Path(p).is_absolute() for p in (config_path, checkout, policy_path) if p is not None):
            raise ToolResultFailure('invalid_request', 'selection', 'provide:request')
        try: raw_policy = Path(policy_path).read_bytes()
        except OSError as exc:
            raise ToolResultFailure('policy_unavailable', 'policy', 'provide:policy') from exc
        try: value = strict_json(raw_policy.decode())
        except (PoiseError, UnicodeError) as exc:
            raise ToolResultFailure('invalid_policy', 'policy', 'correct:policy') from exc
        policy = ToolResultPolicy.parse(value).data
        bound = None if config_path is None else read_project_checkout(config_path, checkout)
        limit = policy['input']['max_bytes']
        if bound is not None: limit = min(limit, bound.config['batch']['max_input_bytes'])
        raw = stream.read(limit+1)
        if len(raw) > limit: raise ToolResultFailure('invalid_request', 'request', 'provide:request')
        try: request = strict_json(raw.decode())
        except (PoiseError, UnicodeError) as exc:
            raise ToolResultFailure('invalid_request', 'request', 'provide:request') from exc
        if isinstance(request, dict): original_exit = request.get('exit_code')
        result = tool_result_tools(bound, policy, dict(os.environ)).execute(request, audience)
        code = 0
    except ToolResultFailure as exc:
        result, code = exc.result(original_exit), 2
    except (PoiseError, OSError, ValueError) as exc:
        result, code = ToolResultFailure('invalid_request', 'selection', 'provide:request').result(original_exit), 2
    output.write(encoded(result))
    return code
