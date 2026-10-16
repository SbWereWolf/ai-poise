"""Full JSON placement transport; existing common tool presentation owns previews."""
import json
import os
from pathlib import Path

from ..composition import placement_tools
from ..common import PoiseError
from ..infrastructure.goal_config import strict_json
from ..infrastructure.project_binding import read_project_checkout, ProjectBindingFailure
from ..modules.placement.contracts import PlacementInputError, PlacementRequest


def execute(config_path, checkout, policy_path, stream, output):
    try:
        if not Path(config_path).is_absolute():
            raise PlacementInputError('invalid_project', 'config', 'provide:config')
        if not Path(checkout).is_absolute():
            raise PlacementInputError('project_binding', 'checkout', 'select:checkout')
        if not Path(policy_path).is_absolute():
            raise PlacementInputError('invalid_policy', 'policy', 'correct:policy')
        try:
            bound = read_project_checkout(config_path, checkout)
        except ProjectBindingFailure as exc:
            if exc.code == 'invalid_project':
                raise PlacementInputError('invalid_project', 'config', 'provide:config') from exc
            raise PlacementInputError('project_binding', 'checkout', 'select:checkout') from exc
        try:
            limit = bound.config['batch']['max_input_bytes']
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise PoiseError('Placement input exceeds configured max_input_bytes')
            value = strict_json(raw.decode('utf-8'))
        except (PoiseError, UnicodeError, RecursionError) as exc:
            raise PlacementInputError('invalid_request', 'request', 'provide:request') from exc
        request = PlacementRequest.parse(value, bound.config['batch']['max_items'])
        result = placement_tools(bound, policy_path, os.environ).execute(request)
        code = 0 if result['status'] == 'ready' else 1
    except PlacementInputError as exc:
        result, code = exc.response(), 2
    output.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + '\n')
    return code
