import json

from ..composition import route_migration_tools
from ..modules.foundation.errors import PoiseError


def execute(config_path, output):
    try:
        result = route_migration_tools(config_path).migrate()
        code = 0
    except (PoiseError, UnicodeError, RecursionError) as exc:
        result = {"status": "rejected", "reason": str(exc)}
        code = 2
    output.write(json.dumps(result, ensure_ascii=False) + "\n")
    return code
