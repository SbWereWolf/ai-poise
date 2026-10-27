"""Read a complete general profile without importing the Task kernel."""
from pathlib import Path

from ..common import PoiseError, configured_root, descendant
from ..modules.projects.harness import HarnessProject
from .goal_config import strict_json


def load_harness_config(path):
    path = Path(path).resolve()
    try:
        profile = HarnessProject.parse(strict_json(path.read_text(encoding='utf-8')))
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise PoiseError(f'general_config_invalid: restore the selected profile {path}: {exc}') from exc
    state = configured_root(path.parent, profile.raw['paths']['state'])
    for key in ('database', 'lock', 'runtime', 'runs', 'stdout', 'stderr', 'response'):
        descendant(state, profile.raw['paths'][key])
    return path.parent, profile.raw, {}
